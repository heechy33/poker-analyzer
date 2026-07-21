"""P1.4 raw-history golden states for the anonymized real HUNL evidence subset."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hand, parse_hands


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "p1_4" / "real_hunl_evidence_subset.txt"

COINPOKER_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"

def test_real_mixed_stake_hunl_subset_has_exact_canonical_action_boundaries() -> None:
    parsed_hands = list(parse_hands(FIXTURE.read_text(encoding="utf-8").splitlines()))
    assert [(hand.stake_sb, hand.stake_bb) for hand in parsed_hands] == [
        (Decimal("0.02"), Decimal("0.05")),
        (Decimal("0.05"), Decimal("0.10")),
    ]

    returned_bet, splash_hand = (ledger_from_parsed(hand) for hand in parsed_hands)

    returned = next(event for event in returned_bet.events if event.verb == "return_uncalled")
    assert (returned.source.line_number, returned.returned_delta) == (20, Decimal("0.10"))
    assert returned.state_after.player_contributed_pot == Decimal("0.20")
    assert returned_bet.settlement.reconciliation_status == "reconciled"

    splash = next(event for event in splash_hand.events if event.verb == "splash_drop")
    assert (splash.source.line_number, splash.promotional_delta) == (5, Decimal("0.40"))
    assert splash.state_after.player_contributed_pot == Decimal("0")
    assert splash.state_after.promotional_drop == Decimal("0.40")
    assert splash_hand.events[-1].state_after.fee_metadata.splash_fee == Decimal("0.01")
    assert splash_hand.settlement.reported_total_pot == Decimal("1.20")
    assert splash_hand.settlement.reconciliation_status == "unreconciled"

def test_existing_raw_all_in_fixture_preserves_raise_to_and_contribution() -> None:
    parsed = parse_hand((COINPOKER_FIXTURE_DIR / "all_in_preflop.txt").read_text(encoding="utf-8").splitlines())
    ledger = ledger_from_parsed(parsed)

    all_in = next(event for event in ledger.events if event.verb == "raise" and event.is_all_in)
    assert all_in.action_amount == Decimal("4.50")
    assert all_in.raise_to == Decimal("5.00")
    assert all_in.contribution_delta == Decimal("4.50")
    assert ledger.settlement.reconciliation_status == "reconciled"
