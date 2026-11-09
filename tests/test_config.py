"""Unit tests for code-graph project configuration loading (.cgraph.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock


from api.config import (
    DEFAULT_IGNORE,
    DEFAULT_RESOLVERS,
    get_default_config_template,
    load_config,
)


def test_load_config_defaults(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert set(DEFAULT_IGNORE).issubset(set(cfg.ignore))
    assert cfg.resolvers == DEFAULT_RESOLVERS
    assert cfg.workers == 4


def test_load_config_from_cgraph_json(tmp_path: Path):
    cfg_data = {
        "ignore": ["custom_dir", "legacy/**"],
        "resolvers": {
            "java": "lsp",
            "python": "tree_sitter",
        },
        "concurrency": {
            "workers": 8,
        },
    }
    (tmp_path / ".cgraph.json").write_text(json.dumps(cfg_data), encoding="utf-8")

    cfg = load_config(tmp_path)
    assert "custom_dir" in cfg.ignore
    assert "legacy/**" in cfg.ignore
    assert cfg.resolvers["java"] == "lsp"
    assert cfg.resolvers["python"] == "tree_sitter"
    assert cfg.workers == 8


def test_load_config_from_cgraphignore(tmp_path: Path):
    (tmp_path / ".cgraphignore").write_text(
        "# Comment line\n"
        "ignored_folder\n"
        "\n"
        "another_dir/**\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert "ignored_folder" in cfg.ignore
    assert "another_dir/**" in cfg.ignore


def test_load_config_env_overrides(tmp_path: Path):
    cfg_data = {
        "resolvers": {"java": "lsp"},
        "concurrency": {"workers": 2},
    }
    (tmp_path / ".cgraph.json").write_text(json.dumps(cfg_data), encoding="utf-8")

    with mock.patch.dict(
        os.environ,
        {"CODE_GRAPH_JAVA_RESOLVER": "tree_sitter", "CODE_GRAPH_INDEX_WORKERS": "12"},
        clear=False,
    ):
        cfg = load_config(tmp_path)
        assert cfg.resolvers["java"] == "tree_sitter"
        assert cfg.workers == 12


def test_default_config_template_valid_json():
    template = get_default_config_template()
    data = json.loads(template)
    assert "ignore" in data
    assert "resolvers" in data
    assert data["resolvers"]["java"] == "tree_sitter"
