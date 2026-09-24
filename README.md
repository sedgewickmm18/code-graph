<div align="center">

# CodeGraph - Knowledge Graph Visualization Tool

**Visualize codebases as knowledge graphs to analyze dependencies, detect bottlenecks, optimize projects, and run automated security scans.**

Connect and ask questions: [![Discord](https://img.shields.io/badge/Discord-%235865F2.svg?&logo=discord&logoColor=white)](https://discord.gg/b32KEzMzce)

[![Try Free](https://img.shields.io/badge/Try%20Free-FalkorDB%20Cloud-FF8101?labelColor=FDE900&link=https://app.falkordb.cloud)](https://app.falkordb.cloud)
[![Dockerhub](https://img.shields.io/docker/pulls/falkordb/falkordb?label=Docker)](https://hub.docker.com/r/falkordb/falkordb/)

![Alt Text](https://res.cloudinary.com/dhd0k02an/image/upload/v1739719361/FalkorDB_-_Github_-_readme_jr6scy.gif)

**[Live Demo](https://code-graph.falkordb.com/)**

</div>

## Project Structure

```
code-graph/
├── api/                  # Python backend (FastAPI)
│   ├── index.py          # FastAPI app, auth deps, API routes, SPA serving
│   ├── graph.py          # FalkorDB graph operations
│   ├── llm.py            # GraphRAG + LiteLLM chat integration
│   ├── project.py        # Repository cloning and analysis orchestration
│   ├── info.py           # Repository metadata stored in Redis/FalkorDB
│   ├── prompts.py        # LLM system and prompt templates
│   ├── cli.py            # cgraph CLI tool (typer)
│   ├── auto_complete.py  # Prefix search helper
│   ├── analyzers/        # Source analyzers (Python, Java, C#)
│   ├── entities/         # Graph/entity models
│   ├── git_utils/        # Git history graph utilities
│   └── code_coverage/    # Coverage utilities
├── app/                  # React frontend (Vite)
│   ├── src/              # Frontend source code
│   ├── public/           # Static assets
│   ├── package.json      # Frontend dependencies and scripts
│   ├── vite.config.ts    # Vite config and /api proxy for dev mode
│   └── tsconfig*.json    # TypeScript config
├── skills/code-graph/    # Claude Code skill for CLI-driven indexing/querying
├── tests/                # Backend/unit and endpoint tests
├── e2e/                  # End-to-end helpers and Playwright assets
├── Dockerfile            # Unified container image
├── docker-compose.yml    # Local FalkorDB + app stack
├── Makefile              # Common dev/build/test commands
├── start.sh              # Container entrypoint
├── pyproject.toml        # Python package and dependency config
└── .env.template         # Example environment variables
```

## Running Locally

### Prerequisites

- Python `>=3.12,<3.14`
- Node.js 20+
- [`uv`](https://docs.astral.sh/uv/)
- A FalkorDB instance (local/cloud) or the optional FalkorDBLite backend

### 1. Start FalkorDB

**Option A:** Free cloud instance at [app.falkordb.cloud](https://app.falkordb.cloud/signup)

**Option B:** Run locally with Docker:

```bash
docker run -p 6379:6379 -it --rm falkordb/falkordb
```

**Option C:** Use embedded FalkorDBLite:

```bash
uv sync --extra light
export CODE_GRAPH_DB_BACKEND=lite
export FALKORDB_LITE_PATH=~/.cache/code-graph/falkordblite.rdb
```

FalkorDBLite runs a local embedded server over a private Unix socket by default. Set `FALKORDB_LITE_PORT` only when a host/port-only integration, such as GraphRAG chat, must connect to the embedded database.

### 2. Configure environment variables

Copy the template and adjust it for your setup:

```bash
cp .env.template .env
```

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `CODE_GRAPH_DB_BACKEND` | Database backend: `falkordb` or `lite` | No | `falkordb` |
| `FALKORDB_HOST` | FalkorDB hostname | No | `localhost` |
| `FALKORDB_PORT` | FalkorDB port | No | `6379` |
| `FALKORDB_USERNAME` | Optional FalkorDB username | No | empty |
| `FALKORDB_PASSWORD` | Optional FalkorDB password | No | empty |
| `FALKORDB_LITE_PATH` | FalkorDBLite database file path | No | `~/.cache/code-graph/falkordblite.rdb` |
| `FALKORDB_LITE_HOST` | Host used when exposing FalkorDBLite over TCP | No | `127.0.0.1` |
| `FALKORDB_LITE_PORT` | Optional TCP port for FalkorDBLite host/port clients | No | empty |
| `SECRET_TOKEN` | Token checked by protected endpoints | No | empty |
| `CODE_GRAPH_PUBLIC` | Set `1` to skip auth on read-only endpoints | No | `0` |
| `ALLOWED_ANALYSIS_DIR` | Root path allowed for `/api/analyze_folder` | No | repository root |
| `MODEL_NAME` | LiteLLM model used by `/api/chat` | No | `gemini/gemini-flash-lite-latest` |
| `CGRAPH_LLM_BASE_URL` | OpenAI-compatible endpoint for `cgraph gen-spec` | No | `http://localhost:8080/v1` |
| `CGRAPH_LLM_MODEL` | Model name for `cgraph gen-spec` | No | `local` |
| `CGRAPH_LLM_API_KEY` | API key for `cgraph gen-spec` endpoint | No | `local` |
| `HOST` | Optional Uvicorn bind host for `start.sh`/`make run-*` | No | `0.0.0.0` or `127.0.0.1` depending on command |
| `PORT` | Optional Uvicorn bind port for `start.sh`/`make run-*` | No | `5000` |

The chat endpoint also needs the provider credential expected by your chosen `MODEL_NAME`. The default model is Gemini, so set `GEMINI_API_KEY` unless you switch to a different LiteLLM provider/model.

### Authentication behavior

- Send `Authorization: Bearer <SECRET_TOKEN>` (or the raw token string) when `SECRET_TOKEN` is configured.
- Read endpoints use the `public_or_auth` dependency.
- Mutating endpoints (`/api/analyze_folder`, `/api/analyze_repo`, `/api/switch_commit`) use the `token_required` dependency.
- If `SECRET_TOKEN` is unset, the current implementation accepts requests without an `Authorization` header.
- Setting `CODE_GRAPH_PUBLIC=1` makes the read-only endpoints public even when `SECRET_TOKEN` is configured.

### 3. Install dependencies

```bash
# Install backend dependencies
uv sync --all-extras

# Install frontend dependencies
npm install --prefix ./app

# Optional: install Playwright dependencies from the repo root
npm install
```

If you do not use `uv`, `pip install -e ".[test]"` also installs the backend package and test dependencies.

### 4. Run the app

**Backend API with auto-reload:**

```bash
uv run uvicorn api.index:app --host 127.0.0.1 --port 5000 --reload
```

**Frontend hot-reload with Vite:**

```bash
# Terminal 1: backend API
uv run uvicorn api.index:app --host 127.0.0.1 --port 5000 --reload

# Terminal 2: Vite dev server
cd app && npm run dev
```

The Vite dev server runs on `http://localhost:3000` and proxies `/api/*` requests to `http://127.0.0.1:5000`.

**Single-process built frontend + backend:**

```bash
npm --prefix ./app run build
uv run uvicorn api.index:app --host 0.0.0.0 --port 5000
```

In this mode, the FastAPI app serves the built React SPA from `app/dist` on `http://localhost:5000`.

### Using Make

```bash
make install       # Install backend + frontend dependencies
make install-cli   # Install cgraph CLI entry point
make build-dev     # Build frontend in development mode
make build-prod    # Build frontend for production
make run-dev       # Build dev frontend + run Uvicorn with reload
make run-prod      # Build prod frontend + run Uvicorn
make test          # Run backend pytest suite
make lint          # Run Ruff + frontend type-check
make e2e           # Run Playwright tests from repo root
make clean         # Remove build/test artifacts
```

`make test` currently points at the right backend test entrypoint, but some legacy analyzer/git-history tests still need maintenance before the suite passes on a clean checkout.

## CLI Tool (`cgraph`)

CodeGraph includes a CLI tool for indexing codebases and querying the knowledge graph directly from the terminal. All output is JSON (to stdout), with status messages on stderr.

### Install

```bash
# Install from PyPI (recommended for end users)
pipx install falkordb-code-graph

# Or with pip
pip install falkordb-code-graph
```

For development (from a local clone):

```bash
make install-cli
# or
uv pip install -e .
```

### Start with

```
uv run ./start.sh 
```

### Usage

```bash
# Ensure FalkorDB is running (auto-starts a Docker container if needed)
cgraph ensure-db

# Index the current project
cgraph index . --ignore node_modules --ignore .git --ignore venv --ignore __pycache__

# Index a remote repository
cgraph index-repo https://github.com/user/repo --ignore node_modules

# List indexed repos
cgraph list

# Search for entities by name prefix
cgraph search parse_config

# Explore relationships (what does node 42 call?)
cgraph neighbors 42 --rel CALLS

# Find call-chain paths between two nodes
cgraph paths 42 99

# Show repo statistics
cgraph info
```

The `--repo` flag defaults to the current directory name. Run `cgraph --help` for full details.

### Generate specs from code

`cgraph gen-spec` queries the knowledge graph for a named capability (class, file, or API route), assembles a structured context packet from docstrings, call edges, auth decorators and filesystem operations, then calls a configurable OpenAI-compatible LLM to draft an OpenSpec `spec.md`.

```bash
# Basic usage — prints the draft spec to stdout
cgraph gen-spec SourceAnalyzer

# Write the spec to a file
cgraph gen-spec SourceAnalyzer -o openspec/specs/source-analyzer/spec.md

# Inspect the assembled context without calling the LLM
cgraph gen-spec SourceAnalyzer --dry-run
```

| Variable | Default | Purpose |
|---|---|---|
| `CGRAPH_LLM_BASE_URL` | `http://localhost:8080/v1` | OpenAI-compatible endpoint for `cgraph gen-spec`; default assumes a local llama.cpp / Ollama / LM Studio server |
| `CGRAPH_LLM_MODEL` | `local` | Model name passed to the endpoint; use `provider/model` syntax for LiteLLM-routed endpoints |
| `CGRAPH_LLM_API_KEY` | `local` | API key for the endpoint; most local servers accept any non-empty string |

### Claude Code Skill

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill is included in `skills/code-graph/`. Install it with:

```bash
npx skills add FalkorDB/code-graph
```

Then ask Claude things like *"what functions call analyze_sources?"* or *"find the dependency chain between parse_config and send_request"* — it will handle the indexing and querying automatically.

### MCP server (`cgraph-mcp`)

For agents that speak the [Model Context Protocol](https://modelcontextprotocol.io)
(Claude Code, Cursor, Cline, …), code-graph ships a stdio MCP server
that exposes the knowledge graph as **12 first-class tools** across two groups:

**Structural tools** (navigate and understand code structure):
`index_repo`, `search_code`, `find_symbol`, `get_neighbors`, `get_file_neighbors`,
`impact_analysis`, `find_path`

**Security tools** (discover and fix vulnerabilities):
`security_scan`, `get_template_vars`, `get_decorators`, `get_fs_ops`, `mark_vulnerability`

Quickstart — Claude Code:

```bash
# 1. Install (in any venv with the cgraph package on PATH)
pip install falkordb-code-graph         # or: uv pip install falkordb-code-graph

# 2. Register with Claude Code
claude mcp add-json code-graph '{
  "command": "cgraph-mcp",
  "env": {
    "FALKORDB_HOST": "localhost",
    "FALKORDB_PORT": "6379",
    "CODE_GRAPH_AUTO_INDEX": "true"
  }
}'

# 3. Drop agent guidance into your repo
cd /path/to/your/repo
cgraph init-agent              # writes CLAUDE.md and .cursorrules
```

Quickstart — Docker Compose:

```bash
docker compose up -d falkordb                       # start the DB
docker compose --profile mcp run --rm -i code-graph-mcp   # attach via stdio
```

The MCP server auto-bootstraps FalkorDB if it's missing on localhost
(via `cgraph ensure-db`). When `CODE_GRAPH_AUTO_INDEX=true` is set,
the current working directory is indexed automatically on start.

#### Security tools quick reference

| Tool | Inputs | Use case |
|---|---|---|
| `security_scan` | `project`, `rules?`, `package_name?` | Discover XSS / auth-drift / path-traversal / CVE findings |
| `get_template_vars` | `project`, `template_file?` | Enumerate unsanitized template variable flows (XSS) |
| `get_decorators` | `symbol_id`, `project` | Check auth decorators on a route handler (auth-drift) |
| `get_fs_ops` | `project`, `tainted_only?` | Find tainted file-system calls with caller + line (path-traversal) |
| `mark_vulnerability` | `project`, `package_name`, `status?` | Tag/untag a package after a CVE alert (CVE blast-radius) |

All security findings include `SourceFile` and `SourceLine` so the agent can call
`read_file` directly without a separate `find_symbol` round-trip. Every tainted
file-system op and CVE caller result includes a `caller_symbol_id` / `CallerSymbolId`
ready for `impact_analysis`.

**Transport:** Phase 1 is stdio only. HTTP/SSE is deferred.

## Running with Docker

### Using Docker Compose

```bash
docker compose up --build
```

This starts FalkorDB and the CodeGraph app together. The checked-in compose file sets `CODE_GRAPH_PUBLIC=1` for the app service.

To run the **MCP stdio server** instead of the web app from the same
image, set `CGRAPH_MODE=mcp` and use the `mcp` profile:

```bash
docker compose --profile mcp run --rm -i code-graph-mcp
```

### Using Docker directly

```bash
docker build -t code-graph .

# Web mode (default)
docker run -p 5000:5000 \
  -e FALKORDB_HOST=host.docker.internal \
  -e FALKORDB_PORT=6379 \
  -e MODEL_NAME=gemini/gemini-flash-lite-latest \
  -e GEMINI_API_KEY=<YOUR_GEMINI_API_KEY> \
  -e SECRET_TOKEN=<YOUR_SECRET_TOKEN> \
  code-graph

# MCP stdio mode (same image)
docker run --rm -i \
  -e CGRAPH_MODE=mcp \
  -e FALKORDB_HOST=host.docker.internal \
  -e FALKORDB_PORT=6379 \
  -e MODEL_NAME=gemini/gemini-flash-lite-latest \
  code-graph
```

## Creating a Code Graph

### Analyze a local folder

`analyze_folder` only accepts paths under `ALLOWED_ANALYSIS_DIR` (defaults to the repository root unless you override it).

```bash
curl -X POST http://127.0.0.1:5000/api/analyze_folder \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_SECRET_TOKEN>" \
  -d '{"path": "<FULL_PATH_TO_FOLDER>", "ignore": [".github", ".git"]}'
```

### Analyze a Git repository

```bash
curl -X POST http://127.0.0.1:5000/api/analyze_repo \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_SECRET_TOKEN>" \
  -d '{"repo_url": "https://github.com/user/repo", "ignore": [".github", ".git"]}'
```

### List indexed repositories

```bash
curl http://127.0.0.1:5000/api/list_repos
```

## Supported Languages & File Types

`api/analyzers/source_analyzer.py` currently enables these analyzers:

| Extension(s) | Analyzer | Notes |
|---|---|---|
| `.py` | Python | Classes, functions, decorators, template vars, FS ops |
| `.java` | Java | Classes, methods, inheritance |
| `.cs` | C# | Classes, methods |
| `.js` | JavaScript | Functions, classes, methods |
| `.kt`, `.kts` | Kotlin | Classes, functions |
| `.html`, `.jinja2`, `.j2` | HTML/Jinja2 | `HtmlElement`, `HtmlForm`, `{{ var }}` refs |
| `.md` | Markdown | `MarkdownSection`, `Requirement`, route detection |
| `openspec/` tree | OpenSpec | `OpenSpecCapability`, `OpenSpecRequirement`, `OpenSpecScenario`, `OpenSpecChange`, `OpenSpecTask`, `OpenSpecDeltaSpec` |

A C analyzer exists in the source tree but is commented out and not currently registered.

### OpenSpec support

Repositories containing an `openspec/` directory (with an `openspec/specs/`
subdirectory) are automatically recognized and indexed by the
`OpenSpecAnalyzer`. The analyzer extracts the full structured specification
tree into the graph:

| Graph node | Source | Description |
|---|---|---|
| `OpenSpecCapability` | `openspec/specs/<name>/spec.md` | A documented capability and its purpose |
| `OpenSpecRequirement` `:Searchable` | `### Requirement:` headings | Verifiable behavior contract |
| `OpenSpecScenario` `:Searchable` | `#### Scenario:` headings | GIVEN/WHEN/THEN test scenario |
| `OpenSpecChange` | `openspec/changes/<name>/` | A proposed or in-flight change |
| `OpenSpecTask` | `openspec/changes/<name>/tasks.md` | A checkbox task item |
| `OpenSpecDeltaSpec` | `openspec/changes/<name>/specs/<cap>/spec.md` | ADDED / MODIFIED / REMOVED / RENAMED delta |

`OpenSpecRequirement` and `OpenSpecScenario` carry the `:Searchable`
multi-label so they appear in auto-complete and GraphRAG chat without any
extra configuration.

See [`docs/OPENSPEC_SCHEMA.md`](docs/OPENSPEC_SCHEMA.md) for the full node
property and relationship reference.

## Security Analysis

When a repository is indexed, code-graph automatically extracts **security-relevant entities** across Python, HTML/Jinja2, and Markdown files and stores them as first-class graph nodes alongside the regular code structure.

### New node labels

| Label | What it represents |
|---|---|
| `HtmlElement` | A DOM element in a `.html` / `.jinja2` template |
| `HtmlForm` | A `<form>` element |
| `Variable` | A Python variable injected into a template context |
| `Decorator` | A decorator applied to a Python function or route handler |
| `Package` | A pip dependency declared in `requirements.txt` / `pyproject.toml` |
| `ExternalFunction` | A function inside a venv `site-packages` path |
| `MarkdownSection` | An H2/H3 heading in a `.md` documentation file |
| `Requirement` | A bullet-point security requirement in a doc |
| `FileSystemOp` | A call to `open()`, `os.path.join()`, `pathlib.Path()`, etc. |

### New relationship edges

| Edge | From → To | Meaning |
|---|---|---|
| `INJECTED_INTO` | `Variable` → `HtmlElement` | Template variable reaches this element |
| `PROCESSED_BY` | `Variable` → `Function` | Variable passes through a sanitizer first |
| `HAS_DECORATOR` | `Function` → `Decorator` | Function has this decorator applied |
| `BELONGS_TO` | `ExternalFunction` → `Package` | Function lives in this package |
| `DEFINES_REQUIREMENT` | `MarkdownSection` → `Requirement` | Doc section declares this requirement |
| `DEFINES_ROUTE` | `MarkdownSection` → `Route` | Doc section documents this API route |
| `HAS_FS_OP` | `Function` → `FileSystemOp` | Function contains this file-system call |

### Security scan endpoint

Run the four built-in security rules against any indexed repo:

```bash
curl -X POST http://localhost:5000/api/security_scan \
  -H "Content-Type: application/json" \
  -d '{"repo": "my-flask-app", "rules": ["xss", "auth_drift", "path_traversal"]}'
```

Available rules:

| Rule | What it detects |
|---|---|
| `xss` | Template variables that reach an `HtmlElement` without passing through a sanitizer (`html.escape`, `markupsafe.escape`, `sanitize_*`, …) |
| `auth_drift` | API routes documented in Markdown as requiring authentication but whose Python handler lacks a `@login_required` decorator |
| `path_traversal` | `open()` / `os.path.join()` calls where the argument originates from `request.args`, `request.form`, or `request.json` |
| `cve` | Functions in your codebase that directly call into a named pip package (supply `"package_name"` in the request to identify blast radius after a CVE alert) |

Response format:

```json
{
  "repo": "my-flask-app",
  "findings": [
    {
      "rule": "xss",
      "name": "XSS: Unsanitized Template Variables",
      "severity": "high",
      "results": [
        {"UnsafeVariable": "user_comment", "TargetElement": "div", "SourceFile": "app/views.py", "SourceLine": 42}
      ]
    }
  ]
}
```

You can also run the queries directly against FalkorDB. The graph name for a repo `my-app` on branch `main` is `code:my-app:main`.

```cypher
-- XSS: find template variables not passed through a sanitizer
MATCH (v:Variable)-[:INJECTED_INTO]->(e:HtmlElement)
WHERE NOT EXISTS {
    MATCH (v)-[:PROCESSED_BY]->(:Function)
}
RETURN v.name AS UnsafeVariable, e.name AS TargetElement,
       v.path AS SourceFile, v.src_line AS SourceLine

-- Auth-drift: routes documented in Markdown but missing @login_required
MATCH (m:MarkdownSection)-[:DEFINES_ROUTE]->(r:Route)
OPTIONAL MATCH (f:Function {route: r.path})
WHERE f IS NULL OR NOT (f)-[:HAS_DECORATOR]->(:Decorator {name: 'login_required'})
RETURN m.title AS DocumentedSection, r.path AS UnsecuredRoute

-- Path-traversal: tainted file-system operations
MATCH (fs:FileSystemOp)
WHERE fs.tainted = true
OPTIONAL MATCH (caller:Function)-[:HAS_FS_OP]->(fs)
RETURN fs.op AS Operation, fs.src_file, fs.src_line, caller.name AS CallerFunction

-- CVE blast-radius: who calls into a vulnerable package?
MATCH (f:Function)-[:CALLS]->(ef:ExternalFunction)-[:BELONGS_TO]->(p:Package {name: 'requests'})
RETURN f.name AS CallerFunction, f.path AS CallerFile,
       ef.name AS VulnerableFunction, ef.module AS Module
```

See [`docs/SECURITY_ENTITY_TRACKING.md`](docs/SECURITY_ENTITY_TRACKING.md) for the full reference, including all node/edge schemas and the architectural overview.

## API Endpoints

### Read endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/list_repos` | List all indexed repositories |
| GET | `/api/graph_entities?repo=<name>` | Fetch a subgraph for a repository |
| POST | `/api/get_neighbors` | Return neighboring nodes for the provided IDs |
| POST | `/api/auto_complete` | Prefix-search indexed entities |
| POST | `/api/repo_info` | Return repository stats and saved metadata |
| POST | `/api/find_paths` | Find paths between two graph nodes |
| POST | `/api/chat` | Ask questions over the code graph via GraphRAG |
| POST | `/api/list_commits` | List commits from the repository's git graph |
| POST | `/api/security_scan` | Run security rules (XSS, auth-drift, path-traversal, CVE) against an indexed repo |

### Mutating endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze_folder` | Analyze a local source folder |
| POST | `/api/analyze_repo` | Clone and analyze a git repository |
| POST | `/api/switch_commit` | Switch the indexed repository to a specific commit |

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

Copyright FalkorDB Ltd. 2025
