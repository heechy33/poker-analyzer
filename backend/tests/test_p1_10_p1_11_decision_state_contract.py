"""P1.10/P1.11 real-fixture trace and Python wire-contract checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.eligibility import assess_eligibility
from app.ledger.decision_state import DecisionStateV1, build_decision_state
from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hand


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "p1_10" / "real_hunl_flop_decision.txt"
STATE_FIXTURE = ROOT / "contracts" / "hunl-decision-state-v1.real-hunl-flop.json"
FIELD_CONTRACT = ROOT / "contracts" / "hunl-decision-state-v1.fields.json"


def _state() -> DecisionStateV1:
    hand = parse_hand(FIXTURE.read_text(encoding="utf-8").splitlines())
    ledger = ledger_from_parsed(hand)
    hero_flop_bet = next(
        event
        for event in ledger.events
        if event.street == "flop" and event.actor_seat == ledger.hand.button_seat - 1 and event.verb == "bet"
    )
    assert assess_eligibility(ledger).status == "supported"
    return build_decision_state(ledger, event_index=hero_flop_bet.event_index)


def test_p1_10_real_fixture_serializes_to_the_human_traced_canonical_state() -> None:
    state = _state()
    expected = json.loads(STATE_FIXTURE.read_text(encoding="utf-8"))

    assert state.model_dump(mode="json") == expected
    assert state.action_event_index == 5
    assert state.action_street_event_index == 0
    assert state.legal_actions == ("check", "bet")
    assert state.rake_schedule_id == "coinpoker-hu-nlhe-0.02-0.05-observed-2026-07-20/1"


def test_p1_11_python_wire_shape_is_locked_to_the_shared_contract() -> None:
    state = _state()
    expected_fields = json.loads(FIELD_CONTRACT.read_text(encoding="utf-8"))
    payload = state.model_dump(mode="json")

    assert state.schema_version == expected_fields["schema_version"]
    assert list(DecisionStateV1.model_fields) == expected_fields["required"]
    assert set(payload) == set(expected_fields["required"])

    # This is the JSON form used at an API boundary: all native-chip money is
    # emitted as a string and Pydantic can recover the exact same state.
    wire = state.model_dump_json()
    round_trip = DecisionStateV1.model_validate_json(wire)
    assert round_trip.model_dump(mode="json") == payload
    assert isinstance(payload["small_blind"], str)
    assert isinstance(payload["player_states"][0]["remaining_stack"], str)


def test_p1_11_python_rejects_missing_or_unknown_boundary_fields() -> None:
    payload = _state().model_dump(mode="json")
    payload.pop("rake_schedule_id")
    with pytest.raises(ValidationError):
        DecisionStateV1.model_validate(payload)

    with pytest.raises(ValidationError):
        DecisionStateV1.model_validate({**_state().model_dump(mode="json"), "future_value": "no"})
