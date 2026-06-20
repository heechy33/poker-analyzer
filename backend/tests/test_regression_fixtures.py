"""P2.3 regression fixture validation — Python layer.

Validates every checked-in envelope under solver-wasm/tests/fixtures/regression/
against the JSON schema and basic solver-input invariants.  Deeper validation
(validate_scenario_envelope) lives in test_scenario_builder.py once builder.py
is on the branch; this file keeps CI green without importing unfinished modules.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FIXTURES_DIR = _REPO_ROOT / "solver-wasm" / "tests" / "fixtures" / "regression"
_SCHEMA_PATH = _REPO_ROOT / "backend" / "schemas" / "scenario_envelope.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))

_MIN_SPR = 0.5

# Fixtures expected to fail shallow-SPR validation (Rust preflight rejects these).
_SHALLOW_SPR_REJECT = {
    "degenerate_allin_tree_0_4_spr.json",
}


def _fixture_paths() -> list[Path]:
    assert _FIXTURES_DIR.is_dir(), f"missing regression dir: {_FIXTURES_DIR}"
    return sorted(_FIXTURES_DIR.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.name,
)
def test_regression_fixture_passes_schema(fixture_path: Path) -> None:
    data = _load(fixture_path)
    jsonschema.validate(instance=data, schema=_SCHEMA)


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.name,
)
def test_regression_fixture_has_smoke_caps(fixture_path: Path) -> None:
    data = _load(fixture_path)
    assert data.get("max_iterations") == 10, fixture_path.name
    assert data.get("target_exploitability_bb") == 999.0, fixture_path.name


@pytest.mark.parametrize(
    "fixture_path",
    [p for p in _fixture_paths() if p.name not in _SHALLOW_SPR_REJECT],
    ids=lambda p: p.name,
)
def test_regression_fixture_spr_above_minimum(fixture_path: Path) -> None:
    data = _load(fixture_path)
    spr = data["effective_stack_bb"] / data["pot_bb"]
    assert spr >= _MIN_SPR, f"{fixture_path.name}: SPR {spr:.2f} < {_MIN_SPR}"


@pytest.mark.parametrize("name", sorted(_SHALLOW_SPR_REJECT))
def test_regression_shallow_spr_fixture_below_minimum(name: str) -> None:
    data = _load(_FIXTURES_DIR / name)
    spr = data["effective_stack_bb"] / data["pot_bb"]
    assert spr < _MIN_SPR, f"{name}: expected SPR < {_MIN_SPR}, got {spr:.2f}"


def test_regression_fixture_count() -> None:
    count = len(_fixture_paths())
    assert count >= 20, f"expected >= 20 regression fixtures, found {count}"
