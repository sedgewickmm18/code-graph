---
name: gen-spec
description: "Use this skill when the user wants to generate, write, or draft
  an OpenSpec specification from code. Trigger phrases include: 'generate spec',
  'write spec', 'spec from code', 'document capability', 'openspec', 'draft a spec',
  'create spec.md', 'spec for this class', 'spec for this file'."
---

# Gen-Spec Skill

You generate OpenSpec `spec.md` drafts from an indexed code knowledge graph.
The MCP tool path (`get_spec_context` / `generate_spec`) is preferred over the
CLI path when `cgraph-mcp` is connected. Both paths are documented below.

---

## Prerequisites

The target repository must be indexed before any spec can be generated:

```
index_repo(path_or_url=".")                          # MCP
# or
cgraph index . --ignore node_modules --ignore .venv  # CLI
```

---

## Step 1 — Discover good capability names

A "capability" is any class name, file basename, route path fragment, or
markdown section title in the graph. The tool resolves names in this order:

| Pass | Matches |
|------|---------|
| 1a | `Class.name` exact |
| 1b | `Class.name` contains |
| 2 | `File.name` contains |
| 2b | `File.path` contains *(catches package/directory names)* |
| 3 | `Route.path` contains |
| 4 | `Decorator.name` contains |
| 5a | `MarkdownSection.title` exact |
| 5b | `MarkdownSection.title` contains |

Use `search_code` to find real names from the graph:

```
search_code(query="dashboard scraper pipeline", project="my-repo")
```

Concrete names produce richer specs than directory names:

| Thin (avoid) | Rich (prefer) |
|---|---|
| `tekton_scraper` *(directory)* | `fetch_runs`, `analyze_errors` *(files)* |
| `src` | `SourceAnalyzer`, `Graph` *(classes)* |
| `api` | `index.py`, `graph.py` *(files)* |

---

## Step 2 — Inspect context before calling the LLM

**Always call `get_spec_context` first.** It is a pure graph query (no LLM
cost) and tells you whether the capability resolved and whether the context
is rich enough to produce a useful spec.

```
get_spec_context(project="my-repo", capability="SourceAnalyzer")
```

The response contains `entity_type`, `entity_path`, and the full `context`
block. Check:

- **`entity_type`** — confirm the right kind of entity was matched.
- **`[DOCSTRING]`** — if `(none)`, the class/function has no docstring; the
  spec will lack purpose context.
- **`[CALLS]`** — if `(none)`, no outbound call edges; the spec will lack
  behavioural detail.

If both `[DOCSTRING]` and `[CALLS]` are `(none)`, try a more specific name:

```
# Too broad — resolves to __init__.py with no docstring
get_spec_context(project="github-tekton-dashboard", capability="tekton_scraper")

# Better — resolves to fetch_runs.py with real content
get_spec_context(project="github-tekton-dashboard", capability="fetch_runs")
```

If the response contains `{"error": "..."}`, no entity matched. Try:
- The exact class name from `search_code` results.
- The exact `MarkdownSection` title (e.g. `"Dashboard Pages"` not `"dashboard"`).
- A concrete filename (`fetch_runs`) instead of a package path (`tekton_scraper`).

---

## Step 3 — Generate the spec

Once `get_spec_context` shows a rich context, call `generate_spec`:

```
generate_spec(
    project="my-repo",
    capability="SourceAnalyzer",
    llm_url="http://localhost:8080/v1",   # optional; overrides CGRAPH_LLM_BASE_URL
    llm_model="llama3",                   # optional; overrides CGRAPH_LLM_MODEL
)
```

The response contains `spec_md` — a raw markdown string in OpenSpec format.

---

## Step 4 — Write the spec to disk

Write the returned `spec_md` to `openspec/specs/<slug>/spec.md`, creating
parent directories as needed. Use a lowercase-hyphenated slug derived from
the capability name:

| Capability | Output path |
|---|---|
| `SourceAnalyzer` | `openspec/specs/source-analyzer/spec.md` |
| `Dashboard Pages` | `openspec/specs/dashboard-pages/spec.md` |
| `fetch_runs` | `openspec/specs/fetch-runs/spec.md` |

---

## Multi-capability workflow

Use `list_spec_candidates` to drive a bulk loop — no manual name discovery needed:

```
candidates = list_spec_candidates(project="my-repo", branch="main")

for c in candidates:
    if not c["recommended"]:
        continue   # skip stubs and undocumented classes

    ctx = get_spec_context(project="my-repo", capability=c["name"])
    if "error" in ctx:
        continue

    result = generate_spec(project="my-repo", capability=c["name"])
    if "spec_md" in result:
        write_file(f"openspec/specs/{c['slug']}/spec.md", result["spec_md"])
```

This generates one spec per `recommended` class with no manual filtering.

---

## Step 5 — Iterate if quality is poor

If the generated spec is thin (few requirements, vague scenarios), the context
was probably too sparse. Improve it by:

1. Choosing a more specific capability name (concrete file > package directory).
2. Checking `[EXISTING REQUIREMENTS]` in the context — if populated, the spec
   will be grounded in documented requirements automatically.
3. Using `dry_run=True` to inspect the exact prompt before retrying:
   ```
   generate_spec(project="my-repo", capability="fetch_runs", dry_run=True)
   ```

---

## CLI path (when cgraph-mcp is not connected)

```bash
# Inspect context — no LLM call
cgraph gen-spec SourceAnalyzer --repo my-repo --dry-run

# Generate and print to stdout
cgraph gen-spec SourceAnalyzer --repo my-repo

# Generate and write to file
cgraph gen-spec SourceAnalyzer --repo my-repo \
  -o openspec/specs/source-analyzer/spec.md

# With explicit LLM endpoint
cgraph gen-spec "Dashboard Pages" \
  --repo github-tekton-dashboard \
  --branch main \
  --llm-url http://localhost:8080/v1 \
  --llm-model llama3 \
  -o openspec/specs/dashboard-pages/spec.md
```

---

## Reference

- Full resolution-order table and worked examples: [`docs/GEN_SPEC_GUIDE.md`](../../docs/GEN_SPEC_GUIDE.md)
- LLM environment variables: `CGRAPH_LLM_BASE_URL`, `CGRAPH_LLM_MODEL`, `CGRAPH_LLM_API_KEY`
- Tool implementations: [`api/mcp/tools/genspec.py`](../../api/mcp/tools/genspec.py)
