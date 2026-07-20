from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Street = Literal["flop", "turn", "river"]
TableFormat = Literal["hu_2max", "6max", "9max"]
UploadStatus = Literal["queued", "parsing", "parsed", "error"]


class HandPlayerOut(BaseModel):
    seat: int
    screen_name: str
    position: str | None
    starting_stack: Decimal
    is_hero: bool
    final_cards: list[str] | None = None


class HandActionOut(BaseModel):
    street: str
    action_order: int
    seat: int
    screen_name: str
    action: str
    amount: Decimal | None = None
    raise_to: Decimal | None = None
    is_all_in: bool = False


class HandSummary(BaseModel):
    id: str
    coinpoker_hand_id: int
    played_at: datetime
    table_name: str
    table_format: TableFormat
    stake_sb: Decimal
    stake_bb: Decimal
    hero_position: str
    hero_cards: list[str]
    hero_net: Decimal
    hero_net_bb: Decimal
    went_to_showdown: bool
    total_pot: Decimal


class HandDetail(HandSummary):
    upload_id: str
    session_id: str | None
    button_seat: int
    hero_seat: int
    flop: list[str] | None
    turn: str | None
    river: str | None
    rake: Decimal
    splash_fee: Decimal
    hero_invested: Decimal
    hero_collected: Decimal
    won_at_showdown: bool | None
    flags: dict[str, Any]
    raw_text: str | None
    players: list[HandPlayerOut]
    actions: list[HandActionOut]


class UploadResponse(BaseModel):
    id: str
    filename: str
    status: str
    hand_count: int | None = None
    error_message: str | None = None
    parse_warnings: str | None = None
    bytes: int | None = None
    uploaded_at: datetime | None = None


class PresignResponse(UploadResponse):
    signed_url: str | None = None
    token: str | None = None
    path: str | None = None
    deduplicated: bool = False


class PresignUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    bytes: int | None = Field(default=None, ge=0)
    sha256: str = Field(min_length=64, max_length=64)

    @field_validator("sha256")
    @classmethod
    def _lowercase_sha256(cls, value: str) -> str:
        return value.lower()


class CompleteUploadRequest(BaseModel):
    raw_content: str | None = Field(default=None, min_length=1)


class HandsListParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    order: str = Field(default="played_at.desc")
    position: str | None = None
    since: date | None = None
    only_losses: bool = False
    table_format: TableFormat | None = None
    stakes: str | None = None

    @field_validator("order")
    @classmethod
    def _validate_order(cls, value: str) -> str:
        allowed_fields = {"played_at", "hero_net", "hero_net_bb", "total_pot"}
        parts = value.split(".", 1)
        if len(parts) != 2:
            raise ValueError("order must be field.direction")
        field_name, direction = parts
        if field_name not in allowed_fields:
            raise ValueError(f"unsupported order field: {field_name}")
        if direction not in {"asc", "desc"}:
            raise ValueError("order direction must be asc or desc")
        return value


class StatsSummaryResponse(BaseModel):
    hands_count: int
    vpip_pct: float
    pfr_pct: float
    three_bet_pct: float
    wtsd_pct: float
    wsd_pct: float
    bb_per_100: float


class PositionStatsRow(BaseModel):
    position: str
    hands: int
    vpip_pct: float
    pfr_pct: float
    three_bet_pct: float
    wtsd_pct: float
    wsd_pct: float
    bb_per_100: float


# Kept for backward compatibility — callers should migrate to StatsSummaryResponse.
StatsResponse = StatsSummaryResponse


class AnalyzeHandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    street: Street


class AnalyzeHandResponse(BaseModel):
    id: str
    hand_id: str
    model: str
    prompt_hash: str
    analysis: str
    leak_tags: list[str]
    cached: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime


class AnalysisListItem(BaseModel):
    id: str
    hand_id: str
    model: str
    prompt_hash: str
    analysis: str
    leak_tags: list[str]
    created_at: datetime


class StakeOption(BaseModel):
    sb: str
    bb: str
    label: str


class FilterOptionsResponse(BaseModel):
    stakes: list[StakeOption]
    table_formats: list[TableFormat] = Field(
        default_factory=lambda: ["hu_2max", "6max", "9max"]
    )


class LeakTagRow(BaseModel):
    """A single aggregated leak pattern surfaced from LLM analyses.

    ``tag`` is the raw tag string (e.g. ``overfold_turn``).
    ``count`` is the number of analyses where this tag appeared.
    ``pct_of_analyses`` is the percentage of total analyses (in the timeframe)
    that contained this tag, rounded to one decimal place.
    """

    tag: str
    count: int
    pct_of_analyses: float
