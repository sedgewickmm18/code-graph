"""Security MCP tools for code-graph.

Exposes five tools that let a coding agent drive security analysis and fix
issues based on the results:

  security_scan        — discover findings (XSS, auth-drift, path-traversal, CVE)
  get_decorators       — what auth decorators does a function have?
  get_template_vars    — what variables reach which HTML elements, and are they safe?
  get_fs_ops           — what file-system operations exist (optionally tainted only)?
  mark_vulnerability   — tag a package's ExternalFunction nodes with a CVE status

Typical agent workflow
----------------------
1. ``security_scan``            → list of findings with file + line coordinates
2. Per-finding investigation    → ``get_template_vars`` / ``get_decorators`` / ``get_fs_ops``
3. ``find_symbol`` + read_file  → navigate to the exact call site (structural tools)
4. Edit the file                → apply the fix
5. ``index_repo`` + ``security_scan`` again → verify zero findings

Conventions
-----------
* Every tool accepts ``project`` (required) and ``branch`` (optional, defaults to
  ``_default``) so the agent can scope queries to a specific per-branch graph.
* ``_project_arg`` / ``_relativize`` / ``_coerce_node_id`` are reused from the
  structural module to keep the response shape consistent.
* All graph I/O uses the async ``AsyncGraphQuery`` to avoid blocking the MCP
  event loop.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..server import app
from .structural import _coerce_node_id, _project_arg, _relativize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _props(node: Any) -> dict[str, Any]:
    """Return the properties dict from a raw FalkorDB Node."""
    if hasattr(node, "properties"):
        return dict(node.properties or {})
    return dict((node or {}).get("properties") or {})


def _node_id(node: Any) -> Optional[int]:
    """Return the integer node ID from a raw FalkorDB Node."""
    if hasattr(node, "id"):
        return node.id
    return (dict(node) if node else {}).get("id")


# ---------------------------------------------------------------------------
# Tool: security_scan
# ---------------------------------------------------------------------------

@app.tool(
    name="security_scan",
    description=(
        "Run security analysis rules against an indexed repository and return structured "
        "findings. Each finding includes source file and line number so the agent can "
        "read and fix the code directly.\n\n"
        "Rules:\n"
        "  'xss'            — template variables that reach an HtmlElement without passing "
        "through a sanitizer (html.escape, markupsafe.escape, sanitize_* functions)\n"
        "  'auth_drift'     — API routes documented in Markdown as requiring authentication "
        "but whose Python handler lacks a @login_required decorator\n"
        "  'path_traversal' — open() / os.path.join() calls whose argument originates from "
        "request.args, request.form, or request.json\n"
        "  'cve'            — functions in the codebase that call into a named pip package "
        "(requires package_name). Combine with mark_vulnerability to tag a package after "
        "a CVE alert.\n\n"
        "Pass rules=None to run all rules except 'cve' (which needs package_name). "
        "Each finding carries {rule, severity, results[]} where every result row "
        "includes SourceFile and SourceLine for direct navigation."
    ),
)
async def security_scan(
    project: str,
    branch: Optional[str] = None,
    rules: Optional[list[str]] = None,
    package_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Run one or more security rules and return structured findings.

    Args:
        project: Project name (returned by ``index_repo``).
        branch: Branch identity. Defaults to ``_default``.
        rules: Subset of ``['xss', 'auth_drift', 'path_traversal', 'cve']`` to
            run. ``None`` runs all rules except ``'cve'`` (which needs
            *package_name* to be meaningful).
        package_name: Required when ``'cve'`` is in *rules*. The pip package
            name to query blast-radius for (e.g. ``'requests'``).

    Returns:
        List of finding dicts::

            [
                {
                    "rule": "xss",
                    "severity": "high",
                    "count": 2,
                    "results": [
                        {
                            "UnsafeVariable": "user_comment",
                            "TargetElement": "div",
                            "SourceFile": "app/views.py",
                            "SourceLine": 42,
                        },
                        ...
                    ],
                },
                ...
            ]
    """
    active: set[str] = set(rules) if rules else {"xss", "auth_drift", "path_traversal"}

    _QUERIES: dict[str, tuple[str, str, list[str]]] = {
        # rule_id → (severity, cypher, column_names)
        "xss": (
            "high",
            (
                "MATCH (v:Variable)-[:INJECTED_INTO]->(e:HtmlElement) "
                "WHERE NOT EXISTS { "
                "    MATCH (v)-[:PROCESSED_BY]->(:Function) "
                "} "
                "RETURN v.name AS UnsafeVariable, e.name AS TargetElement, "
                "       v.path AS SourceFile, v.src_line AS SourceLine"
            ),
            ["UnsafeVariable", "TargetElement", "SourceFile", "SourceLine"],
        ),
        "auth_drift": (
            "high",
            (
                "MATCH (m:MarkdownSection)-[:DEFINES_ROUTE]->(r:Route) "
                "OPTIONAL MATCH (f:Function {route: r.path}) "
                "WHERE f IS NULL OR NOT (f)-[:HAS_DECORATOR]->(:Decorator {name: 'login_required'}) "
                "RETURN m.title AS DocumentedSection, r.path AS UnsecuredRoute, "
                "       r.method AS Method, "
                "       CASE WHEN f IS NULL THEN 'no handler found' ELSE f.name END AS HandlerFunction, "
                "       CASE WHEN f IS NULL THEN null ELSE ID(f) END AS HandlerSymbolId"
            ),
            ["DocumentedSection", "UnsecuredRoute", "Method", "HandlerFunction", "HandlerSymbolId"],
        ),
        "path_traversal": (
            "medium",
            (
                "MATCH (caller:Function)-[:HAS_FS_OP]->(fs:FileSystemOp) "
                "WHERE fs.tainted = true "
                "RETURN fs.op AS Operation, fs.path AS SourceFile, fs.src_line AS SourceLine, "
                "       caller.name AS CallerFunction, ID(caller) AS CallerSymbolId"
            ),
            ["Operation", "SourceFile", "SourceLine", "CallerFunction", "CallerSymbolId"],
        ),
    }

    findings: list[dict[str, Any]] = []
    g = _project_arg(project, branch)

    try:
        for rule_id, (severity, cypher, cols) in _QUERIES.items():
            if rule_id not in active:
                continue
            try:
                res = await g._query(cypher)
                rows = res.result_set or []
                results = []
                for row in rows:
                    rec: dict[str, Any] = {}
                    for i, col in enumerate(cols):
                        val = row[i] if i < len(row) else None
                        # Relativize file paths so the agent sees repo-relative paths
                        if col in ("SourceFile",) and val:
                            val = _relativize(val, project)
                        rec[col] = val
                    results.append(rec)
                findings.append({
                    "rule": rule_id,
                    "severity": severity,
                    "count": len(results),
                    "results": results,
                })
            except Exception:
                logger.warning("security_scan: rule %s failed", rule_id, exc_info=True)
                findings.append({
                    "rule": rule_id,
                    "severity": severity,
                    "count": 0,
                    "results": [],
                    "error": "query failed — graph may not have been indexed with security entities",
                })

        # CVE rule — optional, needs package_name
        if "cve" in active:
            if not package_name:
                findings.append({
                    "rule": "cve",
                    "severity": "critical",
                    "count": 0,
                    "results": [],
                    "error": "package_name is required for the 'cve' rule",
                })
            else:
                cypher = (
                    "MATCH (f:Function)-[:CALLS]->(ef:ExternalFunction)"
                    "-[:BELONGS_TO]->(p:Package {name: $pkg}) "
                    "RETURN f.name AS CallerFunction, f.path AS CallerFile, "
                    "       ef.name AS VulnerableFunction, ef.module AS Module, "
                    "       ID(f) AS CallerSymbolId"
                )
                try:
                    res = await g._query(cypher, {"pkg": package_name})
                    rows = res.result_set or []
                    results = [
                        {
                            "CallerFunction": r[0],
                            "CallerFile": _relativize(r[1], project),
                            "VulnerableFunction": r[2],
                            "Module": r[3],
                            "CallerSymbolId": r[4],
                        }
                        for r in rows
                    ]
                    findings.append({
                        "rule": "cve",
                        "severity": "critical",
                        "package": package_name,
                        "count": len(results),
                        "results": results,
                    })
                except Exception:
                    logger.warning("security_scan: cve rule failed", exc_info=True)
                    findings.append({
                        "rule": "cve",
                        "severity": "critical",
                        "count": 0,
                        "results": [],
                        "error": "query failed",
                    })
    finally:
        await g.close()

    return findings


# ---------------------------------------------------------------------------
# Tool: get_decorators
# ---------------------------------------------------------------------------

@app.tool(
    name="get_decorators",
    description=(
        "Return the decorators applied to a Python function node identified by symbol_id "
        "(obtained from find_symbol). Use after an 'auth_drift' security_scan finding to "
        "confirm whether @login_required or an equivalent auth decorator is present, or "
        "absent. Returns [{name, src_file, src_line}] — one entry per decorator."
    ),
)
async def get_decorators(
    symbol_id: int | str,
    project: str,
    branch: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return decorators attached to a function node.

    Args:
        symbol_id: Integer node id of the Function (from ``find_symbol``).
        project: Project name.
        branch: Branch identity. Defaults to ``_default``.

    Returns:
        List of decorator records::

            [{"name": "login_required", "src_file": "app/views.py", "src_line": 41}]

        Empty list when the function has no decorators or the node is not a
        function.
    """
    node_id = _coerce_node_id(symbol_id)
    cypher = (
        "MATCH (f)-[:HAS_DECORATOR]->(d:Decorator) "
        "WHERE ID(f) = $sid "
        "RETURN d.name AS name, d.path AS src_file, d.src_line AS src_line "
        "ORDER BY d.src_line"
    )
    g = _project_arg(project, branch)
    try:
        res = await g._query(cypher, {"sid": node_id})
        return [
            {
                "name": row[0],
                "src_file": _relativize(row[1], project),
                "src_line": row[2],
            }
            for row in (res.result_set or [])
        ]
    finally:
        await g.close()


# ---------------------------------------------------------------------------
# Tool: get_template_vars
# ---------------------------------------------------------------------------

@app.tool(
    name="get_template_vars",
    description=(
        "Return all template variable injection flows in a project (or a specific template "
        "file). Each result shows the variable name, its Python source file and line, the "
        "HTML element it reaches, and whether it is sanitized (PROCESSED_BY a function). "
        "Use after an 'xss' security_scan finding to see exactly which render_template() "
        "keyword arguments need html.escape() wrapping. "
        "Pass template_file (repo-relative path or substring) to narrow to one template."
    ),
)
async def get_template_vars(
    project: str,
    branch: Optional[str] = None,
    template_file: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return Variable → HtmlElement injection flows with sanitization status.

    Args:
        project: Project name.
        branch: Branch identity. Defaults to ``_default``.
        template_file: Optional repo-relative path (or substring) to a template.
            When given, only flows whose HtmlElement lives in a matching file
            are returned.

    Returns:
        List of flow records::

            [
                {
                    "variable": "user_comment",
                    "var_src_file": "app/views.py",
                    "var_src_line": 42,
                    "element": "div",
                    "element_file": "templates/page.html",
                    "element_line": 15,
                    "sanitized": false,
                    "sanitizer": null,
                },
                ...
            ]

        ``sanitized`` is ``True`` when a ``PROCESSED_BY`` edge exists from the
        variable to a Function node (the sanitizer's name is returned in
        ``sanitizer``).
    """
    cypher = (
        "MATCH (v:Variable)-[:INJECTED_INTO]->(e:HtmlElement) "
        "OPTIONAL MATCH (v)-[:PROCESSED_BY]->(san:Function) "
        "RETURN v.name AS variable, v.path AS var_path, v.src_line AS var_line, "
        "       e.name AS element, e.path AS elem_path, e.src_start AS elem_line, "
        "       san IS NOT NULL AS sanitized, san.name AS sanitizer "
        "ORDER BY v.path, v.src_line"
    )
    g = _project_arg(project, branch)
    try:
        res = await g._query(cypher)
        rows = res.result_set or []
    finally:
        await g.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        var_name, var_path, var_line, elem, elem_path, elem_line, sanitized, sanitizer = (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
        )
        rel_elem = _relativize(elem_path, project)
        # Filter by template_file substring when requested
        if template_file and rel_elem and template_file not in rel_elem:
            continue
        out.append({
            "variable": var_name,
            "var_src_file": _relativize(var_path, project),
            "var_src_line": var_line,
            "element": elem,
            "element_file": rel_elem,
            "element_line": elem_line,
            "sanitized": bool(sanitized),
            "sanitizer": sanitizer,
        })
    return out


# ---------------------------------------------------------------------------
# Tool: get_fs_ops
# ---------------------------------------------------------------------------

@app.tool(
    name="get_fs_ops",
    description=(
        "Return file-system operations (open, os.path.join, pathlib.Path, …) found in a "
        "project. When tainted_only=True (default), returns only calls where the argument "
        "originates from user-controlled input (request.args / request.form / request.json). "
        "Each result includes the enclosing function name, its symbol_id (for use with "
        "impact_analysis), source file, and line number. "
        "Use after a 'path_traversal' security_scan finding to navigate to the exact call "
        "site and determine the fix (e.g. os.path.basename, Path(...).resolve())."
    ),
)
async def get_fs_ops(
    project: str,
    branch: Optional[str] = None,
    tainted_only: bool = True,
) -> list[dict[str, Any]]:
    """Return FileSystemOp nodes, optionally filtered to tainted ones.

    Args:
        project: Project name.
        branch: Branch identity. Defaults to ``_default``.
        tainted_only: When ``True`` (default), only returns operations whose
            ``tainted`` flag is set (argument traced to a request parameter).
            Pass ``False`` to return every tracked file-system call.

    Returns:
        List of operation records::

            [
                {
                    "op": "open",
                    "tainted": true,
                    "src_file": "app/views.py",
                    "src_line": 88,
                    "caller": "serve_file",
                    "caller_symbol_id": 55,
                },
                ...
            ]
    """
    if tainted_only:
        cypher = (
            "MATCH (caller:Function)-[:HAS_FS_OP]->(fs:FileSystemOp) "
            "WHERE fs.tainted = true "
            "RETURN fs.op AS op, fs.tainted AS tainted, "
            "       fs.path AS src_file, fs.src_line AS src_line, "
            "       caller.name AS caller, ID(caller) AS caller_id "
            "ORDER BY fs.path, fs.src_line"
        )
    else:
        cypher = (
            "MATCH (caller:Function)-[:HAS_FS_OP]->(fs:FileSystemOp) "
            "RETURN fs.op AS op, fs.tainted AS tainted, "
            "       fs.path AS src_file, fs.src_line AS src_line, "
            "       caller.name AS caller, ID(caller) AS caller_id "
            "ORDER BY fs.path, fs.src_line"
        )

    g = _project_arg(project, branch)
    try:
        res = await g._query(cypher)
        return [
            {
                "op": row[0],
                "tainted": bool(row[1]),
                "src_file": _relativize(row[2], project),
                "src_line": row[3],
                "caller": row[4],
                "caller_symbol_id": row[5],
            }
            for row in (res.result_set or [])
        ]
    finally:
        await g.close()


# ---------------------------------------------------------------------------
# Tool: mark_vulnerability
# ---------------------------------------------------------------------------

@app.tool(
    name="mark_vulnerability",
    description=(
        "Tag all ExternalFunction nodes belonging to a pip package with a vulnerability "
        "status (default: 'vulnerable'). After marking, security_scan with rule='cve' and "
        "the CVE blast-radius Cypher query will surface every code path that reaches the "
        "affected functions. Pass status='clear' to remove a prior alert. "
        "Returns the number of nodes marked and the package name."
    ),
)
async def mark_vulnerability(
    project: str,
    package_name: str,
    branch: Optional[str] = None,
    status: str = "vulnerable",
) -> dict[str, Any]:
    """Set (or clear) the ``status`` property on ExternalFunction nodes for a package.

    Args:
        project: Project name.
        package_name: pip package name (e.g. ``'requests'``, ``'flask'``).
        branch: Branch identity. Defaults to ``_default``.
        status: Value to set on each ``ExternalFunction.status``. Use
            ``'vulnerable'`` to flag after a CVE alert; ``'clear'`` to remove
            the flag once patched.

    Returns:
        ``{"package": "requests", "status": "vulnerable", "marked": 12}``
    """
    cypher = (
        "MATCH (ef:ExternalFunction)-[:BELONGS_TO]->(p:Package {name: $pkg}) "
        "SET ef.status = $status "
        "RETURN count(ef) AS marked"
    )
    g = _project_arg(project, branch)
    try:
        res = await g._query(cypher, {"pkg": package_name, "status": status})
        rows = res.result_set or []
        marked = int(rows[0][0]) if rows else 0
    finally:
        await g.close()

    return {
        "package": package_name,
        "status": status,
        "marked": marked,
    }
