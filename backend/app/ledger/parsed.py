"""Translation from the CoinPoker parser contract to the canonical ledger.

The parser remains responsible for recognizing raw text.  This module is the
only place that turns those facts into chip-accounting events; persistence and
consumers must use the resulting ledger or one of its projections.
"""

from __future__ import annotations

from decimal import Decimal

from app.ledger.models import (
    DealtProvenanceV1,
    LedgerHandV1,
    LedgerPlayerV1,
    SourceProvenanceV1,
)
from app.ledger.reducer import LedgerReducer, LedgerReductionError, ReductionInputV1
from app.parser.models import ParsedAction, ParsedHand, ParsedSplashDrop
from app.table_formats import table_format_from_size


class ParsedLedgerError(ValueError):
    """A parsed hand cannot be represented by the canonical ledger."""


_ACTION_VERBS = {
    "post_sb": "post_small_blind",
    "post_bb": "post_big_blind",
    "post_ante": "post_ante",
    "fold": "fold",
    "check": "check",
    "call": "call",
    "bet": "bet",
    "raise": "raise",
    "collect": "collect",
}
_ACCOUNTING_ACTIONS = frozenset(_ACTION_VERBS)
_ZERO = Decimal("0")


def ledger_from_parsed(parsed: ParsedHand):
    """Reduce one parser result without reinterpreting legacy action amounts.

    Returns and collections are included in the parser's source-order timeline,
    so the reducer alone determines contribution deltas and final stacks.
    """
    hand = LedgerHandV1(
        raw_hand_id=str(parsed.coinpoker_hand_id),
        played_at=parsed.played_at,
        game="NLHE",
        table_marker=parsed.table_name,
        table_format=table_format_from_size(parsed.table_size),
        button_seat=parsed.button_seat,
        small_blind=parsed.stake_sb,
        big_blind=parsed.stake_bb,
        players=tuple(_player_from_parsed(player, parsed) for player in parsed.players),
        flags=frozenset(name for name, enabled in parsed.flags.items() if enabled is True),
    )
    reducer = LedgerReducer(hand)
    current_street = "preflop"
    for kind, item in _timeline(parsed):
        if kind == "action":
            action = item
            assert isinstance(action, ParsedAction)
            if action.street != current_street and action.street != "showdown":
                _advance_to_street(reducer, parsed, action.street, action.line_number)
                current_street = action.street
            if action.action not in _ACCOUNTING_ACTIONS:
                # Show/muck are card disclosures, not chip-accounting events.
                if action.action in {"show", "muck"}:
                    continue
                raise ParsedLedgerError(
                    f"unsupported accounting action {action.action!r} on line {action.line_number}"
                )
            verb = _ACTION_VERBS[action.action]
            reducer.apply(
                ReductionInputV1(
                    source=_source(action.line_number, "post" if verb.startswith("post_") else "action"),
                    actor_seat=action.seat,
                    verb=verb,
                    action_amount=action.amount,
                    raise_to=_raise_to_for_action(reducer, action, verb),
                    is_all_in=action.is_all_in,
                    pot_award_id=action.pot_award_id,
                    award_amount=action.amount if verb == "collect" else _ZERO,
                    raw_tokens={"action": action.action},
                )
            )
        elif kind == "return":
            returned = item
            reducer.apply(
                ReductionInputV1(
                    source=_source(returned.line_number, "action"),
                    actor_seat=returned.seat,
                    verb="return_uncalled",
                    action_amount=returned.amount,
                    raw_tokens={"kind": "uncalled_return"},
                )
            )

        else:
            splash_drop = item
            assert isinstance(splash_drop, ParsedSplashDrop)
            reducer.apply(
                ReductionInputV1(
                    source=_source(splash_drop.line_number, "action"),
                    verb="splash_drop",
                    promotional_delta=splash_drop.amount,
                    raw_tokens={"kind": "splash_drop"},
                )
            )
    reducer.apply(
        ReductionInputV1(
            source=_source(_summary_line(parsed), "summary"),
            verb="fee_summary",
            observed_rake=parsed.rake,
            splash_fee=parsed.splash_fee,
        )
    )
    try:
        return reducer.finalize(reported_total_pot=parsed.total_pot)
    except LedgerReductionError as exc:
        raise ParsedLedgerError(str(exc)) from exc


def _player_from_parsed(player, parsed: ParsedHand) -> LedgerPlayerV1:
    dealt_line = parsed.dealt_player_lines.get(player.screen_name)
    source = _source(dealt_line, "dealt") if dealt_line is not None else _source(1, "derived")
    return LedgerPlayerV1(
        seat=player.seat,
        alias=player.screen_name,
        position=player.position,
        starting_stack=player.starting_stack,
        is_hero=player.is_hero,
        dealt=DealtProvenanceV1(
            dealt_in=True,
            source=source,
            inferred=dealt_line is None,
        ),
        decision_cards=tuple(parsed.hero_cards) if player.is_hero else None,
    )


def _timeline(parsed: ParsedHand):
    events = [("action", action.line_number, action) for action in parsed.actions]
    events.extend(("return", returned.line_number, returned) for returned in parsed.uncalled_returns)
    events.extend(("splash_drop", drop.line_number, drop) for drop in parsed.splash_drops)
    return tuple((kind, item) for kind, _, item in sorted(events, key=lambda event: event[1]))


def _raise_to_for_action(
    reducer: LedgerReducer, action: ParsedAction, verb: str
) -> Decimal | None:
    """Derive the resulting wager for CoinPoker's contribution-style ALLIN text."""
    if verb != "raise" or not action.is_all_in:
        return action.raise_to
    if not reducer.events:
        return next(player.starting_stack for player in reducer.hand.players if player.seat == action.seat)
    player = next(
        state for state in reducer.events[-1].state_after.player_states if state.seat == action.seat
    )
    return player.street_contribution + player.remaining_stack


def _advance_to_street(
    reducer: LedgerReducer,
    parsed: ParsedHand,
    target: str,
    line_number: int,
) -> None:
    while _current_street(reducer) != target:
        current = _current_street(reducer)
        if current == "preflop":
            next_street, cards = "flop", tuple(parsed.flop or ())
        elif current == "flop":
            next_street, cards = "turn", (parsed.turn,) if parsed.turn else ()
        elif current == "turn":
            next_street, cards = "river", (parsed.river,) if parsed.river else ()
        else:
            raise ParsedLedgerError(f"cannot advance ledger from {current} to {target}")
        if not cards:
            raise ParsedLedgerError(f"missing board cards required before {target}")
        reducer.apply(
            ReductionInputV1(
                source=_source(line_number, "board"),
                verb="street_transition",
                next_street=next_street,
                board_cards=cards,
            )
        )


def _summary_line(parsed: ParsedHand) -> int:
    return max(
        [action.line_number for action in parsed.actions]
        + [returned.line_number for returned in parsed.uncalled_returns]
        + [drop.line_number for drop in parsed.splash_drops]
        + [1]
    ) + 1


def _source(line_number: int, line_type: str) -> SourceProvenanceV1:
    return SourceProvenanceV1(line_number=line_number, line_type=line_type)


def _current_street(reducer: LedgerReducer) -> str:
    return reducer.events[-1].state_after.street if reducer.events else "preflop"
