from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Street = Literal["preflop", "flop", "turn", "river", "showdown"]
Action = Literal[
    "post_sb",
    "post_bb",
    "post_ante",
    "fold",
    "check",
    "call",
    "bet",
    "raise",
    "all_in",
    "show",
    "muck",
    "collect",
]

MONEY_QUANT = Decimal("0.0001")
CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT)


def _validate_cards(cards: list[str], expected_count: int | None = None) -> list[str]:
    if expected_count is not None and len(cards) != expected_count:
        raise ValueError(f"expected {expected_count} cards")
    invalid = [card for card in cards if not CARD_RE.fullmatch(card)]
    if invalid:
        raise ValueError(f"invalid cards: {', '.join(invalid)}")
    return cards


class ParsedPlayer(BaseModel):
    seat: int = Field(ge=1)
    screen_name: str = Field(min_length=1)
    starting_stack: Decimal
    position: str = Field(min_length=1)
    is_hero: bool = False
    final_cards: list[str] | None = None

    @field_validator("starting_stack")
    @classmethod
    def _quantize_starting_stack(cls, value: Decimal) -> Decimal:
        return quantize_money(value)

    @field_validator("final_cards")
    @classmethod
    def _validate_final_cards(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_cards(value, expected_count=2)


class ParsedAction(BaseModel):
    street: Street
    action_order: int = Field(ge=0)
    seat: int = Field(ge=1)
    screen_name: str = Field(min_length=1)
    action: Action
    amount: Decimal | None = None
    raise_to: Decimal | None = None
    is_all_in: bool = False
    line_number: int = Field(default=1, ge=1, exclude=True)
    pot_award_id: str | None = Field(default=None, exclude=True)

    @field_validator("amount", "raise_to")
    @classmethod
    def _quantize_optional_money(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return quantize_money(value)


class ParsedReturn(BaseModel):
    """An uncalled-chip return retained as an ordered parser fact."""

    street: Street
    seat: int = Field(ge=1)
    screen_name: str = Field(min_length=1)
    amount: Decimal
    line_number: int = Field(default=1, ge=1, exclude=True)

    @field_validator("amount")
    @classmethod
    def _quantize_amount(cls, value: Decimal) -> Decimal:
        return quantize_money(value)


class ParsedSplashDrop(BaseModel):
    """A non-player promotional pot inflow retained in raw source order."""

    amount: Decimal
    line_number: int = Field(default=1, ge=1, exclude=True)

    @field_validator("amount")
    @classmethod
    def _quantize_amount(cls, value: Decimal) -> Decimal:
        return quantize_money(value)


class ParsedHand(BaseModel):
    coinpoker_hand_id: int
    played_at: datetime
    table_name: str
    table_size: int = Field(ge=2)
    stake_sb: Decimal
    stake_bb: Decimal
    button_seat: int = Field(ge=1)
    hero_seat: int = Field(ge=1)
    hero_position: str
    hero_cards: list[str] = Field(min_length=2, max_length=2)
    flop: list[str] | None = None
    turn: str | None = None
    river: str | None = None
    total_pot: Decimal
    rake: Decimal
    splash_fee: Decimal
    hero_invested: Decimal
    hero_collected: Decimal
    hero_net: Decimal
    hero_net_bb: Decimal
    went_to_showdown: bool
    won_at_showdown: bool | None
    flags: dict[str, Any] = Field(default_factory=dict)
    raw_text: str
    players: list[ParsedPlayer]
    actions: list[ParsedAction]
    # Raw CoinPoker exports may explicitly list every dealt player without
    # revealing opponents' cards. Keep this provenance out of legacy parser
    # serialization while making it available to the canonical ledger.
    dealt_player_lines: dict[str, int] = Field(default_factory=dict, exclude=True)
    uncalled_returns: list[ParsedReturn] = Field(default_factory=list, exclude=True)

    splash_drops: list[ParsedSplashDrop] = Field(default_factory=list, exclude=True)
    @field_validator(
        "stake_sb",
        "stake_bb",
        "total_pot",
        "rake",
        "splash_fee",
        "hero_invested",
        "hero_collected",
        "hero_net",
        "hero_net_bb",
    )
    @classmethod
    def _quantize_money_fields(cls, value: Decimal) -> Decimal:
        return quantize_money(value)

    @field_validator("played_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("played_at must be timezone-aware")
        return value

    @field_validator("table_size")
    @classmethod
    def _validate_table_size(cls, value: int) -> int:
        if value not in {2, 6, 9}:
            raise ValueError("table_size must be 2, 6, or 9")
        return value

    @field_validator("hero_cards")
    @classmethod
    def _validate_hero_cards(cls, value: list[str]) -> list[str]:
        return _validate_cards(value, expected_count=2)

    @field_validator("flop")
    @classmethod
    def _validate_flop(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_cards(value, expected_count=3)

    @field_validator("turn", "river")
    @classmethod
    def _validate_board_card(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _validate_cards([value], expected_count=1)
        return value
