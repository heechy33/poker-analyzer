"""Versioned CoinPoker rake policy and fail-closed resolution.

The parser's summary rake is an observed settlement fact. This module holds
the separately versioned policy used by future decision-state construction.
It intentionally covers only the approved 0.02/0.05 observed-date row.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


RAKE_SCHEDULE_VERSION = "coinpoker-rake-schedule/1"
RakeErrorCode = Literal[
    "rake_schedule_unknown",
    "rake_schedule_ambiguous",
    "excluded_splash_fee",
    "excluded_splash_drop",
]

_ZERO = Decimal("0")

# Compact normalized snapshot of the official policy facts used by the row.
OFFICIAL_SOURCE_SNAPSHOT = (
    "CoinPoker Rake Guide: Learn About Our Poker Fees\n"
    "URL: https://coinpoker.com/rake/\n"
    "Last Updated: 25 June 2026\n"
    "Cash Games: 5%\n"
    "Heads Up Games, $0.02/$0.05: 2 Player Cap $0.15; 2 Player Cap 3.00BB\n"
    "Splash Pots: 0.1BB fee per hand on tables where Splash Pots are active; "
    "charged per pot; applies whether the hand ends preflop or postflop.\n"
)
OFFICIAL_SOURCE_SNAPSHOT_SHA256 = (
    "89bd21a1fe482c65193b3dd173d201b5b57defe65ebc573d7bb60c3e66deb582"
)


class RakeScheduleError(ValueError):
    """A rake policy cannot be selected or applied safely."""

    def __init__(self, code: RakeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class RakeScheduleV1(BaseModel):
    """One immutable, source-backed rake schedule row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coinpoker-rake-schedule/1"] = RAKE_SCHEDULE_VERSION
    schedule_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_updated: date
    effective_from: date
    effective_to: date | None = None
    game: Literal["NLHE"]
    stake_sb: Decimal
    stake_bb: Decimal
    table_format: Literal["hu_2max", "6max", "9max"]
    player_count_rule: Literal["heads_up_two_player_cap"]
    rate: Decimal
    cap_chips: Decimal
    cap_bb: Decimal
    rake_basis: Literal["net_player_contributions_after_returns"]
    rounding_quantum: Decimal
    rounding_mode: Literal["half_even"]
    no_flop_rule: Literal["zero"]
    splash_fee_policy: Literal["exclude_nonzero"]
    splash_drop_policy: Literal["exclude_nonzero_outside_rake_basis"]
    review_status: Literal["approved"]
    source_snapshot_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _validate_schedule(self) -> RakeScheduleV1:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        if self.stake_sb < _ZERO or self.stake_bb <= _ZERO:
            raise ValueError("stakes must be non-negative and big blind must be positive")
        if self.cap_chips <= _ZERO or self.cap_bb <= _ZERO:
            raise ValueError("rake caps must be positive")
        if not _ZERO <= self.rate <= Decimal("1"):
            raise ValueError("rate must be between zero and one")
        if self.rounding_quantum <= _ZERO:
            raise ValueError("rounding_quantum must be positive")
        if self.cap_chips != self.cap_bb * self.stake_bb:
            raise ValueError("chip and BB caps must agree at the exact stake")
        if len(self.source_snapshot_sha256) != 64:
            raise ValueError("source_snapshot_sha256 must be a SHA-256 digest")
        return self

    def applies_to(
        self,
        *,
        played_on: date,
        stake_sb: Decimal,
        stake_bb: Decimal,
        game: str,
        table_format: str,
        players_dealt: int,
    ) -> bool:
        return (
            self.effective_from <= played_on
            and (self.effective_to is None or played_on < self.effective_to)
            and self.stake_sb == stake_sb
            and self.stake_bb == stake_bb
            and self.game == game
            and self.table_format == table_format
            and self.player_count_rule == "heads_up_two_player_cap"
            and players_dealt == 2
        )


class RakeCalculationV1(BaseModel):
    """The deterministic fee result, retaining the policy basis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: str
    basis_amount: Decimal
    rake: Decimal
    capped: bool
    no_flop: bool


APPROVED_RAKE_SCHEDULE = RakeScheduleV1(
    schedule_id="coinpoker-hu-nlhe-0.02-0.05-observed-2026-07-20/1",
    source_url="https://coinpoker.com/rake/",
    source_updated=date(2026, 6, 25),
    effective_from=date(2026, 7, 20),
    game="NLHE",
    stake_sb=Decimal("0.02"),
    stake_bb=Decimal("0.05"),
    table_format="hu_2max",
    player_count_rule="heads_up_two_player_cap",
    rate=Decimal("0.05"),
    cap_chips=Decimal("0.15"),
    cap_bb=Decimal("3.0"),
    rake_basis="net_player_contributions_after_returns",
    rounding_quantum=Decimal("0.01"),
    rounding_mode="half_even",
    no_flop_rule="zero",
    splash_fee_policy="exclude_nonzero",
    splash_drop_policy="exclude_nonzero_outside_rake_basis",
    review_status="approved",
    source_snapshot_sha256=OFFICIAL_SOURCE_SNAPSHOT_SHA256,
)

RAKE_SCHEDULES: tuple[RakeScheduleV1, ...] = (APPROVED_RAKE_SCHEDULE,)


def resolve_rake_schedule(
    *,
    played_at: date | datetime,
    stake_sb: Decimal,
    stake_bb: Decimal,
    game: str,
    table_format: str,
    players_dealt: int,
    schedules: Sequence[RakeScheduleV1] = RAKE_SCHEDULES,
) -> RakeScheduleV1:
    """Resolve exactly one row; never choose a nearest or fallback schedule."""
    played_on = played_at.date() if isinstance(played_at, datetime) else played_at
    matches = tuple(
        schedule
        for schedule in schedules
        if schedule.applies_to(
            played_on=played_on,
            stake_sb=stake_sb,
            stake_bb=stake_bb,
            game=game,
            table_format=table_format,
            players_dealt=players_dealt,
        )
    )
    if not matches:
        raise RakeScheduleError(
            "rake_schedule_unknown",
            "no uniquely evidenced rake schedule matches the hand facts",
        )
    if len(matches) != 1:
        raise RakeScheduleError(
            "rake_schedule_ambiguous",
            "multiple rake schedules match the hand facts",
        )
    return matches[0]


def calculate_rake(
    schedule: RakeScheduleV1,
    *,
    player_contributed_pot: Decimal,
    flop_dealt: bool,
    splash_fee: Decimal = _ZERO,
    splash_drop: Decimal = _ZERO,
) -> RakeCalculationV1:
    """Calculate rake from net player money, with explicit exclusion gates."""
    if player_contributed_pot < _ZERO:
        raise ValueError("player_contributed_pot must be non-negative")
    if splash_fee < _ZERO or splash_drop < _ZERO:
        raise ValueError("splash amounts must be non-negative")
    if splash_fee != _ZERO:
        raise RakeScheduleError(
            "excluded_splash_fee",
            "non-zero splash fee is excluded from the initial rake cohort",
        )
    if splash_drop != _ZERO:
        raise RakeScheduleError(
            "excluded_splash_drop",
            "splash drop is outside the player rake basis and excluded from the initial cohort",
        )

    if not flop_dealt:
        return RakeCalculationV1(
            schedule_id=schedule.schedule_id,
            basis_amount=player_contributed_pot,
            rake=_ZERO,
            capped=False,
            no_flop=True,
        )

    uncapped = player_contributed_pot * schedule.rate
    capped = uncapped > schedule.cap_chips
    raw_rake = min(uncapped, schedule.cap_chips)
    rake = raw_rake.quantize(schedule.rounding_quantum, rounding=ROUND_HALF_EVEN)
    return RakeCalculationV1(
        schedule_id=schedule.schedule_id,
        basis_amount=player_contributed_pot,
        rake=rake,
        capped=capped,
        no_flop=False,
    )


def verify_official_source_snapshot() -> bool:
    """Return whether the checked-in source evidence has not been edited."""
    return (
        hashlib.sha256(OFFICIAL_SOURCE_SNAPSHOT.encode("utf-8")).hexdigest()
        == OFFICIAL_SOURCE_SNAPSHOT_SHA256
    )
