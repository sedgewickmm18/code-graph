import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import tomllib
from multilspy import SyncLanguageServer
from tree_sitter import Language, Node

import tree_sitter_python as tspython

from ...entities.entity import Entity
from ...entities.file import File
from ..tree_sitter_base import TreeSitterAnalyzer
from .ts_resolver import TreeSitterPythonResolver

if TYPE_CHECKING:
    from ...graph import Graph

import logging
logger = logging.getLogger('code_graph')

# -------------------------------------------------------------------
# Constants for security entity extraction
# -------------------------------------------------------------------

# Function names that count as template rendering calls
_RENDER_CALLS = frozenset({
    "render_template", "render_template_string",
    "TemplateResponse", "render",
})

# Known sanitization function names (T4 — XSS)
_SANITIZERS = frozenset({
    "escape", "html_escape", "sanitize", "sanitize_input",
    "clean", "bleach_clean",
})
_SANITIZER_PREFIXES = ("sanitize_", "clean_", "escape_")

# File-system operation names to track (T6 — path traversal)
_FS_OPS = frozenset({"open", "join", "abspath", "realpath", "expanduser"})
_FS_OP_ATTRS = frozenset({"os.path.join", "os.path.abspath", "os.path.realpath",
                           "os.path.expanduser", "pathlib.Path"})

# Request taint sources (T6)
_TAINT_SOURCES = frozenset({
    "request.args", "request.form", "request.json",
    "request.data", "request.values", "request.files",
    "request.get_json",
})


_RESOLVER_ENV = "CODE_GRAPH_PY_RESOLVER"
_RESOLVER_TREE_SITTER = "tree_sitter"


class PythonAnalyzer(TreeSitterAnalyzer):
    entity_node_types = {
        'class_definition': "Class",
        'function_definition': "Function",
    }
    type_definition_node_types = ('class_definition',)
    callable_definition_node_types = ('function_definition', 'class_definition')
    type_resolution_keys = ("base_class", "parameters", "return_type")
    method_resolution_keys = ("call",)

    def __init__(self) -> None:
        super().__init__(Language(tspython.language()))
        # Resolver selection: defaults to 'tree_sitter' static project-wide resolver
        # for fast sub-second indexing without virtualenv/LSP overhead.
        # Set CODE_GRAPH_PY_RESOLVER=jedi or CODE_GRAPH_PY_RESOLVER=lsp to force historical LSP mode.
        resolver_choice = os.environ.get(_RESOLVER_ENV, _RESOLVER_TREE_SITTER).strip().lower()
        if resolver_choice in (_RESOLVER_TREE_SITTER, ""):
            self._ts_resolver: Optional[TreeSitterPythonResolver] = (
                TreeSitterPythonResolver(self.language)
            )
            logger.info("PythonAnalyzer: tree-sitter static resolver enabled (default)")
        else:
            self._ts_resolver = None

    def resolve(
        self,
        files: dict[Path, File],
        lsp: SyncLanguageServer,
        file_path: Path,
        path: Path,
        node: Node,
    ) -> list[tuple[File, Node]]:
        """Resolve a name node to ``(File, def_node)`` pairs.

        When ``CODE_GRAPH_PY_RESOLVER=tree_sitter`` is set, bypass the LSP
        and use the project-wide static resolver. Otherwise fall through to
        the default jedi-backed implementation in ``AbstractAnalyzer``.
        """
        if self._ts_resolver is not None:
            return self._ts_resolver.resolve(files, file_path, path, node)
        return super().resolve(files, lsp, file_path, path, node)

    def needs_lsp(self) -> bool:
        # When the tree-sitter resolver is active we don't touch the LSP, so
        # the orchestrator can skip starting one.
        return self._ts_resolver is None

    def add_dependencies(self, path: Path, files: list[Path]):
        # When the tree-sitter resolver is active, we resolve statically
        # against the in-project files only — installing the project's
        # transitive Python deps just to feed jedi adds 10s–10min of
        # zero-value pip work. Short-circuit it.
        if self._ts_resolver is not None:
            return
        if Path(f"{path}/venv").is_dir():
            return
        subprocess.run(["python3", "-m", "venv", "venv"], cwd=str(path))
        if Path(f"{path}/pyproject.toml").is_file():
            subprocess.run(["pip", "install", "poetry"], cwd=str(path), env={"VIRTUAL_ENV": f"{path}/venv", "PATH": f"{path}/venv/bin:{os.environ['PATH']}"})
            subprocess.run(["poetry", "install"], cwd=str(path), env={"VIRTUAL_ENV": f"{path}/venv", "PATH": f"{path}/venv/bin:{os.environ['PATH']}"})
            try:
                with open(f"{path}/pyproject.toml", 'rb') as file:
                    pyproject_data = tomllib.load(file)
                    dependencies = (pyproject_data.get("tool") or {}).get("poetry", {}).get("dependencies", {})
                    for requirement in dependencies:
                        files.extend(Path(f"{path}/venv/lib").rglob(f"**/site-packages/{requirement}/*.py"))
            except Exception as e:
                logger.warning("Failed to parse %s/pyproject.toml: %s", path, e)
        elif Path(f"{path}/requirements.txt").is_file():
            subprocess.run(["pip", "install", "-r", "requirements.txt"], cwd=str(path), env={"VIRTUAL_ENV": f"{path}/venv", "PATH": f"{path}/venv/bin:{os.environ['PATH']}"})
            with open(f"{path}/requirements.txt", 'r') as file:
                requirements = [line.strip().split("==") for line in file if line.strip()]
                for requirement in requirements:
                    files.extend(Path(f"{path}/venv/lib/").rglob(f"**/site-packages/{requirement}/*.py"))

    def get_entity_name(self, node: Node) -> str:
        if node.type in ['class_definition', 'function_definition']:
            return node.child_by_field_name('name').text.decode('utf-8')
        raise ValueError(f"Unknown entity type: {node.type}")

    def get_entity_docstring(self, node: Node) -> Optional[str]:
        if node.type in ['class_definition', 'function_definition']:
            body = node.child_by_field_name('body')
            if body.child_count > 0 and body.children[0].type == 'expression_statement':
                docstring_node = body.children[0].child(0)
                return docstring_node.text.decode('utf-8')
            return None
        raise ValueError(f"Unknown entity type: {node.type}")

    def add_symbols(self, entity: Entity) -> None:
        if entity.node.type == 'class_definition':
            superclasses = entity.node.child_by_field_name("superclasses")
            if superclasses:
                base_classes_captures = self._captures("(argument_list (_) @base_class)", superclasses)
                if 'base_class' in base_classes_captures:
                    for base_class in base_classes_captures['base_class']:
                        entity.add_symbol("base_class", base_class)
        elif entity.node.type == 'function_definition':
            call_captures = self._captures("(call) @reference.call", entity.node)
            if 'reference.call' in call_captures:
                for caller in call_captures['reference.call']:
                    entity.add_symbol("call", caller)
            param_captures = self._captures("(typed_parameter type: (_) @parameter)", entity.node)
            if 'parameter' in param_captures:
                for parameter in param_captures['parameter']:
                    entity.add_symbol("parameters", parameter)
            return_type = entity.node.child_by_field_name('return_type')
            if return_type:
                entity.add_symbol("return_type", return_type)

            # T2: decorators live in the decorated_definition parent node
            # (tree-sitter-python wraps decorated functions in decorated_definition)
            parent = entity.node.parent
            if parent is not None and parent.type == "decorated_definition":
                for child in parent.children:
                    if child.type == "decorator":
                        entity.add_symbol("decorator", child)

            # T4: render_template / TemplateResponse calls → Variable nodes
            # T6: file-system operations → FileSystemOp nodes
            for call_node in call_captures.get('reference.call', []):
                func_name = self._call_name(call_node)
                if func_name in _RENDER_CALLS:
                    entity.add_symbol("render_call", call_node)
                if self._is_sanitizer(func_name):
                    entity.add_symbol("sanitizer_call", call_node)
                if func_name in _FS_OPS or func_name in _FS_OP_ATTRS:
                    entity.add_symbol("fs_op", call_node)

    # ------------------------------------------------------------------
    # Security entity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _call_name(call_node: Node) -> str:
        """Return the bare function name from a call node."""
        func = call_node.child_by_field_name("function")
        if func is None:
            return ""
        if func.type == "attribute":
            obj = func.child_by_field_name("object")
            attr = func.child_by_field_name("attribute")
            if obj is not None and attr is not None:
                return f"{obj.text.decode('utf-8')}.{attr.text.decode('utf-8')}"
            if attr is not None:
                return attr.text.decode("utf-8")
        return func.text.decode("utf-8")

    @staticmethod
    def _is_sanitizer(name: str) -> bool:
        return name in _SANITIZERS or any(name.startswith(p) for p in _SANITIZER_PREFIXES)

    @staticmethod
    def _node_is_tainted(node: Node) -> bool:
        """Return True if any argument text looks like a request taint source."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        raw = args.text.decode("utf-8", errors="replace")
        return any(src in raw for src in _TAINT_SOURCES)

    def persist_security_entities(
        self,
        entity: Entity,
        file_path: Path,
        graph: "Graph",  # type: ignore[name-defined]
        func_id: int,
    ) -> None:
        """Persist Decorator, Variable, and FileSystemOp nodes for a function entity.

        Called by SourceAnalyzer after first_pass graph IDs are available.
        """
        path_str = str(file_path)

        # T2 — Decorators
        for dec_node in entity.symbols.get("decorator", []):
            # The decorator text starts with '@'; strip it and any arguments
            raw = dec_node.text.decode("utf-8")
            name_part = raw.lstrip("@").split("(")[0].strip()
            graph.add_decorator(
                name=name_part,
                path=path_str,
                src_line=dec_node.start_point.row,
                func_id=func_id,
            )

        # T4 — Template context variables
        for call_node in entity.symbols.get("render_call", []):
            args = call_node.child_by_field_name("arguments")
            if args is None:
                continue
            # Extract keyword arguments as Variable nodes
            kw_captures = self._captures("(keyword_argument name: (identifier) @kw)", args)
            for kw_node in kw_captures.get("kw", []):
                var_name = kw_node.text.decode("utf-8")
                graph.add_variable(
                    name=var_name,
                    path=path_str,
                    src_line=call_node.start_point.row,
                )

        # T6 — File-system operations
        for call_node in entity.symbols.get("fs_op", []):
            op_name = self._call_name(call_node)
            tainted = self._node_is_tainted(call_node)
            graph.add_fs_op(
                op=op_name,
                path=path_str,
                src_line=call_node.start_point.row,
                func_id=func_id,
                tainted=tainted,
            )

    def is_dependency(self, file_path: str) -> bool:
        return "venv" in file_path

    def _module_parts(self, file_path: Path, root: Path) -> Optional[list[str]]:
        """Dotted module path components for ``file_path`` relative to ``root``."""
        try:
            rel = file_path.relative_to(root)
        except ValueError:
            return None
        parts = list(rel.with_suffix('').parts)
        if parts and parts[-1] == '__init__':
            parts = parts[:-1]
        return parts

    def build_import_index(self, files: dict[Path, File], root: Path) -> object:
        """Index in-repo files by dotted module name.

        Two maps: ``exact`` keyed by the full dotted path from ``root`` and
        ``suffix`` keyed by every trailing sub-path (first file wins). The
        suffix map tolerates ``src/``/``lib/`` layouts where the import name
        (``matplotlib.axes``) differs from the path-from-root
        (``lib.matplotlib.axes``).

        Only Python files are indexed; ``files`` carries every analyzed
        source file, and a Python ``import pkg.mod`` must not resolve to a
        same-named non-Python file such as ``pkg/mod.java``.
        """
        exact: dict[str, File] = {}
        suffix: dict[str, File] = {}
        for fpath, file in files.items():
            if fpath.suffix != '.py':
                continue
            if self.is_dependency(str(fpath)):
                continue
            parts = self._module_parts(fpath, root)
            if not parts:
                continue
            exact.setdefault('.'.join(parts), file)
            for i in range(len(parts)):
                suffix.setdefault('.'.join(parts[i:]), file)
        return {'exact': exact, 'suffix': suffix}

    def _resolve_dotted(self, dotted: str, index: dict) -> Optional[File]:
        if not dotted:
            return None
        f = index['exact'].get(dotted) or index['suffix'].get(dotted)
        if f is None and '.' in dotted:
            # imported name may be a symbol inside a module; drop the last part.
            parent = dotted.rsplit('.', 1)[0]
            f = index['exact'].get(parent) or index['suffix'].get(parent)
        return f

    def _import_requests(self, file: File) -> list[tuple[str, int]]:
        """Extract (dotted, level) resolution requests from import statements."""
        requests: list[tuple[str, int]] = []
        captures = self._captures(
            "(import_statement) @i (import_from_statement) @f",
            file.tree.root_node,
        )
        for node in captures.get('i', []):
            for child in node.named_children:
                target = child
                if child.type == 'aliased_import':
                    target = child.child_by_field_name('name')
                if target is not None and target.type == 'dotted_name':
                    requests.append((target.text.decode('utf-8'), 0))
        for node in captures.get('f', []):
            module = node.child_by_field_name('module_name')
            level = 0
            base = ''
            if module is not None:
                if module.type == 'relative_import':
                    prefix = next((c for c in module.children if c.type == 'import_prefix'), None)
                    level = len(prefix.text.decode('utf-8')) if prefix is not None else 1
                    dotted_part = next((c for c in module.named_children if c.type == 'dotted_name'), None)
                    base = dotted_part.text.decode('utf-8') if dotted_part is not None else ''
                else:
                    base = module.text.decode('utf-8')
            requests.append((base, level))
            for name_node in node.children_by_field_name('name'):
                leaf = name_node
                if name_node.type == 'aliased_import':
                    leaf = name_node.child_by_field_name('name')
                if leaf is not None:
                    name_txt = leaf.text.decode('utf-8')
                    requests.append((f"{base}.{name_txt}" if base else name_txt, level))
        return requests

    def resolve_imports(self, file: File, root: Path, index: object) -> list[File]:
        if not index:
            return []
        package_parts = self._module_parts(file.path, root)
        if package_parts is None:
            return []
        # Package of the importing file = its parent dotted path.
        package_parts = package_parts[:-1] if package_parts else []
        seen: set[Path] = set()
        targets: list[File] = []
        for dotted, level in self._import_requests(file):
            if level:
                base = package_parts[: len(package_parts) - (level - 1)] if level > 1 else list(package_parts)
                full = '.'.join([*base, dotted]) if dotted else '.'.join(base)
            else:
                full = dotted
            resolved = self._resolve_dotted(full, index)
            if resolved is None or resolved.path == file.path or resolved.path in seen:
                continue
            if self.is_dependency(str(resolved.path)):
                continue
            seen.add(resolved.path)
            targets.append(resolved)
        return targets

    def _extract_type_target(self, node: Node) -> Optional[Node]:
        if node.type == 'attribute':
            return node.child_by_field_name('attribute')
        return node

    def _extract_call_target(self, node: Node) -> Optional[Node]:
        if node.type == 'call':
            node = node.child_by_field_name('function')
            if node and node.type == 'attribute':
                node = node.child_by_field_name('attribute')
        return node
