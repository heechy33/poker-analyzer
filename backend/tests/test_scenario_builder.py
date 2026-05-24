"""Tests for the postflop scenario builder.

These run entirely against parsed-fixture in-memory objects — no database
required. The range_library is supplied as an in-memory ``dict``-backed
lookup that mirrors the (table_size, position, action_sequence) keying used
by the real Postgres lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from app.parser.coinpoker import parse_hand
from app.scenario.builder import build_scenario, canonical_hash
from app.scenario.ranges import (
    apply_combo_weights,
    combo_to_class,
    combos_in_class,
    parse_range_string,
    remove_combo_from_range,
)
from app.services.ingest import (
    actions_from_parsed,
    hand_from_parsed,
    players_from_parsed,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000002")


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


_OPEN_CALL_FIXTURE = _Fixture(
    name="hand_004.txt",
    # 0.10 SB + 0.80 raise + 0.65 call + 0.25 BB = 1.80 chips → 7.20 BB.
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
    # 0.10 SB + 0.25 BB + 3.00 (Hero CO) + 3.00 (UTG opener) = 6.35 chips → 25.40 BB.
    expected_pot_bb=25.40,
    # 25.00 - 3.00 = 22.00 chips → 88.00 BB.
    expected_eff_bb=88.00,
    expected_hero_seq="vs_UTG_open_3bet",
    expected_villain_seq="vs_CO_3bet_call",
    expected_oop="UTG",
    expected_ip="CO",
    expected_confidence="high",
)

_LIMPED_FIXTURE = _Fixture(
    name="hand_005.txt",
    # 0.25 (limp+post) + 0.25 (BB check) = 0.50 chips → 2.00 BB.
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
    seed.pop((6, "CO", "vs_UTG_open_3bet"))
    seed[(6, "CO", "default_call")] = "TT+,AQs+,KQs,AKo"
    fallback_lookup = _build_lookup(seed)

    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    result = await build_scenario(hand, players, actions, "flop", fallback_lookup)
    assert result["metadata"]["confidence"] == "low"


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
async def test_unknown_street_raises(range_lookup) -> None:
    hand, players, actions = _load_fixture(_THREE_BET_FIXTURE.name)
    from app.scenario.builder import ScenarioBuildError

    with pytest.raises(ScenarioBuildError):
        await build_scenario(hand, players, actions, "preflop", range_lookup)
