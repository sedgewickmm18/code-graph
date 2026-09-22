from contextlib import nullcontext
from pathlib import Path
from typing import Optional

from api.entities.entity import Entity
from api.entities.file import File

from ..graph import Graph
from .analyzer import AbstractAnalyzer
# from .c.analyzer import CAnalyzer
from .csharp.analyzer import CSharpAnalyzer
from .html.analyzer import HtmlAnalyzer
from .java.analyzer import JavaAnalyzer
from .javascript.analyzer import JavaScriptAnalyzer
from .kotlin.analyzer import KotlinAnalyzer
from .markdown.analyzer import MarkdownAnalyzer
from .python.analyzer import PythonAnalyzer

from multilspy import SyncLanguageServer
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

import logging
import sys
# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(filename)s - %(asctime)s - %(levelname)s - %(message)s')

# Singleton instances
_html_analyzer = HtmlAnalyzer()
_markdown_analyzer = MarkdownAnalyzer()

# List of available analyzers (AbstractAnalyzer subclasses)
analyzers: dict[str, AbstractAnalyzer] = {
    # '.c': CAnalyzer(),
    # '.h': CAnalyzer(),
    '.py': PythonAnalyzer(),
    '.java': JavaAnalyzer(),
    '.cs': CSharpAnalyzer(),
    '.js': JavaScriptAnalyzer(),
    '.kt': KotlinAnalyzer(),
    '.kts': KotlinAnalyzer(),
    '.html': _html_analyzer,
    '.jinja2': _html_analyzer,
    '.j2': _html_analyzer,
}

class NullLanguageServer:
    def start_server(self):
        return nullcontext()

class SourceAnalyzer():
    def __init__(self) -> None:
        self.files: dict[Path, File] = {}

    def supported_types(self) -> list[str]:
        """
        """
        return list(analyzers.keys())

    def create_entity_hierarchy(self, entity: Entity, file: File, analyzer: AbstractAnalyzer, graph: Graph):
        types = analyzer.get_entity_types()
        stack = list(entity.node.children)
        while stack:
            node = stack.pop()
            if node.type in types:
                child = Entity(node)
                child.id = graph.add_entity(analyzer.get_entity_label(node), analyzer.get_entity_name(node), analyzer.get_entity_docstring(node), str(file.path), node.start_point.row, node.end_point.row, {})
                if not analyzer.is_dependency(str(file.path)):
                    analyzer.add_symbols(child)
                file.add_entity(child)
                entity.add_child(child)
                graph.connect_entities("DEFINES", entity.id, child.id)
                self.create_entity_hierarchy(child, file, analyzer, graph)
            else:
                stack.extend(node.children)

    def create_hierarchy(self, file: File, analyzer: AbstractAnalyzer, graph: Graph):
        types = analyzer.get_entity_types()
        stack = [file.tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in types:
                entity = Entity(node)
                entity.id = graph.add_entity(analyzer.get_entity_label(node), analyzer.get_entity_name(node), analyzer.get_entity_docstring(node), str(file.path), node.start_point.row, node.end_point.row, {})
                if not analyzer.is_dependency(str(file.path)):
                    analyzer.add_symbols(entity)
                file.add_entity(entity)
                graph.connect_entities("DEFINES", file.id, entity.id)
                self.create_entity_hierarchy(entity, file, analyzer, graph)
            else:
                stack.extend(node.children)

    # ------------------------------------------------------------------
    # Security entity passes (T2, T4, T5, T6)
    # ------------------------------------------------------------------

    def security_pass(self, graph: Graph, files: list[Path], path: Path) -> None:
        """Persist Decorator, Variable, FileSystemOp nodes for every Python function.

        Must run after first_pass so that entity.id values are available.
        Markdown files are also processed here.
        """
        from .python.analyzer import PythonAnalyzer as _PyAnalyzer

        for file_path in files:
            # --- Markdown (T5) ---
            if file_path.suffix == ".md":
                try:
                    _markdown_analyzer.analyze_file(file_path, graph)
                except Exception:
                    logging.warning("security_pass: markdown failed for %s", file_path, exc_info=True)
                continue

            # --- Python security entities (T2, T4, T6) ---
            if file_path not in self.files:
                continue
            analyzer = analyzers.get(file_path.suffix)
            if not isinstance(analyzer, _PyAnalyzer):
                continue
            if analyzer.is_dependency(str(file_path)):
                continue

            file = self.files[file_path]
            for _, entity in file.entities.items():
                if entity.node.type != "function_definition":
                    continue
                func_id = getattr(entity, "id", None)
                if func_id is None:
                    continue
                try:
                    analyzer.persist_security_entities(entity, file_path, graph, func_id)
                except Exception:
                    logging.warning(
                        "security_pass: persist failed for %s in %s",
                        analyzer.get_entity_name(entity.node), file_path,
                        exc_info=True,
                    )

        # --- HTML / Jinja2 template refs (T4 INJECTED_INTO edges) ---
        self._link_template_injections(graph)

    def _link_template_injections(self, graph: Graph) -> None:
        """Wire Variable -[:INJECTED_INTO]-> HtmlElement edges.

        For each template reference collected by HtmlAnalyzer, look up the
        Variable node by name and connect it.
        """
        for file_path, file in self.files.items():
            if file_path.suffix not in (".html", ".jinja2", ".j2"):
                continue
            for entity_node, entity in file.entities.items():
                var_names = _html_analyzer.template_refs.get(entity_node.id, [])
                elem_id = getattr(entity, "id", None)
                if elem_id is None or not var_names:
                    continue
                for var_name in var_names:
                    # Find the Variable node by name
                    q = "MATCH (v:Variable {name: $name}) RETURN v"
                    res = graph._query(q, {"name": var_name}).result_set
                    for row in res:
                        var_id = row[0].id
                        graph.link_variable_to_element(var_id, elem_id)

    def first_pass(self, path: Path, files: list[Path], ignore: list[str], graph: Graph) -> None:
        """
        Perform the first pass analysis on source files in the given directory tree.

        Args:
            ignore (list(str)): List of paths to ignore
            executor (concurrent.futures.Executor): The executor to run tasks concurrently.
        """

        supoorted_types = self.supported_types()
        for ext in set([file.suffix for file in files if file.suffix in supoorted_types]):
            analyzers[ext].add_dependencies(path, files)
        
        files_len = len(files)
        for i, file_path in enumerate(files):
            # Skip none supported files
            if file_path.suffix not in analyzers:
                logging.info(f"Skipping none supported file {file_path}")
                continue

            # Skip ignored files
            if any([i in str(file_path) for i in ignore]):
                logging.info(f"Skipping ignored file {file_path}")
                continue

            logging.info(f'Processing file ({i + 1}/{files_len}): {file_path}')

            analyzer = analyzers[file_path.suffix]

            # Parse file
            source_code = file_path.read_bytes()
            tree = analyzer.parser.parse(source_code)

            # Create file entity
            file = File(file_path, tree)
            self.files[file_path] = file

            # Walk thought the AST
            graph.add_file(file)
            self.create_hierarchy(file, analyzer, graph)

    def second_pass(self, graph: Graph, files: list[Path], path: Path) -> None:
        """
        Resolve symbol references across the codebase via LSP and write the
        resulting edges (CALLS / EXTENDS / IMPLEMENTS / RETURNS / PARAMETERS)
        into the graph.

        Symbol resolution dominates index wall-time on large repos: every
        file's entities trigger several `lsp.request_definition` calls and
        most of them are I/O-bound waiting on the language server.
        multilspy's SyncLanguageServer schedules each request onto a single
        asyncio loop running in a daemon thread (via
        `asyncio.run_coroutine_threadsafe`), which makes concurrent calls
        from multiple worker threads safe and lets us pipeline them.

        We therefore split second_pass into two phases:

          A. Parallel resolution. A bounded thread pool processes files in
             parallel, calling `entity.resolved_symbol(...)` per entity so
             each `Symbol.resolved_symbol` set gets populated. No graph
             writes happen here.

          B. Serial edge writes. The main thread iterates the same files
             in their original order and emits the EXTENDS / CALLS / ...
             edges. Keeping graph writes on one thread avoids contending on
             FalkorDB MERGE locks and produces a deterministic edge order
             matching the pre-parallel implementation.

        Pool size is controlled by `CODE_GRAPH_INDEX_WORKERS` (default 4),
        so resolution runs in parallel by default. This is an intentional
        behaviour change, but the edge output is identical to the
        single-worker path (verified on a 204-file repo): phase B always
        writes in the original file order regardless of how phase A
        interleaves. Set the var to 1 to run a single resolver thread (useful
        when multilspy/jedi misbehaves under concurrency); note this still
        dispatches through the pool rather than the main thread.

        Files whose resolution raises are logged with their traceback and
        excluded from phase B, so one bad file degrades to a logged skip
        instead of a partial or aborted graph -- and that behaviour no longer
        depends on the worker count.
        """
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        logger = MultilspyLogger()
        logger.logger.setLevel(logging.ERROR)
        lsps = {}
        # Only start LSPs for languages present in the candidate `files` list
        has_java = any(f.suffix == '.java' for f in files)
        has_py = any(f.suffix == '.py' for f in files)
        has_cs = any(f.suffix == '.cs' for f in files)

        if has_java and analyzers.get(".java") and analyzers[".java"].needs_lsp():
            config = MultilspyConfig.from_dict({"code_language": "java"})
            lsps[".java"] = SyncLanguageServer.create(config, logger, str(path))
        else:
            lsps[".java"] = NullLanguageServer()

        if has_py and analyzers.get(".py") and analyzers[".py"].needs_lsp():
            py_venv = path / "venv"
            py_dotvenv = path / ".venv"
            if py_venv.is_dir() and (py_venv / "bin" / "python").exists():
                env_path = str(py_venv)
            elif py_dotvenv.is_dir() and (py_dotvenv / "bin" / "python").exists():
                env_path = str(py_dotvenv)
            else:
                env_path = sys.prefix
                logging.info(
                    "No venv at %s; falling back to host env %s for jedi LSP",
                    path, env_path,
                )
            config = MultilspyConfig.from_dict({
                "code_language": "python",
                "environment_path": env_path,
            })
            lsps[".py"] = SyncLanguageServer.create(config, logger, str(path))
        else:
            lsps[".py"] = NullLanguageServer()

        import shutil
        if has_cs and analyzers.get(".cs") and analyzers[".cs"].needs_lsp() and (shutil.which("dotnet") or shutil.which("mono")):
            config = MultilspyConfig.from_dict({"code_language": "csharp"})
            lsps[".cs"] = SyncLanguageServer.create(config, logger, str(path))
        else:
            lsps[".cs"] = NullLanguageServer()
        # For now, use NullLanguageServer for Kotlin as kotlin-language-server setup is not yet integrated
        lsps[".kt"] = NullLanguageServer()
        lsps[".kts"] = NullLanguageServer()
        lsps[".js"] = NullLanguageServer()
        with lsps[".java"].start_server(), lsps[".py"].start_server(), lsps[".cs"].start_server(), lsps[".js"].start_server(), lsps[".kt"].start_server(), lsps[".kts"].start_server():
            try:
                n_workers = max(1, int(os.environ.get("CODE_GRAPH_INDEX_WORKERS", "4")))
            except ValueError:
                n_workers = 4

            # Drop files we don't actually have an entry for and skip files
            # whose language has no real LSP (NullLanguageServer provides
            # no symbol info, so resolution would be a no-op). De-duplicate
            # while preserving order so a path that appears twice in `files`
            # isn't resolved concurrently by two workers racing on the same
            # entity.resolved_symbols sets.
            resolvable: list[Path] = []
            seen: set[Path] = set()
            for file_path in files:
                if file_path in seen:
                    continue
                seen.add(file_path)
                if file_path not in self.files:
                    # first_pass skipped this file (e.g. parse error, empty,
                    # untracked, or ignored after entering the candidate list).
                    # Skip in second_pass too instead of crashing the whole
                    # index.
                    logging.warning(
                        "second_pass: %s not in files map (first_pass skipped it); skipping",
                        file_path,
                    )
                    continue
                analyzer = analyzers.get(file_path.suffix)
                # Skip symbol resolution when no real LSP is available *and* the
                # analyzer can't resolve statically (e.g. tree-sitter resolver).
                if isinstance(lsps.get(file_path.suffix), NullLanguageServer) and (
                    analyzer is None or analyzer.needs_lsp()
                ):
                    continue
                resolvable.append(file_path)

            total = len(resolvable)
            logging.info(
                "second_pass: resolving symbols in %d files with %d worker(s)",
                total, n_workers,
            )

            def _resolve_file(file_path: Path) -> Path:
                # Populate Symbol.resolved_symbol sets for every entity in
                # this file. Pure LSP work, safe to run from worker threads
                # because SyncLanguageServer multiplexes requests through a
                # single asyncio loop.
                file = self.files[file_path]
                for _, entity in file.entities.items():
                    entity.resolved_symbol(
                        lambda key, symbol, fp=file_path: analyzers[fp.suffix].resolve_symbol(
                            self.files, lsps[fp.suffix], fp, path, key, symbol
                        )
                    )
                return file_path

            # Phase A: resolve symbols. A single code path for every worker
            # count keeps the failure policy identical regardless of
            # CODE_GRAPH_INDEX_WORKERS -- a ThreadPoolExecutor with
            # max_workers=1 simply processes one file at a time.
            failed: set[Path] = set()
            done = 0
            log_every = max(1, total // 50) if total else 1
            with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="sa-resolve") as ex:
                futures = {ex.submit(_resolve_file, fp): fp for fp in resolvable}
                for fut in as_completed(futures):
                    fp = futures[fut]
                    try:
                        fut.result()
                    except Exception:
                        # Exclude this file from phase B so we never persist a
                        # partially resolved file; keep going so one bad file
                        # doesn't abort the whole index.
                        failed.add(fp)
                        logging.warning(
                            "second_pass: resolution failed for %s; excluding from edge writes",
                            fp, exc_info=True,
                        )
                    done += 1
                    if done % log_every == 0 or done == total:
                        logging.info("second_pass: resolved %d/%d files", done, total)

            # Phase B: serial edge writes batched per relationship type, in the
            # original file order so the graph is bit-identical to the single-threaded path.
            # Files whose resolution failed are skipped (see phase A).
            edges_by_rel: dict[str, list[tuple[int, int]]] = {
                "EXTENDS": [],
                "IMPLEMENTS": [],
                "CALLS": [],
                "RETURNS": [],
                "PARAMETERS": [],
            }
            for file_path in resolvable:
                if file_path in failed:
                    continue
                file = self.files[file_path]
                for _, entity in file.entities.items():
                    for key, resolved_set in entity.resolved_symbols.items():
                        for resolved in resolved_set:
                            if key in ("base_class", "extend_interface"):
                                edges_by_rel["EXTENDS"].append((entity.id, resolved.id))
                            elif key == "implement_interface":
                                edges_by_rel["IMPLEMENTS"].append((entity.id, resolved.id))
                            elif key == "call":
                                edges_by_rel["CALLS"].append((entity.id, resolved.id))
                            elif key == "return_type":
                                edges_by_rel["RETURNS"].append((entity.id, resolved.id))
                            elif key == "parameters":
                                edges_by_rel["PARAMETERS"].append((entity.id, resolved.id))

            for rel, pairs in edges_by_rel.items():
                if pairs:
                    graph.connect_entities_batch(rel, pairs)

    def link_imports(self, graph: Graph, root: Path) -> None:
        """Add ``IMPORTS`` edges (File -> File) via per-language resolution.

        Purely syntactic for Python (no LSP), so this runs after ``first_pass``
        once every file has a graph id. Languages whose analyzer does not
        implement import resolution are silently skipped.
        """
        indices: dict[str, object] = {}
        import_pairs: list[tuple[int, int]] = []
        for file_path, file in self.files.items():
            analyzer = analyzers.get(file_path.suffix)
            if analyzer is None:
                continue
            if file_path.suffix not in indices:
                indices[file_path.suffix] = analyzer.build_import_index(self.files, root)
            index = indices[file_path.suffix]
            if not index:
                continue
            for target in analyzer.resolve_imports(file, root, index):
                if getattr(file, "id", None) is None or getattr(target, "id", None) is None:
                    continue
                import_pairs.append((file.id, target.id))
        if import_pairs:
            graph.connect_entities_batch("IMPORTS", import_pairs)

    def analyze_files(self, files: list[Path], path: Path, graph: Graph) -> None:
        self.first_pass(path, files, [], graph)
        self.link_imports(graph, path)
        self.second_pass(graph, files, path)
        graph.derive_overrides()
        self.security_pass(graph, files, path)

    def analyze_sources(self, path: Path, ignore: list[str], graph: Graph) -> None:
        path = path.resolve()
        raw_files = (
            list(path.rglob("*.java"))
            + list(path.rglob("*.py"))
            + list(path.rglob("*.cs"))
            + list(path.rglob("*.js"))
            + list(path.rglob("*.kt"))
            + list(path.rglob("*.kts"))
            + list(path.rglob("*.html"))
            + list(path.rglob("*.jinja2"))
            + list(path.rglob("*.j2"))
            + list(path.rglob("*.md"))
        )
        # Filter ignored patterns upfront
        default_ignore = [".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".tox", "site-packages"]
        combined_ignore = list(set(ignore + default_ignore))
        files = [
            f for f in raw_files
            if not any(ign in str(f) or ign in f.parts for ign in combined_ignore)
        ]
        # First pass analysis of the source code
        self.first_pass(path, files, combined_ignore, graph)

        # Link import edges (syntactic, language-specific, no LSP)
        self.link_imports(graph, path)

        # Second pass analysis of the source code
        self.second_pass(graph, files, path)

        # Derive override edges from the resolved class hierarchy
        graph.derive_overrides()

        # Security entity pass: decorators, variables, FS ops, markdown
        self.security_pass(graph, files, path)

    def analyze_local_folder(self, path: str, g: Graph, ignore: Optional[list[str]] = []) -> None:
        """
        Analyze path.

        Args:
            path (str): Path to a local folder containing source files to process
            ignore (List(str)): List of paths to skip
        """

        logging.info(f"Analyzing local folder {path}")

        # Analyze source files
        self.analyze_sources(Path(path), ignore, g)

        logging.info("Done analyzing path")

    def analyze_local_repository(self, path: str, ignore: Optional[list[str]] = None, branch: Optional[str] = None) -> Graph:
        """
        Analyze a local Git repository.

        Args:
            path (str): Path to a local git repository
            ignore (List(str)): List of paths to skip
            branch (Optional[str]): Branch name. Auto-detected from the
                checkout when ``None``.
        """
        if ignore is None:
            ignore = []

        from pygit2.repository import Repository
        from ..project import detect_branch

        proj_name = Path(path).name
        if branch is None:
            branch = detect_branch(Path(path))
        graph = Graph(proj_name, branch=branch)
        self.analyze_local_folder(path, graph, ignore)

        # Save processed commit hash to the DB
        repo = Repository(path)
        current_commit = repo.walk(repo.head.target).__next__()
        graph.set_graph_commit(current_commit.short_id)

        return graph

