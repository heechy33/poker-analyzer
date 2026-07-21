"""Decision-time HUNL state projection for P1.7.

The projection is deliberately built from one ledger event's ``state_before``
snapshot. A complete ledger is useful for replay and settlement validation,
but its suffix is not information available when a hero decision is made.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.ledger.models import (
    CanonicalLedgerV1,
    LedgerEventV1,
    LedgerModel,
    LedgerStateV1,
    LegalRaiseBoundsV1,
    PlayerStateV1,
    StreetV1,
)
from app.rake import RakeScheduleError, resolve_rake_schedule


DECISION_STATE_SCHEMA_V1 = "hunl-decision-state/1"
DecisionActionV1 = Literal["fold", "check", "call", "bet", "raise"]
_DECISION_VERBS = frozenset({"fold", "check", "call", "bet", "raise"})


class DecisionStateError(ValueError):
    """A ledger cannot produce the requested decision-time state."""


class DecisionPlayerV1(LedgerModel):
    """Player identity available at the decision, without opponent cards."""

    seat: int = Field(ge=1)
    alias: str = Field(min_length=1)
    position: str = Field(min_length=1)
    starting_stack: Decimal
    is_hero: bool
    dealt_in: bool


class DecisionStateV1(LedgerModel):
    """Mathematical inputs immediately before one hero action.

    This model intentionally has no settlement, outcome, final-board, final
    rake, eligibility, range, solver, or grading fields. ``rake_schedule_id``
    is selected from immutable hand facts, never from the observed summary.
    """

    schema_version: Literal["hunl-decision-state/1"]
    raw_hand_id: str = Field(min_length=1)
    played_at: datetime
    game: Literal["NLHE"]
    table_marker: str = Field(min_length=1)
    table_format: Literal["hu_2max", "6max", "9max"]
    button_seat: int = Field(ge=1)
    small_blind: Decimal
    big_blind: Decimal
    action_event_index: int = Field(ge=0)
    action_street_event_index: int | None = Field(ge=0)
    street: StreetV1
    players: tuple[DecisionPlayerV1, ...]
    hero_seat: int = Field(ge=1)
    hero_position: str = Field(min_length=1)
    hero_combo: tuple[str, str]
    active_seats: frozenset[int]
    folded_seats: frozenset[int]
    all_in_seats: frozenset[int]
    players_reached_flop: frozenset[int]
    player_states: tuple[PlayerStateV1, ...]
    amount_to_call: Decimal
    last_full_raise: Decimal
    legal_raise_bounds: LegalRaiseBoundsV1
    legal_actions: tuple[DecisionActionV1, ...]
    player_contributed_pot: Decimal
    board_prefix: tuple[str, ...]
    rake_schedule_id: str | None


def build_decision_state(
    ledger: CanonicalLedgerV1,
    *,
    event_index: int | None = None,
    hero_seat: int | None = None,
    street: StreetV1 | None = None,
) -> DecisionStateV1:
    """Build one hero decision state from the ledger prefix only.

    Pass ``event_index`` when a hand has multiple hero decisions. If it is
    omitted, the requested hero/street pair must identify exactly one action.
    """

    hand_hero = next((player for player in ledger.hand.players if player.is_hero), None)
    if hand_hero is None:
        raise DecisionStateError("ledger has no hero")
    selected_hero = hand_hero.seat if hero_seat is None else hero_seat
    if selected_hero != hand_hero.seat:
        raise DecisionStateError("hero_seat does not match the ledger hero")

    if event_index is not None:
        try:
            event = ledger.events[event_index]
        except IndexError as exc:
            raise DecisionStateError(f"unknown decision event index {event_index}") from exc
        if event.event_index != event_index:
            raise DecisionStateError("ledger event index does not match its position")
        if event.actor_seat != selected_hero or event.verb not in _DECISION_VERBS:
            raise DecisionStateError("event is not a hero decision action")
        if street is not None and event.street != street:
            raise DecisionStateError("decision event is on a different street")
    else:
        candidates = tuple(
            event
            for event in ledger.events
            if event.actor_seat == selected_hero
            and event.verb in _DECISION_VERBS
            and (street is None or event.street == street)
        )
        if len(candidates) != 1:
            raise DecisionStateError(
                "event_index is required unless exactly one hero decision matches"
            )
        event = candidates[0]

    return _project_event(ledger, event, hand_hero.seat)


def build_hero_decision_states(
    ledger: CanonicalLedgerV1,
    *,
    hero_seat: int | None = None,
    street: StreetV1 | None = None,
) -> tuple[DecisionStateV1, ...]:
    """Build every matching hero decision without reading any later suffix."""

    selected_hero = hero_seat or next(
        player.seat for player in ledger.hand.players if player.is_hero
    )
    return tuple(
        _project_event(ledger, event, selected_hero)
        for event in ledger.events
        if event.actor_seat == selected_hero
        and event.verb in _DECISION_VERBS
        and (street is None or event.street == street)
    )


def _project_event(
    ledger: CanonicalLedgerV1, event: LedgerEventV1, hero_seat: int
) -> DecisionStateV1:
    hand = ledger.hand
    hero = next(player for player in hand.players if player.seat == hero_seat)
    if hero.decision_cards is None:
        raise DecisionStateError("hero decision cards are unavailable")

    prefix = event.state_before
    hero_state = next(
        (state for state in prefix.player_states if state.seat == hero_seat), None
    )
    if hero_state is None or hero_seat not in prefix.active_seats or hero_state.all_in:
        raise DecisionStateError("hero is not able to act at the selected decision")
    if prefix.street != event.street:
        raise DecisionStateError("decision event street disagrees with its prefix state")

    players = tuple(
        DecisionPlayerV1(
            seat=player.seat,
            alias=player.alias,
            position=player.position,
            starting_stack=player.starting_stack,
            is_hero=player.is_hero,
            dealt_in=player.dealt.dealt_in,
        )
        for player in hand.players
    )
    return DecisionStateV1(
        schema_version=DECISION_STATE_SCHEMA_V1,
        raw_hand_id=hand.raw_hand_id,
        played_at=hand.played_at,
        game=hand.game,
        table_marker=hand.table_marker,
        table_format=hand.table_format,
        button_seat=hand.button_seat,
        small_blind=hand.small_blind,
        big_blind=hand.big_blind,
        action_event_index=event.event_index,
        action_street_event_index=event.street_event_index,
        street=prefix.street,
        players=players,
        hero_seat=hero_seat,
        hero_position=hero.position,
        hero_combo=hero.decision_cards,
        active_seats=prefix.active_seats,
        folded_seats=prefix.folded_seats,
        all_in_seats=prefix.all_in_seats,
        players_reached_flop=prefix.players_reached_flop,
        player_states=prefix.player_states,
        amount_to_call=prefix.amount_to_call,
        last_full_raise=prefix.last_full_raise,
        legal_raise_bounds=prefix.legal_raise_bounds,
        legal_actions=_legal_actions(prefix, hero_seat),
        player_contributed_pot=prefix.player_contributed_pot,
        board_prefix=prefix.board.first_run,
        rake_schedule_id=_rake_schedule_id(ledger),
    )


def _legal_actions(state: LedgerStateV1, hero_seat: int) -> tuple[DecisionActionV1, ...]:
    hero = next(player for player in state.player_states if player.seat == hero_seat)
    bounds = state.legal_raise_bounds
    raise_available = (
        bounds.action_reopened
        and bounds.min_raise_to is not None
        and bounds.max_raise_to is not None
        and bounds.min_raise_to <= bounds.max_raise_to
    )
    if state.amount_to_call > Decimal("0"):
        actions: list[DecisionActionV1] = ["fold"]
        if hero.remaining_stack > Decimal("0"):
            actions.append("call")
        if raise_available:
            actions.append("raise")
        return tuple(actions)

    actions = ["check"]
    if raise_available:
        highest_wager = max(
            player.street_contribution for player in state.player_states
        )
        actions.append("bet" if highest_wager == Decimal("0") else "raise")
    return tuple(actions)


def _rake_schedule_id(ledger: CanonicalLedgerV1) -> str | None:
    """Resolve policy from immutable hand facts, never observed settlement fees."""

    try:
        schedule = resolve_rake_schedule(
            played_at=ledger.hand.played_at,
            stake_sb=ledger.hand.small_blind,
            stake_bb=ledger.hand.big_blind,
            game=ledger.hand.game,
            table_format=ledger.hand.table_format,
            players_dealt=sum(player.dealt.dealt_in for player in ledger.hand.players),
        )
    except RakeScheduleError:
        return None
    return schedule.schedule_id


__all__ = [
    "DECISION_STATE_SCHEMA_V1",
    "DecisionActionV1",
    "DecisionPlayerV1",
    "DecisionStateError",
    "DecisionStateV1",
    "build_decision_state",
    "build_hero_decision_states",
]
