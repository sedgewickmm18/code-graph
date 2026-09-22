"""Markdown analyzer — extracts MarkdownSection and Requirement nodes.

Parses ``.md`` files without a tree-sitter grammar (pure Python regex/line scan)
because tree-sitter-markdown's API has historically been unstable and the
structural information we need (headings, bullet requirements) is easily
recoverable from the raw text.

Graph nodes created:
  - ``MarkdownSection`` — an H2 or H3 heading
  - ``Requirement``     — a bullet item under a section

Graph edges created (via graph helpers):
  - ``MarkdownSection -[:DEFINES_REQUIREMENT]-> Requirement``
  - ``MarkdownSection -[:DEFINES_ROUTE]-> Route``  (when heading names a path)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from api.graph import Graph

import logging
logger = logging.getLogger("code_graph")

# Matches HTTP-method + path patterns like "POST /api/login" inside headings.
_ROUTE_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s]*)",
    re.IGNORECASE,
)

# H2/H3 headings
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")

# Unordered bullet items (-, *, +)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")


@dataclass
class ParsedSection:
    title: str
    level: int
    lineno: int
    route: Optional[tuple[str, str]] = None   # (method, path) if found
    requirements: list[tuple[str, int]] = field(default_factory=list)


def parse_markdown(text: str) -> list[ParsedSection]:
    """Extract sections and their bullet requirements from Markdown source."""
    sections: list[ParsedSection] = []
    current: Optional[ParsedSection] = None

    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()

        h_m = _HEADING_RE.match(line)
        if h_m:
            current = ParsedSection(
                title=h_m.group(2).strip(),
                level=len(h_m.group(1)),
                lineno=i,
            )
            route_m = _ROUTE_RE.search(current.title)
            if route_m:
                current.route = (route_m.group(1).upper(), route_m.group(2))
            sections.append(current)
            continue

        if current is not None:
            b_m = _BULLET_RE.match(line)
            if b_m:
                current.requirements.append((b_m.group(1).strip(), i))

    return sections


class MarkdownAnalyzer:
    """Analyzer for ``.md`` files.

    This does **not** subclass ``AbstractAnalyzer`` because it operates on
    plain text rather than a tree-sitter AST.  ``SourceAnalyzer`` calls
    ``analyze_file`` directly for ``.md`` files.
    """

    def analyze_file(self, file_path: Path, graph: "Graph") -> None:  # type: ignore[name-defined]
        """Parse ``file_path`` and persist MarkdownSection / Requirement nodes."""
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("MarkdownAnalyzer: cannot read %s: %s", file_path, exc)
            return

        sections = parse_markdown(text)
        for sec in sections:
            sec_id = graph.add_markdown_section(
                title=sec.title,
                level=sec.level,
                path=str(file_path),
                src_line=sec.lineno,
                route_method=sec.route[0] if sec.route else None,
                route_path=sec.route[1] if sec.route else None,
            )
            for req_text, req_lineno in sec.requirements:
                graph.add_requirement(
                    text=req_text,
                    path=str(file_path),
                    src_line=req_lineno,
                    section_id=sec_id,
                )

        logger.debug("MarkdownAnalyzer: %s — %d sections", file_path.name, len(sections))
