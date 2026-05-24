"""Scenario builder for the postflop-solver WASM frontend.

The scenario builder converts a parsed hand + range_library lookup into a
deterministic JSON envelope consumed by the in-browser CFR solver. No solver
execution happens in the backend.
"""

from app.scenario.builder import (
    BET_TREE,
    RangeLookup,
    ScenarioBuildError,
    build_scenario,
    canonical_hash,
)
from app.scenario.ranges import (
    RangeParseError,
    apply_combo_weights,
    combo_to_class,
    combos_in_class,
    parse_range_string,
    remove_combo_from_range,
)

__all__ = [
    "BET_TREE",
    "RangeLookup",
    "RangeParseError",
    "ScenarioBuildError",
    "apply_combo_weights",
    "build_scenario",
    "canonical_hash",
    "combo_to_class",
    "combos_in_class",
    "parse_range_string",
    "remove_combo_from_range",
]
