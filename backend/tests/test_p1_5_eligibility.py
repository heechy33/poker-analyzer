"""P1.5/P1.6 fail-closed HUNL cohort eligibility contracts."""

from __future__ import annotations

from pathlib import Path

from app.eligibility import ELIGIBILITY_POLICY_VERSION, assess_eligibility
from app.ledger.parsed import ledger_from_parsed
from app.parser.coinpoker import parse_hand


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"


def _ledger(name: str):
    return ledger_from_parsed(parse_hand((FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines()))


def test_p1_5_result_is_versioned_ordered_and_currently_cannot_support() -> None:
    result = assess_eligibility(_ledger("splash_fee_summary.txt"))

    assert result.policy_version == ELIGIBILITY_POLICY_VERSION
    assert result.status == "unsupported"
    assert result.reason_codes == (
        "players_dealt_ambiguous",
        "excluded_splash_fee",
        "unsupported_open_size",
        "unsupported_stake",
        "rake_schedule_unknown",
    )
    assert result.facts.rake_schedule_id is None
    assert result.facts.effective_stack_bb == 100


def test_p1_6_sixmax_hu_postflop_stays_replay_only() -> None:
    result = assess_eligibility(_ledger("sixmax_hu_postflop.txt"))

    assert result.facts.players_reached_flop == 2
    assert result.status == "unsupported"
    assert result.reason_codes[0] == "unsupported_table_format"
    assert "unsupported_table_format" in result.reason_codes


def test_p1_6_multiway_flop_stays_unsupported_after_later_folds() -> None:
    result = assess_eligibility(_ledger("multiway_flop_hu_turn_reconciled.txt"), decision_street="turn")

    assert result.facts.players_reached_flop == 3
    assert result.status == "unsupported"
    assert "players_reached_flop_not_two" in result.reason_codes
    assert result.reason_codes.index("players_reached_flop_not_two") < result.reason_codes.index(
        "unsupported_decision_street"
    )
