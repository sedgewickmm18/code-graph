import logging
import os
from pathlib import Path
import subprocess
from typing import Optional
from xml.etree import ElementTree

from multilspy import SyncLanguageServer
from tree_sitter import Language, Node
import tree_sitter_java as tsjava

from ...entities import Entity, File
from ..analyzer import AbstractAnalyzer
from .ts_resolver import TreeSitterJavaResolver

logger = logging.getLogger('code_graph')

_RESOLVER_ENV = "CODE_GRAPH_JAVA_RESOLVER"
_RESOLVER_TREE_SITTER = "tree_sitter"
_RESOLVER_LSP = "lsp"


class JavaAnalyzer(AbstractAnalyzer):
    def __init__(self, resolver: Optional[str] = None) -> None:
        super().__init__(Language(tsjava.language()))
        resolver_choice = (resolver or os.environ.get(_RESOLVER_ENV, _RESOLVER_TREE_SITTER)).strip().lower()
        if resolver_choice in (_RESOLVER_TREE_SITTER, ""):
            self._ts_resolver: Optional[TreeSitterJavaResolver] = TreeSitterJavaResolver(self.language)
            logger.info("JavaAnalyzer: tree-sitter static resolver enabled (default)")
        else:
            self._ts_resolver = None
            logger.info("JavaAnalyzer: LSP resolver enabled")

    def add_dependencies(self, path: Path, files: list[Path]):
        if self._ts_resolver is not None:
            # Tree-sitter static resolver processes in-repo source files directly
            return

        subprocess.run(["rm", "-rf", f"{path}/temp_deps"])
        pom_path = Path(path) / 'pom.xml'
        if not pom_path.is_file():
            # Non-Maven Java sources (e.g. Gradle, or polyglot repos with stray
            # .java files): no pom.xml means no Maven dependencies to resolve.
            logger.info("no pom.xml at %s; skipping Maven dependency resolution", path)
            return
        pom = ElementTree.parse(str(pom_path))
        for dependency in pom.findall('.//{http://maven.apache.org/POM/4.0.0}dependency'):
            groupId = dependency.find('{http://maven.apache.org/POM/4.0.0}groupId').text.replace('.', '/')
            artifactId = dependency.find('{http://maven.apache.org/POM/4.0.0}artifactId').text
            version = dependency.find('{http://maven.apache.org/POM/4.0.0}version').text
            jar_path = f"{Path.home()}/.m2/repository/{groupId}/{artifactId}/{version}/{artifactId}-{version}-sources.jar"

            os.makedirs(f"{path}/temp_deps/{artifactId}-{version}", exist_ok=True)
            subprocess.run(["cp", jar_path, f"{artifactId}-{version}.jar"], cwd=f"{path}/temp_deps/{artifactId}-{version}")
            subprocess.run(["unzip", f"{artifactId}-{version}.jar"], cwd=f"{path}/temp_deps/{artifactId}-{version}")
        files.extend(Path(f"{path}/temp_deps").rglob("*.java"))

    def get_entity_label(self, node: Node) -> str:
        if node.type == 'class_declaration':
            return "Class"
        elif node.type == 'interface_declaration':
            return "Interface"
        elif node.type == 'enum_declaration':
            return "Enum"
        elif node.type == 'method_declaration':
            return "Method"
        elif node.type == 'constructor_declaration':
            return "Constructor"
        elif node.type == 'record_declaration':
            return "Class"
        raise ValueError(f"Unknown entity type: {node.type}")

    def get_entity_name(self, node: Node) -> str:
        if node.type in ['class_declaration', 'interface_declaration', 'enum_declaration', 'method_declaration', 'constructor_declaration', 'record_declaration']:
            return node.child_by_field_name('name').text.decode('utf-8')
        raise ValueError(f"Unknown entity type: {node.type}")
    
    def get_entity_docstring(self, node: Node) -> Optional[str]:
        if node.type in ['class_declaration', 'interface_declaration', 'enum_declaration', 'method_declaration', 'constructor_declaration', 'record_declaration']:
            if node.prev_sibling and node.prev_sibling.type == "block_comment":
                return node.prev_sibling.text.decode('utf-8')
            return None
        raise ValueError(f"Unknown entity type: {node.type}")        

    def get_entity_types(self) -> list[str]:
        return ['class_declaration', 'interface_declaration', 'enum_declaration', 'method_declaration', 'constructor_declaration', 'record_declaration']
    
    def add_symbols(self, entity: Entity) -> None:
        if entity.node.type in ('class_declaration', 'record_declaration'):
            interfaces_captures = self._captures("(super_interfaces (type_list (type_identifier) @interface))", entity.node)
            if 'interface' in interfaces_captures:
                for interface in interfaces_captures['interface']:
                    entity.add_symbol("implement_interface", interface)
            base_class_captures = self._captures("(superclass (type_identifier) @base_class)", entity.node)
            if 'base_class' in base_class_captures:
                base_class = base_class_captures['base_class'][0]
                entity.add_symbol("base_class", base_class)
        elif entity.node.type == 'interface_declaration':
            extends_captures = self._captures("(extends_interfaces (type_list (type_identifier) @type))?", entity.node)
            if 'type' in extends_captures:
                for interface in extends_captures['type']:
                    entity.add_symbol("extend_interface", interface)
        elif entity.node.type in ['method_declaration', 'constructor_declaration']:
            captures = self._captures("(method_invocation) @reference.call", entity.node)
            if 'reference.call' in captures:
                for caller in captures['reference.call']:
                    entity.add_symbol("call", caller)
            if entity.node.type == 'method_declaration':
                captures = self._captures("(formal_parameters (formal_parameter type: (_) @parameter))", entity.node)
                if 'parameter' in captures:
                    for parameter in captures['parameter']:
                        entity.add_symbol("parameters", parameter)
                ret_type = entity.node.child_by_field_name('type')
                if ret_type is not None:
                    entity.add_symbol("return_type", ret_type)

    def is_dependency(self, file_path: str) -> bool:
        return ".jar" in file_path

    def resolve_path(self, file_path: str, path: Path) -> str:
        if ".jar" in file_path:
            args = file_path.replace(".jar", "").replace(".class", ".java").split("/")
            targs = "/".join(["/".join(arg.split(".")) for arg in args[2:-1]])
            return f"{path}/temp_deps/{args[1]}/{targs}/{args[-1]}"
        return file_path

    def resolve(
        self,
        files: dict[Path, File],
        lsp: SyncLanguageServer,
        file_path: Path,
        path: Path,
        node: Node,
    ) -> list[tuple[File, Node]]:
        if self._ts_resolver is not None:
            return self._ts_resolver.resolve(files, file_path, path, node)
        return super().resolve(files, lsp, file_path, path, node)

    def needs_lsp(self) -> bool:
        return self._ts_resolver is None

    def resolve_type(self, files: dict[Path, File], lsp: SyncLanguageServer, file_path: Path, path: Path, node: Node) -> list[Entity]:
        res = []
        for file, resolved_node in self.resolve(files, lsp, file_path, path, node):
            type_dec = self.find_parent(resolved_node, ['class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration'])
            if type_dec in file.entities:
                res.append(file.entities[type_dec])
        return res

    def resolve_method(self, files: dict[Path, File], lsp: SyncLanguageServer, file_path: Path, path: Path, node: Node) -> list[Entity]:
        res = []
        target = node.child_by_field_name('name') if node.child_by_field_name('name') else node
        for file, resolved_node in self.resolve(files, lsp, file_path, path, target):
            method_dec = self.find_parent(resolved_node, ['method_declaration', 'constructor_declaration', 'class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration'])
            if method_dec and method_dec.type in ['class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration']:
                continue
            if method_dec in file.entities:
                res.append(file.entities[method_dec])
        return res
    
    def resolve_symbol(self, files: dict[Path, File], lsp: SyncLanguageServer, file_path: Path, path: Path, key: str, symbol: Node) -> list[Entity]:
        if key in ["implement_interface", "base_class", "extend_interface", "parameters", "return_type"]:
            return self.resolve_type(files, lsp, file_path, path, symbol)
        elif key in ["call"]:
            return self.resolve_method(files, lsp, file_path, path, symbol)
        else:
            raise ValueError(f"Unknown key {key}")

    def build_import_index(self, files: dict[Path, File], root: Path) -> object:
        """Index Java files by fully-qualified class names and package names."""
        fqcn_map: dict[str, File] = {}
        package_map: dict[str, list[File]] = {}

        for fpath, file in files.items():
            if fpath.suffix != ".java" or file.tree is None:
                continue
            pkg = ""
            for child in file.tree.root_node.named_children:
                if child.type == "package_declaration":
                    pkg_id = child.child_by_field_name("name") or next(
                        (c for c in child.named_children if c.type in ("scoped_identifier", "identifier")),
                        None,
                    )
                    if pkg_id is not None:
                        pkg = pkg_id.text.decode("utf-8")
                    break

            if pkg:
                package_map.setdefault(pkg, []).append(file)

            for child in file.tree.root_node.named_children:
                if child.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        cname = name_node.text.decode("utf-8")
                        fqcn = f"{pkg}.{cname}" if pkg else cname
                        fqcn_map[fqcn] = file

        return {"fqcn": fqcn_map, "package": package_map}

    def resolve_imports(self, file: File, root: Path, index: object) -> list[File]:
        """Resolve import statements in a Java file to in-repo File targets."""
        if not index or file.tree is None:
            return []

        fqcn_map: dict[str, File] = index.get("fqcn", {})
        package_map: dict[str, list[File]] = index.get("package", {})

        seen: set[Path] = set()
        targets: list[File] = []

        for child in file.tree.root_node.named_children:
            if child.type != "import_declaration":
                continue

            is_wildcard = any(c.type == "asterisk" for c in child.children)
            is_static = any(c.type == "static" for c in child.children)

            target_node = next(
                (c for c in child.named_children if c.type in ("scoped_identifier", "identifier")),
                None,
            )
            if target_node is None:
                continue
            imp_text = target_node.text.decode("utf-8")

            if is_wildcard:
                if is_static:
                    # import static com.example.Helper.*;
                    if imp_text in fqcn_map:
                        f = fqcn_map[imp_text]
                        if f.path != file.path and f.path not in seen:
                            seen.add(f.path)
                            targets.append(f)
                else:
                    # import com.example.model.*;
                    for f in package_map.get(imp_text, []):
                        if f.path != file.path and f.path not in seen:
                            seen.add(f.path)
                            targets.append(f)
            else:
                if is_static:
                    # import static com.example.Helper.method;
                    parent_fqcn = imp_text.rsplit(".", 1)[0] if "." in imp_text else imp_text
                    if parent_fqcn in fqcn_map:
                        f = fqcn_map[parent_fqcn]
                        if f.path != file.path and f.path not in seen:
                            seen.add(f.path)
                            targets.append(f)
                else:
                    # import com.example.model.User;
                    if imp_text in fqcn_map:
                        f = fqcn_map[imp_text]
                        if f.path != file.path and f.path not in seen:
                            seen.add(f.path)
                            targets.append(f)

        return targets
