from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ledger.models import (
    LEDGER_SCHEMA_V1,
    CanonicalLedgerV1,
    DealtProvenanceV1,
    FeeMetadataV1,
    LedgerEventV1,
    LedgerHandV1,
    LedgerPlayerV1,
    LedgerStateV1,
    LegalRaiseBoundsV1,
    PlayerStateV1,
    SourceProvenanceV1,
)


def _source(line_number: int = 1) -> SourceProvenanceV1:
    return SourceProvenanceV1(
        line_number=line_number,
        line_type="action",
        raw_tokens={"line": "Hero: raises 0.15 to 0.20"},
    )


def _hand() -> LedgerHandV1:
    dealt = DealtProvenanceV1(dealt_in=True, source=_source(), inferred=False)
    return LedgerHandV1(
        raw_hand_id="1001",
        played_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        game="NLHE",
        table_marker="200601",
        table_format="hu_2max",
        button_seat=1,
        small_blind=Decimal("0.02"),
        big_blind=Decimal("0.05"),
        players=(
            LedgerPlayerV1(
                seat=1,
                alias="hero",
                position="BTN/SB",
                starting_stack=Decimal("5.00"),
                is_hero=True,
                dealt=dealt,
                decision_cards=("As", "Kd"),
            ),
            LedgerPlayerV1(
                seat=2,
                alias="villain-1",
                position="BB",
                starting_stack=Decimal("5.00"),
                dealt=dealt,
            ),
        ),
    )


def _state(*, pot: str, hero_street: str, amount_to_call: str) -> LedgerStateV1:
    return LedgerStateV1(
        street="preflop",
        active_seats=frozenset({1, 2}),
        player_states=(
            PlayerStateV1(
                seat=1,
                active=True,
                folded=False,
                all_in=False,
                street_contribution=Decimal(hero_street),
                total_contribution=Decimal(hero_street),
                remaining_stack=Decimal("5.00") - Decimal(hero_street),
            ),
            PlayerStateV1(
                seat=2,
                active=True,
                folded=False,
                all_in=False,
                street_contribution=Decimal("0.05"),
                total_contribution=Decimal("0.05"),
                remaining_stack=Decimal("4.95"),
            ),
        ),
        amount_to_call=Decimal(amount_to_call),
        last_full_raise=Decimal("0.05"),
        legal_raise_bounds=LegalRaiseBoundsV1(
            min_raise_to=Decimal("0.15"), max_raise_to=Decimal("5.00"), action_reopened=True
        ),
        player_contributed_pot=Decimal(pot),
        fee_metadata=FeeMetadataV1(),
    )


def _raise_event() -> LedgerEventV1:
    return LedgerEventV1(
        event_index=0,
        street_event_index=0,
        street="preflop",
        source=_source(),
        actor_seat=1,
        verb="raise",
        # Raw CoinPoker raise amount is distinct from newly committed chips.
        action_amount=Decimal("0.15"),
        contribution_delta=Decimal("0.18"),
        raise_to=Decimal("0.20"),
        raise_increment=Decimal("0.15"),
        state_before=_state(pot="0.07", hero_street="0.02", amount_to_call="0.03"),
        state_after=_state(pot="0.25", hero_street="0.20", amount_to_call="0.15"),
    )


def test_canonical_ledger_contract_preserves_versioned_decimal_action_state() -> None:
    ledger = CanonicalLedgerV1.create(hand=_hand(), events=(_raise_event(),))

    body = ledger.model_dump(mode="json")
    event = body["events"][0]
    assert ledger.schema_version == LEDGER_SCHEMA_V1
    assert event["action_amount"] == "0.15"
    assert event["contribution_delta"] == "0.18"
    assert event["raise_to"] == "0.20"
    assert event["raise_increment"] == "0.15"
    assert event["state_after"]["player_contributed_pot"] == "0.25"
    assert event["state_after"]["legal_raise_bounds"]["min_raise_to"] == "0.15"
    assert ledger.ledger_hash == ledger.computed_hash()


def test_ledger_hash_rejects_any_canonical_content_mutation() -> None:
    ledger = CanonicalLedgerV1.create(hand=_hand(), events=(_raise_event(),))

    with pytest.raises(ValidationError, match="ledger_hash does not match"):
        CanonicalLedgerV1(
            hand=_hand(),
            events=(_raise_event(),),
            ledger_hash="0" * 64,
        )

    with pytest.raises(ValidationError, match="ledger_hash does not match"):
        CanonicalLedgerV1(
            hand=_hand().model_copy(update={"table_marker": "different-table"}),
            events=(_raise_event(),),
            ledger_hash=ledger.ledger_hash,
        )


def test_state_rejects_inconsistent_player_membership_and_raise_bounds() -> None:
    state = _state(pot="0.07", hero_street="0.02", amount_to_call="0.03")
    with pytest.raises(ValidationError, match="must be disjoint"):
        LedgerStateV1(
            **(state.model_dump() | {"folded_seats": frozenset({1})}),
        )

    with pytest.raises(ValidationError, match="min_raise_to cannot exceed"):
        LegalRaiseBoundsV1(min_raise_to=Decimal("2"), max_raise_to=Decimal("1"))
