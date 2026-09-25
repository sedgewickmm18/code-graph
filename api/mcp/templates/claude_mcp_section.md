# code-graph MCP server — agent guidance

This repo is indexed into a FalkorDB **code knowledge graph** exposed
to you over MCP as `code-graph`. Use it instead of grepping when you
need to understand how symbols connect.

## When to call each tool

### Structural tools

| Tool | Call this when… | Example |
|---|---|---|
| `index_repo(path_or_url, branch?)` | **First** thing in a new repo; or after large changes outside your edits. Project name is **derived from the folder or repo URL** — read it back from the response. | `index_repo(path_or_url=".")` |
| `search_code(query, project)` | You know part of a symbol name and need its id (hybrid prefix + ranked match). | `search_code(query="processPay", project="myrepo")` |
| `find_symbol(name, project, file?)` | You know the exact symbol name (optionally in a given file) and want its id directly. | `find_symbol(name="processPayment", project="myrepo")` |
| `get_neighbors(symbol_id, project, relation?, direction?)` | "Who calls this?" (`direction="IN"`), "What does this call?" (`direction="OUT"`), or other edges via `relation` (CALLS/IMPORTS/DEFINES). | `get_neighbors(symbol_id=42, project="myrepo", direction="IN")` |
| `get_file_neighbors(file, project)` | Symbols a file defines / depends on — "what's in this file and what does it touch?" | `get_file_neighbors(file="api/graph.py", project="myrepo")` |
| `impact_analysis(symbol_id, project, direction, depth)` | **"What breaks if I change this?"** Transitive upstream callers. | `impact_analysis(symbol_id=42, project="myrepo", direction="IN", depth=3)` |
| `find_path(source_id, dest_id, project)` | Show the call chain between two known symbols. | `find_path(source_id=10, dest_id=42, project="myrepo")` |

### Security tools

| Tool | Call this when… | Example |
|---|---|---|
| `security_scan(project, rules?, package_name?)` | **Discover** security issues. Runs XSS, auth-drift, path-traversal, and/or CVE blast-radius rules. Start here. | `security_scan(project="myrepo", rules=["xss","auth_drift","path_traversal"])` |
| `get_template_vars(project, template_file?)` | Investigate an **XSS** finding — see which render_template() kwargs reach which HTML elements unsanitized. | `get_template_vars(project="myrepo", template_file="page.html")` |
| `get_decorators(symbol_id, project)` | Investigate an **auth_drift** finding — confirm whether @login_required or equivalent is present on a route handler. | `get_decorators(symbol_id=17, project="myrepo")` |
| `get_fs_ops(project, tainted_only?)` | Investigate a **path_traversal** finding — list file-system calls that receive user-controlled input. | `get_fs_ops(project="myrepo", tainted_only=True)` |
| `mark_vulnerability(project, package_name, status?)` | **Tag a package** after a CVE alert so subsequent `security_scan(rules=["cve"])` surfaces the blast radius. Use `status="clear"` once patched. | `mark_vulnerability(project="myrepo", package_name="requests")` |

### Gen-spec tools

| Tool | Call this when… | Example |
|---|---|---|
| `get_spec_context(project, capability, branch?)` | **Before every spec generation** — inspect the assembled context (docstrings, calls, auth, requirements) to verify the capability resolved correctly and the context is rich enough. Returns `{capability, entity_type, entity_path, context}` or `{error}`. No LLM call. | `get_spec_context(project="myrepo", capability="SourceAnalyzer")` |
| `generate_spec(project, capability, branch?, llm_url?, llm_model?, dry_run?)` | **Generate** the spec draft. Returns `{capability, spec_md}` — write `spec_md` to `openspec/specs/<slug>/spec.md`. Pass `dry_run=True` to inspect the context packet without calling the LLM. | `generate_spec(project="myrepo", capability="SourceAnalyzer")` |

## Security analysis workflow

```
1. security_scan(rules=["xss","auth_drift","path_traversal"])
        │
        ├─ XSS findings?
        │     get_template_vars(template_file=...)
        │     read_file(src_file, range=src_line±5)
        │     → wrap variable with html.escape()
        │
        ├─ auth_drift findings?
        │     find_symbol(handler_name, project)  → symbol_id
        │     get_decorators(symbol_id, project)
        │     impact_analysis(symbol_id, direction="IN")
        │     → add @login_required decorator
        │
        └─ path_traversal findings?
              get_fs_ops(project, tainted_only=True)
              read_file(src_file, range=src_line±5)
              impact_analysis(caller_symbol_id, direction="IN")
              → sanitize path with os.path.basename / Path.resolve()

2. Edit the file(s)

3. index_repo(path_or_url=".")           ← re-index after edits
   security_scan(rules=["xss",…])        ← verify zero findings
```

## Generate a spec workflow

```
1. list_spec_candidates(project)
         │  → ready-to-loop list, recommended=True items first
         │
2. get_spec_context(project, capability)
         │  → check entity_type, [DOCSTRING], [CALLS]
         │  → if both are "(none)", skip or try a more specific name
         │
3. generate_spec(project, capability)
         │  → receive spec_md string
         │
4. write spec_md to openspec/specs/<slug>/spec.md
```

## Rules of thumb

1. **Start with `search_code` or `find_symbol`** to turn names into ids. Most tools take a `symbol_id`.
2. **Use `get_neighbors` with `direction`** for who-calls / what-calls: `IN` = callers, `OUT` = callees. Pass `relation` for IMPORTS/DEFINES edges.
3. **`impact_analysis` before refactoring.** Even when you think you know
   the answer — the transitive closure often surprises you.
4. **`branch` is optional** but pass it when working on a feature branch
   so you query the right per-branch index.
5. **Security findings include `SourceFile` + `SourceLine`** — use them
   directly with `read_file` to navigate to the exact call site without a
   separate `find_symbol` round-trip.
6. **After fixing, re-index and re-scan** to confirm zero findings.
7. **Always `get_spec_context` before `generate_spec`.** If `[DOCSTRING]` and
   `[CALLS]` are both `(none)` the spec will be thin — try a more specific
   capability name (e.g. `fetch_runs` instead of `tekton_scraper`).
8. **`generate_spec` returns `spec_md`** — write it to
   `openspec/specs/<slug>/spec.md` yourself using your file-write tool.
9. **Response shape.** Tools that return collections put the array in
   `structuredContent.result` per the MCP spec. `index_repo` and
   `mark_vulnerability` return a single object.

## Environment

- `CODE_GRAPH_AUTO_INDEX=true` — auto-index CWD on first tool call (off by
  default; opt-in because indexing big repos takes minutes).
- `FALKORDB_HOST` / `FALKORDB_PORT` — defaults to `localhost:6379`. If
  unreachable on localhost, the server runs `cgraph ensure-db` to
  spin up the official Docker image.
