"""Pre-built Cypher security queries for code-graph.

Each query string is parameterised for use with FalkorDB.  Import the
constants directly or call :func:`run_security_scan` from the FastAPI
endpoint.

Use cases
---------
1. XSS — unsanitized template variables
2. Auth-drift — unprotected API routes
3. CVE blast-radius — calls into a vulnerable package
4. Path-traversal — tainted file-system operations
"""

from __future__ import annotations

from typing import Optional

from .graph import Graph

# ---------------------------------------------------------------------------
# Query 1 — XSS: Variable injected into HTML without a sanitizer
# ---------------------------------------------------------------------------
XSS_UNSANITIZED_VARIABLES = """
MATCH (v:Variable)-[:INJECTED_INTO]->(e:HtmlElement)
WHERE NOT EXISTS {
    MATCH (v)-[:PROCESSED_BY]->(:Function)
}
RETURN v.name AS UnsafeVariable, e.name AS TargetElement,
       v.path AS SourceFile, v.src_line AS SourceLine
"""

# ---------------------------------------------------------------------------
# Query 2 — Auth-drift: route documented in Markdown but no auth decorator
# ---------------------------------------------------------------------------
AUTH_DRIFT_UNPROTECTED_ROUTES = """
MATCH (m:MarkdownSection)-[:DEFINES_ROUTE]->(r:Route)
OPTIONAL MATCH (f:Function {route: r.path})
WHERE f IS NULL OR NOT (f)-[:HAS_DECORATOR]->(:Decorator {name: 'login_required'})
RETURN m.title AS DocumentedSection, r.path AS UnsecuredRoute,
       r.method AS Method,
       CASE WHEN f IS NULL THEN 'no handler found' ELSE f.name END AS HandlerFunction
"""

# ---------------------------------------------------------------------------
# Query 3 — CVE blast-radius: calls into a named package
# ---------------------------------------------------------------------------
CVE_BLAST_RADIUS = """
MATCH (f:Function)-[:CALLS]->(ef:ExternalFunction)-[:BELONGS_TO]->(p:Package {name: $package_name})
RETURN f.name AS CallerFunction, f.path AS CallerFile,
       ef.name AS VulnerableFunction, ef.module AS Module
ORDER BY f.path
"""

# Shortest path from any HtmlForm trigger to a vulnerable ExternalFunction
CVE_SHORTEST_PATH = """
MATCH path = shortestPath(
    (h:HtmlForm)-[:TRIGGERS|CALLS*..10]->(ef:ExternalFunction {status: 'vulnerable'})
)
RETURN path
"""

# ---------------------------------------------------------------------------
# Query 4 — Path-traversal: tainted file-system operations
# ---------------------------------------------------------------------------
PATH_TRAVERSAL_TAINTED_OPS = """
MATCH (fs:FileSystemOp)
WHERE fs.tainted = true
OPTIONAL MATCH (caller:Function)-[:HAS_FS_OP]->(fs)
RETURN fs.op AS Operation, fs.path AS SourceFile, fs.src_line AS Line,
       CASE WHEN caller IS NULL THEN 'unknown' ELSE caller.name END AS CallerFunction
ORDER BY fs.path, fs.src_line
"""

# ---------------------------------------------------------------------------
# Combined scan runner
# ---------------------------------------------------------------------------

_RULES: dict[str, tuple[str, str, Optional[str]]] = {
    # rule_id → (display_name, query, severity)
    "xss": ("XSS: Unsanitized Template Variables", XSS_UNSANITIZED_VARIABLES, "high"),
    "auth_drift": ("Auth-Drift: Unprotected Routes", AUTH_DRIFT_UNPROTECTED_ROUTES, "high"),
    "path_traversal": ("Path-Traversal: Tainted FS Operations", PATH_TRAVERSAL_TAINTED_OPS, "medium"),
}


def run_security_scan(
    graph: Graph,
    rules: Optional[list[str]] = None,
    package_name: Optional[str] = None,
) -> list[dict]:
    """Run the requested security rules against *graph* and return findings.

    Args:
        graph: An initialised :class:`~api.graph.Graph` instance.
        rules: List of rule ids to run.  ``None`` runs all rules.
            Valid ids: ``"xss"``, ``"auth_drift"``, ``"path_traversal"``,
            ``"cve"`` (requires *package_name*).
        package_name: Required when ``"cve"`` is in *rules*.

    Returns:
        A list of finding dicts::

            [
                {
                    "rule": "xss",
                    "name": "XSS: Unsanitized Template Variables",
                    "severity": "high",
                    "results": [{"UnsafeVariable": "user_comment", ...}],
                },
                ...
            ]
    """
    active = set(rules) if rules else set(_RULES.keys())
    findings: list[dict] = []

    for rule_id, (name, query, severity) in _RULES.items():
        if rule_id not in active:
            continue
        try:
            result = graph._query(query)
            header = result.header or []
            results = [
                {col: row[i] for i, col in enumerate(header)}
                for row in (result.result_set or [])
            ]
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "security_scan: rule %s failed", rule_id, exc_info=True
            )
            results = []
        findings.append({"rule": rule_id, "name": name, "severity": severity, "results": results})

    # CVE rule — optional, needs package_name
    if "cve" in active and package_name:
        try:
            rows = graph._query(CVE_BLAST_RADIUS, {"package_name": package_name}).result_set
            results = [
                {"CallerFunction": r[0], "CallerFile": r[1], "VulnerableFunction": r[2], "Module": r[3]}
                for r in rows
            ]
        except Exception:
            results = []
        findings.append({
            "rule": "cve",
            "name": f"CVE Blast-Radius: {package_name}",
            "severity": "critical",
            "results": results,
        })

    return findings
