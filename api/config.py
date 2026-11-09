"""Configuration loading and defaults for code-graph indexing."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("code_graph")

DEFAULT_IGNORE = [
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    "site-packages",
    "target",
    "out",
    ".gradle",
    ".m2",
    "**/test/**",
    "**/tests/**",
    "subprojects/test/**",
    "src/test/java/**",
]

DEFAULT_RESOLVERS = {
    "java": "tree_sitter",
    "python": "tree_sitter",
    "csharp": "lsp",
}


@dataclass
class CgraphConfig:
    """Project-level configuration for code-graph."""

    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    resolvers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_RESOLVERS))
    workers: int = 4

    def is_ignored(self, file_path: Path) -> bool:
        """Check if a file matches any of the ignore patterns."""
        str_path = str(file_path)
        parts = file_path.parts
        for pattern in self.ignore:
            if pattern in str_path or pattern in parts:
                return True
            # Simple wildcard matching for **/pattern/**
            clean_pat = pattern.strip("*").strip("/")
            if clean_pat and clean_pat in parts:
                return True
        return False


def load_config(root_path: Path) -> CgraphConfig:
    """Load configuration for a project root, falling back to defaults and env vars."""
    config = CgraphConfig()

    # 1. Look for .cgraph.json or .cgraph-settings.json
    for filename in (".cgraph.json", ".cgraph-settings.json"):
        cfg_file = root_path / filename
        if cfg_file.is_file():
            try:
                data: dict[str, Any] = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(data.get("ignore"), list):
                    config.ignore = list(set(config.ignore + [str(item) for item in data["ignore"]]))
                if isinstance(data.get("resolvers"), dict):
                    config.resolvers.update({str(k).lower(): str(v).lower() for k, v in data["resolvers"].items()})
                if isinstance(data.get("concurrency"), dict) and "workers" in data["concurrency"]:
                    try:
                        config.workers = max(1, int(data["concurrency"]["workers"]))
                    except ValueError:
                        pass
                logger.info("Loaded code-graph config from %s", cfg_file)
            except Exception as e:
                logger.warning("Failed to parse config file %s: %s", cfg_file, e)
            break

    # 2. Look for .cgraphignore
    cgraphignore = root_path / ".cgraphignore"
    if cgraphignore.is_file():
        try:
            lines = cgraphignore.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    config.ignore.append(line)
            config.ignore = list(set(config.ignore))
            logger.info("Loaded ignore rules from %s", cgraphignore)
        except Exception as e:
            logger.warning("Failed to read .cgraphignore at %s: %s", cgraphignore, e)

    # 3. Environment variable overrides (highest precedence)
    if os.environ.get("CODE_GRAPH_JAVA_RESOLVER"):
        config.resolvers["java"] = os.environ["CODE_GRAPH_JAVA_RESOLVER"].strip().lower()
    if os.environ.get("CODE_GRAPH_PY_RESOLVER"):
        config.resolvers["python"] = os.environ["CODE_GRAPH_PY_RESOLVER"].strip().lower()
    if os.environ.get("CODE_GRAPH_INDEX_WORKERS"):
        try:
            config.workers = max(1, int(os.environ["CODE_GRAPH_INDEX_WORKERS"]))
        except ValueError:
            pass

    return config


def get_default_config_template() -> str:
    """Return the JSON string for scaffolding .cgraph.json."""
    data = {
        "$schema": "https://raw.githubusercontent.com/FalkorDB/code-graph/main/schemas/cgraph.schema.json",
        "ignore": [
            ".git",
            "node_modules",
            "venv",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            ".tox",
            "site-packages",
            "target",
            "out",
            ".gradle",
            ".m2",
            "**/test/**",
            "**/tests/**",
            "subprojects/test/**",
            "src/test/java/**",
        ],
        "resolvers": {
            "java": "tree_sitter",
            "python": "tree_sitter",
            "csharp": "lsp",
        },
        "concurrency": {
            "workers": 4,
        },
    }
    return json.dumps(data, indent=2) + "\n"
