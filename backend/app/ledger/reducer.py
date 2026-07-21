"""Pure reducer for the P1 canonical CoinPoker NLHE ledger.

No parser, database, or solver code is imported here.  Callers provide
normalized raw facts and the reducer derives contribution, pot, stack, and
legal-raise state for every emitted ledger event.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.ledger.models import (
    BoardRunsV1,
    CanonicalLedgerV1,
    FeeMetadataV1,
    LedgerEventV1,
    LedgerHandV1,
    LedgerModel,
    LedgerStateV1,
    LegalRaiseBoundsV1,
    LedgerVerbV1,
    PlayerStateV1,
    SettlementV1,
    SourceProvenanceV1,
    StreetV1,
)


_ZERO = Decimal("0")
_STREET_ORDER: tuple[StreetV1, ...] = (
    "preflop",
    "flop",
    "turn",
    "river",
    "showdown",
)
_ACTOR_VERBS = frozenset(
    {
        "post_small_blind",
        "post_big_blind",
        "post_ante",
        "post_dead_blind",
        "post_straddle",
        "fold",
        "check",
        "call",
        "bet",
        "raise",
        "return_uncalled",
        "collect",
    }
)
_FORCED_CONTRIBUTION_VERBS = frozenset(
    {
        "post_small_blind",
        "post_big_blind",
        "post_ante",
        "post_dead_blind",
        "post_straddle",
    }
)


class LedgerReductionError(ValueError):
    """A raw fact cannot be represented by a legal, conserved ledger event."""


class ReductionInputV1(LedgerModel):
    """One normalized raw fact to apply to a :class:`LedgerReducer`.

    ``action_amount`` retains the raw amount as written by CoinPoker.  The
    reducer calculates the actual contribution for raises and never assumes
    that the text between ``raises`` and ``to`` is a contribution delta.
    """

    source: SourceProvenanceV1
    verb: LedgerVerbV1
    actor_seat: int | None = Field(default=None, ge=1)
    action_amount: Decimal | None = None
    raise_to: Decimal | None = None
    is_all_in: bool = False
    raw_tokens: dict[str, str] = Field(default_factory=dict)
    next_street: StreetV1 | None = None
    board_cards: tuple[str, ...] = ()
    board_run: Literal["first_run", "second_run"] = "first_run"
    pot_award_id: str | None = None
    award_amount: Decimal | None = None
    promotional_delta: Decimal | None = None
    observed_rake: Decimal | None = None
    splash_fee: Decimal | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> ReductionInputV1:
        if self.verb in _ACTOR_VERBS and self.actor_seat is None:
            raise ValueError(f"{self.verb} requires actor_seat")
        if self.verb not in _ACTOR_VERBS and self.actor_seat is not None:
            raise ValueError(f"{self.verb} must not have actor_seat")
        return self


@dataclass
class _ReducerState:
    street: StreetV1
    active: set[int]
    folded: set[int]
    all_in: set[int]
    reached_flop: set[int]
    street_contributions: dict[int, Decimal]
    total_contributions: dict[int, Decimal]
    remaining_stacks: dict[int, Decimal]
    last_full_raise: Decimal
    acted_since_full_raise: set[int]
    board: BoardRunsV1
    fee_metadata: FeeMetadataV1
    promotional_drop: Decimal


class LedgerReducer:
    """Build canonical events while enforcing no-limit chip accounting rules."""

    def __init__(self, hand: LedgerHandV1) -> None:
        self.hand = hand
        seats = {player.seat for player in hand.players}
        active = {player.seat for player in hand.players if player.dealt.dealt_in}
        self._state = _ReducerState(
            street="preflop",
            active=active,
            folded=set(),
            all_in=set(),
            reached_flop=set(),
            street_contributions={seat: _ZERO for seat in seats},
            total_contributions={seat: _ZERO for seat in seats},
            remaining_stacks={player.seat: player.starting_stack for player in hand.players},
            last_full_raise=hand.big_blind,
            acted_since_full_raise=set(),
            board=BoardRunsV1(),
            fee_metadata=FeeMetadataV1(),
            promotional_drop=_ZERO,
        )
        self._events: list[LedgerEventV1] = []
        self._street_event_counts: dict[StreetV1, int] = {
            street: 0 for street in _STREET_ORDER
        }
        self._awards_total = _ZERO
        self._assert_conservation()

    @property
    def events(self) -> tuple[LedgerEventV1, ...]:
        return tuple(self._events)

    def apply(self, item: ReductionInputV1) -> LedgerEventV1:
        """Apply one raw fact and return its fully derived ledger event."""
        self._validate_event_street(item)
        before = self._snapshot(actor_seat=item.actor_seat)
        contribution_delta = _ZERO
        promotional_delta = _ZERO
        returned_delta = _ZERO
        raise_to: Decimal | None = None
        raise_increment: Decimal | None = None
        award_amount = _ZERO
        event_street = self._state.street
        event_all_in = False

        if item.verb in _FORCED_CONTRIBUTION_VERBS:
            contribution_delta = self._required_amount(item)
            if item.is_all_in:
                raise LedgerReductionError("forced contributions cannot use is_all_in")
            self._ensure_commit_fits(item, contribution_delta)
            self._commit_amount(item, contribution_delta)
            if item.verb == "post_big_blind":
                self._state.last_full_raise = contribution_delta
        elif item.verb == "fold":
            self._require_active_actor(item)
            self._state.active.remove(_actor(item))
            self._state.folded.add(_actor(item))
            self._state.acted_since_full_raise.add(_actor(item))
        elif item.verb == "check":
            self._require_active_actor(item)
            if self._amount_to_call(_actor(item)) != _ZERO:
                raise LedgerReductionError("check is illegal while chips are owed")
            self._state.acted_since_full_raise.add(_actor(item))
        elif item.verb == "call":
            self._require_active_actor(item)
            owed = self._amount_to_call(_actor(item))
            if owed == _ZERO:
                raise LedgerReductionError("call is illegal when no chips are owed")
            contribution_delta = self._call_amount(item, owed)
            event_all_in = contribution_delta == self._state.remaining_stacks[_actor(item)]
            self._commit_amount(item, contribution_delta)
            self._state.acted_since_full_raise.add(_actor(item))
        elif item.verb == "bet":
            self._require_active_actor(item)
            if self._highest_street_contribution() != _ZERO:
                raise LedgerReductionError("bet is illegal after a wager; use raise")
            if not self._is_action_reopened(_actor(item)):
                raise LedgerReductionError("betting action has not reopened")
            contribution_delta = self._required_amount(item)
            self._ensure_commit_fits(item, contribution_delta)
            event_all_in = self._will_be_all_in(item, contribution_delta)
            if contribution_delta < self.hand.big_blind and not event_all_in:
                raise LedgerReductionError("opening bet below the big blind must be all-in")
            self._commit_amount(item, contribution_delta)
            self._state.last_full_raise = max(contribution_delta, self.hand.big_blind)
            self._state.acted_since_full_raise = {_actor(item)}
        elif item.verb == "raise":
            self._require_active_actor(item)
            if not self._is_action_reopened(_actor(item)):
                raise LedgerReductionError("raise is illegal because action has not reopened")
            prior_wager = self._highest_street_contribution()
            if item.raise_to is None:
                raise LedgerReductionError("raise_to must exceed the current street wager")
            raise_to = _required_nonnegative(item.raise_to, "raise_to")
            if raise_to <= prior_wager:
                raise LedgerReductionError("raise_to must exceed the current street wager")
            contribution_delta = raise_to - self._state.street_contributions[_actor(item)]
            self._ensure_commit_fits(item, contribution_delta)
            event_all_in = self._will_be_all_in(item, contribution_delta)
            raise_increment = raise_to - prior_wager
            if raise_increment < self._state.last_full_raise:
                if not event_all_in:
                    raise LedgerReductionError("short raise must be all-in")
            self._commit_amount(item, contribution_delta)
            if raise_increment < self._state.last_full_raise:
                self._state.acted_since_full_raise.add(_actor(item))
            else:
                self._state.last_full_raise = raise_increment
                self._state.acted_since_full_raise = {_actor(item)}
        elif item.verb == "return_uncalled":
            # An all-in wager can be uncalled when every opponent folds.  A
            # return is settlement of already committed chips, not a further
            # betting action, so it must remain legal for that active player.
            self._require_active_seat(item)
            returned_delta = self._required_amount(item)
            actor = _actor(item)
            if returned_delta > self._state.street_contributions[actor]:
                raise LedgerReductionError("returned amount exceeds actor street contribution")
            self._state.street_contributions[actor] -= returned_delta
            self._state.total_contributions[actor] -= returned_delta
            self._state.remaining_stacks[actor] += returned_delta
        elif item.verb == "street_transition":
            event_street = self._apply_street_transition(item)
        elif item.verb == "splash_drop":
            promotional_delta = _required_nonnegative(item.promotional_delta, "promotional_delta")
            if promotional_delta == _ZERO:
                raise LedgerReductionError("splash_drop requires promotional_delta")
            self._state.promotional_drop += promotional_delta
            self._state.fee_metadata = self._state.fee_metadata.model_copy(
                update={"promotional_drop": self._state.promotional_drop}
            )
        elif item.verb == "fee_summary":
            self._apply_fee_summary(item)
        elif item.verb == "collect":
            self._require_active_or_folded_actor(item)
            if not item.pot_award_id:
                raise LedgerReductionError("collect requires pot_award_id")
            award_amount = _required_nonnegative(item.award_amount, "award_amount")
            if award_amount == _ZERO:
                raise LedgerReductionError("collect requires a positive award_amount")
            self._awards_total += award_amount
        else:  # Defensive guard for future additions to LedgerVerbV1.
            raise LedgerReductionError(f"unsupported reducer verb: {item.verb}")

        self._assert_conservation()
        after = self._snapshot()
        event = LedgerEventV1(
            event_index=len(self._events),
            street_event_index=self._street_event_index(item.verb, event_street),
            street=event_street,
            source=item.source,
            actor_seat=item.actor_seat,
            verb=item.verb,
            action_amount=item.action_amount,
            contribution_delta=contribution_delta,
            promotional_delta=promotional_delta,
            returned_delta=returned_delta,
            raise_to=raise_to,
            raise_increment=raise_increment,
            is_all_in=event_all_in,
            pot_award_id=item.pot_award_id,
            award_amount=award_amount,
            raw_tokens=item.raw_tokens,
            state_before=before,
            state_after=after,
        )
        self._events.append(event)
        return event

    def finalize(
        self,
        *,
        reported_total_pot: Decimal | None = None,
        promotional_payout_observed: Decimal | None = None,
    ) -> CanonicalLedgerV1:
        """Validate settlement facts and return the immutable canonical ledger."""
        player_pot = self._player_pot()
        fees = self._state.fee_metadata.observed_rake + self._state.fee_metadata.splash_fee
        if self._awards_total != _ZERO and self._awards_total + fees != player_pot:
            raise LedgerReductionError("awards, rake, and splash fee do not reconcile player pot")
        if reported_total_pot is not None:
            if reported_total_pot != player_pot + self._state.promotional_drop:
                raise LedgerReductionError(
                    "reported total pot must equal player pot plus promotional drop"
                )
            status: Literal["pending", "reconciled", "unreconciled"] = (
                "unreconciled"
                if self._state.promotional_drop and promotional_payout_observed is None
                else "reconciled"
            )
        else:
            status = "pending"
        settlement = SettlementV1(
            reported_total_pot=reported_total_pot,
            reconciliation_status=status,
            promotional_payout_observed=promotional_payout_observed,
        )
        return CanonicalLedgerV1.create(
            hand=self.hand,
            events=tuple(self._events),
            settlement=settlement,
        )

    def _apply_street_transition(self, item: ReductionInputV1) -> StreetV1:
        next_street = item.next_street
        if next_street is None:
            raise LedgerReductionError("street_transition requires next_street")
        if item.board_run == "second_run":
            if next_street != "river" or len(self._state.board.first_run) != 5:
                raise LedgerReductionError("second run must be a complete alternate river board")
            self._state.board = BoardRunsV1(
                first_run=self._state.board.first_run,
                second_run=item.board_cards,
            )
            return next_street
        expected = _next_street(self._state.street)
        if next_street != expected:
            raise LedgerReductionError(
                f"street transition must advance from {self._state.street} to {expected}"
            )
        required_cards = 3 if next_street == "flop" else 1 if next_street in {"turn", "river"} else 0
        if len(item.board_cards) != required_cards:
            raise LedgerReductionError(
                f"{next_street} transition requires {required_cards} board card(s)"
            )
        first_run = self._state.board.first_run + item.board_cards
        self._state.board = BoardRunsV1(
            first_run=first_run,
            second_run=self._state.board.second_run,
        )
        self._state.street = next_street
        self._state.street_contributions = {
            seat: _ZERO for seat in self._state.street_contributions
        }
        self._state.last_full_raise = self.hand.big_blind
        self._state.acted_since_full_raise.clear()
        if next_street == "flop":
            self._state.reached_flop = set(self._state.active)
        return next_street

    def _apply_fee_summary(self, item: ReductionInputV1) -> None:
        rake = _required_nonnegative(item.observed_rake, "observed_rake")
        splash_fee = _required_nonnegative(item.splash_fee, "splash_fee")
        self._state.fee_metadata = FeeMetadataV1(
            observed_rake=rake,
            splash_fee=splash_fee,
            promotional_drop=self._state.promotional_drop,
            schedule_id=self._state.fee_metadata.schedule_id,
        )

    def _validate_event_street(self, item: ReductionInputV1) -> None:
        if item.verb in {"street_transition", "splash_drop", "fee_summary", "collect"}:
            return
        if self._state.street == "showdown":
            raise LedgerReductionError("betting actions cannot occur after showdown")

    def _required_amount(self, item: ReductionInputV1) -> Decimal:
        amount = _required_nonnegative(item.action_amount, "action_amount")
        if amount == _ZERO:
            raise LedgerReductionError("action_amount must be positive")
        return amount

    def _call_amount(self, item: ReductionInputV1, owed: Decimal) -> Decimal:
        amount = self._required_amount(item)
        remaining = self._state.remaining_stacks[_actor(item)]
        if amount > owed:
            raise LedgerReductionError("call amount cannot exceed amount owed")
        if amount == owed:
            if item.is_all_in and amount != remaining:
                raise LedgerReductionError("all-in action must commit the actor's remaining stack")
            return amount
        if amount != remaining or not item.is_all_in:
            raise LedgerReductionError("short call must commit the full remaining stack as all-in")
        return amount

    def _commit_amount(self, item: ReductionInputV1, amount: Decimal) -> Decimal:
        actor = _actor(item)
        self._ensure_commit_fits(item, amount)
        self._state.remaining_stacks[actor] -= amount
        self._state.street_contributions[actor] += amount
        self._state.total_contributions[actor] += amount
        if self._state.remaining_stacks[actor] == _ZERO:
            self._state.all_in.add(actor)
        return amount

    def _ensure_commit_fits(self, item: ReductionInputV1, amount: Decimal) -> None:
        if amount > self._state.remaining_stacks[_actor(item)]:
            raise LedgerReductionError("contribution exceeds remaining stack")

    def _will_be_all_in(self, item: ReductionInputV1, amount: Decimal) -> bool:
        actual_all_in = amount == self._state.remaining_stacks[_actor(item)]
        if item.is_all_in and not actual_all_in:
            raise LedgerReductionError("all-in action must commit the actor's remaining stack")
        return actual_all_in

    def _require_active_actor(self, item: ReductionInputV1) -> None:
        self._require_active_seat(item)
        if _actor(item) in self._state.all_in:
            raise LedgerReductionError("all-in actor cannot take another betting action")

    def _require_active_seat(self, item: ReductionInputV1) -> None:
        actor = _actor(item)
        if actor not in self._state.active:
            raise LedgerReductionError("actor is not active")

    def _require_active_or_folded_actor(self, item: ReductionInputV1) -> None:
        if _actor(item) not in self._state.remaining_stacks:
            raise LedgerReductionError("actor is not seated")

    def _highest_street_contribution(self) -> Decimal:
        return max(self._state.street_contributions[seat] for seat in self._state.active)

    def _amount_to_call(self, actor: int) -> Decimal:
        return self._highest_street_contribution() - self._state.street_contributions[actor]

    def _is_action_reopened(self, actor: int) -> bool:
        return actor not in self._state.acted_since_full_raise

    def _snapshot(self, actor_seat: int | None = None) -> LedgerStateV1:
        actor = actor_seat if actor_seat in self._state.active - self._state.all_in else None
        if actor is None:
            candidates = self._state.active - self._state.all_in
            actor = min(
                candidates,
                key=lambda seat: (self._state.street_contributions[seat], seat),
                default=None,
            )
        bounds = self._legal_raise_bounds(actor)
        player_states = tuple(
            PlayerStateV1(
                seat=player.seat,
                active=player.seat in self._state.active,
                folded=player.seat in self._state.folded,
                all_in=player.seat in self._state.all_in,
                street_contribution=self._state.street_contributions[player.seat],
                total_contribution=self._state.total_contributions[player.seat],
                remaining_stack=self._state.remaining_stacks[player.seat],
            )
            for player in self.hand.players
        )
        return LedgerStateV1(
            street=self._state.street,
            active_seats=frozenset(self._state.active),
            folded_seats=frozenset(self._state.folded),
            all_in_seats=frozenset(self._state.all_in),
            players_reached_flop=frozenset(self._state.reached_flop),
            player_states=player_states,
            amount_to_call=self._amount_to_call(actor) if actor is not None else _ZERO,
            last_full_raise=self._state.last_full_raise,
            legal_raise_bounds=bounds,
            player_contributed_pot=self._player_pot(),
            promotional_drop=self._state.promotional_drop,
            board=self._state.board,
            fee_metadata=self._state.fee_metadata,
        )

    def _legal_raise_bounds(self, actor: int | None) -> LegalRaiseBoundsV1:
        if actor is None or not self._is_action_reopened(actor):
            return LegalRaiseBoundsV1(action_reopened=False)
        current_wager = self._highest_street_contribution()
        min_raise_to = current_wager + self._state.last_full_raise
        if current_wager == _ZERO:
            min_raise_to = self.hand.big_blind
        max_raise_to = (
            self._state.street_contributions[actor] + self._state.remaining_stacks[actor]
        )
        if max_raise_to < min_raise_to:
            min_raise_to = None
        return LegalRaiseBoundsV1(
            min_raise_to=min_raise_to,
            max_raise_to=max_raise_to,
            action_reopened=True,
        )

    def _street_event_index(self, verb: LedgerVerbV1, street: StreetV1) -> int | None:
        if verb in {"street_transition", "splash_drop", "fee_summary", "collect"}:
            return None
        index = self._street_event_counts[street]
        self._street_event_counts[street] += 1
        return index

    def _player_pot(self) -> Decimal:
        return sum(self._state.total_contributions.values(), start=_ZERO)

    def _assert_conservation(self) -> None:
        starting = sum((player.starting_stack for player in self.hand.players), start=_ZERO)
        remaining = sum(self._state.remaining_stacks.values(), start=_ZERO)
        committed = sum(self._state.total_contributions.values(), start=_ZERO)
        if starting != remaining + committed:
            raise AssertionError("stack and committed-chip conservation failed")
        if any(amount < _ZERO for amount in self._state.remaining_stacks.values()):
            raise AssertionError("remaining stack is negative")


def reduce_ledger(
    hand: LedgerHandV1,
    inputs: tuple[ReductionInputV1, ...],
    *,
    reported_total_pot: Decimal | None = None,
    promotional_payout_observed: Decimal | None = None,
) -> CanonicalLedgerV1:
    """Convenience API for one-shot deterministic reduction."""
    reducer = LedgerReducer(hand)
    for item in inputs:
        reducer.apply(item)
    return reducer.finalize(
        reported_total_pot=reported_total_pot,
        promotional_payout_observed=promotional_payout_observed,
    )


def _actor(item: ReductionInputV1) -> int:
    if item.actor_seat is None:  # Covered by model validation; keeps mypy narrow.
        raise LedgerReductionError(f"{item.verb} requires actor_seat")
    return item.actor_seat


def _required_nonnegative(value: Decimal | None, name: str) -> Decimal:
    if value is None or not value.is_finite() or value < _ZERO:
        raise LedgerReductionError(f"{name} must be a finite non-negative decimal")
    return value


def _next_street(current: StreetV1) -> StreetV1:
    if current == "river":
        return "showdown"
    return _STREET_ORDER[_STREET_ORDER.index(current) + 1]
