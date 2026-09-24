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
