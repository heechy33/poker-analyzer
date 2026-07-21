"""P1.2 parser-to-ledger and compatibility-projection contracts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.ledger.parsed import ParsedLedgerError, ledger_from_parsed
from app.parser.coinpoker import parse_hand
from app.services.ingest import (
    actions_from_ledger,
    hand_from_parsed,
    ledger_record_from_result,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"
USER_ID = UUID("00000000-0000-0000-0000-000000000001")
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000002")
HAND_ID = UUID("00000000-0000-0000-0000-000000000003")


def _parsed(name: str):
    return parse_hand((FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines())


def test_parser_ledger_is_the_single_source_for_raise_accounting_and_projection() -> None:
    parsed = _parsed("hand_001.txt")
    ledger = ledger_from_parsed(parsed)

    raise_event = next(event for event in ledger.events if event.verb == "raise")
    assert raise_event.action_amount == Decimal("0.65")
    assert raise_event.raise_to == Decimal("0.90")
    assert raise_event.contribution_delta == Decimal("0.80")
    assert raise_event.source.line_number == 9

    projection = actions_from_ledger(ledger, HAND_ID, USER_ID)
    raise_row = next(row for row in projection if row.action == "raise")
    assert raise_row.ledger_event_index == raise_event.event_index
    assert raise_row.amount == raise_event.action_amount
    assert raise_row.contribution_delta == raise_event.contribution_delta
    assert raise_row.raise_increment == raise_event.raise_increment

    hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID, ledger=ledger)
    assert hand.ledger_status == "valid"
    assert hand.hero_invested == Decimal("0.90")
    assert hand.hero_collected == Decimal("1.15")
    assert hand.hero_net == Decimal("0.25")


def test_invalid_ledger_is_persistable_but_has_no_canonical_payload() -> None:
    parsed = _parsed("uncalled_bet.txt")
    with pytest.raises(ParsedLedgerError, match="do not reconcile"):
        ledger_from_parsed(parsed)

    failure = "awards, rake, and splash fee do not reconcile player pot"
    hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID, ledger_error=failure)
    record = ledger_record_from_result(
        hand_id=HAND_ID,
        user_id=USER_ID,
        ledger=None,
        failure_reason=failure,
    )

    assert hand.ledger_status == "invalid_ledger"
    assert hand.flags["invalid_ledger"] is True
    assert record.status == "invalid_ledger"
    assert record.payload is None
    assert record.failure_reason == failure


def test_parser_retains_ordered_return_facts_for_the_adapter() -> None:
    parsed = _parsed("uncalled_bet.txt")

    assert len(parsed.uncalled_returns) == 1
    returned = parsed.uncalled_returns[0]
    assert returned.street == "flop"
    assert returned.seat == parsed.hero_seat
    assert returned.amount == Decimal("0.50")
    assert returned.line_number > max(
        action.line_number for action in parsed.actions if action.street == "flop"
    )
