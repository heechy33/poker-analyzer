"""P1.7 decision-time information-boundary contracts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.ledger.decision_state import build_decision_state
from app.ledger.models import CanonicalLedgerV1
from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hand


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"


def _ledger(name: str = "split_pot.txt") -> CanonicalLedgerV1:
    parsed = parse_hand((FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines())
    return ledger_from_parsed(parsed)


def _hero_seat(ledger: CanonicalLedgerV1) -> int:
    return next(player.seat for player in ledger.hand.players if player.is_hero)


def test_p1_7_projects_exact_hero_combo_board_prefix_and_reducer_actions() -> None:
    ledger = _ledger()
    hero_flop_event = next(
        event
        for event in ledger.events
        if event.street == "flop" and event.actor_seat == _hero_seat(ledger) and event.verb == "bet"
    )

    state = build_decision_state(ledger, event_index=hero_flop_event.event_index)

    assert state.schema_version == "hunl-decision-state/1"
    assert state.hero_combo == ("Ah", "Qh")
    assert state.board_prefix == ("Ad", "Kd", "2c")
    assert state.player_contributed_pot == hero_flop_event.state_before.player_contributed_pot
    assert state.amount_to_call == hero_flop_event.state_before.amount_to_call
    assert state.legal_raise_bounds == hero_flop_event.state_before.legal_raise_bounds
    assert state.legal_actions == ("check", "bet")
    assert all(
        player.decision_cards is None
        for player in ledger.hand.players
        if not player.is_hero
    )


def test_p1_7_forbidden_suffix_and_showdown_cards_do_not_change_state() -> None:
    ledger = _ledger()
    hero_flop_event = next(
        event
        for event in ledger.events
        if event.street == "flop" and event.actor_seat == _hero_seat(ledger) and event.verb == "bet"
    )
    baseline = build_decision_state(ledger, event_index=hero_flop_event.event_index)

    # Removing the complete future suffix (turn, river, later betting, awards,
    # and final fee/settlement observations) must be observationally identical.
    prefix_only = CanonicalLedgerV1.create(
        hand=ledger.hand,
        events=ledger.events[: hero_flop_event.event_index + 1],
    )
    assert build_decision_state(
        prefix_only, event_index=hero_flop_event.event_index
    ).model_dump(mode="json") == baseline.model_dump(mode="json")

    # Even if a malformed upstream projection attaches opponent cards, they
    # are showdown information and are not serialized into the decision state.
    villain = next(player for player in ledger.hand.players if not player.is_hero)
    hand_with_showdown_cards = ledger.hand.model_copy(
        update={
            "players": tuple(
                player.model_copy(update={"decision_cards": ("Jc", "Td")})
                if player.seat == villain.seat
                else player
                for player in ledger.hand.players
            )
        }
    )
    cards_variant = CanonicalLedgerV1.create(
        hand=hand_with_showdown_cards,
        events=ledger.events,
        settlement=ledger.settlement,
    )
    assert build_decision_state(
        cards_variant, event_index=hero_flop_event.event_index
    ).model_dump(mode="json") == baseline.model_dump(mode="json")


def test_p1_7_requires_an_exact_decision_when_multiple_hero_actions_match() -> None:
    ledger = _ledger()
    try:
        build_decision_state(ledger)
    except ValueError as exc:
        assert "event_index is required" in str(exc)
    else:
        raise AssertionError("multiple hero decisions must not be silently selected")


def test_p1_7_each_forbidden_suffix_mutation_is_ignored_independently() -> None:
    ledger = _ledger()
    hero_flop_event = next(
        event
        for event in ledger.events
        if event.street == "flop"
        and event.actor_seat == _hero_seat(ledger)
        and event.verb == "bet"
    )
    baseline = build_decision_state(ledger, event_index=hero_flop_event.event_index)

    def with_event(event, **updates) -> CanonicalLedgerV1:
        events = list(ledger.events)
        events[event.event_index] = event.model_copy(update=updates)
        return CanonicalLedgerV1.create(
            hand=ledger.hand,
            events=tuple(events),
            settlement=ledger.settlement,
        )

    turn_event = next(
        event
        for event in ledger.events
        if event.event_index > hero_flop_event.event_index
        and event.verb == "street_transition"
        and event.street == "turn"
    )
    turn_board = turn_event.state_after.board.model_copy(
        update={"first_run": ("Ad", "Kd", "2c", "3h")}
    )
    turn_variant = with_event(
        turn_event,
        state_after=turn_event.state_after.model_copy(update={"board": turn_board}),
    )

    river_event = next(
        event
        for event in ledger.events
        if event.event_index > hero_flop_event.event_index
        and event.verb == "street_transition"
        and event.street == "river"
    )
    river_board = river_event.state_after.board.model_copy(
        update={"first_run": ("Ad", "Kd", "2c", "2s", "3c")}
    )
    river_variant = with_event(
        river_event,
        state_after=river_event.state_after.model_copy(update={"board": river_board}),
    )

    later_action = next(
        event
        for event in ledger.events
        if event.event_index > hero_flop_event.event_index
        and event.verb in {"check", "call", "bet", "raise", "fold"}
    )
    later_action_variant = with_event(
        later_action, raw_tokens={"mutated_future_action": "true"}
    )

    award_event = next(
        event
        for event in ledger.events
        if event.event_index > hero_flop_event.event_index and event.verb == "collect"
    )
    award_variant = with_event(award_event, award_amount=Decimal("999"))
    result_variant = CanonicalLedgerV1.create(
        hand=ledger.hand,
        events=ledger.events,
        settlement=ledger.settlement.model_copy(update={"reported_total_pot": Decimal("999")}),
    )

    for variant in (turn_variant, river_variant, later_action_variant, award_variant, result_variant):
        assert build_decision_state(
            variant, event_index=hero_flop_event.event_index
        ).model_dump(mode="json") == baseline.model_dump(mode="json")

def test_p1_7_allowed_prefix_fact_changes_decision_state() -> None:
    ledger = _ledger()
    hero_flop_event = next(
        event
        for event in ledger.events
        if event.street == "flop"
        and event.actor_seat == _hero_seat(ledger)
        and event.verb == "bet"
    )
    baseline = build_decision_state(ledger, event_index=hero_flop_event.event_index)
    hero = next(player for player in ledger.hand.players if player.is_hero)
    changed_hand = ledger.hand.model_copy(
        update={
            "players": tuple(
                player.model_copy(update={"decision_cards": ("As", "Qs")})
                if player.seat == hero.seat
                else player
                for player in ledger.hand.players
            )
        }
    )
    changed = CanonicalLedgerV1.create(
        hand=changed_hand,
        events=ledger.events,
        settlement=ledger.settlement,
    )

    changed_state = build_decision_state(changed, event_index=hero_flop_event.event_index)
    assert changed_state.hero_combo == ("As", "Qs")
    assert changed_state.model_dump(mode="json") != baseline.model_dump(mode="json")