"""Tests for cgraph gen-spec: spec_context, spec_llm, and the CLI --dry-run path."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# spec_context tests — require a live FalkorDB with the code-graph indexed
# ---------------------------------------------------------------------------

FALKORDB_AVAILABLE = bool(os.getenv("FALKORDB_HOST"))


@pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="FALKORDB_HOST not set — skipping live graph tests",
)
def test_build_spec_context_returns_sections() -> None:
    """build_spec_context with a real graph should contain the mandatory sections."""
    from api.graph import Graph
    from api.spec_context import build_spec_context

    branch = os.getenv("FALKORDB_BRANCH", "_default")
    g = Graph("code-graph", branch=branch)
    ctx = build_spec_context(g, "SourceAnalyzer")

    assert "[CAPABILITY]" in ctx
    assert "[DOCSTRING]" in ctx


@pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="FALKORDB_HOST not set — skipping live graph tests",
)
def test_build_spec_context_unknown_capability_raises() -> None:
    """build_spec_context should raise ValueError for an unknown capability."""
    from api.graph import Graph
    from api.spec_context import build_spec_context

    branch = os.getenv("FALKORDB_BRANCH", "_default")
    g = Graph("code-graph", branch=branch)

    with pytest.raises(ValueError, match="No entity found"):
        build_spec_context(g, "ThisClassDoesNotExist_XYZ")


# ---------------------------------------------------------------------------
# spec_llm tests — mock openai so no real endpoint is needed
# ---------------------------------------------------------------------------


def _make_mock_openai_client(content: str) -> MagicMock:
    """Return a mock openai.OpenAI() that returns *content* from chat.completions."""
    mock_message = MagicMock()
    mock_message.content = content

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion

    return mock_client


def test_generate_spec_returns_mocked_content() -> None:
    """generate_spec should return the assistant message content as-is."""
    from api.spec_llm import generate_spec

    expected = "# TestCapability Specification\n\n## Purpose\nTest purpose."

    mock_client = _make_mock_openai_client(expected)

    with patch("openai.OpenAI", return_value=mock_client):
        result = generate_spec(
            context="[CAPABILITY]\nName: TestCapability\nFile: test.py\n",
            capability_name="TestCapability",
            base_url="http://localhost:8080/v1",
            model="test-model",
            api_key="test-key",
        )

    assert result == expected
    mock_client.chat.completions.create.assert_called_once()


def test_generate_spec_raises_on_connection_error() -> None:
    """generate_spec should raise RuntimeError when the endpoint is unreachable."""
    from api.spec_llm import generate_spec

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("Connection refused")

    with patch("openai.OpenAI", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Failed to reach LLM endpoint"):
            generate_spec(
                context="context",
                capability_name="Cap",
                base_url="http://localhost:8080/v1",
                model="local",
                api_key="local",
            )


# ---------------------------------------------------------------------------
# CLI --dry-run tests — verify context JSON output without LLM call
# ---------------------------------------------------------------------------


def test_gen_spec_dry_run_no_llm_call() -> None:
    """--dry-run should print context as JSON and never call generate_spec."""
    from typer.testing import CliRunner

    from api.cli import app

    runner = CliRunner()

    mock_graph = MagicMock()
    mock_context = "[CAPABILITY]\nName: MyClass\nFile: myclass.py\n[DOCSTRING]\n(none)\n"

    with (
        patch("api.graph.Graph", return_value=mock_graph),
        patch("api.spec_context.build_spec_context", return_value=mock_context),
        patch("api.spec_llm.generate_spec") as mock_llm,
    ):
        result = runner.invoke(app, ["gen-spec", "MyClass", "--dry-run"])

    assert result.exit_code == 0, result.output
    mock_llm.assert_not_called()

    import json

    # The runner mixes stdout+stderr; the JSON line is the last non-empty line.
    json_line = next(
        line for line in reversed(result.output.splitlines()) if line.strip().startswith("{")
    )
    output = json.loads(json_line)
    assert output["status"] == "dry-run"
    assert "context" in output
    assert output["capability"] == "MyClass"


# ---------------------------------------------------------------------------
# MCP tool tests — get_spec_context and generate_spec
# ---------------------------------------------------------------------------


def test_get_spec_context_mcp_tool_success() -> None:
    """get_spec_context tool should return the expected dict on a good match."""
    import asyncio

    from api.mcp.tools.genspec import get_spec_context

    mock_context = "[CAPABILITY]\nName: MyClass\nFile: myclass.py\nType: Class\n"

    mock_entity = ("MyClass", "myclass.py", "A class.", 42, "Class")

    with (
        patch("api.spec_context._resolve_entity", return_value=mock_entity),
        patch("api.spec_context.build_spec_context", return_value=mock_context),
        patch("api.graph.Graph"),
    ):
        result = asyncio.run(
            get_spec_context(project="myrepo", capability="MyClass")
        )

    assert result["capability"] == "MyClass"
    assert result["entity_type"] == "Class"
    assert result["entity_path"] == "myclass.py"
    assert result["context"] == mock_context


def test_get_spec_context_mcp_tool_not_found() -> None:
    """get_spec_context tool should return {error} when capability is not found."""
    import asyncio

    from api.mcp.tools.genspec import get_spec_context

    with (
        patch("api.spec_context._resolve_entity", side_effect=ValueError("No entity found")),
        patch("api.graph.Graph"),
    ):
        result = asyncio.run(
            get_spec_context(project="myrepo", capability="NoSuchThing")
        )

    assert "error" in result
    assert "No entity found" in result["error"]


def test_generate_spec_mcp_tool_dry_run() -> None:
    """generate_spec with dry_run=True returns context but not spec_md."""
    import asyncio

    from api.mcp.tools.genspec import generate_spec

    mock_context = "[CAPABILITY]\nName: MyClass\nFile: myclass.py\nType: Class\n"
    mock_entity = ("MyClass", "myclass.py", "A class.", 42, "Class")

    with (
        patch("api.spec_context._resolve_entity", return_value=mock_entity),
        patch("api.spec_context.build_spec_context", return_value=mock_context),
        patch("api.graph.Graph"),
        patch("api.spec_llm.generate_spec") as mock_llm,
    ):
        result = asyncio.run(
            generate_spec(project="myrepo", capability="MyClass", dry_run=True)
        )

    mock_llm.assert_not_called()
    assert result["capability"] == "MyClass"
    assert result["context"] == mock_context
    assert "spec_md" not in result


def test_generate_spec_mcp_tool_calls_llm() -> None:
    """generate_spec without dry_run should call the LLM and return spec_md."""
    import asyncio

    from api.mcp.tools.genspec import generate_spec

    mock_context = "[CAPABILITY]\nName: MyClass\n"
    mock_entity = ("MyClass", "myclass.py", "", 42, "Class")
    expected_spec = "# MyClass Specification\n\n## Purpose\nDoes things."

    with (
        patch("api.spec_context._resolve_entity", return_value=mock_entity),
        patch("api.spec_context.build_spec_context", return_value=mock_context),
        patch("api.graph.Graph"),
        patch("api.spec_llm.generate_spec", return_value=expected_spec),
    ):
        result = asyncio.run(
            generate_spec(project="myrepo", capability="MyClass")
        )

    assert result["capability"] == "MyClass"
    assert result["spec_md"] == expected_spec
    assert "error" not in result


def test_list_spec_candidates_filters_noise() -> None:
    """list_spec_candidates should strip build artefacts, venv, and test stubs."""
    import asyncio

    from api.mcp.tools.genspec import list_spec_candidates

    # Rows: (name, path, method_count, doc)
    mock_rows = [
        ("Graph",           "/repo/api/graph.py",                        50, "A real class."),
        ("Graph",           "/repo/build/lib/api/graph.py",              50, "A real class."),  # dup in build/
        ("TestFoo",         "/repo/tests/test_foo.py",                   3,  "A test."),         # test class
        ("Widget",          "/repo/.venv/lib/site-packages/x/widget.py", 5,  "Vendored."),       # venv
        ("Project",         "/repo/api/project.py",                      5,  ""),                # no docstring
        ("AsyncGraphQuery", "/repo/api/graph.py",                        9,  "Async query."),
    ]

    mock_result = type("R", (), {"result_set": mock_rows})()
    mock_graph = type("G", (), {"_query": lambda self, q: mock_result})()

    with patch("api.graph.Graph", return_value=mock_graph):
        result = asyncio.run(list_spec_candidates(project="myrepo"))

    names = [r["name"] for r in result]

    # Graph deduplicated to one entry
    assert names.count("Graph") == 1
    # Test class excluded
    assert "TestFoo" not in names
    # Venv excluded
    assert "Widget" not in names
    # Project included (no docstring, but not noise)
    assert "Project" in names
    # AsyncGraphQuery included
    assert "AsyncGraphQuery" in names

    # recommended flag correct
    graph_entry = next(r for r in result if r["name"] == "Graph")
    assert graph_entry["recommended"] is True
    assert graph_entry["slug"] == "graph"

    project_entry = next(r for r in result if r["name"] == "Project")
    assert project_entry["has_docstring"] is False
    assert project_entry["recommended"] is False


def test_list_spec_candidates_min_methods() -> None:
    """min_methods filter should exclude classes below the threshold."""
    import asyncio

    from api.mcp.tools.genspec import list_spec_candidates

    mock_rows = [
        ("Big",   "/repo/api/big.py",   10, "Big class."),
        ("Small", "/repo/api/small.py",  1, "Small class."),
    ]
    mock_result = type("R", (), {"result_set": mock_rows})()
    mock_graph = type("G", (), {"_query": lambda self, q: mock_result})()

    with patch("api.graph.Graph", return_value=mock_graph):
        result = asyncio.run(
            list_spec_candidates(project="myrepo", min_methods=3)
        )

    names = [r["name"] for r in result]
    assert "Big" in names
    assert "Small" not in names
