"""Negative checks for routes intentionally absent during the Phase 0 rebuild."""

from app.main import app


def test_mcp_route_is_not_mounted() -> None:
    route_paths = {getattr(route, "path", None) for route in app.routes}
    assert "/mcp" not in route_paths


def test_legacy_solver_routes_are_not_in_openapi() -> None:
    paths = set(app.openapi().get("paths", {}))
    assert not any(path.startswith("/solver") for path in paths)
    assert not any(path.endswith("/scenario") for path in paths)
