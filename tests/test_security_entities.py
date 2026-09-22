"""Tests for security entity extraction in PythonAnalyzer.

Tests T2 (decorators), T4 (render_template variable capture), T6 (FS ops).
These are unit tests against the AST-level symbol extraction — no FalkorDB
connection required.
"""

import textwrap
import pytest


from api.analyzers.python.analyzer import (
    PythonAnalyzer,
    _SANITIZERS,
)
from api.entities.entity import Entity


def _build_entity(source: str) -> "tuple[PythonAnalyzer, Entity]":
    """Parse Python source, extract the first function entity, call add_symbols.

    We parse via the full tree so that decorated_definition parent nodes
    are present — the decorator extraction relies on node.parent being set.
    """
    analyzer = PythonAnalyzer()
    tree = analyzer.parser.parse(source.encode())
    root = tree.root_node

    # Find first function_definition node (may be inside decorated_definition)
    stack = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            entity = Entity(node)
            analyzer.add_symbols(entity)
            return analyzer, entity
        stack.extend(node.children)
    raise ValueError("No function_definition found in source")


class TestDecoratorExtraction:
    def test_single_decorator(self):
        src = textwrap.dedent("""\
            @login_required
            def view(request):
                pass
        """)
        _, entity = _build_entity(src)
        dec_nodes = entity.symbols.get("decorator", [])
        assert len(dec_nodes) == 1
        raw = dec_nodes[0].text.decode("utf-8")
        assert "login_required" in raw

    def test_multiple_decorators(self):
        src = textwrap.dedent("""\
            @app.route("/login")
            @login_required
            def login():
                pass
        """)
        _, entity = _build_entity(src)
        dec_nodes = entity.symbols.get("decorator", [])
        assert len(dec_nodes) == 2

    def test_no_decorator(self):
        src = "def plain(): pass\n"
        _, entity = _build_entity(src)
        assert entity.symbols.get("decorator", []) == []


class TestRenderCallCapture:
    def test_render_template_kwargs(self):
        src = textwrap.dedent("""\
            def view():
                return render_template("page.html", username=user, comment=body)
        """)
        _, entity = _build_entity(src)
        render_nodes = entity.symbols.get("render_call", [])
        assert len(render_nodes) == 1

    def test_template_response(self):
        src = textwrap.dedent("""\
            def view():
                return TemplateResponse("page.html", context={"title": t})
        """)
        _, entity = _build_entity(src)
        assert entity.symbols.get("render_call")

    def test_non_render_call_not_captured(self):
        src = "def view():\n    return json_response({'ok': True})\n"
        _, entity = _build_entity(src)
        assert not entity.symbols.get("render_call")


class TestFileSystemOpCapture:
    def test_open_call(self):
        src = textwrap.dedent("""\
            def handler():
                with open(filename, "r") as f:
                    return f.read()
        """)
        _, entity = _build_entity(src)
        assert entity.symbols.get("fs_op")

    def test_os_path_join(self):
        src = textwrap.dedent("""\
            import os
            def handler():
                path = os.path.join(base, user_input)
                return path
        """)
        _, entity = _build_entity(src)
        fs_nodes = entity.symbols.get("fs_op", [])
        assert fs_nodes

    def test_taint_detection(self):
        src = textwrap.dedent("""\
            def handler():
                with open(request.args.get("file"), "r") as f:
                    return f.read()
        """)
        analyzer, entity = _build_entity(src)
        fs_nodes = entity.symbols.get("fs_op", [])
        assert fs_nodes
        # Verify taint is detected
        assert analyzer._node_is_tainted(fs_nodes[0])

    def test_no_taint_for_literal(self):
        src = textwrap.dedent("""\
            def handler():
                with open("config.json", "r") as f:
                    return f.read()
        """)
        analyzer, entity = _build_entity(src)
        fs_nodes = entity.symbols.get("fs_op", [])
        assert fs_nodes
        assert not analyzer._node_is_tainted(fs_nodes[0])


class TestIsSanitizer:
    @pytest.mark.parametrize("name", list(_SANITIZERS))
    def test_known_sanitizers(self, name: str):
        assert PythonAnalyzer._is_sanitizer(name)

    def test_prefix_match(self):
        assert PythonAnalyzer._is_sanitizer("sanitize_html")
        assert PythonAnalyzer._is_sanitizer("escape_user_input")

    def test_not_sanitizer(self):
        assert not PythonAnalyzer._is_sanitizer("render_template")
        assert not PythonAnalyzer._is_sanitizer("open")
