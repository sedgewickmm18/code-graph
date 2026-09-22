"""Tests for MarkdownAnalyzer — MarkdownSection and Requirement extraction."""


from api.analyzers.markdown.analyzer import parse_markdown


class TestParseMarkdown:
    def test_basic_heading(self):
        md = "## Authentication\n- JWT must be verified\n- Tokens expire after 1h\n"
        sections = parse_markdown(md)
        assert len(sections) == 1
        assert sections[0].title == "Authentication"
        assert sections[0].level == 2
        assert len(sections[0].requirements) == 2
        texts = [r[0] for r in sections[0].requirements]
        assert "JWT must be verified" in texts

    def test_h3_heading(self):
        md = "### POST /api/login\n- Requires valid credentials\n"
        sections = parse_markdown(md)
        assert sections[0].level == 3
        assert sections[0].title == "POST /api/login"

    def test_route_detection(self):
        md = "## POST /api/user\n- Must be authenticated\n"
        sections = parse_markdown(md)
        assert sections[0].route is not None
        method, path = sections[0].route
        assert method == "POST"
        assert path == "/api/user"

    def test_no_route_in_plain_heading(self):
        md = "## General Configuration\n"
        sections = parse_markdown(md)
        assert sections[0].route is None

    def test_multiple_sections(self):
        md = (
            "## Section A\n- req 1\n- req 2\n"
            "## Section B\n- req 3\n"
        )
        sections = parse_markdown(md)
        assert len(sections) == 2
        assert sections[0].title == "Section A"
        assert len(sections[0].requirements) == 2
        assert sections[1].title == "Section B"
        assert len(sections[1].requirements) == 1

    def test_bullet_variants(self):
        md = "## Auth\n- dash\n* star\n+ plus\n"
        sections = parse_markdown(md)
        assert len(sections[0].requirements) == 3

    def test_empty_doc(self):
        assert parse_markdown("") == []

    def test_h1_ignored(self):
        md = "# Top-level heading\nsome text\n## Sub\n- req\n"
        sections = parse_markdown(md)
        # H1 should not become a section
        assert len(sections) == 1
        assert sections[0].title == "Sub"

    def test_line_numbers(self):
        md = "## First\n- bullet\n## Second\n"
        sections = parse_markdown(md)
        assert sections[0].lineno == 1
        assert sections[0].requirements[0][1] == 2  # line 2
        assert sections[1].lineno == 3

    def test_get_route_methods(self):
        methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
        for m in methods:
            md = f"## {m} /api/resource\n"
            sections = parse_markdown(md)
            assert sections[0].route[0] == m
