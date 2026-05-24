from __future__ import annotations

import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from app.parser import ParsedHand, parse_hand


hypothesis = pytest.importorskip("hypothesis")
strategies = pytest.importorskip("hypothesis.strategies")

given = hypothesis.given
settings = hypothesis.settings
st = strategies

MAX_EXAMPLES = int(os.getenv("HYPOTHESIS_MAX_EXAMPLES", "200"))
DECK = [
    f"{rank}{suit}"
    for rank in "23456789TJQKA"
    for suit in "cdhs"
]
MONEY_QUANT = Decimal("0.0001")
TETHER_SIGN = "\u20ae"
MOJIBAKE_TETHER_SIGN = "\u00e2\u201a\u00ae"


def normalize_currency(text: str) -> str:
    return text.replace(MOJIBAKE_TETHER_SIGN, TETHER_SIGN)


@st.composite
def mini_hand_texts(draw: st.DrawFn) -> str:
    hand_id = draw(st.integers(min_value=9400000000, max_value=9499999999))
    minute = draw(st.integers(min_value=0, max_value=59))
    (
        stake_sb,
        stake_bb,
        raise_amount,
        raise_to,
        call_amount,
        bet_amount,
        return_amount,
        collected,
        total_pot,
    ) = draw(
        st.sampled_from(
            [
                (
                    Decimal("0.10"),
                    Decimal("0.25"),
                    Decimal("0.65"),
                    Decimal("0.90"),
                    Decimal("0.65"),
                    Decimal("1.20"),
                    Decimal("0.50"),
                    Decimal("1.80"),
                    Decimal("2.30"),
                ),
                (
                    Decimal("0.25"),
                    Decimal("0.50"),
                    Decimal("1.25"),
                    Decimal("1.50"),
                    Decimal("1.00"),
                    Decimal("2.00"),
                    Decimal("1.00"),
                    Decimal("3.00"),
                    Decimal("4.00"),
                ),
                (
                    Decimal("0.50"),
                    Decimal("1.00"),
                    Decimal("2.50"),
                    Decimal("3.00"),
                    Decimal("2.00"),
                    Decimal("3.50"),
                    Decimal("1.50"),
                    Decimal("5.00"),
                    Decimal("6.50"),
                ),
            ]
        )
    )
    cards = draw(st.lists(st.sampled_from(DECK), min_size=5, max_size=5, unique=True))
    hero_cards = cards[:2]
    flop = cards[2:]
    return normalize_currency(
        f"""
CoinPoker Hand #{hand_id}: NLH (₮{stake_sb}/₮{stake_bb}) - 2026/05/23 17:{minute:02d}:00 PDT
Table 'property-{hand_id}' heads-up Seat #1 is the button
Seat 1: Hero (₮25.00 in chips)
Seat 2: Villain (₮25.00 in chips)
Hero: posts small blind ₮{stake_sb}
Villain: posts big blind ₮{stake_bb}
*** HOLE CARDS ***
Dealt to Hero [{hero_cards[0]} {hero_cards[1]}]
Hero: raises ₮{raise_amount} to ₮{raise_to}
Villain: calls ₮{call_amount}
*** FLOP *** [{flop[0]} {flop[1]} {flop[2]}]
Hero: bets ₮{bet_amount}
Villain: folds
Uncalled bet (₮{return_amount}) returned to Hero
Hero collected ₮{collected} from pot
*** SUMMARY ***
Total pot ₮{total_pot} | Rake ₮0.00 | Splash Fee ₮0.00
""".strip()
    )


def canonical(hand: ParsedHand) -> dict[str, object]:
    return hand.model_dump(mode="json")


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_round_trip_stability(hand_text: str) -> None:
    parsed = parse_hand(hand_text.splitlines())
    reparsed = ParsedHand.model_validate(canonical(parsed))
    assert canonical(reparsed) == canonical(parsed)


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_action_order_is_monotonic_per_street(hand_text: str) -> None:
    hand = parse_hand(hand_text.splitlines())
    orders_by_street: dict[str, list[int]] = defaultdict(list)
    for action in hand.actions:
        orders_by_street[action.street].append(action.action_order)

    for orders in orders_by_street.values():
        assert orders == list(range(len(orders)))


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_hero_net_identity(hand_text: str) -> None:
    hand = parse_hand(hand_text.splitlines())
    assert hand.hero_net == hand.hero_collected - hand.hero_invested


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_hero_net_bb_matches_stake_bb(hand_text: str) -> None:
    hand = parse_hand(hand_text.splitlines())
    if hand.stake_bb > 0:
        assert hand.hero_net_bb == (hand.hero_net / hand.stake_bb).quantize(MONEY_QUANT)


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_board_cardinality_and_no_duplicate_visible_cards(hand_text: str) -> None:
    hand = parse_hand(hand_text.splitlines())
    visible_cards = list(hand.hero_cards)
    if hand.flop is not None:
        assert len(hand.flop) == 3
        visible_cards.extend(hand.flop)
    if hand.turn is not None:
        assert len(hand.turn) == 2
        visible_cards.append(hand.turn)
    if hand.river is not None:
        assert len(hand.river) == 2
        visible_cards.append(hand.river)
    assert len(visible_cards) == len(set(visible_cards))


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_raise_consistency(hand_text: str) -> None:
    hand = parse_hand(hand_text.splitlines())
    for action in hand.actions:
        if action.action == "raise":
            assert action.amount is not None
            assert action.raise_to is not None
            assert action.raise_to > action.amount


@given(mini_hand_texts())
@settings(max_examples=MAX_EXAMPLES)
def test_hero_collected_does_not_exceed_total_pot_for_simple_pots(hand_text: str) -> None:
    hand = parse_hand(hand_text.splitlines())
    if hand.flags.get("side_pots"):
        pytest.skip("side-pot allocation is asserted by golden fixtures in v1")
    assert hand.hero_collected <= hand.total_pot


def test_uncalled_bet_line_affects_hero_net(coinpoker_fixture_dir: Path) -> None:
    fixture = (coinpoker_fixture_dir / "uncalled_bet.txt").read_text(encoding="utf-8")
    original = parse_hand(fixture.splitlines())
    mutated_text = "\n".join(
        line for line in fixture.splitlines() if not line.startswith("Uncalled bet")
    )
    mutated = parse_hand(mutated_text.splitlines())

    assert original.hero_net != mutated.hero_net
    assert original.hero_net == mutated.hero_net + Decimal("0.5000")
