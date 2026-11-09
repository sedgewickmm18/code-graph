"""Tree-sitter-based static symbol resolver for Java.

A drop-in replacement for the Eclipse JDT.LS / multilspy round-trip used by
``JavaAnalyzer.resolve``. Builds a project-wide symbol table from the
already-parsed tree-sitter trees and answers definition queries by static name,
package, import, and receiver-type resolution.

Selected by default or explicitly via ``CODE_GRAPH_JAVA_RESOLVER=tree_sitter``.
To opt into JDT.LS LSP resolution, set ``CODE_GRAPH_JAVA_RESOLVER=lsp``.

Returns lists of ``(File, Node)`` tuples where ``Node`` is the definition's
tree-sitter node in the target file, preserving compatibility with
``JavaAnalyzer.resolve_type`` and ``JavaAnalyzer.resolve_method``.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Node

from api.entities.file import File

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Definition:
    """A type or callable definition in the project."""

    file_path: Path
    node: Node
    kind: str  # 'class' | 'interface' | 'enum' | 'record' | 'method' | 'constructor' | 'field'
    name: str
    package: str = ""
    class_name: Optional[str] = None
    return_type: Optional[str] = None


@dataclass
class _CompilationUnitIndex:
    """Per-file index of package, imports, types, methods, and fields."""

    file_path: Path
    package: str = ""
    # simple type name -> _Definition (Class, Interface, Enum, Record)
    types: dict[str, _Definition] = field(default_factory=dict)
    # class_name -> { method_name: [_Definition, ...] }
    class_methods: dict[str, dict[str, list[_Definition]]] = field(default_factory=dict)
    # class_name -> { field_name: field_type_name }
    class_fields: dict[str, dict[str, str]] = field(default_factory=dict)
    # class_name -> superclass_name
    class_super: dict[str, str] = field(default_factory=dict)
    # class_name -> [interface_name, ...]
    class_interfaces: dict[str, list[str]] = field(default_factory=dict)
    # Single type imports: simple_name -> FQCN (e.g. 'AuthService' -> 'com.example.AuthService')
    single_imports: dict[str, str] = field(default_factory=dict)
    # Wildcard imports: [package_name, ...] (e.g. ['com.example.util', 'java.util'])
    wildcard_imports: list[str] = field(default_factory=list)
    # Static imports: method_or_field_name -> FQCN (e.g. 'assertEquals' -> 'org.junit.Assert.assertEquals')
    static_imports: dict[str, str] = field(default_factory=dict)
    # Static wildcard imports: [class_fqcn, ...] (e.g. ['org.junit.Assert'])
    static_wildcards: list[str] = field(default_factory=list)


def _extract_text(node: Optional[Node]) -> str:
    """Safely extract decoded UTF-8 text from a node."""
    if node is None:
        return ""
    return node.text.decode("utf-8")


def _find_parent_types(node: Node, target_types: tuple[str, ...]) -> Optional[Node]:
    """Walk up parent chain until finding a node of target type."""
    curr = node.parent
    while curr is not None:
        if curr.type in target_types:
            return curr
        curr = curr.parent
    return None


class TreeSitterJavaResolver:
    """Project-wide Java symbol resolver.

    Builds an in-memory package and type index across all parsed `.java` files
    and resolves type references and method invocations without starting a JVM
    or JDT.LS instance.
    """

    def __init__(self, language: Language) -> None:
        self._language = language
        self._files_id: Optional[int] = None
        self._files: Optional[dict[Path, File]] = None
        self._project_root: Optional[Path] = None
        # file_path -> _CompilationUnitIndex
        self._file_indices: dict[Path, _CompilationUnitIndex] = {}
        # package -> { simple_type_name: _Definition }
        self._package_types: dict[str, dict[str, _Definition]] = defaultdict(dict)
        # simple_type_name -> [_Definition, ...] (cross-package index)
        self._all_types_by_name: dict[str, list[_Definition]] = defaultdict(list)
        # method_name -> [_Definition, ...]
        self._all_methods_by_name: dict[str, list[_Definition]] = defaultdict(list)
        self._build_lock = threading.Lock()

    def _ensure_built(self, files: dict[Path, File], project_root: Path) -> None:
        if self._files_id == id(files) and self._project_root == project_root:
            return
        with self._build_lock:
            if self._files_id == id(files) and self._project_root == project_root:
                return

            file_indices: dict[Path, _CompilationUnitIndex] = {}
            package_types: dict[str, dict[str, _Definition]] = defaultdict(dict)
            all_types_by_name: dict[str, list[_Definition]] = defaultdict(list)
            all_methods_by_name: dict[str, list[_Definition]] = defaultdict(list)

            for file_path, file in files.items():
                if file_path.suffix != ".java" or file.tree is None:
                    continue
                cu = _CompilationUnitIndex(file_path=file_path)
                file_indices[file_path] = cu
                self._index_file(
                    cu,
                    file.tree.root_node,
                    package_types,
                    all_types_by_name,
                    all_methods_by_name,
                )

            self._files = files
            self._file_indices = file_indices
            self._package_types = package_types
            self._all_types_by_name = all_types_by_name
            self._all_methods_by_name = all_methods_by_name
            self._project_root = project_root
            self._files_id = id(files)

    def _index_file(
        self,
        cu: _CompilationUnitIndex,
        root: Node,
        package_types: dict[str, dict[str, _Definition]],
        all_types_by_name: dict[str, list[_Definition]],
        all_methods_by_name: dict[str, list[_Definition]],
    ) -> None:
        # 1. Package declaration
        for child in root.named_children:
            if child.type == "package_declaration":
                pkg_id = child.child_by_field_name("name") or next(
                    (c for c in child.named_children if c.type in ("scoped_identifier", "identifier")),
                    None,
                )
                if pkg_id is not None:
                    cu.package = _extract_text(pkg_id)
                break

        # 2. Import declarations
        for child in root.named_children:
            if child.type == "import_declaration":
                self._index_import(cu, child)

        # 3. Type declarations (classes, interfaces, enums, records)
        self._index_types_and_members(
            cu,
            root,
            package_types,
            all_types_by_name,
            all_methods_by_name,
        )

    def _index_import(self, cu: _CompilationUnitIndex, node: Node) -> None:
        is_static = any(c.type == "static" for c in node.children)
        is_wildcard = any(c.type == "asterisk" for c in node.children)

        # Find the imported identifier / scoped identifier
        target_node = next(
            (c for c in node.named_children if c.type in ("scoped_identifier", "identifier")),
            None,
        )
        if target_node is None:
            return
        target_text = _extract_text(target_node)
        if not target_text:
            return

        if is_static:
            if is_wildcard:
                cu.static_wildcards.append(target_text)
            else:
                simple_name = target_text.rsplit(".", 1)[-1]
                cu.static_imports[simple_name] = target_text
        else:
            if is_wildcard:
                cu.wildcard_imports.append(target_text)
            else:
                simple_name = target_text.rsplit(".", 1)[-1]
                cu.single_imports[simple_name] = target_text

    def _index_types_and_members(
        self,
        cu: _CompilationUnitIndex,
        root: Node,
        package_types: dict[str, dict[str, _Definition]],
        all_types_by_name: dict[str, list[_Definition]],
        all_methods_by_name: dict[str, list[_Definition]],
    ) -> None:
        type_node_types = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
        }

        # Traverse all top-level and inner type declarations
        stack = [root]
        while stack:
            curr = stack.pop()
            if curr.type in type_node_types:
                kind = type_node_types[curr.type]
                name_node = curr.child_by_field_name("name")
                if name_node is not None:
                    name = _extract_text(name_node)
                    d = _Definition(
                        file_path=cu.file_path,
                        node=curr,
                        kind=kind,
                        name=name,
                        package=cu.package,
                    )
                    cu.types[name] = d
                    package_types[cu.package][name] = d
                    all_types_by_name[name].append(d)

                    # Extract superclass
                    super_node = curr.child_by_field_name("superclass")
                    if super_node is not None:
                        # superclass node has a type_identifier or scoped_type_identifier child
                        super_id = next(
                            (c for c in super_node.named_children if "type" in c.type or c.type == "identifier"),
                            None,
                        )
                        if super_id is not None:
                            cu.class_super[name] = _extract_text(super_id)

                    # Extract interfaces
                    interfaces_node = curr.child_by_field_name("interfaces")
                    if interfaces_node is not None:
                        for iface in interfaces_node.named_children:
                            if iface.type == "type_list":
                                for t in iface.named_children:
                                    cu.class_interfaces.setdefault(name, []).append(_extract_text(t))
                            elif "type" in iface.type or iface.type == "identifier":
                                cu.class_interfaces.setdefault(name, []).append(_extract_text(iface))

                    # Index fields and methods in body
                    body_node = curr.child_by_field_name("body")
                    if body_node is not None:
                        self._index_body_members(
                            cu,
                            name,
                            body_node,
                            all_methods_by_name,
                        )

            # Recurse into children
            stack.extend(reversed(curr.named_children))

    def _index_body_members(
        self,
        cu: _CompilationUnitIndex,
        class_name: str,
        body_node: Node,
        all_methods_by_name: dict[str, list[_Definition]],
    ) -> None:
        for member in body_node.named_children:
            if member.type == "field_declaration":
                type_node = member.child_by_field_name("type")
                type_text = _extract_text(type_node)
                for decl in member.named_children:
                    if decl.type == "variable_declarator":
                        var_name_node = decl.child_by_field_name("name")
                        if var_name_node is not None:
                            var_name = _extract_text(var_name_node)
                            cu.class_fields.setdefault(class_name, {})[var_name] = type_text

            elif member.type in ("method_declaration", "constructor_declaration"):
                mname_node = member.child_by_field_name("name")
                if mname_node is not None:
                    mname = _extract_text(mname_node)
                    ret_type_node = member.child_by_field_name("type")
                    ret_type = _extract_text(ret_type_node) if ret_type_node else None
                    kind = "method" if member.type == "method_declaration" else "constructor"
                    d = _Definition(
                        file_path=cu.file_path,
                        node=member,
                        kind=kind,
                        name=mname,
                        package=cu.package,
                        class_name=class_name,
                        return_type=ret_type,
                    )
                    cu.class_methods.setdefault(class_name, {}).setdefault(mname, []).append(d)
                    all_methods_by_name[mname].append(d)

    # ---------------------------------------------------------------------------
    # Query / Resolve
    # ---------------------------------------------------------------------------

    def resolve(
        self,
        files: dict[Path, File],
        file_path: Path,
        project_root: Path,
        node: Node,
    ) -> list[tuple[File, Node]]:
        """Resolve an AST node (type identifier or method invocation name) to (File, Node) pairs."""
        self._ensure_built(files, project_root)
        cu = self._file_indices.get(file_path)
        if cu is None:
            return []

        # Check if this node is a method invocation name
        if node.parent is not None and node.parent.type == "method_invocation" and node.parent.child_by_field_name("name") == node:
            defs = self._resolve_method_invocation(cu, node.parent, node)
        else:
            # Otherwise treat as a type identifier / reference
            defs = self._resolve_type_node(cu, node)

        out: list[tuple[File, Node]] = []
        seen: set[tuple[Path, int]] = set()
        for d in defs:
            f = files.get(d.file_path)
            if f is None:
                continue
            key = (d.file_path, d.node.start_byte)
            if key not in seen:
                seen.add(key)
                out.append((f, d.node))
        return out

    def _resolve_type_node(self, cu: _CompilationUnitIndex, node: Node) -> list[_Definition]:
        """Extract type name and resolve to Type Definitions."""
        type_name = _extract_text(node)
        # Strip generic parameters like List<User> -> List
        if "<" in type_name:
            type_name = type_name.split("<", 1)[0].strip()
        # Strip array brackets
        type_name = type_name.replace("[]", "").strip()
        if not type_name:
            return []
        return self._resolve_type_name(cu, type_name)

    def _resolve_type_name(self, cu: _CompilationUnitIndex, type_name: str) -> list[_Definition]:
        """Resolve a type name via local, single-import, same-package, wildcard-import, or global fallback."""
        # 1. Qualified type name: com.example.model.User
        if "." in type_name:
            pkg, simple_name = type_name.rsplit(".", 1)
            if pkg in self._package_types and simple_name in self._package_types[pkg]:
                return [self._package_types[pkg][simple_name]]
            # Fallback to simple name
            type_name = simple_name

        # 2. Local type in the same file
        if type_name in cu.types:
            return [cu.types[type_name]]

        # 3. Explicit single-type import: import com.example.User;
        if type_name in cu.single_imports:
            fqcn = cu.single_imports[type_name]
            pkg, simple_name = fqcn.rsplit(".", 1) if "." in fqcn else ("", fqcn)
            if pkg in self._package_types and simple_name in self._package_types[pkg]:
                return [self._package_types[pkg][simple_name]]

        # 4. Same package
        if cu.package in self._package_types and type_name in self._package_types[cu.package]:
            return [self._package_types[cu.package][type_name]]

        # 5. Wildcard package imports: import com.example.util.*;
        for pkg in cu.wildcard_imports:
            if pkg in self._package_types and type_name in self._package_types[pkg]:
                return [self._package_types[pkg][type_name]]

        # 6. Global cross-project fallback
        candidates = self._all_types_by_name.get(type_name, [])
        return list(candidates)

    def _resolve_method_invocation(
        self,
        cu: _CompilationUnitIndex,
        call_node: Node,
        method_name_node: Node,
    ) -> list[_Definition]:
        """Resolve a method invocation to matching Method/Constructor Definitions."""
        method_name = _extract_text(method_name_node)
        if not method_name:
            return []

        recv_node = call_node.child_by_field_name("object")

        # Find enclosing class and method
        enclosing_class = _find_parent_types(
            call_node,
            ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"),
        )
        enclosing_class_name = _extract_text(enclosing_class.child_by_field_name("name")) if enclosing_class else ""

        enclosing_method = _find_parent_types(
            call_node,
            ("method_declaration", "constructor_declaration"),
        )

        recv_type_name: Optional[str] = None

        if recv_node is None:
            # Unqualified call: e.g. `helper()` or static import
            recv_type_name = enclosing_class_name
        else:
            recv_text = _extract_text(recv_node).strip()
            if recv_text == "this":
                recv_type_name = enclosing_class_name
            elif recv_text == "super":
                recv_type_name = cu.class_super.get(enclosing_class_name)
            else:
                if recv_text.startswith("this."):
                    recv_text = recv_text[5:]

                # 1. Check if receiver matches a known Class (static invocation: StringUtils.isEmpty)
                type_defs = self._resolve_type_name(cu, recv_text)
                if type_defs:
                    recv_type_name = type_defs[0].name
                else:
                    # 2. Check local variables & parameters in enclosing method
                    if enclosing_method is not None:
                        recv_type_name = self._find_var_type_in_method(enclosing_method, recv_text)

                    # 3. Check fields in enclosing class
                    if not recv_type_name and enclosing_class_name:
                        recv_type_name = cu.class_fields.get(enclosing_class_name, {}).get(recv_text)

        # If receiver type was identified, look up method in that type's hierarchy
        if recv_type_name:
            defs = self._lookup_method_in_type(cu, recv_type_name, method_name)
            if defs:
                return defs

        # Static imports check (e.g. assertEquals or static wildcards)
        if method_name in cu.static_imports:
            fqcn = cu.static_imports[method_name]
            class_fqcn, _ = fqcn.rsplit(".", 1) if "." in fqcn else (fqcn, "")
            class_simple = class_fqcn.rsplit(".", 1)[-1]
            defs = self._lookup_method_in_type(cu, class_simple, method_name)
            if defs:
                return defs

        for static_cls_fqcn in cu.static_wildcards:
            class_simple = static_cls_fqcn.rsplit(".", 1)[-1]
            defs = self._lookup_method_in_type(cu, class_simple, method_name)
            if defs:
                return defs

        # Fallback: project-wide search by method name
        candidates = self._all_methods_by_name.get(method_name, [])
        if len(candidates) == 1:
            return list(candidates)
        # If multiple, prefer methods from imported or same-package classes
        matching = [
            d for d in candidates
            if d.package == cu.package or (d.class_name and d.class_name in cu.single_imports)
        ]
        if matching:
            return matching
        return list(candidates)

    def _find_var_type_in_method(self, method_node: Node, var_name: str) -> Optional[str]:
        """Find the declared type for a local variable or parameter."""
        # Check formal parameters
        params_node = method_node.child_by_field_name("parameters")
        if params_node is not None:
            for p in params_node.named_children:
                if p.type == "formal_parameter":
                    pname = _extract_text(p.child_by_field_name("name"))
                    if pname == var_name:
                        ptype = _extract_text(p.child_by_field_name("type"))
                        return ptype.split("<", 1)[0].replace("[]", "").strip()

        # Check local variable declarations in method body
        body_node = method_node.child_by_field_name("body")
        if body_node is not None:
            stack = [body_node]
            while stack:
                curr = stack.pop()
                if curr.type == "local_variable_declaration":
                    type_node = curr.child_by_field_name("type")
                    type_text = _extract_text(type_node).split("<", 1)[0].replace("[]", "").strip() if type_node else ""
                    for decl in curr.named_children:
                        if decl.type == "variable_declarator":
                            vname = _extract_text(decl.child_by_field_name("name"))
                            if vname == var_name and type_text:
                                return type_text
                stack.extend(reversed(curr.named_children))
        return None

    def _lookup_method_in_type(
        self,
        cu: _CompilationUnitIndex,
        type_name: str,
        method_name: str,
        seen: Optional[set[str]] = None,
    ) -> list[_Definition]:
        """Look up method in a type and its inheritance chain."""
        if seen is None:
            seen = set()
        if type_name in seen:
            return []
        seen.add(type_name)

        # Resolve type to definition
        type_defs = self._resolve_type_name(cu, type_name)
        if not type_defs:
            return []

        type_def = type_defs[0]
        target_cu = self._file_indices.get(type_def.file_path)
        if target_cu is None:
            return []

        # Direct methods
        methods = target_cu.class_methods.get(type_def.name, {}).get(method_name, [])
        if methods:
            return list(methods)

        # Check superclass
        super_name = target_cu.class_super.get(type_def.name)
        if super_name:
            super_methods = self._lookup_method_in_type(target_cu, super_name, method_name, seen)
            if super_methods:
                return super_methods

        # Check interfaces
        for iface_name in target_cu.class_interfaces.get(type_def.name, []):
            iface_methods = self._lookup_method_in_type(target_cu, iface_name, method_name, seen)
            if iface_methods:
                return iface_methods

        return []
