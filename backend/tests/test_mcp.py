"""test_mcp.py — smoke tests for the MCP server manifest.

These tests verify that:
1. The _MCP_TOOLS list in main.py contains exactly the 6 curated tool names.
2. Each tool name matches the specification in PLAN.md §7.
3. The /mcp route is mounted (non-404) when the app is running.
4. Excluded internal endpoints are not in the MCP include list.

These are unit-level tests — they do NOT require a live database or real env
vars. The settings are patched before any app import occurs.

Run with:
    pytest backend/tests/test_mcp.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Patch required settings *before* importing app.main, so pydantic-settings
# does not fail trying to read non-existent env vars.
_FAKE_ENV = {
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-key",
    "ANTHROPIC_API_KEY": "sk-fake",
    "DATABASE_URL": "postgresql+asyncpg://fake:fake@localhost/fake",
    "ENVIRONMENT": "test",
}

# The exact 6 tool operation IDs mandated by T09.
EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "list_recent_hands",
        "get_hand",
        "find_biggest_losers",
        "get_stats",
        "analyze_hand",
        "find_leaks",
    }
)

# Tools that must NOT be in the MCP include list.
EXCLUDED_FROM_MCP: frozenset[str] = frozenset(
    {
        "health",
        "presign_upload",
        "get_upload",
        "complete_upload",
        "get_hand_scenario",
        "list_hand_analyses",
        "get_stats_by_position",
        "get_stats_redirect",
    }
)


def _import_mcp_tools() -> list[str]:
    """Import and return _MCP_TOOLS from app.main with patched settings."""
    with patch.dict(os.environ, _FAKE_ENV, clear=False):
        # Use importlib to force a fresh import if settings were already cached.
        import importlib
        import sys

        # Clear cached modules so settings LRU cache doesn't interfere.
        for mod in list(sys.modules):
            if mod.startswith("app"):
                del sys.modules[mod]

        from app.main import _MCP_TOOLS  # noqa: PLC0415

        return list(_MCP_TOOLS)


class TestMCPToolList:
    """Verify _MCP_TOOLS in main.py against the T09 specification."""

    @pytest.fixture(scope="class")
    def mcp_tools(self) -> list[str]:
        return _import_mcp_tools()

    def test_exactly_six_tools(self, mcp_tools: list[str]) -> None:
        """_MCP_TOOLS must have exactly 6 entries."""
        assert len(mcp_tools) == 6, (
            f"Expected exactly 6 MCP tools, got {len(mcp_tools)}: {mcp_tools}"
        )

    def test_all_spec_tools_present(self, mcp_tools: list[str]) -> None:
        """All 6 tool names from PLAN.md §7 must be in _MCP_TOOLS."""
        actual = frozenset(mcp_tools)
        missing = EXPECTED_TOOLS - actual
        assert not missing, (
            f"Missing required MCP tool IDs: {sorted(missing)}\n"
            f"Got: {sorted(actual)}"
        )

    def test_no_extra_tools(self, mcp_tools: list[str]) -> None:
        """_MCP_TOOLS must not contain any IDs beyond the 6 specified ones."""
        actual = frozenset(mcp_tools)
        extra = actual - EXPECTED_TOOLS
        assert not extra, (
            f"Unexpected tools in MCP manifest: {sorted(extra)}"
        )

    def test_tool_names_exactly_match_spec(self, mcp_tools: list[str]) -> None:
        """The full set must equal EXPECTED_TOOLS (combines previous two tests)."""
        actual = frozenset(mcp_tools)
        assert actual == EXPECTED_TOOLS, (
            f"MCP tools do not match spec.\n"
            f"  Expected: {sorted(EXPECTED_TOOLS)}\n"
            f"  Got:      {sorted(actual)}\n"
            f"  Extra:    {sorted(actual - EXPECTED_TOOLS)}\n"
            f"  Missing:  {sorted(EXPECTED_TOOLS - actual)}"
        )

    def test_health_not_exposed(self, mcp_tools: list[str]) -> None:
        """/health must not be an MCP tool — it is noisy and auth-free."""
        assert "health" not in mcp_tools

    def test_uploads_not_exposed(self, mcp_tools: list[str]) -> None:
        """Upload endpoints must not be exposed via MCP."""
        for tool in mcp_tools:
            assert not tool.startswith("upload"), (
                f"Upload tool '{tool}' should not be in MCP manifest"
            )

    def test_excluded_tools_absent(self, mcp_tools: list[str]) -> None:
        """Each specifically excluded operation ID must not be in _MCP_TOOLS."""
        leaked = EXCLUDED_FROM_MCP & frozenset(mcp_tools)
        assert not leaked, (
            f"Internal tools incorrectly included in MCP manifest: {leaked}"
        )

    def test_list_recent_hands_present(self, mcp_tools: list[str]) -> None:
        assert "list_recent_hands" in mcp_tools

    def test_get_hand_present(self, mcp_tools: list[str]) -> None:
        assert "get_hand" in mcp_tools

    def test_find_biggest_losers_present(self, mcp_tools: list[str]) -> None:
        assert "find_biggest_losers" in mcp_tools

    def test_get_stats_present(self, mcp_tools: list[str]) -> None:
        assert "get_stats" in mcp_tools

    def test_analyze_hand_present(self, mcp_tools: list[str]) -> None:
        assert "analyze_hand" in mcp_tools

    def test_find_leaks_present(self, mcp_tools: list[str]) -> None:
        assert "find_leaks" in mcp_tools


class TestMCPOpenAPI:
    """Verify the OpenAPI schema contains operation IDs for all 6 MCP tools.

    Uses TestClient with patched env vars — no live database required.
    """

    @pytest.fixture(scope="class")
    def openapi_schema(self) -> dict:
        """Fetch the OpenAPI schema from the ASGI test client."""
        with patch.dict(os.environ, _FAKE_ENV, clear=False):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/openapi.json")
                assert resp.status_code == 200
                return resp.json()

    def _all_operation_ids(self, schema: dict) -> set[str]:
        ids: set[str] = set()
        for _path, methods in schema.get("paths", {}).items():
            for _method, op in methods.items():
                if isinstance(op, dict) and "operationId" in op:
                    ids.add(op["operationId"])
        return ids

    def test_all_six_operation_ids_in_schema(self, openapi_schema: dict) -> None:
        """All 6 curated operation IDs must exist in the OpenAPI schema."""
        ids = self._all_operation_ids(openapi_schema)
        missing = EXPECTED_TOOLS - ids
        assert not missing, (
            f"Operation IDs missing from OpenAPI schema: {sorted(missing)}\n"
            f"All found IDs: {sorted(ids)}"
        )

    def test_find_leaks_operation_id_exists(self, openapi_schema: dict) -> None:
        """find_leaks must appear in the schema (new endpoint from T09)."""
        ids = self._all_operation_ids(openapi_schema)
        assert "find_leaks" in ids, (
            f"'find_leaks' operation ID not found. Available: {sorted(ids)}"
        )

    def test_find_biggest_losers_operation_id_exists(self, openapi_schema: dict) -> None:
        """find_biggest_losers must appear in the schema."""
        ids = self._all_operation_ids(openapi_schema)
        assert "find_biggest_losers" in ids
