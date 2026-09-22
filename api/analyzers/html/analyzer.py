"""HTML / Jinja2 analyzer using tree-sitter-html.

Extracts:
  - HtmlElement nodes for every element tag (div, span, input, …)
  - HtmlForm nodes specifically for <form> elements
  - TemplateVar stubs for every ``{{ identifier }}`` Jinja2 expression

These stubs are resolved into Variable→HtmlElement INJECTED_INTO edges
by the SourceAnalyzer after the Python pass has created the Variable nodes.
"""

import re
from pathlib import Path
from typing import Optional

import tree_sitter_html as tshtml
from tree_sitter import Language, Node

from api.entities.entity import Entity
from ..tree_sitter_base import TreeSitterAnalyzer

import logging
logger = logging.getLogger("code_graph")


# Tag names that are treated as HtmlForm (own label)
_FORM_TAGS = frozenset({"form"})

# Jinja2 variable expression pattern inside attribute values and text nodes
# tree-sitter-html does not parse Jinja2 natively, so we scan raw text.
_JINJA_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class HtmlAnalyzer(TreeSitterAnalyzer):
    """Analyzer for HTML and Jinja2 template files.

    Entity types:
      - ``element`` → HtmlElement (or HtmlForm for <form>)

    No LSP is required; all analysis is purely syntactic.
    """

    # entity_node_types maps tree-sitter node type -> graph label.
    # The actual label is dynamic (HtmlElement vs HtmlForm) so we override
    # get_entity_label / get_entity_name / get_entity_types directly.
    entity_node_types: dict[str, str] = {
        "element": "HtmlElement",
    }
    type_definition_node_types: tuple[str, ...] = ()
    callable_definition_node_types: tuple[str, ...] = ()
    type_resolution_keys: tuple[str, ...] = ()
    method_resolution_keys: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__(Language(tshtml.language()))
        # Stores (element_node, [var_name, …]) so SourceAnalyzer can wire
        # INJECTED_INTO edges after the Python Variable nodes are created.
        self.template_refs: dict[int, list[str]] = {}  # node.id → [var_names]

    # ------------------------------------------------------------------
    # AbstractAnalyzer interface
    # ------------------------------------------------------------------

    def is_dependency(self, file_path: str) -> bool:
        return False

    def resolve_path(self, file_path: str, path: Path) -> str:
        return file_path

    def add_dependencies(self, path: Path, files: list[Path]) -> None:
        pass

    def get_entity_types(self) -> list[str]:
        return ["element"]

    def get_entity_label(self, node: Node) -> str:
        tag = self._tag_name(node)
        if tag in _FORM_TAGS:
            return "HtmlForm"
        return "HtmlElement"

    def get_entity_name(self, node: Node) -> str:
        tag = self._tag_name(node)
        # Include a rough id/class attribute to make names more meaningful
        attr_id = self._attr_value(node, "id")
        if attr_id:
            return f"{tag}#{attr_id}"
        return tag

    def get_entity_docstring(self, node: Node) -> Optional[str]:
        return None

    def add_symbols(self, entity: Entity) -> None:
        """Scan element text/attribute content for Jinja2 {{ var }} references."""
        vars_found: list[str] = []
        self._collect_jinja_vars(entity.node, vars_found)
        if vars_found:
            self.template_refs[entity.node.id] = vars_found

    def needs_lsp(self) -> bool:
        return False

    def resolve_symbol(
        self,
        files: dict,
        lsp: object,
        file_path: Path,
        path: Path,
        key: str,
        symbol: Node,
    ) -> list[Entity]:
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tag_name(self, node: Node) -> str:
        """Extract the lowercase tag name from an element node."""
        for child in node.children:
            if child.type in ("start_tag", "self_closing_tag"):
                tag_node = child.child_by_field_name("name")
                if tag_node is None:
                    # field names vary across grammar versions; fall back to
                    # first named child
                    for c in child.named_children:
                        if c.type == "tag_name":
                            return c.text.decode("utf-8").lower()
                    return child.named_children[0].text.decode("utf-8").lower() if child.named_children else "unknown"
                return tag_node.text.decode("utf-8").lower()
        return "unknown"

    def _attr_value(self, node: Node, attr_name: str) -> Optional[str]:
        """Return the value of a named attribute, or None if not present.

        tree-sitter-html uses ``attribute_name`` and ``attribute_value`` /
        ``quoted_attribute_value`` node types (no field names on the attribute).
        """
        for child in node.children:
            if child.type in ("start_tag", "self_closing_tag"):
                for attr in child.children:
                    if attr.type == "attribute":
                        # Find attribute_name child
                        name_node = next(
                            (c for c in attr.children if c.type == "attribute_name"),
                            None,
                        )
                        if name_node is None or name_node.text.decode("utf-8").lower() != attr_name:
                            continue
                        # Find the attribute_value (inside quoted_attribute_value)
                        for val_child in attr.children:
                            if val_child.type == "attribute_value":
                                return val_child.text.decode("utf-8")
                            if val_child.type == "quoted_attribute_value":
                                inner = next(
                                    (c for c in val_child.children if c.type == "attribute_value"),
                                    None,
                                )
                                if inner:
                                    return inner.text.decode("utf-8")
        return None

    def _collect_jinja_vars(self, node: Node, out: list[str]) -> None:
        """Recursively scan for Jinja2 {{ var }} patterns in text content."""
        if node.type in ("text", "raw_text", "attribute_value"):
            raw = node.text.decode("utf-8", errors="replace")
            for m in _JINJA_VAR_RE.finditer(raw):
                var_name = m.group(1)
                if var_name not in out:
                    out.append(var_name)
        for child in node.children:
            self._collect_jinja_vars(child, out)
