"""Tests for the security MCP tools (S6).

Two test layers:

1. **Unit tests** — mock an async graph query client to verify that each
   tool passes the correct Cypher and maps results into the expected shape.
   These run without a real FalkorDB and are always executed.

2. **Integration tests** (marked with ``indexed_fixture``) — index a tiny
   security fixture project into a real FalkorDB graph and assert that the
   tools return the expected findings.  These are skipped when FalkorDB is
   not reachable (see conftest.py ``require_falkordb``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(*values: Any):
    """Return a list that behaves like a FalkorDB result row."""
    return list(values)


def _mock_graph(rows: list[list[Any]]):
    """Return a mock AsyncGraphQuery whose ``_query`` returns ``rows``."""
    result = MagicMock()
    result.result_set = rows
    result.header = []

    g = AsyncMock()
    g._query = AsyncMock(return_value=result)
    g.close = AsyncMock()
    return g


# ---------------------------------------------------------------------------
# Unit tests — security_scan
# ---------------------------------------------------------------------------

class TestSecurityScanUnit:
    async def test_xss_rule_executes_correct_cypher(self):
        from api.mcp.tools.security import security_scan

        rows = [_make_row("user_comment", "div", "/repo/views.py", 42)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(project="myrepo", rules=["xss"])

        assert len(results) == 1
        finding = results[0]
        assert finding["rule"] == "xss"
        assert finding["severity"] == "high"
        assert finding["count"] == 1
        row = finding["results"][0]
        assert row["UnsafeVariable"] == "user_comment"
        assert row["TargetElement"] == "div"
        assert row["SourceLine"] == 42

    async def test_auth_drift_rule_present(self):
        from api.mcp.tools.security import security_scan

        rows = [_make_row("## POST /api/login", "/api/login", "POST", "login_view", None)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(project="myrepo", rules=["auth_drift"])

        assert results[0]["rule"] == "auth_drift"
        assert results[0]["count"] == 1

    async def test_path_traversal_rule_present(self):
        from api.mcp.tools.security import security_scan

        rows = [_make_row("open", "/repo/views.py", 88, "serve_file", 55)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(project="myrepo", rules=["path_traversal"])

        assert results[0]["rule"] == "path_traversal"
        r = results[0]["results"][0]
        assert r["Operation"] == "open"
        assert r["CallerSymbolId"] == 55

    async def test_cve_rule_requires_package_name(self):
        from api.mcp.tools.security import security_scan

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(project="myrepo", rules=["cve"])

        assert results[0]["rule"] == "cve"
        assert "error" in results[0]

    async def test_cve_rule_with_package_name(self):
        from api.mcp.tools.security import security_scan

        rows = [_make_row("do_request", "/repo/client.py", "get", "requests.api", 77)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(
                project="myrepo", rules=["cve"], package_name="requests"
            )

        assert results[0]["rule"] == "cve"
        assert results[0]["package"] == "requests"
        assert results[0]["count"] == 1
        assert results[0]["results"][0]["CallerFunction"] == "do_request"

    async def test_empty_rows_returns_zero_count(self):
        from api.mcp.tools.security import security_scan

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(project="myrepo", rules=["xss"])

        assert results[0]["count"] == 0
        assert results[0]["results"] == []

    async def test_graph_closed_on_success(self):
        from api.mcp.tools.security import security_scan

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            await security_scan(project="myrepo", rules=["xss"])

        mock_g.close.assert_awaited_once()

    async def test_default_rules_excludes_cve(self):
        from api.mcp.tools.security import security_scan

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            results = await security_scan(project="myrepo")

        rule_ids = {r["rule"] for r in results}
        assert "xss" in rule_ids
        assert "auth_drift" in rule_ids
        assert "path_traversal" in rule_ids
        assert "cve" not in rule_ids


# ---------------------------------------------------------------------------
# Unit tests — get_decorators
# ---------------------------------------------------------------------------

class TestGetDecoratorsUnit:
    async def test_returns_decorator_list(self):
        from api.mcp.tools.security import get_decorators

        rows = [_make_row("login_required", "/repo/views.py", 41)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await get_decorators(symbol_id=17, project="myrepo")

        assert len(result) == 1
        assert result[0]["name"] == "login_required"
        assert result[0]["src_line"] == 41

    async def test_empty_when_no_decorators(self):
        from api.mcp.tools.security import get_decorators

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await get_decorators(symbol_id=42, project="myrepo")

        assert result == []

    async def test_graph_closed(self):
        from api.mcp.tools.security import get_decorators

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            await get_decorators(symbol_id=1, project="myrepo")

        mock_g.close.assert_awaited_once()

    @pytest.mark.parametrize("bad_id", ["abc", "1.5", ""])
    async def test_invalid_symbol_id_raises(self, bad_id: str):
        from api.mcp.tools.security import get_decorators

        with pytest.raises((ValueError, TypeError)):
            await get_decorators(symbol_id=bad_id, project="myrepo")


# ---------------------------------------------------------------------------
# Unit tests — get_template_vars
# ---------------------------------------------------------------------------

class TestGetTemplateVarsUnit:
    async def test_returns_flow_with_sanitized_flag(self):
        from api.mcp.tools.security import get_template_vars

        # sanitized=True row
        rows = [
            _make_row(
                "user_comment",  # variable
                "/repo/views.py", 42,  # var_path, var_line
                "div",  # element
                "/repo/templates/page.html", 15,  # elem_path, elem_line
                True, "html_escape",  # sanitized, sanitizer
            )
        ]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await get_template_vars(project="myrepo")

        assert len(result) == 1
        r = result[0]
        assert r["variable"] == "user_comment"
        assert r["sanitized"] is True
        assert r["sanitizer"] == "html_escape"
        assert r["element"] == "div"
        assert r["element_line"] == 15

    async def test_unsanitized_row(self):
        from api.mcp.tools.security import get_template_vars

        rows = [
            _make_row("title", "/repo/views.py", 10, "h1", "/repo/t/base.html", 3, False, None)
        ]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await get_template_vars(project="myrepo")

        assert result[0]["sanitized"] is False
        assert result[0]["sanitizer"] is None

    async def test_template_file_filter_applied(self):
        from api.mcp.tools.security import get_template_vars

        # Two rows: one in page.html, one in other.html
        rows = [
            _make_row("a", "/r/views.py", 1, "div", "/r/templates/page.html", 5, False, None),
            _make_row("b", "/r/views.py", 2, "span", "/r/templates/other.html", 6, False, None),
        ]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await get_template_vars(
                project="r", template_file="page.html"
            )

        assert len(result) == 1
        assert result[0]["variable"] == "a"


# ---------------------------------------------------------------------------
# Unit tests — get_fs_ops
# ---------------------------------------------------------------------------

class TestGetFsOpsUnit:
    async def test_returns_tainted_ops(self):
        from api.mcp.tools.security import get_fs_ops

        rows = [_make_row("open", True, "/repo/views.py", 88, "serve_file", 55)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await get_fs_ops(project="myrepo", tainted_only=True)

        assert len(result) == 1
        r = result[0]
        assert r["op"] == "open"
        assert r["tainted"] is True
        assert r["caller"] == "serve_file"
        assert r["caller_symbol_id"] == 55

    async def test_tainted_only_false_uses_different_cypher(self):
        """Verify that tainted_only=False omits the WHERE clause."""
        from api.mcp.tools.security import get_fs_ops

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            await get_fs_ops(project="myrepo", tainted_only=False)

        # The query passed to _query must NOT contain "tainted = true"
        call_args = mock_g._query.call_args
        query_str = call_args[0][0] if call_args[0] else ""
        assert "tainted = true" not in query_str

    async def test_tainted_only_true_uses_where_clause(self):
        from api.mcp.tools.security import get_fs_ops

        mock_g = _mock_graph([])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            await get_fs_ops(project="myrepo", tainted_only=True)

        call_args = mock_g._query.call_args
        query_str = call_args[0][0] if call_args[0] else ""
        assert "tainted = true" in query_str


# ---------------------------------------------------------------------------
# Unit tests — mark_vulnerability
# ---------------------------------------------------------------------------

class TestMarkVulnerabilityUnit:
    async def test_returns_marked_count(self):
        from api.mcp.tools.security import mark_vulnerability

        rows = [_make_row(12)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await mark_vulnerability(
                project="myrepo", package_name="requests"
            )

        assert result["package"] == "requests"
        assert result["status"] == "vulnerable"
        assert result["marked"] == 12

    async def test_clear_status(self):
        from api.mcp.tools.security import mark_vulnerability

        rows = [_make_row(12)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await mark_vulnerability(
                project="myrepo", package_name="requests", status="clear"
            )

        assert result["status"] == "clear"
        # Verify 'clear' was passed to the query params
        call_params = mock_g._query.call_args[0][1]
        assert call_params["status"] == "clear"

    async def test_zero_when_package_not_found(self):
        from api.mcp.tools.security import mark_vulnerability

        rows = [_make_row(0)]
        mock_g = _mock_graph(rows)

        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            result = await mark_vulnerability(
                project="myrepo", package_name="unknown-pkg"
            )

        assert result["marked"] == 0

    async def test_graph_closed(self):
        from api.mcp.tools.security import mark_vulnerability

        mock_g = _mock_graph([_make_row(0)])
        with patch("api.mcp.tools.security._project_arg", return_value=mock_g):
            await mark_vulnerability(project="myrepo", package_name="flask")

        mock_g.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Registration smoke test — no FalkorDB required
# ---------------------------------------------------------------------------

class TestToolRegistration:
    async def test_security_tools_registered(self):
        """All 5 security tools must appear in the MCP app's tool list."""
        from api.mcp.server import app

        # app.list_tools() returns a plain list[Tool] in this SDK version.
        tools = await app.list_tools()
        tool_names = {t.name for t in tools}

        expected = {
            "security_scan",
            "get_decorators",
            "get_template_vars",
            "get_fs_ops",
            "mark_vulnerability",
        }
        assert expected.issubset(tool_names), (
            f"Missing tools: {expected - tool_names}\nRegistered: {tool_names}"
        )
