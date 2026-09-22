"""Tests for HtmlAnalyzer — HtmlElement, HtmlForm, and template variable extraction."""

import textwrap


from api.analyzers.html.analyzer import HtmlAnalyzer, _JINJA_VAR_RE


def _parse(source: str) -> "tuple[HtmlAnalyzer, object]":
    """Helper: parse HTML source and return (analyzer, tree)."""
    analyzer = HtmlAnalyzer()
    tree = analyzer.parser.parse(source.encode())
    return analyzer, tree


class TestTagNameExtraction:
    def test_simple_div(self):
        analyzer, tree = _parse("<div id='main'>Hello</div>")
        root = tree.root_node
        # Find the element node
        elements = [n for n in root.children if n.type == "element"]
        assert elements, "Expected at least one element node"
        assert analyzer._tag_name(elements[0]) == "div"

    def test_form_tag(self):
        analyzer, tree = _parse('<form action="/login" method="POST"></form>')
        root = tree.root_node
        elements = [n for n in root.children if n.type == "element"]
        assert elements
        assert analyzer._tag_name(elements[0]) == "form"
        assert analyzer.get_entity_label(elements[0]) == "HtmlForm"

    def test_non_form_tag(self):
        analyzer, tree = _parse("<span>text</span>")
        root = tree.root_node
        elements = [n for n in root.children if n.type == "element"]
        assert elements
        assert analyzer.get_entity_label(elements[0]) == "HtmlElement"


class TestAttrExtraction:
    def test_id_attr(self):
        analyzer, tree = _parse('<div id="comment-box">text</div>')
        root = tree.root_node
        elements = [n for n in root.children if n.type == "element"]
        assert elements
        name = analyzer.get_entity_name(elements[0])
        assert name == "div#comment-box"

    def test_no_id_attr(self):
        analyzer, tree = _parse("<p>paragraph</p>")
        root = tree.root_node
        elements = [n for n in root.children if n.type == "element"]
        assert elements
        assert analyzer.get_entity_name(elements[0]) == "p"


class TestJinjaVarRegex:
    def test_simple_var(self):
        m = list(_JINJA_VAR_RE.finditer("Hello {{ user_name }}!"))
        assert len(m) == 1
        assert m[0].group(1) == "user_name"

    def test_multiple_vars(self):
        text = "{{ title }} — {{ body }}"
        names = [m.group(1) for m in _JINJA_VAR_RE.finditer(text)]
        assert names == ["title", "body"]

    def test_no_match(self):
        assert not list(_JINJA_VAR_RE.finditer("plain text"))


class TestTemplateRefs:
    def test_collects_jinja_vars_in_text(self):
        html = textwrap.dedent("""\
            <div>
              <p>{{ user_comment }}</p>
              <span>{{ post_title }}</span>
            </div>
        """)
        from api.entities.entity import Entity

        analyzer, tree = _parse(html)
        # Manually walk to the first element and call add_symbols
        root = tree.root_node

        def find_elements(node):
            results = []
            if node.type == "element":
                results.append(node)
            for child in node.children:
                results.extend(find_elements(child))
            return results

        for elem_node in find_elements(root):
            e = Entity(elem_node)
            e.node = elem_node
            analyzer.add_symbols(e)

        # template_refs should have been populated
        all_vars: list[str] = []
        for vars_list in analyzer.template_refs.values():
            all_vars.extend(vars_list)
        assert "user_comment" in all_vars
        assert "post_title" in all_vars
