"""Fail-closed Phase 1 HUNL cohort eligibility.

This policy intentionally establishes *eligibility only*. A supported result
would permit a later, separately approved solve-spec gate; it never means a
hand was solved or can be graded. The initial cohort is fixed to the evidenced
2.4 BB BTN/SB open; it still has no paired range data or solver authorization.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ledger.models import CanonicalLedgerV1
from app.rake import RakeScheduleError, resolve_rake_schedule


ELIGIBILITY_POLICY_VERSION = "hunl-flop-open-call/1"
INITIAL_COHORT_ID = "coinpoker-hunl-flop-open-call/1"

EligibilityStatus = Literal["supported", "unsupported"]
EligibilityReasonCode = Literal[
    "invalid_ledger",
    "unsupported_game",
    "unsupported_table_format",
    "players_dealt_ambiguous",
    "players_dealt_not_two",
    "players_reached_flop_not_two",
    "unsupported_positions",
    "excluded_ante",
    "excluded_dead_blind",
    "excluded_straddle",
    "excluded_bomb_pot",
    "excluded_run_it_twice",
    "excluded_side_pot",
    "excluded_splash_fee",
    "excluded_splash_drop",
    "unsupported_effective_stack",
    "unsupported_preflop_branch",
    "unsupported_open_size",
    "unsupported_stake",
    "rake_schedule_unknown",
    "rake_schedule_ambiguous",
    "unsupported_decision_street",
]

_REASON_PRIORITY: tuple[EligibilityReasonCode, ...] = (
    "invalid_ledger",
    "unsupported_game",
    "unsupported_table_format",
    "players_dealt_ambiguous",
    "players_dealt_not_two",
    "players_reached_flop_not_two",
    "unsupported_positions",
    "excluded_ante",
    "excluded_dead_blind",
    "excluded_straddle",
    "excluded_bomb_pot",
    "excluded_run_it_twice",
    "excluded_side_pot",
    "excluded_splash_fee",
    "excluded_splash_drop",
    "unsupported_effective_stack",
    "unsupported_preflop_branch",
    "unsupported_open_size",
    "unsupported_stake",
    "rake_schedule_unknown",
    "rake_schedule_ambiguous",
    "unsupported_decision_street",
)


class EligibilityFactsV1(BaseModel):
    """Auditable facts used to determine an eligibility result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table_format: str
    players_dealt: int
    players_dealt_inferred: bool
    players_reached_flop: int
    effective_stack_bb: Decimal
    positions: tuple[str, ...]
    preflop_open_to: Decimal | None
    preflop_call_seen: bool
    stake_sb: Decimal
    stake_bb: Decimal
    decision_street: str
    excluded_flags: tuple[str, ...]
    rake_schedule_id: str | None = None


class EligibilityV1(BaseModel):
    """Stable Phase 1 eligibility result, ordered by the published policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EligibilityStatus
    policy_version: Literal["hunl-flop-open-call/1"] = ELIGIBILITY_POLICY_VERSION
    cohort_id: str = INITIAL_COHORT_ID
    reason_codes: tuple[EligibilityReasonCode, ...]
    facts: EligibilityFactsV1


class CohortConfigurationV1(BaseModel):
    """The narrow configuration deliberately kept separate from range data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort_id: str = INITIAL_COHORT_ID
    supported_stake_sb: Decimal = Decimal("0.02")
    supported_stake_bb: Decimal = Decimal("0.05")
    effective_stack_bb: Decimal = Decimal("100")
    # P1.8: exact 2.4 BB native-chip open, evidenced by three real 100 BB
    # HUNL open/call flop hands. This identifies the state cohort only; a
    # future paired range pack must match it before any solve path may exist.
    open_to_bb: Decimal = Decimal("2.4")
    native_chip_tolerance: Decimal = Decimal("0.00")


INITIAL_COHORT_CONFIGURATION = CohortConfigurationV1()


def assess_eligibility(
    ledger: CanonicalLedgerV1,
    *,
    decision_street: str = "flop",
    configuration: CohortConfigurationV1 = INITIAL_COHORT_CONFIGURATION,
) -> EligibilityV1:
    """Evaluate the complete ledger conservatively and without fallback rows."""
    hand = ledger.hand
    final_state = ledger.events[-1].state_after if ledger.events else None
    dealt_players = tuple(player for player in hand.players if player.dealt.dealt_in)
    positions = tuple(player.position for player in dealt_players)
    reached_flop = final_state.players_reached_flop if final_state is not None else frozenset()
    effective_stack_bb = (
        min(player.starting_stack for player in dealt_players) / hand.big_blind
        if dealt_players
        else Decimal("0")
    )
    open_event, call_seen, branch_is_open_call = _preflop_open_call(ledger)
    open_to = open_event.raise_to if open_event is not None else None
    excluded = _excluded_facts(ledger)
    schedule_id, rake_reason = _resolved_schedule(ledger, len(dealt_players))

    facts = EligibilityFactsV1(
        table_format=hand.table_format,
        players_dealt=len(dealt_players),
        players_dealt_inferred=any(player.dealt.inferred for player in dealt_players),
        players_reached_flop=len(reached_flop),
        effective_stack_bb=effective_stack_bb,
        positions=positions,
        preflop_open_to=open_to,
        preflop_call_seen=call_seen,
        stake_sb=hand.small_blind,
        stake_bb=hand.big_blind,
        decision_street=decision_street,
        excluded_flags=excluded,
        rake_schedule_id=schedule_id,
    )

    reasons: set[EligibilityReasonCode] = set()
    if hand.game != "NLHE":
        reasons.add("unsupported_game")
    if hand.table_format != "hu_2max":
        reasons.add("unsupported_table_format")
    if any(player.dealt.inferred for player in dealt_players):
        reasons.add("players_dealt_ambiguous")
    if len(dealt_players) != 2:
        reasons.add("players_dealt_not_two")
    if len(reached_flop) != 2:
        reasons.add("players_reached_flop_not_two")
    if set(positions) != {"BTN/SB", "BB"}:
        reasons.add("unsupported_positions")
    reasons.update(excluded)
    if effective_stack_bb != configuration.effective_stack_bb:
        reasons.add("unsupported_effective_stack")
    if not branch_is_open_call:
        reasons.add("unsupported_preflop_branch")
    elif configuration.open_to_bb is None or open_to is None or abs(
        open_to - configuration.open_to_bb * hand.big_blind
    ) > configuration.native_chip_tolerance:
        reasons.add("unsupported_open_size")
    if (
        hand.small_blind != configuration.supported_stake_sb
        or hand.big_blind != configuration.supported_stake_bb
    ):
        reasons.add("unsupported_stake")
    if rake_reason is not None:
        reasons.add(rake_reason)
    if decision_street != "flop":
        reasons.add("unsupported_decision_street")

    ordered = tuple(code for code in _REASON_PRIORITY if code in reasons)
    return EligibilityV1(
        status="supported" if not ordered else "unsupported",
        cohort_id=configuration.cohort_id,
        reason_codes=ordered,
        facts=facts,
    )


def _preflop_open_call(ledger: CanonicalLedgerV1):
    decisions = tuple(
        event
        for event in ledger.events
        if event.street == "preflop" and event.verb in {"fold", "check", "call", "bet", "raise"}
    )
    if len(decisions) != 2:
        return None, False, False
    open_event, call_event = decisions
    button = ledger.hand.button_seat
    bb = next((player.seat for player in ledger.hand.players if player.position == "BB"), None)
    branch_is_open_call = (
        open_event.verb == "raise"
        and open_event.actor_seat == button
        and call_event.verb == "call"
        and call_event.actor_seat == bb
    )
    return open_event if open_event.verb == "raise" else None, call_event.verb == "call", branch_is_open_call


def _excluded_facts(ledger: CanonicalLedgerV1) -> tuple[EligibilityReasonCode, ...]:
    event_verbs = {event.verb for event in ledger.events}
    flags = ledger.hand.flags
    final_state = ledger.events[-1].state_after if ledger.events else None
    reasons: set[EligibilityReasonCode] = set()
    if "post_ante" in event_verbs:
        reasons.add("excluded_ante")
    if "post_dead_blind" in event_verbs:
        reasons.add("excluded_dead_blind")
    if "post_straddle" in event_verbs:
        reasons.add("excluded_straddle")
    if "bomb_pot" in flags:
        reasons.add("excluded_bomb_pot")
    if "run_it_twice" in flags:
        reasons.add("excluded_run_it_twice")
    if "side_pots" in flags:
        reasons.add("excluded_side_pot")
    if final_state is not None and final_state.fee_metadata.splash_fee != Decimal("0"):
        reasons.add("excluded_splash_fee")
    if "splash_drop" in event_verbs or (
        final_state is not None and final_state.promotional_drop != Decimal("0")
    ):
        reasons.add("excluded_splash_drop")
    return tuple(code for code in _REASON_PRIORITY if code in reasons)


def _resolved_schedule(
    ledger: CanonicalLedgerV1, players_dealt: int
) -> tuple[str | None, EligibilityReasonCode | None]:
    hand = ledger.hand
    try:
        schedule = resolve_rake_schedule(
            played_at=hand.played_at,
            stake_sb=hand.small_blind,
            stake_bb=hand.big_blind,
            game=hand.game,
            table_format=hand.table_format,
            players_dealt=players_dealt,
        )
    except RakeScheduleError as exc:
        return None, exc.code
    return schedule.schedule_id, None
