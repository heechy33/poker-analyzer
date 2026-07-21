from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Upload(SQLModel, table=True):
    __tablename__ = "uploads"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(index=True)
    filename: str
    storage_path: str
    sha256: str = Field(index=True)
    bytes: int | None = None
    hand_count: int | None = None
    status: str = Field(default="queued")
    error_message: str | None = None
    parse_warnings: str | None = None
    uploaded_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(index=True)
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ended_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    stake_bb: Decimal = Field(sa_column=Column(Numeric(8, 4), nullable=False))
    table_size: int = Field(sa_column=Column(SmallInteger, nullable=False))
    hands_played: int = Field(default=0)
    hero_net: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False),
    )
    hero_net_bb: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Hand(SQLModel, table=True):
    __tablename__ = "hands"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(index=True)
    upload_id: UUID = Field(foreign_key="uploads.id", index=True)
    session_id: UUID | None = Field(default=None, foreign_key="sessions.id", index=True)
    coinpoker_hand_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    played_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    table_name: str
    table_size: int = Field(sa_column=Column(SmallInteger, nullable=False))
    stake_sb: Decimal = Field(sa_column=Column(Numeric(8, 4), nullable=False))
    stake_bb: Decimal = Field(sa_column=Column(Numeric(8, 4), nullable=False))
    button_seat: int = Field(sa_column=Column(SmallInteger, nullable=False))
    hero_seat: int = Field(sa_column=Column(SmallInteger, nullable=False))
    hero_position: str
    hero_cards: list[str] = Field(sa_column=Column(ARRAY(String), nullable=False))
    flop: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    turn: str | None = None
    river: str | None = None
    total_pot: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    rake: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False),
    )
    splash_fee: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False),
    )
    hero_invested: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False),
    )
    hero_collected: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False),
    )
    hero_net: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    hero_net_bb: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    went_to_showdown: bool = Field(default=False)
    won_at_showdown: bool | None = None
    flags: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    ledger_status: str = Field(default="legacy_unbackfilled")
    ledger_version: str | None = None
    ledger_hash: str | None = None
    raw_text: str | None = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class HandPlayer(SQLModel, table=True):
    __tablename__ = "hand_players"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    hand_id: UUID = Field(foreign_key="hands.id", index=True)
    user_id: UUID = Field(index=True)
    seat: int = Field(sa_column=Column(SmallInteger, nullable=False))
    screen_name: str
    position: str | None = None
    starting_stack: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    is_hero: bool = Field(default=False)
    final_cards: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class HandAction(SQLModel, table=True):
    __tablename__ = "hand_actions"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    hand_id: UUID = Field(foreign_key="hands.id", index=True)
    user_id: UUID = Field(index=True)
    street: str
    action_order: int = Field(sa_column=Column(SmallInteger, nullable=False))
    seat: int = Field(sa_column=Column(SmallInteger, nullable=False))
    screen_name: str
    action: str
    amount: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 4)))
    raise_to: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 4)))
    is_all_in: bool = Field(default=False)
    ledger_event_index: int | None = Field(default=None, index=True)
    contribution_delta: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 4)))
    returned_delta: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 4)))
    raise_increment: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 4)))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class LlmAnalysis(SQLModel, table=True):
    __tablename__ = "llm_analyses"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(index=True)
    hand_id: UUID | None = Field(default=None, foreign_key="hands.id")
    model: str
    prompt_hash: str
    analysis_text: str
    leak_tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(String), nullable=False, server_default="{}"),
    )
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class HandLedger(SQLModel, table=True):
    """One immutable canonical-ledger payload per imported hand."""

    __tablename__ = "hand_ledgers"

    hand_id: UUID = Field(foreign_key="hands.id", primary_key=True)
    user_id: UUID = Field(index=True)
    status: str
    schema_version: str | None = None
    ledger_hash: str | None = Field(default=None, index=True)
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    summary_diff: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    failure_reason: str | None = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
