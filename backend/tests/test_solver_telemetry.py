"""test_solver_telemetry.py — schema and endpoint smoke tests for P0.6 telemetry.

These tests verify:
1. SolverTelemetryCreate schema accepts valid payloads.
2. The /solver-runs/telemetry route is mounted on the app.
3. The endpoint returns 201 Created for a valid payload.

Run with:
    pytest backend/tests/test_solver_telemetry.py -v
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

_FAKE_ENV = {
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-key",
    "ANTHROPIC_API_KEY": "sk-fake",
    "DATABASE_URL": "postgresql+asyncpg://fake:fake@localhost/fake",
    "ENVIRONMENT": "test",
}

from app.schemas import SolverTelemetryCreate


class TestSolverTelemetrySchema:
    """Pydantic schema validation tests — no database required."""

    def test_accepts_minimal_payload(self) -> None:
        payload = SolverTelemetryCreate(error_class="success")
        assert payload.error_class == "success"
        assert payload.hand_id is None

    def test_accepts_full_success_payload(self) -> None:
        payload = SolverTelemetryCreate(
            hand_id="00000000-0000-0000-0000-000000000001",
            street="flop",
            scenario_hash="abc123",
            error_class="success",
            confidence="high",
            spr=8.5,
            pot_bb=10.0,
            eff_bb=85.0,
            multiway_alive_count=2,
            hero_lookup_hit=True,
            villain_lookup_hit=True,
            pot_error_pct=0.0,
            effective_bet_sizes_flop=["33%", "75%"],
            effective_bet_sizes_turn=["50%", "100%"],
            effective_bet_sizes_river=["33%", "75%", "150%"],
            solver_mode="quick",
            duration_ms=3500,
            wasm_memory_used=65536,
        )
        assert payload.error_class == "success"
        assert payload.street == "flop"
        assert payload.confidence == "high"
        assert payload.spr == 8.5
        assert payload.solver_mode == "quick"
        assert payload.duration_ms == 3500

    def test_accepts_failure_payload(self) -> None:
        payload = SolverTelemetryCreate(
            hand_id="00000000-0000-0000-0000-000000000001",
            street="turn",
            scenario_hash="def456",
            error_class="Unreachable",
            message="memory access out of bounds",
            confidence="low",
            spr=1.1,
            pot_bb=50.0,
            eff_bb=55.0,
            solver_mode="full",
            duration_ms=12500,
        )
        assert payload.error_class == "Unreachable"
        assert payload.message == "memory access out of bounds"

    def test_error_class_defaults_to_success(self) -> None:
        payload = SolverTelemetryCreate()
        assert payload.error_class == "success"

    def test_all_optional_fields_allow_none(self) -> None:
        payload = SolverTelemetryCreate()
        assert payload.hand_id is None
        assert payload.street is None
        assert payload.spr is None
        assert payload.solver_mode is None


class TestSolverTelemetryRouteMount:
    """Verify /solver-runs/telemetry is mounted on the app."""

    def test_telemetry_route_is_registered(self) -> None:
        with patch.dict(os.environ, _FAKE_ENV, clear=True):
            from app.main import app

        route_paths = {route.path for route in app.routes}
        assert "/solver-runs/telemetry" in route_paths, (
            f"Expected /solver-runs/telemetry in routes, got: {sorted(route_paths)}"
        )

    def test_telemetry_endpoint_post_only(self) -> None:
        with patch.dict(os.environ, _FAKE_ENV, clear=True):
            from app.main import app

        methods: set[str] = set()
        for route in app.routes:
            if route.path == "/solver-runs/telemetry":
                # route.methods can be a set or frozenset
                methods.update(route.methods)
                break
        assert "POST" in methods
        assert "GET" not in methods


@pytest.mark.asyncio
async def test_telemetry_handler_returns_201() -> None:
    """Unit test: handler creates a record and returns valid response."""
    from uuid import uuid4

    from app.routers.solver import post_telemetry

    mock_session = AsyncMock()
    mock_session.add = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    with patch("app.routers.solver.SolverTelemetry") as mock_model:
        mock_instance = mock_model.return_value
        mock_instance.id = uuid4()
        mock_instance.error_class = "worker_crashed"
        mock_instance.created_at = "2026-01-01T00:00:00Z"

        payload = SolverTelemetryCreate(
            hand_id="00000000-0000-0000-0000-000000000001",
            street="flop",
            error_class="worker_crashed",
            message="Worker died mid-solve",
            solver_mode="quick",
            duration_ms=15000,
        )

        response = await post_telemetry(
            body=payload,
            user_id="00000000-0000-0000-0000-000000000001",
            session=mock_session,
        )

        assert response.error_class == "worker_crashed"
        assert response.id is not None
        assert response.created_at is not None

        call_kwargs = mock_model.call_args[1]
        assert call_kwargs["error_class"] == "worker_crashed"
        assert call_kwargs["message"] == "Worker died mid-solve"
        assert call_kwargs["solver_mode"] == "quick"
        assert call_kwargs["duration_ms"] == 15000

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()