# cgraph gen-spec — Usage Guide

`cgraph gen-spec` queries the code-graph knowledge graph for a named
capability, assembles a structured context packet from docstrings, call
edges, auth decorators, and filesystem operations, then calls a
configurable OpenAI-compatible LLM to draft an OpenSpec `spec.md`.

---

## Prerequisites

1. The target repository must be indexed:
   ```bash
   cgraph index /path/to/repo --repo <name>
   # or
   cgraph index-repo https://github.com/org/repo
   ```
2. An OpenAI-compatible LLM endpoint must be reachable (local or remote).
   Set the three `CGRAPH_LLM_*` environment variables, or pass them as
   flags (see [Configuration](#configuration)).

---

## Basic usage

```bash
cgraph gen-spec <capability> [options]
```

`<capability>` is matched against the graph in this order:

| Pass | Matches |
|------|---------|
| 1a | `Class.name` exact |
| 1b | `Class.name` contains |
| 2 | `File.name` contains |
| 2b | `File.path` contains (catches package/directory names) |
| 3 | `Route.path` contains |
| 4 | `Decorator.name` contains |
| 5a | `MarkdownSection.title` exact |
| 5b | `MarkdownSection.title` contains |

---

## Configuration

| Variable | Flag | Default | Purpose |
|----------|------|---------|---------|
| `CGRAPH_LLM_BASE_URL` | `--llm-url` | `http://localhost:8080/v1` | OpenAI-compatible endpoint (llama.cpp, Ollama, LM Studio, …) |
| `CGRAPH_LLM_MODEL` | `--llm-model` | `local` | Model name passed to the endpoint |
| `CGRAPH_LLM_API_KEY` | *(env only)* | `local` | API key — most local servers accept any non-empty string |

---

## Examples

### code-graph (Python/FastAPI project)

The `code-graph` repo has Python class nodes, so class names work directly.

```bash
# Inspect the context that will be sent to the LLM — no API call made
cgraph gen-spec SourceAnalyzer \
  --repo code-graph \
  --branch staging \
  --dry-run

# Generate a spec and print to stdout
cgraph gen-spec SourceAnalyzer \
  --repo code-graph \
  --branch staging \
  --llm-url http://localhost:8080/v1 \
  --llm-model llama3

# Write the spec to a file (OpenSpec convention)
cgraph gen-spec SourceAnalyzer \
  --repo code-graph \
  --branch staging \
  -o openspec/specs/source-analyzer/spec.md
```

Other useful capability names for this repo:

```bash
cgraph gen-spec Graph        --repo code-graph --branch staging --dry-run
cgraph gen-spec AsyncGraphQuery --repo code-graph --branch staging --dry-run
cgraph gen-spec index.py     --repo code-graph --branch staging --dry-run
```

---

### github-tekton-dashboard (Rust/TypeScript/Python project)

This repo has no top-level Python classes. The graph contains `File`,
`MarkdownSection`, and `Requirement` nodes. Use file basenames, package
path fragments, or markdown section titles.

#### By markdown section title

The repo's `README.md` is fully indexed with requirement nodes attached
to each section. Use the exact section title (or a substring):

```bash
# Dry-run — inspect context first
cgraph gen-spec "Dashboard Pages" \
  --repo github-tekton-dashboard \
  --branch main \
  --dry-run

# Generate spec for the architecture section
cgraph gen-spec Architecture \
  --repo github-tekton-dashboard \
  --branch main \
  -o openspec/specs/architecture/spec.md

# Any substring of a section title works
cgraph gen-spec "Data Cleanup" \
  --repo github-tekton-dashboard \
  --branch main \
  --dry-run
```

#### By file basename

```bash
# Matches File.name = "fetch_runs" (.py)
cgraph gen-spec fetch_runs \
  --repo github-tekton-dashboard \
  --branch main \
  --dry-run

cgraph gen-spec analyze_errors \
  --repo github-tekton-dashboard \
  --branch main \
  -o openspec/specs/analyze-errors/spec.md

cgraph gen-spec sqlite_backend \
  --repo github-tekton-dashboard \
  --branch main \
  --dry-run
```

#### By package/directory path fragment

When the capability name appears only in the directory path (not the
filename), pass 2b matches it against `File.path`:

```bash
# "tekton_scraper" is a directory, not a filename — matched via path
cgraph gen-spec tekton_scraper \
  --repo github-tekton-dashboard \
  --branch main \
  --dry-run
```

---

## Workflow tip: dry-run first

Always run `--dry-run` before calling the LLM. It prints the assembled
context as JSON so you can verify the match and the richness of the
`[EXISTING REQUIREMENTS]` section before burning tokens:

```bash
cgraph gen-spec "Dashboard Pages" \
  --repo github-tekton-dashboard \
  --branch main \
  --dry-run | jq .context
```

If the context looks thin (e.g. `[DOCSTRING]: (none)` and empty
`[CALLS]`), try a more specific capability name — a concrete file like
`fetch_runs` will usually produce richer context than a package directory
like `tekton_scraper`.
