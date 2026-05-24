"""Unit tests for the analysis prompt assembler and prompt-hash helper.

The prompt-hash determinism tests cover the contract acceptance criterion
*"prompt_hash dedupes repeat requests"* — identical inputs always produce
the same hash, so the cache lookup will always hit.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.llm.prompts import (
    SYSTEM_PROMPT,
    build_analysis_prompt,
    compute_prompt_hash,
)
from app.parser.coinpoker import parse_hand
from app.services.ingest import (
    actions_from_parsed,
    hand_from_parsed,
    players_from_parsed,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"
USER_ID = UUID("00000000-0000-0000-0000-000000000001")
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000002")
HAND_ID = UUID("00000000-0000-0000-0000-0000000000aa")


def _load_bundle(name: str):
    parsed = parse_hand(
        (FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines()
    )
    hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID)
    hand.id = HAND_ID
    players = players_from_parsed(parsed, HAND_ID, USER_ID)
    actions = actions_from_parsed(parsed, HAND_ID, USER_ID)
    return hand, players, actions


# ---------------------------------------------------------------------------
# compute_prompt_hash — determinism + cache-key uniqueness
# ---------------------------------------------------------------------------


def test_prompt_hash_is_deterministic() -> None:
    a = compute_prompt_hash(HAND_ID, "river", "abc123")
    b = compute_prompt_hash(HAND_ID, "river", "abc123")
    assert a == b


def test_prompt_hash_changes_with_street() -> None:
    a = compute_prompt_hash(HAND_ID, "river", "abc")
    b = compute_prompt_hash(HAND_ID, "turn", "abc")
    assert a != b


def test_prompt_hash_changes_with_scenario_hash() -> None:
    a = compute_prompt_hash(HAND_ID, "river", "abc")
    b = compute_prompt_hash(HAND_ID, "river", "xyz")
    assert a != b


def test_prompt_hash_changes_with_hand_id() -> None:
    other_hand = UUID("11111111-1111-1111-1111-111111111111")
    a = compute_prompt_hash(HAND_ID, "river", "abc")
    b = compute_prompt_hash(other_hand, "river", "abc")
    assert a != b


def test_prompt_hash_handles_empty_scenario_hash() -> None:
    """No scenario attached must still produce a stable, distinct hash."""
    a = compute_prompt_hash(HAND_ID, "river", None)
    b = compute_prompt_hash(HAND_ID, "river", "")
    # Both empty-ish inputs collapse to the same key.
    assert a == b
    # Different from a populated scenario hash.
    assert a != compute_prompt_hash(HAND_ID, "river", "abc")


def test_prompt_hash_string_and_uuid_equal() -> None:
    """``hand_id`` may be passed either as UUID or its string form."""
    assert compute_prompt_hash(HAND_ID, "river", "abc") == compute_prompt_hash(
        str(HAND_ID), "river", "abc"
    )


def test_prompt_hash_is_hex_sha256() -> None:
    digest = compute_prompt_hash(HAND_ID, "flop", None)
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# build_analysis_prompt — body content
# ---------------------------------------------------------------------------


def test_build_prompt_includes_required_sections() -> None:
    hand, players, actions = _load_bundle("hand_004.txt")
    prompt = build_analysis_prompt(
        hand, players, actions, street="flop", scenario_hash=None
    )

    assert "# Hand" in prompt
    assert "# Board" in prompt
    assert "# Seats" in prompt
    assert "# Action sequence" in prompt
    assert "# Solver context" in prompt
    assert "# Allowed leak tags" in prompt
    assert "# Response schema" in prompt
    # JSON schema instruction is present.
    assert '"analysis"' in prompt
    assert '"leak_tags"' in prompt


def test_build_prompt_handles_missing_solver_summary() -> None:
    hand, players, actions = _load_bundle("hand_004.txt")
    prompt = build_analysis_prompt(
        hand, players, actions, street="flop", scenario_hash=None
    )
    assert "no solver scenario attached" in prompt


def test_build_prompt_handles_no_river_card() -> None:
    """Works with a hand that has no solver data + no river street."""
    hand, players, actions = _load_bundle("hand_004.txt")
    prompt = build_analysis_prompt(
        hand,
        players,
        actions,
        street="flop",
        scenario_hash=None,
        solver_summary=None,
    )
    # Should not raise and should include the action sequence we have.
    assert "## preflop" in prompt


def test_build_prompt_includes_solver_summary_fields() -> None:
    hand, players, actions = _load_bundle("hand_004.txt")
    summary = {
        "hero_action": "bet 33%",
        "solver_best_action": "check",
        "ev_diff_bb": -1.85,
        "action_frequencies": {"check": 0.6, "bet_33": 0.4},
        "notes": "blockers heavy",
    }
    prompt = build_analysis_prompt(
        hand,
        players,
        actions,
        street="flop",
        scenario_hash="deadbeef",
        solver_summary=summary,
    )
    assert "scenario_hash: deadbeef" in prompt
    assert "hero_action: bet 33%" in prompt
    assert "solver_best_action: check" in prompt
    assert "ev_diff_bb: -1.85" in prompt
    assert "60.00%" in prompt
    assert "blockers heavy" in prompt


def test_build_prompt_includes_hero_position_and_cards() -> None:
    hand, players, actions = _load_bundle("hand_004.txt")
    prompt = build_analysis_prompt(
        hand, players, actions, street="flop"
    )
    assert f"hero_position: {hand.hero_position}" in prompt
    assert "hero_cards: " in prompt


def test_build_prompt_lists_all_leak_tags() -> None:
    from app.llm.tags import LEAK_TAGS

    hand, players, actions = _load_bundle("hand_004.txt")
    prompt = build_analysis_prompt(
        hand, players, actions, street="flop"
    )
    for tag in LEAK_TAGS:
        assert f"- {tag}" in prompt


def test_system_prompt_is_non_empty_string() -> None:
    assert isinstance(SYSTEM_PROMPT, str)
    assert "poker coach" in SYSTEM_PROMPT.lower()
    assert "json" in SYSTEM_PROMPT.lower()


@pytest.mark.parametrize("street", ["flop", "turn", "river"])
def test_build_prompt_for_each_street_does_not_raise(street: str) -> None:
    hand, players, actions = _load_bundle("hand_004.txt")
    prompt = build_analysis_prompt(
        hand, players, actions, street=street
    )
    assert f"focus_street: {street}" in prompt
