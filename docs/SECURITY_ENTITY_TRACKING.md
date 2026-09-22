# Extended Entity Tracking for Security Analysis

> **Status:** Implementation in progress  
> **Plan graph:** `plan:code-graph:extended-entity-tracking` in FalkorDB  
> **Branch:** `staging`

---

## Overview

This document describes how code-graph is being extended to track **security-relevant
entities** beyond classes, functions, modules, and files. By adding HTML elements,
Python decorators, pip packages, template variables, Markdown documentation sections,
and file-system operations as first-class graph nodes, four classes of security
vulnerability can be detected with Cypher queries directly against the indexed graph.

---

## New Node Labels

| Label | What it represents | Source |
|---|---|---|
| `HtmlElement` | A DOM element in a `.html` / `.jinja2` template | HTML analyzer |
| `HtmlForm` | A `<form>` element — the classic XSS entry-point | HTML analyzer |
| `Variable` | A Python variable injected into a template | Python analyzer |
| `Decorator` | A decorator applied to a Python function / route | Python analyzer |
| `Package` | A pip dependency declared in `requirements.txt` / `pyproject.toml` | Python analyzer |
| `ExternalFunction` | A function inside a venv `site-packages` path | Python analyzer |
| `MarkdownSection` | An H2/H3 heading in a `.md` documentation file | Markdown analyzer |
| `Requirement` | A bullet-point security requirement in a doc | Markdown analyzer |
| `FileSystemOp` | A call to `open()`, `os.path.join()`, `pathlib.Path()`, etc. | Python analyzer |

## New Relationship Edges

| Edge | From → To | Meaning |
|---|---|---|
| `INJECTED_INTO` | `Variable` → `HtmlElement` | Template variable reaches this element |
| `PROCESSED_BY` | `Variable` → `Function` | Variable passes through a sanitizer first |
| `HAS_DECORATOR` | `Function` → `Decorator` | Function has this decorator applied |
| `BELONGS_TO` | `ExternalFunction` → `Package` | Function lives in this package |
| `DEFINES_REQUIREMENT` | `MarkdownSection` → `Requirement` | Doc section declares this requirement |
| `IMPLEMENTS` | `Function` → `Requirement` | Python function satisfies this requirement |
| `DEFINES_ROUTE` | `MarkdownSection` → `Route` | Doc section documents this API route |
| `TAINTED_BY` | `FileSystemOp` → `Function` | File-system call receives user-controlled input |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     source_analyzer.py                       │
│  first_pass → per-file analyzer → graph nodes + edges        │
│  second_pass → cross-file symbol resolution → CALLS edges    │
└────────────────────┬────────────────────────────────────────┘
                     │ dispatches by file extension
        ┌────────────┼───────────────────────┐
        ▼            ▼                        ▼
 PythonAnalyzer  HtmlAnalyzer          MarkdownAnalyzer
 (.py)           (.html/.jinja2/.j2)   (.md)
        │            │                        │
        │  Variable  │  HtmlElement           │  MarkdownSection
        │  Decorator │  HtmlForm              │  Requirement
        │  Package   │  {{ var }} refs        │
        │  ExtFunc   │                        │
        │  FileSystemOp                       │
        └────────────┴────────────────────────┘
                     │
              api/graph.py helpers
              (add_decorator, add_package,
               add_html_element, add_variable,
               add_fs_op, add_external_function)
                     │
              FalkorDB graph
```

---

## Security Use Cases

### 1 — Cross-Site Scripting (XSS) Detection

**How it works:**  
The Python analyzer tracks `render_template()` / `TemplateResponse()` call keyword
arguments as `Variable` nodes. The HTML analyzer extracts `{{ var }}` template
references as `INJECTED_INTO` edges. If the variable passed through `html.escape`,
`markupsafe.escape`, or any function matching `sanitize_*` before reaching the
template, a `PROCESSED_BY` edge is added.

**Cypher query — find unsanitized template variables:**
```cypher
MATCH (v:Variable)-[:INJECTED_INTO]->(e:HtmlElement)
WHERE NOT EXISTS {
  MATCH (v)-[:PROCESSED_BY]->(:Function)
}
RETURN v.name AS UnsafeVariable, e.id AS TargetElement
```

---

### 2 — Auth-Drift Detection (Markdown → Code)

**How it works:**  
The Markdown analyzer parses documentation headings into `MarkdownSection` nodes
and bullet requirements into `Requirement` nodes. When a heading matches an API
route pattern (e.g. `POST /api/login`), a `DEFINES_ROUTE` edge is created. The
Python analyzer records `@login_required`, `@token_required`, etc. as `Decorator`
nodes via `HAS_DECORATOR` edges.

**Cypher query — find routes documented as requiring auth but missing a decorator:**
```cypher
MATCH (m:MarkdownSection)-[:DEFINES_ROUTE]->(r:Route)
MATCH (f:Function {route: r.path})
WHERE NOT (f)-[:HAS_DECORATOR]->(:Decorator {name: 'login_required'})
RETURN m.title, r.path AS UnsecuredRoute
```

---

### 3 — CVE Blast-Radius Analysis

**How it works:**  
pip packages from `requirements.txt` / `pyproject.toml` are stored as `Package`
nodes. When the symbol resolver resolves a call into a venv `site-packages` path,
an `ExternalFunction` node is created and linked to its `Package` via `BELONGS_TO`.
A security agent can mark a package node with `status: 'vulnerable'` after a CVE
alert and then query the exact call-chain from user-facing code to the vulnerable
function.

**Cypher query — find the shortest call path to a vulnerable external function:**
```cypher
MATCH path = shortestPath(
  (h:HtmlForm)-[:TRIGGERS*..10]->(ef:ExternalFunction {status: 'vulnerable'})
)
RETURN path
```

**Cypher query — list all code that directly calls functions in a vulnerable package:**
```cypher
MATCH (f:Function)-[:CALLS]->(ef:ExternalFunction)-[:BELONGS_TO]->(p:Package {name: $pkg})
RETURN f.name AS CallerFunction, f.src_file, ef.name AS VulnerableFunction
```

---

### 4 — Path-Traversal / Shadow Exfiltration Detection

**How it works:**  
The Python analyzer detects calls to `open()`, `os.path.join()`, `os.path.abspath()`,
`pathlib.Path()` and creates `FileSystemOp` nodes. If any argument originates from
a request parameter (`request.args`, `request.form`, `request.json`), a `TAINTED_BY`
edge is added to mark the operation as receiving user-controlled input.

**Cypher query — find tainted file-system operations:**
```cypher
MATCH (fs:FileSystemOp)-[:TAINTED_BY]->(src:Function)
WHERE NOT (fs)-[:VALIDATED_BY]->(:Function)
RETURN fs.op AS Operation, fs.src_file, fs.src_line, src.name AS TaintSource
```

---

## New Files Added

| File | Purpose |
|---|---|
| `api/analyzers/html/analyzer.py` | HTML/Jinja2 tree-sitter analyzer |
| `api/analyzers/markdown/analyzer.py` | Markdown heading/requirement extractor |
| `api/security_queries.py` | Pre-built Cypher query strings for all 4 use cases |
| `api/mcp/tools/security.py` | Five MCP tools for agent-driven security analysis |

## Files Modified

| File | Change |
|---|---|
| `api/analyzers/python/analyzer.py` | Decorator, Variable, Package, ExternalFunction, FileSystemOp extraction |
| `api/analyzers/source_analyzer.py` | Register `.html`, `.jinja2`, `.j2`, `.md` extensions |
| `api/graph.py` | Helper methods: `add_decorator`, `add_package`, `add_html_element`, `add_variable`, `add_fs_op`, `add_external_function` |
| `api/index.py` | New `/api/security_scan` endpoint; new labels in `/api/graph_entities` |
| `api/mcp/tools/__init__.py` | Register security tool module |
| `api/mcp/templates/claude_mcp_section.md` | Security tools table + workflow |
| `api/mcp/templates/cursorrules.template` | Security tool selection rules |

---

## Running a Security Scan

### HTTP endpoint

Once the repo is indexed with `POST /api/analyze_repo` or `cgraph index`, run:

```bash
curl -X POST http://localhost:5000/api/security_scan \
  -H "Content-Type: application/json" \
  -d '{"repo": "my-flask-app", "rules": ["xss", "auth_drift", "path_traversal"]}'
```

Response format:
```json
{
  "repo": "my-flask-app",
  "findings": [
    {
      "rule": "xss",
      "severity": "high",
      "results": [
        {"UnsafeVariable": "user_comment", "TargetElement": "div#comment-body"}
      ]
    }
  ]
}
```

### MCP tools (agent-driven)

The MCP server (`cgraph-mcp`) exposes five security tools for coding agents.

#### Tool inventory

| Tool | Description |
|---|---|
| `security_scan` | Discover findings. Runs XSS, auth-drift, path-traversal, and/or CVE rules. Returns file + line coordinates with every finding. |
| `get_template_vars` | List all Variable→HtmlElement injection flows with a `sanitized` flag. Narrowable to a specific template file. |
| `get_decorators` | Return the decorators on a function node (by `symbol_id`). |
| `get_fs_ops` | List file-system operations, optionally filtered to tainted-only. Returns `caller_symbol_id` for use with `impact_analysis`. |
| `mark_vulnerability` | Set `status='vulnerable'` on all `ExternalFunction` nodes in a package. Pass `status='clear'` once patched. |

#### Agentic workflow

```
1. security_scan(project, rules=["xss","auth_drift","path_traversal"])
        │
        ├─ XSS findings?
        │     get_template_vars(project, template_file=...)
        │     read_file(SourceFile, range=SourceLine±5)
        │     → wrap variable with html.escape()
        │
        ├─ auth_drift findings?
        │     find_symbol(HandlerFunction, project)  → symbol_id
        │     get_decorators(symbol_id, project)
        │     impact_analysis(symbol_id, direction="IN")
        │     → add @login_required decorator
        │
        └─ path_traversal findings?
              get_fs_ops(project, tainted_only=True)
              read_file(src_file, range=src_line±5)
              impact_analysis(caller_symbol_id, direction="IN")
              → sanitize with os.path.basename() / Path(...).resolve()

2. Edit the file(s)

3. index_repo(path_or_url=".")           ← re-index after edits
   security_scan(rules=["xss",…])        ← verify zero findings
```

#### CVE blast-radius with MCP

```
# 1. Mark the package after a CVE alert
mark_vulnerability(project, package_name="requests", status="vulnerable")

# 2. Discover all callers
security_scan(project, rules=["cve"], package_name="requests")
→ returns CallerFunction + CallerFile + VulnerableFunction + CallerSymbolId

# 3. Trace the call chain from the entry point
impact_analysis(CallerSymbolId, project, direction="IN")

# 4. Fix / upgrade the package, then clear the flag
mark_vulnerability(project, package_name="requests", status="clear")
```

---

## Task Execution Order

```
T1 (HtmlAnalyzer) ──┐
T2 (Decorators)  ──┤
T3 (Packages)    ──┼──► T4 (Variables/INJECTED_INTO) ──► T7 (graph.py helpers) ──► T8 (API/Frontend) ──► T10 (Tests)
T5 (Markdown)    ──┤                                                              └──► T9 (security_scan) ──┘
T6 (FileSystemOp)──┘
```

All Phase 1 tasks (T1–T6) are independent and can be developed in parallel.
T7 consolidates their graph helper methods. T8 and T9 both depend on T7 and
can proceed in parallel. T10 requires both T8 and T9.

MCP security tools (S1–S6) are independent of T1–T10 and can be layered on
top of any indexed graph.
