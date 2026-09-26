"""Tests for MarkdownAnalyzer — MarkdownSection and Requirement extraction."""

from pathlib import Path
from unittest.mock import MagicMock

from api.analyzers.markdown.analyzer import MarkdownAnalyzer, parse_markdown
from api.analyzers.source_analyzer import _MARKDOWN_EXTENSIONS, SourceAnalyzer


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


class TestMarkdownAnalyzer:
    def test_analyze_file_creates_sections_and_requirements(self, tmp_path: Path):
        for ext in _MARKDOWN_EXTENSIONS:
            md_file = tmp_path / f"doc{ext}"
            md_file.write_text(
                "## Section Heading\n- Requirement 1\n- Requirement 2\n",
                encoding="utf-8",
            )
            mock_graph = MagicMock()
            mock_graph.add_markdown_section.return_value = 42

            analyzer = MarkdownAnalyzer()
            analyzer.analyze_file(md_file, mock_graph)

            mock_graph.add_markdown_section.assert_called_once_with(
                title="Section Heading",
                level=2,
                path=str(md_file),
                src_line=1,
                route_method=None,
                route_path=None,
            )
            assert mock_graph.add_requirement.call_count == 2
            mock_graph.add_requirement.assert_any_call(
                text="Requirement 1",
                path=str(md_file),
                src_line=2,
                section_id=42,
            )
            mock_graph.add_requirement.assert_any_call(
                text="Requirement 2",
                path=str(md_file),
                src_line=3,
                section_id=42,
            )


class TestSourceAnalyzerMarkdownExtensions:
    def test_security_pass_processes_all_markdown_extensions(self, tmp_path: Path):
        sa = SourceAnalyzer()
        mock_graph = MagicMock()
        mock_graph._query.return_value.result_set = []

        files: list[Path] = []
        for ext in _MARKDOWN_EXTENSIONS:
            doc = tmp_path / f"sample{ext}"
            doc.write_text("## Auth\n- Verify token\n", encoding="utf-8")
            files.append(doc)

        mock_graph.add_markdown_section.return_value = 100
        sa.security_pass(mock_graph, files, tmp_path)

        assert mock_graph.add_markdown_section.call_count == len(_MARKDOWN_EXTENSIONS)
        assert mock_graph.add_requirement.call_count == len(_MARKDOWN_EXTENSIONS)
