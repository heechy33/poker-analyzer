"""P1.1 canonical, versioned ledger data contract.

This module defines data only.  It deliberately does not translate legacy
``hand_actions`` rows or reconstruct chip accounting; P1.2/P1.3 own those
responsibilities.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LEDGER_SCHEMA_V1 = "coinpoker-nlhe-ledger/1"

TableFormat = Literal["hu_2max", "6max", "9max"]
StreetV1 = Literal["preflop", "flop", "turn", "river", "showdown"]
LedgerVerbV1 = Literal[
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
    "splash_drop",
    "street_transition",
    "return_uncalled",
    "collect",
    "fee_summary",
]
LineTypeV1 = Literal[
    "header",
    "table",
    "seat",
    "dealt",
    "post",
    "action",
    "board",
    "summary",
    "derived",
]

_CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")


def _require_nonnegative(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite() or value < _ZERO:
        raise ValueError(f"{field_name} must be a finite non-negative decimal")
    return value


class LedgerModel(BaseModel):
    """Strict base model that preserves Decimal JSON values as chip strings."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceProvenanceV1(LedgerModel):
    line_number: int = Field(ge=1)
    line_type: LineTypeV1
    raw_tokens: dict[str, str] = Field(default_factory=dict)


class DealtProvenanceV1(LedgerModel):
    dealt_in: bool
    source: SourceProvenanceV1
    inferred: bool = False


class LedgerPlayerV1(LedgerModel):
    seat: int = Field(ge=1)
    alias: str = Field(min_length=1)
    position: str = Field(min_length=1)
    starting_stack: Decimal
    is_hero: bool = False
    dealt: DealtProvenanceV1
    decision_cards: tuple[str, str] | None = None

    @model_validator(mode="after")
    def _validate_player(self) -> LedgerPlayerV1:
        _require_nonnegative(self.starting_stack, "starting_stack")
        if self.decision_cards is not None:
            if len(set(self.decision_cards)) != 2 or any(
                not _CARD_RE.fullmatch(card) for card in self.decision_cards
            ):
                raise ValueError("decision_cards must contain two distinct valid cards")
        return self


class BoardRunsV1(LedgerModel):
    """First-run and optional second-run boards stay isolated by contract."""

    first_run: tuple[str, ...] = ()
    second_run: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_runs(self) -> BoardRunsV1:
        for name, cards in (("first_run", self.first_run), ("second_run", self.second_run)):
            if len(cards) > 5 or len(set(cards)) != len(cards):
                raise ValueError(f"{name} must contain at most five distinct cards")
            if any(not _CARD_RE.fullmatch(card) for card in cards):
                raise ValueError(f"{name} contains an invalid card")
        return self


class FeeMetadataV1(LedgerModel):
    observed_rake: Decimal = _ZERO
    splash_fee: Decimal = _ZERO
    promotional_drop: Decimal = _ZERO
    schedule_id: str | None = None

    @model_validator(mode="after")
    def _validate_fees(self) -> FeeMetadataV1:
        for name in ("observed_rake", "splash_fee", "promotional_drop"):
            _require_nonnegative(getattr(self, name), name)
        return self


class LedgerHandV1(LedgerModel):
    """Immutable table/hand facts available to the ledger reducer."""

    schema_version: Literal["coinpoker-nlhe-ledger/1"] = LEDGER_SCHEMA_V1
    raw_hand_id: str = Field(min_length=1)
    played_at: datetime
    game: Literal["NLHE"]
    table_marker: str = Field(min_length=1)
    table_format: TableFormat
    button_seat: int = Field(ge=1)
    small_blind: Decimal
    big_blind: Decimal
    players: tuple[LedgerPlayerV1, ...] = Field(min_length=2)
    board_runs: BoardRunsV1 = Field(default_factory=BoardRunsV1)
    flags: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _validate_hand(self) -> LedgerHandV1:
        _require_nonnegative(self.small_blind, "small_blind")
        _require_nonnegative(self.big_blind, "big_blind")
        if self.big_blind == _ZERO:
            raise ValueError("big_blind must be greater than zero")
        seats = [player.seat for player in self.players]
        if len(set(seats)) != len(seats):
            raise ValueError("players must have unique seats")
        if self.button_seat not in seats:
            raise ValueError("button_seat must identify a seated player")
        if sum(player.is_hero for player in self.players) != 1:
            raise ValueError("exactly one player must be marked hero")
        if self.table_format == "hu_2max" and len(self.players) != 2:
            raise ValueError("hu_2max requires exactly two seated players")
        return self


class LegalRaiseBoundsV1(LedgerModel):
    min_raise_to: Decimal | None = None
    max_raise_to: Decimal | None = None
    action_reopened: bool = False

    @model_validator(mode="after")
    def _validate_bounds(self) -> LegalRaiseBoundsV1:
        for name in ("min_raise_to", "max_raise_to"):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative(value, name)
        if (
            self.min_raise_to is not None
            and self.max_raise_to is not None
            and self.min_raise_to > self.max_raise_to
        ):
            raise ValueError("min_raise_to cannot exceed max_raise_to")
        return self


class PlayerStateV1(LedgerModel):
    seat: int = Field(ge=1)
    active: bool
    folded: bool
    all_in: bool
    street_contribution: Decimal
    total_contribution: Decimal
    remaining_stack: Decimal

    @model_validator(mode="after")
    def _validate_player_state(self) -> PlayerStateV1:
        for name in ("street_contribution", "total_contribution", "remaining_stack"):
            _require_nonnegative(getattr(self, name), name)
        if self.active and self.folded:
            raise ValueError("a player cannot be active and folded")
        if self.all_in and not self.active:
            raise ValueError("an all-in player must remain active")
        return self


class LedgerStateV1(LedgerModel):
    """Deterministic state immediately before or after one ledger event."""

    street: StreetV1
    active_seats: frozenset[int] = frozenset()
    folded_seats: frozenset[int] = frozenset()
    all_in_seats: frozenset[int] = frozenset()
    players_reached_flop: frozenset[int] = frozenset()
    player_states: tuple[PlayerStateV1, ...]
    amount_to_call: Decimal
    last_full_raise: Decimal
    legal_raise_bounds: LegalRaiseBoundsV1 = Field(default_factory=LegalRaiseBoundsV1)
    player_contributed_pot: Decimal
    promotional_drop: Decimal = _ZERO
    board: BoardRunsV1 = Field(default_factory=BoardRunsV1)
    fee_metadata: FeeMetadataV1 = Field(default_factory=FeeMetadataV1)

    @model_validator(mode="after")
    def _validate_state(self) -> LedgerStateV1:
        for name in (
            "amount_to_call",
            "last_full_raise",
            "player_contributed_pot",
            "promotional_drop",
        ):
            _require_nonnegative(getattr(self, name), name)
        state_seats = [state.seat for state in self.player_states]
        if len(set(state_seats)) != len(state_seats):
            raise ValueError("player_states must have unique seats")
        known = frozenset(state_seats)
        for name, seats in (
            ("active_seats", self.active_seats),
            ("folded_seats", self.folded_seats),
            ("all_in_seats", self.all_in_seats),
            ("players_reached_flop", self.players_reached_flop),
        ):
            if not seats <= known:
                raise ValueError(f"{name} contains a seat without player state")
        if self.active_seats & self.folded_seats:
            raise ValueError("active_seats and folded_seats must be disjoint")
        if not self.all_in_seats <= self.active_seats:
            raise ValueError("all_in_seats must be active")
        return self


class LedgerEventV1(LedgerModel):
    """One normalized raw, action, transition, or settlement event."""

    event_index: int = Field(ge=0)
    street_event_index: int | None = Field(default=None, ge=0)
    street: StreetV1
    source: SourceProvenanceV1
    actor_seat: int | None = Field(default=None, ge=1)
    verb: LedgerVerbV1
    action_amount: Decimal | None = None
    contribution_delta: Decimal = _ZERO
    promotional_delta: Decimal = _ZERO
    returned_delta: Decimal = _ZERO
    raise_to: Decimal | None = None
    raise_increment: Decimal | None = None
    is_all_in: bool = False
    pot_award_id: str | None = None
    award_amount: Decimal = _ZERO
    raw_tokens: dict[str, str] = Field(default_factory=dict)
    state_before: LedgerStateV1
    state_after: LedgerStateV1

    @model_validator(mode="after")
    def _validate_event(self) -> LedgerEventV1:
        for name in (
            "contribution_delta",
            "promotional_delta",
            "returned_delta",
            "award_amount",
        ):
            _require_nonnegative(getattr(self, name), name)
        for name in ("action_amount", "raise_to", "raise_increment"):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative(value, name)
        if self.verb == "splash_drop" and self.promotional_delta == _ZERO:
            raise ValueError("splash_drop requires promotional_delta")
        if self.verb == "collect" and not self.pot_award_id:
            raise ValueError("collect requires pot_award_id")
        if self.verb != "collect" and self.award_amount != _ZERO:
            raise ValueError("award_amount is only valid for collect")
        if self.is_all_in and self.verb not in {"call", "bet", "raise"}:
            raise ValueError("is_all_in is only valid for call, bet, or raise")
        if self.verb == "raise" and self.raise_to is None:
            raise ValueError("raise requires raise_to")
        if self.verb != "raise" and self.raise_increment is not None:
            raise ValueError("raise_increment is only valid for raise")
        return self


class SettlementV1(LedgerModel):
    reported_total_pot: Decimal | None = None
    reconciliation_status: Literal["pending", "reconciled", "unreconciled"] = "pending"
    promotional_payout_observed: Decimal | None = None

    @model_validator(mode="after")
    def _validate_settlement(self) -> SettlementV1:
        for name in ("reported_total_pot", "promotional_payout_observed"):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative(value, name)
        return self


class CanonicalLedgerV1(LedgerModel):
    """A complete ledger whose hash covers every canonical field but itself."""

    schema_version: Literal["coinpoker-nlhe-ledger/1"] = LEDGER_SCHEMA_V1
    hand: LedgerHandV1
    events: tuple[LedgerEventV1, ...]
    settlement: SettlementV1 = Field(default_factory=SettlementV1)
    ledger_hash: str = Field(min_length=64, max_length=64)

    @classmethod
    def create(
        cls,
        *,
        hand: LedgerHandV1,
        events: tuple[LedgerEventV1, ...],
        settlement: SettlementV1 | None = None,
    ) -> CanonicalLedgerV1:
        """Build a validated ledger with a deterministic SHA-256 content hash."""
        settlement = settlement or SettlementV1()
        payload = {
            "schema_version": LEDGER_SCHEMA_V1,
            "hand": hand.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "settlement": settlement.model_dump(mode="json"),
        }
        ledger_hash = _hash_payload(payload)
        return cls(
            hand=hand,
            events=events,
            settlement=settlement,
            ledger_hash=ledger_hash,
        )

    def computed_hash(self) -> str:
        return _hash_payload(self._payload_without_hash())

    def _payload_without_hash(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"ledger_hash"})

    @model_validator(mode="after")
    def _validate_ledger(self) -> CanonicalLedgerV1:
        if not _HASH_RE.fullmatch(self.ledger_hash):
            raise ValueError("ledger_hash must be a lowercase SHA-256 hex digest")
        indexes = [event.event_index for event in self.events]
        if indexes != list(range(len(self.events))):
            raise ValueError("event_index values must be contiguous and start at zero")
        if self.ledger_hash != self.computed_hash():
            raise ValueError("ledger_hash does not match canonical ledger content")
        return self


def _hash_payload(payload: dict[str, object]) -> str:
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
