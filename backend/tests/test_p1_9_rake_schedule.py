"""P1.9 versioned CoinPoker rake contract and offline evidence checks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hands
from app.rake import (
    APPROVED_RAKE_SCHEDULE,
    OFFICIAL_SOURCE_SNAPSHOT_SHA256,
    RakeScheduleError,
    calculate_rake,
    resolve_rake_schedule,
    verify_official_source_snapshot,
)


EVIDENCE_FILES = tuple(
    sorted(
        (Path(__file__).resolve().parents[2] / "docs").glob(
            "*_2026-07-20_to_2026-07-20_Cash*.txt"
        )
    )
)


def test_approved_row_is_versioned_and_source_backed() -> None:
    assert APPROVED_RAKE_SCHEDULE.schedule_id == (
        "coinpoker-hu-nlhe-0.02-0.05-observed-2026-07-20/1"
    )
    assert APPROVED_RAKE_SCHEDULE.effective_from == date(2026, 7, 20)
    assert APPROVED_RAKE_SCHEDULE.rate == Decimal("0.05")
    assert APPROVED_RAKE_SCHEDULE.cap_chips == Decimal("0.15")
    assert APPROVED_RAKE_SCHEDULE.cap_bb == Decimal("3.0")
    assert APPROVED_RAKE_SCHEDULE.rounding_quantum == Decimal("0.01")
    assert APPROVED_RAKE_SCHEDULE.rounding_mode == "half_even"
    assert APPROVED_RAKE_SCHEDULE.source_snapshot_sha256 == OFFICIAL_SOURCE_SNAPSHOT_SHA256
    assert verify_official_source_snapshot()


def test_resolver_requires_one_exact_schedule_row() -> None:
    resolved = resolve_rake_schedule(
        played_at=datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
        stake_sb=Decimal("0.02"),
        stake_bb=Decimal("0.05"),
        game="NLHE",
        table_format="hu_2max",
        players_dealt=2,
    )
    assert resolved == APPROVED_RAKE_SCHEDULE

    with pytest.raises(RakeScheduleError, match="no uniquely evidenced") as unknown:
        resolve_rake_schedule(
            played_at=date(2026, 7, 19),
            stake_sb=Decimal("0.02"),
            stake_bb=Decimal("0.05"),
            game="NLHE",
            table_format="hu_2max",
            players_dealt=2,
        )
    assert unknown.value.code == "rake_schedule_unknown"

    with pytest.raises(RakeScheduleError) as ambiguous:
        resolve_rake_schedule(
            played_at=date(2026, 7, 20),
            stake_sb=Decimal("0.02"),
            stake_bb=Decimal("0.05"),
            game="NLHE",
            table_format="hu_2max",
            players_dealt=2,
            schedules=(APPROVED_RAKE_SCHEDULE, APPROVED_RAKE_SCHEDULE),
        )
    assert ambiguous.value.code == "rake_schedule_ambiguous"


def test_rake_math_is_decimal_no_flop_half_even_and_capped() -> None:
    assert calculate_rake(
        APPROVED_RAKE_SCHEDULE,
        player_contributed_pot=Decimal("100"),
        flop_dealt=False,
    ).rake == Decimal("0")
    # 0.10 * 5% = 0.005: half-even rounds to the even cent, 0.00.
    assert calculate_rake(
        APPROVED_RAKE_SCHEDULE,
        player_contributed_pot=Decimal("0.10"),
        flop_dealt=True,
    ).rake == Decimal("0.00")
    assert calculate_rake(
        APPROVED_RAKE_SCHEDULE,
        player_contributed_pot=Decimal("0.30"),
        flop_dealt=True,
    ).rake == Decimal("0.02")
    capped = calculate_rake(
        APPROVED_RAKE_SCHEDULE,
        player_contributed_pot=Decimal("6.20"),
        flop_dealt=True,
    )
    assert capped.rake == Decimal("0.15")
    assert capped.capped is True


def test_splash_fee_and_drop_are_explicitly_excluded() -> None:
    with pytest.raises(RakeScheduleError) as fee:
        calculate_rake(
            APPROVED_RAKE_SCHEDULE,
            player_contributed_pot=Decimal("1.00"),
            flop_dealt=True,
            splash_fee=Decimal("0.01"),
        )
    assert fee.value.code == "excluded_splash_fee"

    with pytest.raises(RakeScheduleError) as drop:
        calculate_rake(
            APPROVED_RAKE_SCHEDULE,
            player_contributed_pot=Decimal("1.00"),
            flop_dealt=True,
            splash_drop=Decimal("0.40"),
        )
    assert drop.value.code == "excluded_splash_drop"


@pytest.mark.skipif(not EVIDENCE_FILES, reason="private 85-hand evidence is not available")
def test_private_85_hand_evidence_reconciles_supported_row_and_fails_other_stake_closed() -> None:
    evidence = EVIDENCE_FILES[0]
    hands = list(parse_hands(evidence.read_text(encoding="utf-8").splitlines()))
    assert len(hands) == 85
    assert sum(hand.flop is not None for hand in hands) == 50
    assert sum(hand.flop is None for hand in hands) == 35

    supported = [hand for hand in hands if hand.stake_sb == Decimal("0.02")]
    assert len(supported) == 52
    assert sum(hand.flop is not None for hand in supported) == 30
    assert sum(hand.flop is None for hand in supported) == 22
    assert sum(bool(hand.splash_drops) for hand in hands) == 1

    for hand in supported:
        schedule = resolve_rake_schedule(
            played_at=hand.played_at,
            stake_sb=hand.stake_sb,
            stake_bb=hand.stake_bb,
            game="NLHE",
            table_format="hu_2max",
            players_dealt=2,
        )
        ledger = ledger_from_parsed(hand)
        player_pot = ledger.events[-1].state_after.player_contributed_pot
        calculation = calculate_rake(
            schedule,
            player_contributed_pot=player_pot,
            flop_dealt=hand.flop is not None,
        )
        assert calculation.rake == hand.rake

    unsupported_stake = next(hand for hand in hands if hand.stake_bb == Decimal("0.10"))
    with pytest.raises(RakeScheduleError) as unknown:
        resolve_rake_schedule(
            played_at=unsupported_stake.played_at,
            stake_sb=unsupported_stake.stake_sb,
            stake_bb=unsupported_stake.stake_bb,
            game="NLHE",
            table_format="hu_2max",
            players_dealt=2,
        )
    assert unknown.value.code == "rake_schedule_unknown"
