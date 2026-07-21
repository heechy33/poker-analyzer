"""Local evidence audit for the real Phase 1 acceptance candidates."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from app.eligibility import assess_eligibility
from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hands


EVIDENCE_FILES = tuple(
    sorted(
        (Path(__file__).resolve().parents[2] / "docs").glob(
            "*_2026-07-21_to_2026-07-21_Cash*.txt"
        )
    )
)


@pytest.mark.skipif(not EVIDENCE_FILES, reason="private July 21 evidence is not available")
def test_real_docs_confirm_the_approved_2_4bb_cohort_and_reject_3bb() -> None:
    hands = list(parse_hands(EVIDENCE_FILES[0].read_text(encoding="utf-8").splitlines()))
    candidates: list[tuple[int, Decimal, str, tuple[str, ...]]] = []

    for hand in hands:
        ledger = ledger_from_parsed(hand)
        hero_seat = next(player.seat for player in ledger.hand.players if player.is_hero)
        dealt = [player for player in ledger.hand.players if player.dealt.dealt_in]
        effective_stack_bb = min(player.starting_stack for player in dealt) / ledger.hand.big_blind
        preflop = tuple(
            event
            for event in ledger.events
            if event.street == "preflop"
            and event.verb in {"fold", "check", "call", "bet", "raise"}
        )
        if not (
            ledger.hand.table_format == "hu_2max"
            and len(dealt) == 2
            and effective_stack_bb == Decimal("100")
            and len(preflop) == 2
            and preflop[0].verb == "raise"
            and preflop[1].verb == "call"
            and preflop[0].actor_seat == ledger.hand.button_seat
            and hand.flop is not None
            and any(
                event.street == "flop"
                and event.actor_seat == hero_seat
                and event.verb in {"fold", "check", "call", "bet", "raise"}
                for event in ledger.events
            )
            and hand.splash_fee == Decimal("0")
            and not hand.splash_drops
        ):
            continue

        open_to_bb = preflop[0].raise_to / ledger.hand.big_blind
        result = assess_eligibility(ledger)
        assert all(not player.dealt.inferred for player in dealt)

        candidates.append(
            (hand.coinpoker_hand_id, open_to_bb, result.status, result.reason_codes)
        )

    assert len(candidates) == 5
    assert Counter(open_to for _, open_to, _, _ in candidates) == Counter(
        {Decimal("2.4"): 3, Decimal("3.0"): 2}
    )
    assert all(
        status == "supported" and reasons == ()
        for _, open_to, status, reasons in candidates
        if open_to == Decimal("2.4")
    )
    assert all(
        status == "unsupported" and reasons == ("unsupported_open_size",)
        for _, open_to, status, reasons in candidates
        if open_to == Decimal("3.0")
    )
