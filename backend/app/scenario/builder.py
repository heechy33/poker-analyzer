"""Build the postflop scenario JSON envelope consumed by the WASM solver.

Public entry point: :func:`build_scenario`.

The builder reconstructs the state at the *start* of a postflop street
(before the first action on that street) by replaying preflop actions with
``Decimal`` precision, looks up GTO baseline ranges from
``range_library``, removes hero's known combo and any villain showdown
information, and emits a deterministic JSON envelope plus metadata.

No solver execution happens here — the envelope is the input the browser
WASM bundle consumes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from app.models import Hand, HandAction, RangeLibrary
from app.scenario.ranges import (
    apply_combo_weights,
    parse_range_string,
    remove_combo_from_range,
)

# Fixed v1 bet tree per PLAN.md §6.
BET_TREE: dict[str, Any] = {
    "flop": ["33%", "75%"],
    "turn": ["50%", "100%"],
    "river": ["33%", "75%", "150%"],
    "allin_always": True,
}

_STREET_ORDER: tuple[str, ...] = ("preflop", "flop", "turn", "river")
_POSTFLOP_STREETS = {"flop", "turn", "river"}

# Higher index = later to act on flop/turn/river → in position (IP).
# Heads-up postflop: BB acts first, BTN/SB acts last.
_POSTFLOP_PRIORITY: dict[str, int] = {
    "SB": 0,
    "BB": 1,
    "UTG": 2,
    "UTG+1": 3,
    "MP": 4,
    "LJ": 5,
    "HJ": 6,
    "CO": 7,
    "BTN": 8,
    "BTN/SB": 8,
}

_POT_QUANT = Decimal("0.0001")
_BB_QUANT = Decimal("0.01")


class ScenarioBuildError(ValueError):
    """Raised when scenario construction can't proceed (no flop, hero folded, ...)."""


# A range lookup callable returns either a RangeLibrary row or None.
RangeLookup = Callable[[int, str, str], Awaitable[RangeLibrary | None]]


class _PlayerLike(Protocol):
    seat: int
    screen_name: str
    position: str | None
    starting_stack: Decimal
    is_hero: bool
    final_cards: list[str] | None


@dataclass
class _SeatState:
    name: str
    position: str
    seat: int
    starting_stack: Decimal
    invested: Decimal
    alive: bool
    is_hero: bool
    final_cards: list[str] | None


async def build_scenario(
    hand: Hand,
    players: Sequence[_PlayerLike],
    actions: Sequence[HandAction],
    street: str,
    range_lookup: RangeLookup,
) -> dict[str, Any]:
    """Build the scenario envelope for the given postflop street.

    Parameters
    ----------
    hand:
        The :class:`~app.models.Hand` row for the hand being analyzed.
    players:
        Sequence of seat-level rows (typically ``HandPlayer``), one per
        seated player. Position labels must be set.
    actions:
        Pre-sorted sequence of ``HandAction`` rows. They will be sorted again
        defensively.
    street:
        Target postflop street: ``"flop"``, ``"turn"``, or ``"river"``.
    range_lookup:
        Async callable ``(table_size, position, action_sequence)`` returning a
        :class:`~app.models.RangeLibrary` row or ``None``.

    Returns
    -------
    dict
        ``{"scenario": <envelope>, "metadata": {...}, "scenario_hash": ...}``.
    """
    if street not in _POSTFLOP_STREETS:
        raise ScenarioBuildError(f"unsupported street: {street}")

    board = _board_for_street(hand, street)
    if board is None:
        raise ScenarioBuildError(f"hand has no {street} board")

    sorted_actions = sorted(
        actions,
        key=lambda a: (
            _STREET_ORDER.index(a.street) if a.street in _STREET_ORDER else 99,
            a.action_order,
            getattr(a, "id", 0) or 0,
        ),
    )

    seats = _build_seat_states(players)
    _replay_until(sorted_actions, seats, until_street=street)

    hero_state = _hero_state(seats.values())
    if not hero_state.alive:
        raise ScenarioBuildError("hero folded before this street")

    alive_states = [s for s in seats.values() if s.alive]
    if len(alive_states) < 2:
        raise ScenarioBuildError("fewer than two players alive at this street")

    confidence = "high" if len(alive_states) == 2 else "low"

    villain_state = _select_villain(hero_state, alive_states, sorted_actions)
    if villain_state is None:
        raise ScenarioBuildError("could not pick a villain for this street")

    # Pot at start of street = sum of all invested chips up to now.
    pot_chips = sum((s.invested for s in seats.values()), Decimal("0"))
    bb = Decimal(hand.stake_bb) if hand.stake_bb else Decimal("1")
    if bb <= 0:
        bb = Decimal("1")

    effective_stack_chips = min(
        max(hero_state.starting_stack - hero_state.invested, Decimal("0")),
        max(villain_state.starting_stack - villain_state.invested, Decimal("0")),
    )
    pot_bb = _to_bb(pot_chips, bb)
    eff_bb = _to_bb(effective_stack_chips, bb)

    oop_state, ip_state = _determine_in_position(hero_state, villain_state)

    hero_seq = _action_sequence_for(
        hero_state, villain_state, sorted_actions, seats
    )
    villain_seq = _action_sequence_for(
        villain_state, hero_state, sorted_actions, seats
    )

    hero_range, hero_conf = await _lookup_range(
        range_lookup, int(hand.table_size), hero_state.position, hero_seq
    )
    villain_range, villain_conf = await _lookup_range(
        range_lookup, int(hand.table_size), villain_state.position, villain_seq
    )
    if hero_conf == "low" or villain_conf == "low":
        confidence = "low"

    if hero_state.is_hero and hand.hero_cards:
        hero_range = remove_combo_from_range(hero_range, hand.hero_cards, board)
    if villain_state.final_cards:
        villain_range = remove_combo_from_range(
            villain_range, villain_state.final_cards, board
        )

    envelope: dict[str, Any] = {
        "board": list(board),
        "pot_bb": pot_bb,
        "effective_stack_bb": eff_bb,
        "oop_player": oop_state.position,
        "ip_player": ip_state.position,
        "hero_range": _normalize_range(hero_range),
        "villain_range": _normalize_range(villain_range),
        "bet_tree": BET_TREE,
    }
    scenario_hash = canonical_hash(envelope)

    metadata = {
        "scenario_hash": scenario_hash,
        "confidence": confidence,
        "oop_position": oop_state.position,
        "ip_position": ip_state.position,
        "hero_position": hero_state.position,
        "villain_position": villain_state.position,
        "hero_screen_name": hero_state.name,
        "villain_screen_name": villain_state.name,
        "hero_action_sequence": hero_seq,
        "villain_action_sequence": villain_seq,
        "pot_chips": str(pot_chips.quantize(_POT_QUANT)),
        "effective_stack_chips": str(effective_stack_chips.quantize(_POT_QUANT)),
        "stake_bb_chips": str(bb),
    }

    return {
        "scenario": envelope,
        "metadata": metadata,
        "scenario_hash": scenario_hash,
    }


def canonical_hash(envelope: dict[str, Any]) -> str:
    """SHA-256 of a sort-keyed JSON serialisation of the envelope."""
    payload = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_seat_states(
    players: Sequence[_PlayerLike],
) -> dict[str, _SeatState]:
    seats: dict[str, _SeatState] = {}
    for player in players:
        position = (player.position or "").strip() or f"SEAT{player.seat}"
        seats[player.screen_name] = _SeatState(
            name=player.screen_name,
            position=position,
            seat=int(player.seat),
            starting_stack=Decimal(player.starting_stack),
            invested=Decimal("0"),
            alive=True,
            is_hero=bool(player.is_hero),
            final_cards=list(player.final_cards) if player.final_cards else None,
        )
    return seats


def _replay_until(
    actions: Sequence[HandAction],
    seats: dict[str, _SeatState],
    until_street: str,
) -> None:
    """Replay actions through the END of every street prior to `until_street`."""
    target_idx = _STREET_ORDER.index(until_street)
    streets_to_replay = _STREET_ORDER[:target_idx]

    for street in streets_to_replay:
        street_actions = [a for a in actions if a.street == street]
        if not street_actions:
            continue
        in_street = {name: Decimal("0") for name in seats}
        for action in street_actions:
            name = action.screen_name
            if name not in seats:
                continue
            seat = seats[name]
            verb = action.action
            if verb in ("post_sb", "post_bb"):
                in_street[name] += _decimal(action.amount)
            elif verb == "fold":
                seat.alive = False
            elif verb == "check":
                continue
            elif verb == "call":
                in_street[name] += _decimal(action.amount)
            elif verb == "bet":
                in_street[name] += _decimal(action.amount)
            elif verb == "raise":
                target = _decimal(action.raise_to)
                if target > in_street[name]:
                    in_street[name] = target
            elif verb == "all_in":
                # The CoinPoker parser collapses "is all-in" into the
                # accompanying call/bet/raise verb's flag, so a bare all_in
                # action carries no chip delta.
                continue
            else:
                # show / muck / collect happen on the showdown pseudo-street.
                continue
        for name, amount in in_street.items():
            seats[name].invested += amount


def _hero_state(states: Sequence[_SeatState]) -> _SeatState:
    for state in states:
        if state.is_hero:
            return state
    raise ScenarioBuildError("hand has no hero player")


def _select_villain(
    hero: _SeatState,
    alive_states: Sequence[_SeatState],
    actions: Sequence[HandAction],
) -> _SeatState | None:
    """Pick the primary villain.

    Heads-up postflop: the only other alive player.
    Multi-way: the last preflop aggressor (other than hero), or fall back to
    the player who voluntarily put the most chips in.
    """
    candidates = [s for s in alive_states if s.name != hero.name]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    last_aggressor: _SeatState | None = None
    for action in reversed(actions):
        if action.street != "preflop":
            continue
        if action.action != "raise":
            continue
        if action.screen_name == hero.name:
            continue
        for state in candidates:
            if state.name == action.screen_name:
                last_aggressor = state
                break
        if last_aggressor is not None:
            break
    if last_aggressor is not None:
        return last_aggressor

    return max(candidates, key=lambda s: (s.invested, _POSTFLOP_PRIORITY.get(s.position, -1)))


def _determine_in_position(
    hero: _SeatState, villain: _SeatState
) -> tuple[_SeatState, _SeatState]:
    """Return (oop, ip) by postflop position priority."""
    hero_p = _POSTFLOP_PRIORITY.get(hero.position, -1)
    villain_p = _POSTFLOP_PRIORITY.get(villain.position, -1)
    if hero_p > villain_p:
        return villain, hero
    if villain_p > hero_p:
        return hero, villain
    # Tie-break by seat: smaller seat acts first → OOP.
    if hero.seat < villain.seat:
        return hero, villain
    return villain, hero


def _action_sequence_for(
    player: _SeatState,
    opponent: _SeatState,
    actions: Sequence[HandAction],
    seats: dict[str, _SeatState],
) -> str:
    """Derive the range_library action_sequence key from preflop history."""
    raises = [a for a in actions if a.street == "preflop" and a.action == "raise"]
    last_aggressor_pos = _last_preflop_aggressor_position(actions, seats)

    if not raises:
        # Limped pot.
        if player.position == "BB":
            return "limp_check"
        return "limp"

    raise_count = len(raises)
    label = _raise_label(raise_count)
    last_raise = raises[-1]

    if player.name == last_raise.screen_name:
        # Player is the aggressor heading into the flop.
        if raise_count == 1:
            return "open"
        prev_label = _raise_label(raise_count - 1)
        prev_raise = raises[-2]
        prev_pos = _seat_position_by_name(prev_raise.screen_name, seats)
        if prev_pos is None:
            return f"{label}"
        return f"vs_{prev_pos}_{prev_label}_{label}"

    # Player is a caller of the last raise.
    aggressor_pos = (
        _seat_position_by_name(last_raise.screen_name, seats)
        or last_aggressor_pos
        or opponent.position
    )
    if raise_count == 1:
        return f"vs_{aggressor_pos}_open_call"
    return f"vs_{aggressor_pos}_{label}_call"


def _raise_label(raise_count: int) -> str:
    return {1: "open", 2: "3bet", 3: "4bet", 4: "5bet"}.get(
        raise_count, f"{raise_count}bet"
    )


def _seat_position_by_name(name: str, seats: dict[str, _SeatState]) -> str | None:
    state = seats.get(name)
    return state.position if state else None


def _last_preflop_aggressor_position(
    actions: Sequence[HandAction], seats: dict[str, _SeatState]
) -> str | None:
    for action in reversed(actions):
        if action.street == "preflop" and action.action == "raise":
            return _seat_position_by_name(action.screen_name, seats)
    return None


async def _lookup_range(
    range_lookup: RangeLookup,
    table_size: int,
    position: str,
    action_sequence: str,
) -> tuple[dict[str, float], str]:
    """Resolve hand-class weights for a (position, action_sequence) tuple.

    Falls back to ``(position, "default_call")`` and tags the result as
    ``confidence="low"`` if no exact match is configured.
    """
    row = await range_lookup(table_size, position, action_sequence)
    if row is not None:
        weights = parse_range_string(row.range_string)
        merged = apply_combo_weights(weights, row.combo_weights)
        if merged:
            return merged, "high"

    fallback = await range_lookup(table_size, position, "default_call")
    if fallback is not None:
        weights = parse_range_string(fallback.range_string)
        merged = apply_combo_weights(weights, fallback.combo_weights)
        if merged:
            return merged, "low"

    # Last-ditch fallback: an empty range. Solver would still run but this is
    # diagnosable via confidence="low".
    return {}, "low"


def _board_for_street(hand: Hand, street: str) -> list[str] | None:
    if hand.flop is None:
        return None
    if street == "flop":
        return list(hand.flop)
    if street == "turn":
        if hand.turn is None:
            return None
        return [*hand.flop, hand.turn]
    if street == "river":
        if hand.turn is None or hand.river is None:
            return None
        return [*hand.flop, hand.turn, hand.river]
    return None


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _to_bb(chips: Decimal, bb: Decimal) -> float:
    quantized = (chips / bb).quantize(_BB_QUANT, rounding=ROUND_HALF_UP)
    return float(quantized)


def _normalize_range(weights: dict[str, float]) -> dict[str, float]:
    """Round weights for hash stability and sort keys."""
    rounded: dict[str, float] = {}
    for hand_class, weight in weights.items():
        if weight is None:
            continue
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        if value <= 0.0:
            continue
        rounded[hand_class] = round(value, 4)
    return dict(sorted(rounded.items()))


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"unserialisable: {type(value)!r}")
