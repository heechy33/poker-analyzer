"""P1.2 raw-history backfill contracts that do not require a database."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hand
from app.services.ingest import (
    _apply_canonical_hand_values,
    _backfill_ledger_input,
    _summary_diff,
    hand_from_parsed,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coinpoker"
USER_ID = UUID("00000000-0000-0000-0000-000000000001")
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000002")


def _parsed(name: str):
    return parse_hand((FIXTURES / name).read_text(encoding="utf-8").splitlines())


def test_backfill_replaces_legacy_raise_summary_from_raw_ledger() -> None:
    parsed = _parsed("hand_001.txt")
    legacy_hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID)
    ledger = ledger_from_parsed(parsed)

    diff = _summary_diff(legacy_hand, parsed, ledger)
    assert diff == {
        "hero_invested": {"stored": "0.7500", "canonical": "0.9000"},
        "hero_net": {"stored": "0.4000", "canonical": "0.2500"},
        "hero_net_bb": {"stored": "1.6000", "canonical": "1"},
    }

    _apply_canonical_hand_values(legacy_hand, parsed, ledger)

    assert legacy_hand.ledger_status == "valid"
    assert legacy_hand.ledger_hash == ledger.ledger_hash
    assert legacy_hand.hero_invested == Decimal("0.90")
    assert legacy_hand.hero_net == Decimal("0.25")


def test_backfill_uses_raw_text_and_rejects_missing_or_mismatched_raw_history() -> None:
    parsed = _parsed("hand_001.txt")
    hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID)

    reparsed, ledger, error = _backfill_ledger_input(hand)
    assert reparsed is not None
    assert ledger is not None
    assert error is None

    hand.raw_text = None
    assert _backfill_ledger_input(hand) == (None, None, "raw hand history is missing")

    hand.raw_text = parsed.raw_text.replace("#9100000001", "#9999999999", 1)
    _, ledger, error = _backfill_ledger_input(hand)
    assert ledger is None
    assert error == "raw hand id does not match the stored hand id"
