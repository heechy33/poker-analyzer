"""P1.8 one-dimensional eligibility boundaries around the 2.4 BB cohort."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.eligibility import assess_eligibility
from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hand


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "p1_8"


def _ledger(name: str):
    return ledger_from_parsed(
        parse_hand((FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines())
    )


def _fact_differences(baseline, variant) -> set[str]:
    return {
        field
        for field, value in baseline.facts.model_dump().items()
        if variant.facts.model_dump()[field] != value
    }


def test_p1_8_2_4bb_baseline_is_eligible_but_not_solver_authorization() -> None:
    result = assess_eligibility(_ledger("supported_2_4bb.txt"))

    assert result.status == "supported"
    assert result.reason_codes == ()
    assert result.facts.preflop_open_to == result.facts.stake_bb * Decimal("2.4")


@pytest.mark.parametrize(
    ("fixture", "changed_facts", "reason"),
    [
        ("open_3_0bb.txt", {"preflop_open_to"}, "unsupported_open_size"),
        ("effective_stack_99bb.txt", {"effective_stack_bb"}, "unsupported_effective_stack"),
        ("splash_fee_only.txt", {"excluded_flags"}, "excluded_splash_fee"),
        ("actual_splash_drop.txt", {"excluded_flags"}, "excluded_splash_drop"),
    ],
)
def test_p1_8_each_boundary_fixture_changes_one_cohort_fact_and_fails_closed(
    fixture: str, changed_facts: set[str], reason: str
) -> None:
    baseline = assess_eligibility(_ledger("supported_2_4bb.txt"))
    variant = assess_eligibility(_ledger(fixture))

    assert baseline.status == "supported"
    assert _fact_differences(baseline, variant) == changed_facts
    assert variant.status == "unsupported"
    assert variant.reason_codes == (reason,)
