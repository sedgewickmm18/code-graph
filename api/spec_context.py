"""Graph context assembler for cgraph gen-spec.

Queries the code-graph for a named capability and assembles a structured
plain-text context block suitable as a user message to the spec LLM.
"""

from __future__ import annotations

from .graph import Graph


def build_spec_context(graph: Graph, capability: str) -> str:
    """Build a plain-text context block for *capability* from the graph.

    Matching strategy (first match wins):
    1. Class name — exact, then substring.
    2. File basename — substring.
    3. API route path — substring match on Route.path or Decorator.name.

    The assembled block contains:
    - [CAPABILITY] — matched entity name and source file.
    - [DOCSTRING]  — docstring of the matched entity.
    - [PUBLIC METHODS] — method names + docstrings (classes only).
    - [CALLS]      — depth-1 callees (max 10).
    - [AUTH]       — auth decorators (login_required / token_required / public_or_auth).
    - [FS_OPS]     — FileSystemOp targets attached to the entity.
    - [EXISTING REQUIREMENTS] — Requirement nodes linked to MarkdownSections whose
      path contains the capability name.

    The returned string is kept under 3 000 characters (CALLS/FS_OPS are
    truncated when necessary).

    Args:
        graph: A synchronous :class:`Graph` instance.
        capability: Class name, file basename, or route path fragment.

    Returns:
        Structured plain-text context string.

    Raises:
        ValueError: When no matching entity can be found.
    """
    entity_name, entity_path, docstring, node_id, entity_type = _resolve_entity(
        graph, capability
    )

    lines: list[str] = []

    lines.append("[CAPABILITY]")
    lines.append(f"Name: {entity_name}")
    lines.append(f"File: {entity_path}")
    lines.append(f"Type: {entity_type}")
    lines.append("")

    lines.append("[DOCSTRING]")
    lines.append(docstring if docstring else "(none)")
    lines.append("")

    # Public methods (classes only)
    if entity_type == "Class":
        methods = _get_methods(graph, node_id)
        lines.append("[PUBLIC METHODS]")
        if methods:
            for name, doc in methods:
                entry = f"  {name}"
                if doc:
                    entry += f": {doc[:120]}"
                lines.append(entry)
        else:
            lines.append("  (none)")
        lines.append("")

    # Calls (depth 1, max 10)
    callees = _get_callees(graph, node_id, entity_type)
    lines.append("[CALLS]")
    if callees:
        for callee in callees[:10]:
            lines.append(f"  -> {callee}")
    else:
        lines.append("  (none)")
    lines.append("")

    # Auth decorators
    auth_decs = _get_auth(graph, node_id, entity_type)
    lines.append("[AUTH]")
    if auth_decs:
        for dec in auth_decs:
            lines.append(f"  {dec}")
    else:
        lines.append("  (none)")
    lines.append("")

    # FS ops
    fs_ops = _get_fs_ops(graph, node_id, entity_type)
    lines.append("[FS_OPS]")
    if fs_ops:
        for op in fs_ops[:10]:
            lines.append(f"  {op}")
    else:
        lines.append("  (none)")
    lines.append("")

    # Existing requirements — for MarkdownSection entities fetch by node ID directly
    if entity_type == "MarkdownSection":
        reqs = _get_requirements_for_section(graph, node_id)
    else:
        reqs = _get_existing_requirements(graph, capability)
    lines.append("[EXISTING REQUIREMENTS]")
    if reqs:
        for req in reqs:
            lines.append(f"  - {req}")
    else:
        lines.append("  (none)")

    result = "\n".join(lines)

    # Hard cap at 3000 characters
    if len(result) > 3000:
        result = result[:2997] + "…"

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_AUTH_DECORATORS = {"login_required", "token_required", "public_or_auth"}


def _resolve_entity(
    graph: Graph, capability: str
) -> tuple[str, str, str, int, str]:
    """Return (name, path, docstring, node_id, type) for the best-matching entity."""

    # 1a. Exact class name
    row = graph._query(
        "MATCH (c:Class {name: $name}) RETURN c LIMIT 1",
        {"name": capability},
    ).result_set
    if row:
        node = row[0][0]
        return (
            node.properties.get("name", ""),
            node.properties.get("path", ""),
            node.properties.get("doc", ""),
            node.id,
            "Class",
        )

    # 1b. Substring class name
    row = graph._query(
        "MATCH (c:Class) WHERE c.name CONTAINS $cap RETURN c LIMIT 1",
        {"cap": capability},
    ).result_set
    if row:
        node = row[0][0]
        return (
            node.properties.get("name", ""),
            node.properties.get("path", ""),
            node.properties.get("doc", ""),
            node.id,
            "Class",
        )

    # 2. File basename substring
    row = graph._query(
        "MATCH (f:File) WHERE f.name CONTAINS $cap RETURN f LIMIT 1",
        {"cap": capability},
    ).result_set
    if row:
        node = row[0][0]
        full_path = node.properties.get("path", "") + "/" + node.properties.get("name", "") + node.properties.get("ext", "")
        return (
            node.properties.get("name", ""),
            full_path,
            node.properties.get("doc", ""),
            node.id,
            "File",
        )

    # 2b. File path substring (e.g. directory / package name like "tekton_scraper")
    row = graph._query(
        "MATCH (f:File) WHERE f.path CONTAINS $cap RETURN f LIMIT 1",
        {"cap": capability},
    ).result_set
    if row:
        node = row[0][0]
        full_path = node.properties.get("path", "") + "/" + node.properties.get("name", "") + node.properties.get("ext", "")
        return (
            node.properties.get("name", ""),
            full_path,
            node.properties.get("doc", ""),
            node.id,
            "File",
        )

    # 3. Route path substring
    row = graph._query(
        "MATCH (r:Route) WHERE r.path CONTAINS $cap RETURN r LIMIT 1",
        {"cap": capability},
    ).result_set
    if row:
        node = row[0][0]
        return (
            node.properties.get("path", ""),
            node.properties.get("path", ""),
            node.properties.get("doc", ""),
            node.id,
            "Route",
        )

    # 4. Decorator name substring (e.g. route decorator patterns)
    row = graph._query(
        "MATCH (d:Decorator) WHERE d.name CONTAINS $cap RETURN d LIMIT 1",
        {"cap": capability},
    ).result_set
    if row:
        node = row[0][0]
        return (
            node.properties.get("name", ""),
            node.properties.get("path", ""),
            "",
            node.id,
            "Decorator",
        )

    # 5a. Exact MarkdownSection title
    row = graph._query(
        "MATCH (m:MarkdownSection {title: $name}) RETURN m LIMIT 1",
        {"name": capability},
    ).result_set
    if row:
        node = row[0][0]
        return (
            node.properties.get("title", ""),
            node.properties.get("path", ""),
            "",
            node.id,
            "MarkdownSection",
        )

    # 5b. Substring MarkdownSection title
    row = graph._query(
        "MATCH (m:MarkdownSection) WHERE m.title CONTAINS $cap RETURN m LIMIT 1",
        {"cap": capability},
    ).result_set
    if row:
        node = row[0][0]
        return (
            node.properties.get("title", ""),
            node.properties.get("path", ""),
            "",
            node.id,
            "MarkdownSection",
        )

    raise ValueError(
        f"No entity found for capability {capability!r}. "
        "Try a class name, file basename, route path fragment, or markdown section title."
    )


def _get_methods(graph: Graph, class_id: int) -> list[tuple[str, str]]:
    """Return [(name, docstring), …] for functions directly defined by a class."""
    rows = graph._query(
        """MATCH (c)-[:DEFINES]->(f:Function)
           WHERE ID(c) = $cid
           RETURN f.name, f.doc
           LIMIT 20""",
        {"cid": class_id},
    ).result_set
    return [(r[0] or "", r[1] or "") for r in rows]


def _get_callees(graph: Graph, node_id: int, entity_type: str) -> list[str]:
    """Return callee names (depth 1) for the entity."""
    if entity_type == "Class":
        # Callees of any method defined by the class
        rows = graph._query(
            """MATCH (c)-[:DEFINES]->(f:Function)-[:CALLS]->(callee)
               WHERE ID(c) = $nid
               RETURN DISTINCT callee.name
               LIMIT 10""",
            {"nid": node_id},
        ).result_set
    else:
        rows = graph._query(
            """MATCH (n)-[:CALLS]->(callee)
               WHERE ID(n) = $nid
               RETURN DISTINCT callee.name
               LIMIT 10""",
            {"nid": node_id},
        ).result_set
    return [r[0] for r in rows if r[0]]


def _get_auth(graph: Graph, node_id: int, entity_type: str) -> list[str]:
    """Return auth decorator names attached to the entity or its methods."""
    if entity_type == "Class":
        rows = graph._query(
            """MATCH (c)-[:DEFINES]->(f:Function)-[:HAS_DECORATOR]->(d:Decorator)
               WHERE ID(c) = $nid
               RETURN DISTINCT d.name""",
            {"nid": node_id},
        ).result_set
    else:
        rows = graph._query(
            """MATCH (n)-[:HAS_DECORATOR]->(d:Decorator)
               WHERE ID(n) = $nid
               RETURN DISTINCT d.name""",
            {"nid": node_id},
        ).result_set
    return [r[0] for r in rows if r[0] and r[0] in _AUTH_DECORATORS]


def _get_fs_ops(graph: Graph, node_id: int, entity_type: str) -> list[str]:
    """Return FileSystemOp.op strings attached to the entity or its methods."""
    if entity_type == "Class":
        rows = graph._query(
            """MATCH (c)-[:DEFINES]->(f:Function)-[:HAS_FS_OP]->(fs:FileSystemOp)
               WHERE ID(c) = $nid
               RETURN DISTINCT fs.op
               LIMIT 10""",
            {"nid": node_id},
        ).result_set
    else:
        rows = graph._query(
            """MATCH (n)-[:HAS_FS_OP]->(fs:FileSystemOp)
               WHERE ID(n) = $nid
               RETURN DISTINCT fs.op
               LIMIT 10""",
            {"nid": node_id},
        ).result_set
    return [r[0] for r in rows if r[0]]


def _get_existing_requirements(graph: Graph, capability: str) -> list[str]:
    """Return requirement texts from MarkdownSections whose path contains *capability*."""
    rows = graph._query(
        """MATCH (m:MarkdownSection)-[:DEFINES_REQUIREMENT]->(req:Requirement)
           WHERE m.path CONTAINS $cap
           RETURN req.text
           LIMIT 20""",
        {"cap": capability},
    ).result_set
    return [r[0] for r in rows if r[0]]


def _get_requirements_for_section(graph: Graph, section_id: int) -> list[str]:
    """Return requirement texts linked directly from the MarkdownSection node."""
    rows = graph._query(
        """MATCH (m)-[:DEFINES_REQUIREMENT]->(req:Requirement)
           WHERE ID(m) = $sid
           RETURN req.text
           LIMIT 20""",
        {"sid": section_id},
    ).result_set
    return [r[0] for r in rows if r[0]]
