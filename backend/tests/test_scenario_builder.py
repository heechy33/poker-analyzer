"""Tests for the postflop scenario builder.

These run entirely against parsed-fixture in-memory objects — no database
required. The range_library is supplied as an in-memory ``dict``-backed
lookup that mirrors the (table_size, position, action_sequence) keying used
by the real Postgres lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.parser.coinpoker import parse_hand
from app.scenario.builder import (
    ScenarioBuildError,
    build_scenario,
    canonical_hash,
    validate_scenario_envelope,
)
from app.scenario.ranges import (
    apply_combo_weights,
    combo_to_class,
    combos_in_class,
    parse_range_string,
    remove_combo_from_range,
    tighten_range_for_multiway,
)
from app.services.ingest import (
    actions_from_parsed,
    hand_from_parsed,
    players_from_parsed,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000002")


# Helper: 10 hand classes with weight 1.0 each for range hygiene.
_MIN_RANGE = {
    "AA": 1.0, "KK": 1.0, "QQ": 1.0, "JJ": 1.0, "TT": 1.0,
    "AKs": 1.0, "AQs": 1.0, "KQs": 1.0, "AJs": 1.0, "ATs": 1.0,
}


# ---------------------------------------------------------------------------
# Range parsing unit tests
# ---------------------------------------------------------------------------


def test_parse_range_pair_plus() -> None:
    weights = parse_range_string("TT+")
    assert set(weights) == {"TT", "JJ", "QQ", "KK", "AA"}
    assert all(value == 1.0 for value in weights.values())


def test_parse_range_dash_pair() -> None:
    weights = parse_range_string("99-66")
    assert set(weights) == {"99", "88", "77", "66"}


def test_parse_range_suited_plus() -> None:
    weights = parse_range_string("A2s+")
    expected = {f"A{r}s" for r in "23456789TJQK"}
    assert set(weights) == expected


def test_parse_range_dash_suited() -> None:
    weights = parse_range_string("A5s-A2s")
    assert set(weights) == {"A2s", "A3s", "A4s", "A5s"}


def test_parse_range_per_chunk_weight() -> None:
    weights = parse_range_string("AKs:0.5,QQ+")
    assert weights["AKs"] == pytest.approx(0.5)
    assert weights["QQ"] == 1.0
    assert weights["KK"] == 1.0


def test_combo_to_class_canonical_order() -> None:
    assert combo_to_class("As", "Kd") == "AKo"
    assert combo_to_class("Kd", "As") == "AKo"
    assert combo_to_class("Tc", "Th") == "TT"
    assert combo_to_class("Jh", "Th") == "JTs"


def test_combos_in_class_pair_count() -> None:
    assert len(combos_in_class("AA")) == 6
    assert len(combos_in_class("AKs")) == 4
    assert len(combos_in_class("AKo")) == 12


def test_combos_in_class_blocked() -> None:
    assert len(combos_in_class("AKs", blocked={"As"})) == 3
    assert len(combos_in_class("AA", blocked={"As", "Ah"})) == 1


def test_remove_combo_from_range_reduces_proportionally() -> None:
    base = {"AKo": 1.0, "AKs": 1.0}
    out = remove_combo_from_range(base, ["As", "Kd"], board=[])
    # 12 AKo combos remaining → after removing one, weight = 11/12.
    assert out["AKo"] == pytest.approx(round(11 / 12, 6))
    assert out["AKs"] == 1.0


def test_apply_combo_weights_overrides_and_drops() -> None:
    base = {"AKs": 1.0, "QQ": 1.0}
    merged = apply_combo_weights(base, {"AKs": 0.5, "QQ": 0.0, "AA": 1.0})
    assert merged == {"AKs": 0.5, "AA": 1.0}


def test_tighten_range_for_multiway_removes_bottom_weight() -> None:
    """Bottom-weight classes should be dropped by the ~80% cumulative threshold."""
    weights = {
        "AA": 1.0,
        "KK": 1.0,
        "QQ": 1.0,
        "JJ": 1.0,
        "TT": 1.0,
        "99": 1.0,
        "88": 1.0,
        "77": 0.3,
        "66": 0.2,
        "55": 0.1,
    }
    result = tighten_range_for_multiway(weights)
    # Total weight = 7*1.0 + 0.3 + 0.2 + 0.1 = 7.6. 80% = 6.08.
    # Top 7 entries (AA→88) = 7.0 > 6.08, so 77, 66, 55 should be dropped.
    assert "AA" in result
    assert "88" in result
    assert "77" not in result
    assert "66" not in result
    assert "55" not in result


def test_tighten_range_for_multiway_preserves_at_least_one() -> None:
    """If only one class, it should be kept."""
    result = tighten_range_for_multiway({"AA": 1.0})
    assert result == {"AA": 1.0}


def test_tighten_range_for_multiway_empty() -> None:
    assert tighten_range_for_multiway({}) == {}


# ---------------------------------------------------------------------------
# Scenario builder integration tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Fixture:
    name: str
    expected_pot_bb: float
    expected_eff_bb: float
    expected_hero_seq: str
    expected_villain_seq: str
    expected_oop: str
    expected_ip: str
    expected_confidence: str


# Pot values updated for HU model: only hero+villain contributions.
_OPEN_CALL_FIXTURE = _Fixture(
    name="hand_004.txt",
    # HU: hero 0.90 + BB 0.90 = 1.80 chips → 7.20 BB.
    expected_pot_bb=7.20,
    # min(26.85, 25.00) - 0.90 = 24.10 chips → 96.40 BB.
    expected_eff_bb=96.40,
    expected_hero_seq="open",
    expected_villain_seq="vs_BTN/SB_open_call",
    expected_oop="BB",
    expected_ip="BTN/SB",
    expected_confidence="high",
)

_THREE_BET_FIXTURE = _Fixture(
    name="hand_003.txt",
    # 0.10 SB + 0.25 BB excluded from HU pot.
    # Hero 3.00 + Opener 3.00 = 6.00 chips → 24.00 BB.
    expected_pot_bb=24.00,
    # 25.00 - 3.00 = 22.00 chips → 88.00 BB.
    expected_eff_bb=88.00,
    expected_hero_seq="vs_UTG_3bet_3bet",
    expected_villain_seq="vs_CO_3bet_call",
    expected_oop="UTG",
    expected_ip="CO",
    expected_confidence="high",
)

_LIMPED_FIXTURE = _Fixture(
    name="hand_005.txt",
    # 0.25 (hero limp) + 0.25 (BB check) = 0.50 chips → 2.00 BB.
    expected_pot_bb=2.00,
    # 25.00 - 0.25 = 24.75 chips → 99.00 BB.
    expected_eff_bb=99.00,
    expected_hero_seq="limp",
    expected_villain_seq="limp_check",
    expected_oop="BB",
    expected_ip="BTN/SB",
    expected_confidence="high",
)


def _build_lookup(seed: dict[tuple[int, str, str], str]):
    @dataclass
    class _Row:
        range_string: str
        combo_weights: dict[str, float] | None = None

    async def _lookup(table_size: int, position: str, action_sequence: str):
        return seed.get((table_size, position, action_sequence)) and _Row(
            range_string=seed[(table_size, position, action_sequence)]
        )

    return _lookup


_RANGE_SEED: dict[tuple[int, str, str], str] = {
    (6, "CO", "vs_UTG_open_3bet"): "TT+,AQs+,KQs,AKo",
    (6, "CO", "vs_UTG_3bet_3bet"): "TT+,AQs+,KQs,AKo",
    (6, "UTG", "vs_CO_3bet_call"): "99-JJ,AJs-AQs,KQs,QJs,AQo",
    (2, "BTN/SB", "open"): "22+,A2s+,K7s+,Q9s+,J9s+,T9s,A2o+,K9o+,Q9o+,JTo",
    (2, "BB", "vs_BTN/SB_open_call"): (
        "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo"
    ),
    (2, "BTN/SB", "limp"): "22-99,A2s-A9s,K5s-K9s,Q7s-Q9s,J7s-J9s,T7s-T9s",
    (2, "BB", "limp_check"): "22+,A2s+,K2s+,Q2s+,J5s+,T5s+,A2o+,K8o+,Q8o+,J8o+,T8o+",
}


@pytest.fixture
def range_lookup():
    return _build_lookup(_RANGE_SEED)


def _load_fixture(name: str):
    text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    parsed = parse_hand(text.splitlines())
    hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID)
    players = players_from_parsed(parsed, hand.id, USER_ID)
    actions = actions_from_parsed(parsed, hand.id, USER_ID)
    return hand, players, actions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture",
    [_OPEN_CALL_FIXTURE, _THREE_BET_FIXTURE, _LIMPED_FIXTURE],
    ids=lambda f: f.name,
)
async def test_build_scenario_golden(fixture: _Fixture, range_lookup) -> None:
    hand, players, actions = _load_fixture(fixture.name)
    result = await build_scenario(hand, players, actions, "flop", range_lookup)

    envelope = result["scenario"]
    metadata = result["metadata"]

    assert envelope["pot_bb"] == pytest.approx(fixture.expected_pot_bb, abs=0.01)
    assert envelope["effective_stack_bb"] == pytest.approx(
        fixture.expected_eff_bb, abs=0.01
    )
    assert envelope["oop_player"] == fixture.expected_oop
    assert envelope["ip_player"] == fixture.expected_ip

    assert metadata["hero_action_sequence"] == fixture.expected_hero_seq
    assert metadata["villain_action_sequence"] == fixture.expected_villain_seq
    assert metadata["confidence"] == fixture.expected_confidence
    assert isinstance(metadata.get("confidence_reasons"), list)
    assert "hu_clean" in metadata["confidence_reasons"]
    assert metadata["is_multiway_approximation"] is False
    assert metadata["hu_pot_mode"] is True

    assert envelope["bet_tree"] == {
        "flop": ["33%", "75%"],
        "turn": ["50%", "100%"],
        "river": ["33%", "75%", "150%"],
        "allin_always": True,
    }

    # Hero range must be non-empty for these well-covered seeds.
    assert envelope["hero_range"], "hero_range should be populated by the seed"
    assert envelope["villain_range"], "villain_range should be populated by the seed"


@pytest.mark.asyncio
async def test_scenario_hash_is_deterministic(range_lookup) -> None:
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    first = await build_scenario(hand, players, actions, "flop", range_lookup)
    second = await build_scenario(hand, players, actions, "flop", range_lookup)
    assert first["scenario_hash"] == second["scenario_hash"]
    assert first["scenario_hash"] == canonical_hash(first["scenario"])


@pytest.mark.asyncio
async def test_scenario_falls_back_to_default_call(range_lookup) -> None:
    seed = dict(_RANGE_SEED)
    # Drop the exact key for hero — they should fall back to CO default_call.
    # Drop both old and new key variants for hero.
    seed.pop((6, "CO", "vs_UTG_open_3bet"), None)
    seed.pop((6, "CO", "vs_UTG_3bet_3bet"), None)
    seed[(6, "CO", "default_call")] = "TT+,AQs+,KQs,AKo"
    fallback_lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", fallback_lookup)
    # HU with fallback → medium confidence.
    assert result["metadata"]["confidence"] == "medium"
    assert "hu_library_fallback" in result["metadata"]["confidence_reasons"]


@pytest.mark.asyncio
async def test_hero_combo_is_excluded_from_hero_range(range_lookup) -> None:
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    # Flop is "Ac Kc Qd"; pick a hero combo with neither suit collision so the
    # board blocks 6 of the 12 AKo combos and one more is removed by the hero
    # combo itself, leaving 5/6 of the AKo class.
    hand.hero_cards = ["Ad", "Ks"]
    result = await build_scenario(hand, players, actions, "flop", range_lookup)
    hero_range = result["scenario"]["hero_range"]
    assert hero_range.get("AKo", 0.0) < 1.0
    assert hero_range.get("AKo", 0.0) == pytest.approx(round(5 / 6, 4), abs=1e-4)


@pytest.mark.asyncio
async def test_missing_library_rows_use_builtin_fallback() -> None:
    async def empty_lookup(*_args):
        return None

    hand, players, actions = _load_fixture(_LIMPED_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", empty_lookup)
    envelope = result["scenario"]

    assert envelope["hero_range"]
    assert envelope["villain_range"]
    # HU with full fallback → medium (hu_library_fallback + range_gap).
    assert result["metadata"]["confidence"] == "medium"
    assert "hu_library_fallback" in result["metadata"]["confidence_reasons"]
    assert "range_gap" in result["metadata"]["confidence_reasons"]


@pytest.mark.asyncio
async def test_unknown_street_raises(range_lookup) -> None:
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)

    with pytest.raises(ScenarioBuildError):
        await build_scenario(hand, players, actions, "preflop", range_lookup)


# ---------------------------------------------------------------------------
# HU pot model — multiway pot does not include folded dead money
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pot_bb_is_hu_contributions_only(range_lookup) -> None:
    """Even when folded players contributed chips, pot_bb is hero+villain only."""
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", range_lookup)

    envelope = result["scenario"]
    metadata = result["metadata"]

    # Envelope pot is HU model (hero+opener only).
    assert envelope["pot_bb"] == pytest.approx(24.00, abs=0.01)

    # Metadata exposes both values for debugging.
    hu_chips = Decimal(metadata["pot_chips_hu_model"])
    table_chips = Decimal(metadata["pot_chips_total_table"])
    assert hu_chips <= table_chips, "HU pot should be ≤ total table pot"
    assert hu_chips > 0
    assert table_chips > 0
    assert table_chips > hu_chips, "Table pot includes folded SB dead money"


# ---------------------------------------------------------------------------
# Villain selection (scoring picker)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_villain_selection_includes_reason_in_metadata(range_lookup) -> None:
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", range_lookup)

    metadata = result["metadata"]
    assert "villain_selection_reason" in metadata
    assert isinstance(metadata["villain_selection_reason"], str)
    assert len(metadata["villain_selection_reason"]) > 0
    # Heads-up: reason should mention heads-up.
    assert "heads-up" in metadata["villain_selection_reason"].lower()


# ---------------------------------------------------------------------------
# Confidence tiers (table-driven)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup_scenario,expected_tier,expected_codes",
    [
        # hu_clean: exact keys, 2 alive, HU table size match.
        ("exact_keys_h2", "high", ["hu_clean"]),
        # hu_library_fallback: HU but fallback/default_call used.
        ("missing_keys_h2", "medium", ["hu_library_fallback", "range_gap"]),
    ],
)
async def test_confidence_tiers(
    lookup_scenario: str,
    expected_tier: str,
    expected_codes: list[str],
) -> None:
    """Parametric test for confidence tier assignment."""
    if lookup_scenario == "exact_keys_h2":
        seed = dict(_RANGE_SEED)
    else:
        seed: dict[tuple[int, str, str], str] = {}
        for k, v in _RANGE_SEED.items():
            if k[0] == 2:
                seed[k] = v
        # Remove hero key so it falls back to default_call.
        seed.pop((2, "BTN/SB", "open"), None)
        seed[(2, "BTN/SB", "default_call")] = "TT+,AQs+,KQs"

    lookup = _build_lookup(seed)

    if lookup_scenario == "exact_keys_h2":
        hand, players, actions = _load_fixture(_OPEN_CALL_FIXTURE.name)
    else:
        hand, players, actions = _load_fixture(_OPEN_CALL_FIXTURE.name)

    result = await build_scenario(hand, players, actions, "flop", lookup)
    metadata = result["metadata"]

    assert metadata["confidence"] == expected_tier
    for code in expected_codes:
        assert code in metadata["confidence_reasons"], (
            f"expected {code} in {metadata['confidence_reasons']}"
        )
    assert isinstance(metadata.get("confidence_detail"), str)
    assert len(metadata["confidence_detail"]) > 0


# ---------------------------------------------------------------------------
# Validation — pot/stack
# ---------------------------------------------------------------------------


def test_validate_scenario_envelope_raises_on_zero_pot() -> None:
    envelope = {
        "board": ["Kd", "9c", "Td"],
        "pot_bb": 0.0,
        "effective_stack_bb": 100.0,
        "hero_range": _MIN_RANGE,
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {},
    }
    with pytest.raises(ScenarioBuildError, match="pot_bb"):
        validate_scenario_envelope(envelope, "flop")


def test_validate_scenario_envelope_raises_when_eff_below_min_spr() -> None:
    """SPR must be ≥ 0.5 (shared _MIN_SPR = 0.5)."""
    envelope = {
        "board": ["Kd", "9c", "Td"],
        "pot_bb": 100.0,
        "effective_stack_bb": 40.0,  # SPR = 0.4, below 0.5 minimum
        "hero_range": dict(_MIN_RANGE),
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {},
    }
    with pytest.raises(ScenarioBuildError, match="SPR.*below minimum"):
        validate_scenario_envelope(envelope, "flop")


def test_validate_scenario_envelope_raises_on_empty_hero_range() -> None:
    envelope = {
        "board": ["Kd", "9c", "Td"],
        "pot_bb": 10.0,
        "effective_stack_bb": 100.0,
        "hero_range": {},
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {},
    }
    with pytest.raises(ScenarioBuildError, match="hero_range"):
        validate_scenario_envelope(envelope, "flop")


def test_validate_scenario_envelope_raises_on_wrong_board_length() -> None:
    envelope = {
        "board": ["Kd", "9c"],  # 2 cards for flop
        "pot_bb": 10.0,
        "effective_stack_bb": 100.0,
        "hero_range": dict(_MIN_RANGE),
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {},
    }
    with pytest.raises(ScenarioBuildError, match="board has 2 cards"):
        validate_scenario_envelope(envelope, "flop")


def test_validate_scenario_envelope_passes_valid_envelope() -> None:
    envelope = {
        "board": ["Kd", "9c", "Td"],
        "pot_bb": 10.0,
        "effective_stack_bb": 100.0,
        "hero_range": dict(_MIN_RANGE),
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {},
    }
    # Should not raise.
    validate_scenario_envelope(envelope, "flop")


# ---------------------------------------------------------------------------
# Validation — range hygiene (new in P0.4)
# ---------------------------------------------------------------------------


def test_validate_range_hygiene_rejects_too_few_hand_classes() -> None:
    """Range with fewer than 5 hand classes should be rejected."""
    envelope = {
        "board": ["Kd", "9c", "Td"],
        "pot_bb": 10.0,
        "effective_stack_bb": 100.0,
        "hero_range": {"AA": 1.0, "KK": 1.0, "QQ": 1.0, "JJ": 1.0},  # Only 4 classes
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {},
    }
    with pytest.raises(ScenarioBuildError, match="hand classes"):
        validate_scenario_envelope(envelope, "flop")


def test_validate_range_hygiene_rejects_total_weight_too_low() -> None:
    """Range with 10 classes each at 0.0009 (< 0.001 threshold) → all dropped.

    Zero viable classes → rejected on 'hand classes' check first."""
    envelope = {
        "board": ["Kd", "9c", "Td"],
        "pot_bb": 10.0,
        "effective_stack_bb": 100.0,
        "hero_range": dict(_MIN_RANGE),
        "villain_range": {
            "AA": 0.0009, "KK": 0.0009, "QQ": 0.0009, "JJ": 0.0009, "TT": 0.0009,
            "AKs": 0.0009, "AQs": 0.0009, "KQs": 0.0009, "AJs": 0.0009, "ATs": 0.0009,
        },  # Each < 0.001 → filtered; total weight = 0
        "bet_tree": {},
    }
    with pytest.raises(ScenarioBuildError, match="hand classes"):
        validate_scenario_envelope(envelope, "flop")


# ---------------------------------------------------------------------------
# Validation — degenerate bet tree (new in P0.5)
# ---------------------------------------------------------------------------


def test_validate_degenerate_bet_tree_shallow_spr() -> None:
    """SPR 1.1 with full bet tree + allin_always should be rejected as degenerate.

    This is the audit repro from Prompt 3.
    """
    envelope = {
        "board": ["As", "Kh", "Qd"],
        "pot_bb": 50.0,
        "effective_stack_bb": 55.0,  # SPR = 1.1
        "hero_range": dict(_MIN_RANGE),
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {
            "flop": ["33%", "75%"],
            "turn": ["50%", "100%"],
            "river": ["33%", "75%", "150%"],
            "allin_always": True,
        },
    }
    with pytest.raises(ScenarioBuildError, match="degenerate bet tree"):
        validate_scenario_envelope(envelope, "flop")


def test_validate_degenerate_bet_tree_ok_without_allin_always() -> None:
    """Same shallow SPR but allin_always=False passes degenerate check."""
    envelope = {
        "board": ["As", "Kh", "Qd"],
        "pot_bb": 50.0,
        "effective_stack_bb": 55.0,  # SPR = 1.1, OK (≥ 0.5)
        "hero_range": dict(_MIN_RANGE),
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {
            "flop": ["33%", "75%"],
            "turn": ["50%"],
            "river": ["33%"],
            "allin_always": False,
        },
    }
    # Should NOT raise — allin_always is false so degenerate check passes.
    validate_scenario_envelope(envelope, "flop")


def test_validate_spr_at_borderline_0_5_passes() -> None:
    """SPR exactly 0.5 should pass (threshold check is strict less-than)."""
    envelope = {
        "board": ["As", "Kh", "Qd"],
        "pot_bb": 100.0,
        "effective_stack_bb": 50.0,  # SPR = 0.5 exactly
        "hero_range": dict(_MIN_RANGE),
        "villain_range": dict(_MIN_RANGE),
        "bet_tree": {
            "flop": ["25%"],
            "turn": ["33%"],
            "river": ["50%"],
            "allin_always": False,
        },
    }
    validate_scenario_envelope(envelope, "flop")  # Should not raise


# ---------------------------------------------------------------------------
# Metadata transparency (new required fields)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_includes_new_transparency_fields(range_lookup) -> None:
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", range_lookup)

    metadata = result["metadata"]
    required_meta_keys = [
        "alive_players",
        "is_multiway_approximation",
        "hu_pot_mode",
        "villain_screen_name",
        "villain_position",
        "villain_selection_reason",
        "pot_chips_hu_model",
        "pot_chips_total_table",
        "hero_action_sequence",
        "villain_action_sequence",
        "range_adjustment_hero",
        "range_adjustment_villain",
        "confidence",
        "confidence_reasons",
        "confidence_detail",
    ]
    for key in required_meta_keys:
        assert key in metadata, f"metadata missing key: {key}"

    # For HU hands: not multiway, no range adjustments.
    assert metadata["is_multiway_approximation"] is False
    assert metadata["range_adjustment_hero"] is None
    assert metadata["range_adjustment_villain"] is None
    assert metadata["alive_players"] == 2
    assert len(metadata["confidence_reasons"]) >= 1


# ---------------------------------------------------------------------------
# Telemetry metadata — pot_error_pct & effective bet sizes (P0.6)
# ---------------------------------------------------------------------------


def _make_multiway_range_seed() -> dict[tuple[int, str, str], str]:
    """Range seed covering the key positions used by the side_pot fixture.

    side_pot: Hero(CO)=BTN/SB, BigStack=UTG, ShortStack=BB (3 players alive).
    """
    return {
        # Hero: CO who called UTG raise (vs UTG, BTN/SB open → caller).
        (6, "BTN/SB", "vs_UTG_open_call"): (
            "22+,A2s+,K7s+,Q9s+,J9s+,T9s,A2o+,K9o+,Q9o+,JTo"
        ),
        # BigStack (UTG): raiser.
        (6, "UTG", "open"): (
            "TT+,AQs+,KQs,AKo"
        ),
        # ShortStack (BB): caller of BTN/SB.
        (6, "BB", "vs_BTN/SB_open_call"): (
            "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo"
        ),
    }


@pytest.mark.asyncio
async def test_multiway_pot_error_pct_positive(monkeypatch) -> None:
    """Multiway hand (side_pot fixture with 3 alive) must produce pot_error_pct > 0.

    The HU pot excludes dead money from other players.  In the side_pot fixture,
    ShortStack is all-in preflop but alive at flop, so there are 3 alive players.
    The HU model picks one villain, excluding the other alive player's chips from
    the HU pot, resulting in a non-zero pot_error_pct.

    We monkeypatch allin_always=False for this test because the side_pot fixture
    has shallow SPR that would otherwise trigger the degenerate bet tree check
    before the builder can emit its telemetry metadata.
    """
    import app.scenario.builder as builder_module

    monkeypatch.setattr(
        builder_module,
        "BET_TREE",
        {
            "flop": ["33%", "75%"],
            "turn": ["50%", "100%"],
            "river": ["33%", "75%", "150%"],
            "allin_always": False,
        },
        raising=False,
    )

    seed = _make_multiway_range_seed()
    lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture("side_pot.txt")
    # 3 players alive at flop (Hero, BigStack, ShortStack).
    result = await build_scenario(hand, players, actions, "flop", lookup)

    metadata = result["metadata"]

    # Core assertion from acceptance criteria.
    assert "pot_error_pct" in metadata
    assert isinstance(metadata["pot_error_pct"], (int, float))
    assert metadata["pot_error_pct"] > 0, (
        f"Expected pot_error_pct > 0 for multiway, got {metadata['pot_error_pct']}"
    )

    # Verify other telemetry fields are present.
    assert "spr" in metadata
    assert "pot_bb_telemetry" in metadata
    assert "eff_bb_telemetry" in metadata
    assert metadata["multiway_alive_count"] >= 3

    # Effective bet sizes should be populated.
    assert isinstance(metadata["effective_bet_sizes_flop"], list)
    assert isinstance(metadata["effective_bet_sizes_turn"], list)
    assert isinstance(metadata["effective_bet_sizes_river"], list)

    # HU pot must be non-zero but less than total table pot (multiway).
    assert Decimal(metadata["pot_chips_hu_model"]) > 0
    assert Decimal(metadata["pot_chips_hu_model"]) < Decimal(metadata["pot_chips_total_table"])


@pytest.mark.asyncio
async def test_hu_pot_error_pct_is_zero() -> None:
    """True heads-up table (no folded players) must have pot_error_pct ≈ 0.

    Uses the heads-up open_call fixture (hand_004) where only hero and villain
    are seated at a 2-player table — no folded blind dead money.
    Contrast with 6-max hands where folded blinds create a small non-zero
    pot_error_pct even in otherwise-HU spots.
    """
    seed = dict(_RANGE_SEED)
    lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture(_OPEN_CALL_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", lookup)

    metadata = result["metadata"]
    assert "pot_error_pct" in metadata
    # True HU table → no dead money from folded players → pot_error_pct ≈ 0.
    assert metadata["pot_error_pct"] == pytest.approx(0.0, abs=0.1)


# ---------------------------------------------------------------------------
# Regression fixtures — P2.3
# ---------------------------------------------------------------------------
# Test the same envelope shapes from solver-wasm/tests/fixtures/regression/
# through the Python validation layer (validate_scenario_envelope).
# ---------------------------------------------------------------------------


_REGRESSION_FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "solver-wasm" / "tests" / "fixtures" / "regression"


def _read_regression(name: str) -> dict:
    import json
    path = _REGRESSION_FIXTURES_DIR / name
    assert path.exists(), f"regression fixture not found: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_regression_degenerate_allin_tree_rejected() -> None:
    """Degenerate all-in tree (SPR 1.1, allin_always=true, 3 sizes) must be rejected."""
    envelope = _read_regression("degenerate_allin_tree.json")
    with pytest.raises(ScenarioBuildError) as excinfo:
        validate_scenario_envelope(envelope, "river")
    err = str(excinfo.value)
    assert "degenerate" in err.lower() or "collapsed" in err.lower(), f"unexpected: {err}"


def test_regression_near_degenerate_spr_1_2_passes() -> None:
    """Near-degenerate SPR 1.2 with allin_always=false should pass validation."""
    envelope = _read_regression("near_degenerate_spr_1_2.json")
    validate_scenario_envelope(envelope, "flop")  # Should not raise


def test_regression_near_degenerate_spr_0_6_passes() -> None:
    """Near-degenerate SPR 0.6 must stay above MIN_SPR and avoid all-in tree collapse."""
    envelope = _read_regression("near_degenerate_spr_0_6.json")
    validate_scenario_envelope(envelope, "flop")  # Should not raise


def test_regression_empty_range_after_removal_behavior() -> None:
    """Empty range after card removal: board=AAA, ranges contain AA.
    Should either pass with small ranges or raise cleanly."""
    envelope = _read_regression("empty_range_after_removal.json")
    try:
        validate_scenario_envelope(envelope, "flop")
    except ScenarioBuildError as e:
        assert "range" in str(e).lower() or "empty" in str(e).lower() or "hand classes" in str(e).lower(), f"unexpected: {e}"


def test_regression_wide_ranges_deep_stack_passes() -> None:
    """Wide ranges deep stack (200 bb, 40+ classes) should pass validation."""
    envelope = _read_regression("wide_ranges_deep_stack.json")
    validate_scenario_envelope(envelope, "flop")  # Should not raise


def test_regression_multiway_metadata_passes() -> None:
    """Multiway metadata fixture should pass validation."""
    envelope = _read_regression("multiway_metadata.json")
    validate_scenario_envelope(envelope, "flop")  # Should not raise


def test_regression_fallback_range_passes() -> None:
    """Fallback range fixture should pass validation."""
    envelope = _read_regression("fallback_range.json")
    validate_scenario_envelope(envelope, "flop")  # Should not raise


def test_regression_sequential_10_cycle_no_oob() -> None:
    """Sequential 10-cycle validate test: no OOB or state pollution."""
    envelope = _read_regression("fallback_range.json")
    for cycle in range(1, 11):
        try:
            validate_scenario_envelope(envelope, "flop")
        except ScenarioBuildError as e:
            pytest.fail(f"cycle {cycle}: unexpected reject: {e}")


# ---------------------------------------------------------------------------
# §9 Acceptance tests — hu-grading-spec.md
# ---------------------------------------------------------------------------


# -- §9.1  Golden HU flop --------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_1_golden_hu_flop(range_lookup) -> None:
    """§9 #1: Golden HU flop → high confidence, pot_error==0, spr≥1.0.

    Uses hand_004.txt (heads-up table, BTN/SB open → BB call).
    At a true heads-up table there is no dead money from folded players,
    so pot_chips_hu_model == pot_chips_total_table and pot_error_pct == 0.
    """
    hand, players, actions = _load_fixture("hand_004.txt")
    result = await build_scenario(hand, players, actions, "flop", range_lookup)

    metadata = result["metadata"]
    envelope = result["scenario"]

    # Confidence must be high with hu_clean reason.
    assert metadata["confidence"] == "high"
    assert "hu_clean" in metadata["confidence_reasons"]

    # HU pot model must equal total table pot (no dead money on HU table).
    assert Decimal(metadata["pot_chips_hu_model"]) == Decimal(
        metadata["pot_chips_total_table"]
    )
    assert metadata["pot_error_pct"] == pytest.approx(0.0, abs=0.001)

    # SPR must be ≥ 1.0 for a standard 100 bb deep open + call.
    assert metadata["spr"] >= 1.0

    # Pot within tolerance (±0.1 bb).
    assert envelope["pot_bb"] == pytest.approx(7.20, abs=0.1)


# -- §9.3  Multiway flop (3+ alive) ----------------------------------------


@pytest.mark.asyncio
async def test_acceptance_3_multiway_flop(monkeypatch) -> None:
    """§9 #3: Multiway flop (3+ alive) → low confidence, multiway flag, pot_error>0.

    Uses side_pot.txt (Hero, BigStack, ShortStack — 3 alive at flop).
    Monkeypatches allin_always=False to bypass degenerate bet-tree check
    (shallow SPR in this fixture would otherwise reject before metadata
    is emitted).
    """
    import app.scenario.builder as builder_module

    monkeypatch.setattr(
        builder_module,
        "BET_TREE",
        {
            "flop": ["33%", "75%"],
            "turn": ["50%", "100%"],
            "river": ["33%", "75%", "150%"],
            "allin_always": False,
        },
        raising=False,
    )

    seed = _make_multiway_range_seed()
    lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture("side_pot.txt")
    result = await build_scenario(hand, players, actions, "flop", lookup)

    metadata = result["metadata"]

    # Confidence must be low for multiway.
    assert metadata["confidence"] == "low"
    assert "multiway_hu_approx" in metadata["confidence_reasons"]

    # Multiway flag must be set.
    assert metadata["is_multiway_approximation"] is True

    # pot_error_pct must be > 0 (HU model excludes folded-player dead money).
    assert metadata["pot_error_pct"] > 0


# -- §9.6  Degenerate SPR (<0.5) -------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_6_degenerate_spr_raises() -> None:
    """§9 #6: Degenerate SPR (<0.5) → ScenarioBuildError, reason shallow_spr.

    Uses shallow_spr_hu.txt — HU hand where hero and villain each invest
    10 BB preflop (pot 20 BB) with only 2 BB effective remaining
    (SPR ≈ 0.1).  build_scenario must reject with the
    solver_input_unsolvable_shallow_spr reason.
    """
    # Wide range seeds so ranges are never the rejection cause.
    seed: dict[tuple[int, str, str], str] = {
        (2, "BTN/SB", "open"): "22+,A2s+,K7s+,Q9s+,J9s+,T9s,A2o+,K9o+,Q9o+,JTo",
        (2, "BB", "vs_BTN/SB_open_call"): (
            "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo"
        ),
    }
    lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture("shallow_spr_hu.txt")

    with pytest.raises(ScenarioBuildError, match="solver_input_unsolvable_shallow_spr"):
        await build_scenario(hand, players, actions, "flop", lookup)


# -- §9.10 Multiway flop → HU turn/river -----------------------------------


def _make_mw_flop_hu_turn_range_seed() -> dict[tuple[int, str, str], str]:
    """Range seed for multiway_flop_hu_turn.txt fixture.

    Opener (UTG) opens, Hero (CO) calls, BB calls → 3 to flop.
    BB folds on flop → turn/river are HU between UTG and CO.
    """
    return {
        # Hero (CO): flat-called UTG open.
        (6, "CO", "vs_UTG_open_call"): (
            "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo"
        ),
        # Opener (UTG): opened.
        (6, "UTG", "open"): "TT+,AQs+,KQs,AKo",
        # BB: called UTG open (needed for flop multiway scenario).
        (6, "BB", "vs_UTG_open_call"): (
            "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo"
        ),
    }


@pytest.mark.asyncio
async def test_acceptance_10_multiway_flop_hu_turn_river() -> None:
    """§9 #10: Multiway flop → HU turn/river independently gradeable.

    Uses multiway_flop_hu_turn.txt:
    - 3 players see flop (Opener, Hero, BigBlind) → flop is multiway.
    - BigBlind folds on flop → turn/river are true HU (Opener vs Hero).

    The turn and river streets must be independently gradeable if the
    range library hits (confidence high).
    """
    seed = _make_mw_flop_hu_turn_range_seed()
    lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture("multiway_flop_hu_turn.txt")

    # ── Flop: multiway (3 alive) → must be low confidence ──
    flop_result = await build_scenario(hand, players, actions, "flop", lookup)
    assert flop_result["metadata"]["confidence"] == "low"
    assert flop_result["metadata"]["is_multiway_approximation"] is True
    assert flop_result["metadata"]["alive_players"] >= 3

    # ── Turn: HU (BB folded on flop) → should be independently gradeable ──
    turn_result = await build_scenario(hand, players, actions, "turn", lookup)
    turn_meta = turn_result["metadata"]

    # After BB folds on flop, only 2 players remain alive for the turn.
    assert turn_meta["alive_players"] == 2
    assert turn_meta["is_multiway_approximation"] is False

    # With exact range library hits, turn confidence should be high.
    assert turn_meta["confidence"] == "high"
    assert "hu_clean" in turn_meta["confidence_reasons"]

    # ── River: also HU → independently gradeable ──
    river_result = await build_scenario(hand, players, actions, "river", lookup)
    river_meta = river_result["metadata"]

    assert river_meta["alive_players"] == 2
    assert river_meta["is_multiway_approximation"] is False
    assert river_meta["confidence"] == "high"
    assert "hu_clean" in river_meta["confidence_reasons"]
