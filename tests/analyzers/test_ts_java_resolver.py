"""Unit tests for the tree-sitter Java static resolver and JavaAnalyzer."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava

from api.analyzers.java.analyzer import JavaAnalyzer
from api.analyzers.java.ts_resolver import TreeSitterJavaResolver
from api.entities.file import File

_JAVA = Language(tsjava.language())
_PARSER = Parser(_JAVA)


def _file_from(path: Path, source: str) -> File:
    tree = _PARSER.parse(source.encode("utf-8"))
    return File(path, tree)


def _find_node_by_text(tree_root, text: str, node_type: str = "identifier"):
    stack = [tree_root]
    while stack:
        node = stack.pop()
        if node.type == node_type and node.text.decode("utf-8") == text:
            return node
        stack.extend(node.children)
    raise AssertionError(f"{node_type} with text '{text}' not found")


def _find_method_call_name(tree_root, method_name: str):
    stack = [tree_root]
    while stack:
        node = stack.pop()
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode("utf-8") == method_name:
                return name_node
        stack.extend(node.children)
    raise AssertionError(f"method_invocation for '{method_name}' not found")


def _make_project(tmp_path: Path, layout: dict[str, str]) -> dict[Path, File]:
    files: dict[Path, File] = {}
    for rel, src in layout.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        files[p.resolve()] = _file_from(p.resolve(), src)
    return files


def test_java_analyzer_default_uses_tree_sitter():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CODE_GRAPH_JAVA_RESOLVER", None)
        analyzer = JavaAnalyzer()
        assert analyzer._ts_resolver is not None
        assert analyzer.needs_lsp() is False


def test_java_analyzer_can_opt_into_lsp():
    with mock.patch.dict(os.environ, {"CODE_GRAPH_JAVA_RESOLVER": "lsp"}, clear=False):
        analyzer = JavaAnalyzer()
        assert analyzer._ts_resolver is None
        assert analyzer.needs_lsp() is True


def test_java_resolver_local_class_and_method(tmp_path: Path):
    files = _make_project(
        tmp_path,
        {
            "com/example/Service.java": (
                "package com.example;\n\n"
                "public class Service {\n"
                "    public void run() {\n"
                "        helper();\n"
                "    }\n"
                "    private void helper() {}\n"
                "}\n"
            ),
        },
    )
    resolver = TreeSitterJavaResolver(_JAVA)
    svc_path = (tmp_path / "com/example/Service.java").resolve()
    call_name = _find_method_call_name(files[svc_path].tree.root_node, "helper")

    out = resolver.resolve(files, svc_path, tmp_path.resolve(), call_name)
    assert len(out) == 1
    file, def_node = out[0]
    assert file.path == svc_path
    assert def_node.type == "method_declaration"
    assert def_node.child_by_field_name("name").text.decode("utf-8") == "helper"


def test_java_resolver_single_import_and_field_receiver(tmp_path: Path):
    files = _make_project(
        tmp_path,
        {
            "com/example/util/TokenService.java": (
                "package com.example.util;\n\n"
                "public class TokenService {\n"
                "    public void validate() {}\n"
                "}\n"
            ),
            "com/example/service/AuthService.java": (
                "package com.example.service;\n\n"
                "import com.example.util.TokenService;\n\n"
                "public class AuthService {\n"
                "    private TokenService tokenService;\n"
                "    public void authenticate() {\n"
                "        tokenService.validate();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    resolver = TreeSitterJavaResolver(_JAVA)
    auth_path = (tmp_path / "com/example/service/AuthService.java").resolve()
    token_path = (tmp_path / "com/example/util/TokenService.java").resolve()

    call_name = _find_method_call_name(files[auth_path].tree.root_node, "validate")
    out = resolver.resolve(files, auth_path, tmp_path.resolve(), call_name)
    assert len(out) == 1
    file, def_node = out[0]
    assert file.path == token_path
    assert def_node.type == "method_declaration"
    assert def_node.child_by_field_name("name").text.decode("utf-8") == "validate"


def test_java_resolver_same_package_without_import(tmp_path: Path):
    files = _make_project(
        tmp_path,
        {
            "com/example/Helper.java": (
                "package com.example;\n\n"
                "public class Helper {\n"
                "    public static void compute() {}\n"
                "}\n"
            ),
            "com/example/Main.java": (
                "package com.example;\n\n"
                "public class Main {\n"
                "    public void execute() {\n"
                "        Helper.compute();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    resolver = TreeSitterJavaResolver(_JAVA)
    main_path = (tmp_path / "com/example/Main.java").resolve()
    helper_path = (tmp_path / "com/example/Helper.java").resolve()

    call_name = _find_method_call_name(files[main_path].tree.root_node, "compute")
    out = resolver.resolve(files, main_path, tmp_path.resolve(), call_name)
    assert len(out) == 1
    file, def_node = out[0]
    assert file.path == helper_path
    assert def_node.child_by_field_name("name").text.decode("utf-8") == "compute"


def test_java_resolver_wildcard_import(tmp_path: Path):
    files = _make_project(
        tmp_path,
        {
            "com/example/model/User.java": (
                "package com.example.model;\n\n"
                "public class User {\n"
                "    public String getName() { return \"\"; }\n"
                "}\n"
            ),
            "com/example/service/UserService.java": (
                "package com.example.service;\n\n"
                "import com.example.model.*;\n\n"
                "public class UserService {\n"
                "    public void process(User user) {\n"
                "        user.getName();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    resolver = TreeSitterJavaResolver(_JAVA)
    user_svc_path = (tmp_path / "com/example/service/UserService.java").resolve()
    user_path = (tmp_path / "com/example/model/User.java").resolve()

    call_name = _find_method_call_name(files[user_svc_path].tree.root_node, "getName")
    out = resolver.resolve(files, user_svc_path, tmp_path.resolve(), call_name)
    assert len(out) == 1
    assert out[0][0].path == user_path
    assert out[0][1].child_by_field_name("name").text.decode("utf-8") == "getName"


def test_java_resolver_inherited_superclass_method(tmp_path: Path):
    files = _make_project(
        tmp_path,
        {
            "com/example/BaseProvider.java": (
                "package com.example;\n\n"
                "public class BaseProvider {\n"
                "    public void superLog() {}\n"
                "}\n"
            ),
            "com/example/ChildProvider.java": (
                "package com.example;\n\n"
                "public class ChildProvider extends BaseProvider {\n"
                "    public void doWork() {\n"
                "        superLog();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    resolver = TreeSitterJavaResolver(_JAVA)
    child_path = (tmp_path / "com/example/ChildProvider.java").resolve()
    base_path = (tmp_path / "com/example/BaseProvider.java").resolve()

    call_name = _find_method_call_name(files[child_path].tree.root_node, "superLog")
    out = resolver.resolve(files, child_path, tmp_path.resolve(), call_name)
    assert len(out) == 1
    assert out[0][0].path == base_path
    assert out[0][1].child_by_field_name("name").text.decode("utf-8") == "superLog"


def test_java_analyzer_imports_resolution(tmp_path: Path):
    files = _make_project(
        tmp_path,
        {
            "com/example/A.java": (
                "package com.example;\n"
                "import com.other.B;\n"
                "public class A {}\n"
            ),
            "com/other/B.java": (
                "package com.other;\n"
                "public class B {}\n"
            ),
        },
    )
    analyzer = JavaAnalyzer()
    idx = analyzer.build_import_index(files, tmp_path.resolve())
    a_path = (tmp_path / "com/example/A.java").resolve()
    b_path = (tmp_path / "com/other/B.java").resolve()

    imported_files = analyzer.resolve_imports(files[a_path], tmp_path.resolve(), idx)
    assert len(imported_files) == 1
    assert imported_files[0].path == b_path
