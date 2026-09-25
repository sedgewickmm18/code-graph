"""Gen-spec MCP tools for code-graph.

Exposes three tools that let a coding agent generate OpenSpec spec.md drafts
directly from the knowledge graph:

  list_spec_candidates — query the graph for all production classes worth
                         speccing: docstring-bearing, non-test, non-fixture,
                         deduplicated by name×path with build/venv noise
                         filtered out. Returns a ready-to-loop list.
  get_spec_context     — assemble and return the structured context block for a
                         named capability (no LLM call; use this first to verify
                         that the capability resolves and that context is rich).
  generate_spec        — assemble context then call a configurable
                         OpenAI-compatible LLM and return the spec_md string.
                         The agent is responsible for writing the result to
                         openspec/specs/<capability>/spec.md.

Typical agent workflow
----------------------
1. ``list_spec_candidates(project)``          → get the ready-to-loop list
2. ``get_spec_context(project, capability)``  → verify context richness
3. ``generate_spec(project, capability)``     → returns ``spec_md``
4. Agent writes ``spec_md`` to
   ``openspec/specs/<slug>/spec.md``

Conventions
-----------
* Both tools accept ``project`` (required) and ``branch`` (optional, defaults
  to ``_default``) consistent with all other code-graph tools.
* ``get_spec_context`` never raises — it returns ``{"error": "..."}`` when the
  capability is not found so the agent can try an alternative name.
* ``generate_spec`` with ``dry_run=True`` returns ``{"capability", "context"}``
  without touching the LLM; use it to inspect the packet first.
* Both tools run synchronous graph/LLM code inside a thread executor to avoid
  blocking the MCP event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..server import app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Noise-path fragments filtered out of list_spec_candidates
# ---------------------------------------------------------------------------

_NOISE_PATH_FRAGMENTS = (
    "/build/",
    "/dist/",
    "/.venv/",
    "/venv/",
    "/node_modules/",
    "/site-packages/",
    "/repositories/",
    "/ripwire/test/",
    "/ripwire/bench/",
    "/tests/source_files/",
    "/tests/mcp/fixtures/",
)

_TEST_CLASS_PREFIXES = ("Test", "test_")


def _is_noise(name: str, path: str) -> bool:
    """Return True for build artefacts, venv, test fixtures, and stub classes."""
    for frag in _NOISE_PATH_FRAGMENTS:
        if frag in path:
            return True
    for prefix in _TEST_CLASS_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool: list_spec_candidates
# ---------------------------------------------------------------------------

@app.tool(
    name="list_spec_candidates",
    description=(
        "Return all production classes in the project that are worth generating "
        "an OpenSpec spec for. Filters out: build artefacts, venv/site-packages, "
        "test classes (names starting with 'Test'), test fixtures, ripwire bench "
        "data, and zero-method stubs with no docstring. Deduplicates by "
        "(name, canonical_path) so classes that appear in both api/ and build/ "
        "are listed only once.\n\n"
        "Use this as the first step in a bulk spec-generation workflow:\n"
        "  candidates = list_spec_candidates(project)\n"
        "  for c in candidates:\n"
        "      ctx = get_spec_context(project, c['name'])\n"
        "      spec = generate_spec(project, c['name'])\n"
        "      write spec['spec_md'] to openspec/specs/<c['slug']>/spec.md\n\n"
        "Each result item contains: name, path, method_count, has_docstring, slug "
        "(lowercase-hyphenated name suitable for the openspec directory), and "
        "recommended (bool — True when method_count >= 3 AND has_docstring)."
    ),
)
async def list_spec_candidates(
    project: str,
    branch: Optional[str] = None,
    min_methods: int = 0,
    docstring_only: bool = False,
) -> list[dict[str, Any]]:
    """Return production classes worth speccing.

    Args:
        project: Project name (as returned by ``index_repo``).
        branch: Branch identity. Defaults to ``_default``.
        min_methods: Only include classes with at least this many methods.
            Default 0 (include all non-noise classes).
        docstring_only: When ``True``, exclude classes without a docstring.

    Returns:
        Sorted list of candidate dicts::

            [
                {
                    "name": "Graph",
                    "path": "api/graph.py",
                    "method_count": 50,
                    "has_docstring": true,
                    "slug": "graph",
                    "recommended": true,
                },
                ...
            ]

        Sorted by ``method_count`` descending, then ``name`` ascending.
    """
    loop = asyncio.get_running_loop()

    def _run() -> list[dict[str, Any]]:
        from api.graph import Graph

        g = Graph(project, branch=branch)

        q = """
            MATCH (c:Class)
            OPTIONAL MATCH (c)-[:DEFINES]->(f:Function)
            WITH c, count(f) AS method_count
            RETURN c.name AS name, c.path AS path,
                   method_count,
                   c.doc AS doc
            ORDER BY method_count DESC, c.name ASC
        """
        rows = g._query(q).result_set

        seen: set[tuple[str, str]] = set()
        results: list[dict[str, Any]] = []

        for row in rows:
            name: str = row[0] or ""
            path: str = row[1] or ""
            method_count: int = int(row[2] or 0)
            doc: str = row[3] or ""
            has_docstring = bool(doc.strip())

            if not name:
                continue
            if _is_noise(name, path):
                continue
            if method_count < min_methods:
                continue
            if docstring_only and not has_docstring:
                continue

            # Deduplicate: keep first occurrence (highest method_count due to ORDER BY)
            key = (name, path)
            if key in seen:
                continue
            seen.add(key)

            slug = name.lower().replace("_", "-")
            recommended = method_count >= 3 and has_docstring

            results.append({
                "name": name,
                "path": path,
                "method_count": method_count,
                "has_docstring": has_docstring,
                "slug": slug,
                "recommended": recommended,
            })

        return results

    return await loop.run_in_executor(None, _run)


# ---------------------------------------------------------------------------
# Tool: get_spec_context
# ---------------------------------------------------------------------------

@app.tool(
    name="get_spec_context",
    description=(
        "Assemble the structured context block for a named capability from the "
        "code knowledge graph. Returns the plain-text context that would be sent "
        "to the LLM, along with the resolved entity type and file path. "
        "Call this BEFORE generate_spec to verify the capability resolves "
        "correctly and that the context is rich enough to produce a useful spec.\n\n"
        "Capability resolution order (first match wins):\n"
        "  1a. Class.name exact\n"
        "  1b. Class.name contains\n"
        "  2.  File.name contains\n"
        "  2b. File.path contains  (catches package/directory names)\n"
        "  3.  Route.path contains\n"
        "  4.  Decorator.name contains\n"
        "  5a. MarkdownSection.title exact\n"
        "  5b. MarkdownSection.title contains\n\n"
        "Returns {capability, entity_type, entity_path, context} on success, or "
        "{error} when no entity is found — in which case try a more specific name "
        "(e.g. a concrete file like 'fetch_runs' rather than a directory like "
        "'tekton_scraper', or the exact MarkdownSection title like 'Dashboard Pages')."
    ),
)
async def get_spec_context(
    project: str,
    capability: str,
    branch: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble and return the spec context block for *capability*.

    Args:
        project: Project name (as returned by ``index_repo``).
        capability: Class name, file basename, route path fragment, or
            markdown section title to generate a spec for.
        branch: Branch identity. Defaults to ``_default``.

    Returns:
        On success::

            {
                "capability": "<matched name>",
                "entity_type": "Class|File|Route|Decorator|MarkdownSection",
                "entity_path": "<source file path>",
                "context": "<plain-text context block>",
            }

        On failure::

            {"error": "<human-readable message>"}
    """
    loop = asyncio.get_running_loop()

    def _run() -> dict[str, Any]:
        from api.graph import Graph
        from api.spec_context import build_spec_context, _resolve_entity

        g = Graph(project, branch=branch)
        try:
            entity_name, entity_path, _doc, _node_id, entity_type = _resolve_entity(g, capability)
        except ValueError as exc:
            return {"error": str(exc)}

        context = build_spec_context(g, capability)
        return {
            "capability": entity_name,
            "entity_type": entity_type,
            "entity_path": entity_path,
            "context": context,
        }

    return await loop.run_in_executor(None, _run)


# ---------------------------------------------------------------------------
# Tool: generate_spec
# ---------------------------------------------------------------------------

@app.tool(
    name="generate_spec",
    description=(
        "Generate an OpenSpec spec.md draft for a named capability by querying "
        "the code knowledge graph and calling a configurable OpenAI-compatible "
        "LLM endpoint.\n\n"
        "IMPORTANT: call get_spec_context first to verify the capability resolves "
        "and that [DOCSTRING] / [CALLS] are non-empty. A thin context (both sections "
        "show '(none)') will produce a low-quality spec — try a more specific "
        "capability name instead.\n\n"
        "Returns {capability, spec_md} where spec_md is the raw markdown string. "
        "The agent should write spec_md to openspec/specs/<capability-slug>/spec.md "
        "creating parent directories as needed.\n\n"
        "With dry_run=True the tool returns {capability, context} without calling "
        "the LLM — identical to get_spec_context but as a generate_spec call so "
        "the agent can confirm the packet in one step.\n\n"
        "LLM endpoint resolution: llm_url overrides CGRAPH_LLM_BASE_URL "
        "(default http://localhost:8080/v1); llm_model overrides CGRAPH_LLM_MODEL "
        "(default 'local')."
    ),
)
async def generate_spec(
    project: str,
    capability: str,
    branch: Optional[str] = None,
    llm_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate a spec.md draft for *capability* using the LLM.

    Args:
        project: Project name (as returned by ``index_repo``).
        capability: Class name, file basename, route path fragment, or
            markdown section title to generate a spec for.
        branch: Branch identity. Defaults to ``_default``.
        llm_url: OpenAI-compatible endpoint base URL. Overrides
            ``CGRAPH_LLM_BASE_URL`` env var.
        llm_model: Model name. Overrides ``CGRAPH_LLM_MODEL`` env var.
        dry_run: When ``True``, return the assembled context without calling
            the LLM.

    Returns:
        On dry run::

            {"capability": "...", "context": "..."}

        On success::

            {"capability": "...", "spec_md": "# ... Specification\\n..."}

        On failure::

            {"error": "..."}
    """
    loop = asyncio.get_running_loop()

    def _run() -> dict[str, Any]:
        from api.graph import Graph
        from api.spec_context import build_spec_context, _resolve_entity
        from api.spec_llm import generate_spec as _llm_generate

        g = Graph(project, branch=branch)
        try:
            entity_name, _path, _doc, _node_id, _etype = _resolve_entity(g, capability)
        except ValueError as exc:
            return {"error": str(exc)}

        context = build_spec_context(g, capability)

        if dry_run:
            return {"capability": entity_name, "context": context}

        try:
            spec_md = _llm_generate(
                context=context,
                capability_name=entity_name,
                base_url=llm_url or "",
                model=llm_model or "",
            )
        except RuntimeError as exc:
            return {"error": str(exc)}

        return {"capability": entity_name, "spec_md": spec_md}

    return await loop.run_in_executor(None, _run)
