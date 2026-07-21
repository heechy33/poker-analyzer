from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.ledger.models import (
    DealtProvenanceV1,
    LedgerHandV1,
    LedgerPlayerV1,
    SourceProvenanceV1,
)
from app.ledger.reducer import LedgerReducer, LedgerReductionError, ReductionInputV1


def _source(line_number: int) -> SourceProvenanceV1:
    return SourceProvenanceV1(line_number=line_number, line_type="action")


def _input(line_number: int, verb: str, **kwargs: object) -> ReductionInputV1:
    return ReductionInputV1(source=_source(line_number), verb=verb, **kwargs)


def _hand(*, hero_stack: str = "10", villain_stack: str = "10") -> LedgerHandV1:
    dealt = DealtProvenanceV1(dealt_in=True, source=_source(1))
    return LedgerHandV1(
        raw_hand_id="p1-3",
        played_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        game="NLHE",
        table_marker="reducer-test",
        table_format="hu_2max",
        button_seat=1,
        small_blind=Decimal("0.5"),
        big_blind=Decimal("1"),
        players=(
            LedgerPlayerV1(
                seat=1,
                alias="hero",
                position="BTN/SB",
                starting_stack=Decimal(hero_stack),
                is_hero=True,
                dealt=dealt,
                decision_cards=("As", "Kd"),
            ),
            LedgerPlayerV1(
                seat=2,
                alias="villain-1",
                position="BB",
                starting_stack=Decimal(villain_stack),
                dealt=dealt,
            ),
        ),
    )


def test_reducer_derives_raise_contribution_and_captures_flop_players() -> None:
    reducer = LedgerReducer(_hand())
    reducer.apply(_input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(_input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")))
    raise_event = reducer.apply(
        _input(
            4,
            "raise",
            actor_seat=1,
            # The raw text amount is not the 2.5 chips newly committed.
            action_amount=Decimal("2"),
            raise_to=Decimal("3"),
        )
    )
    call_event = reducer.apply(_input(5, "call", actor_seat=2, action_amount=Decimal("2")))
    flop_event = reducer.apply(
        _input(6, "street_transition", next_street="flop", board_cards=("As", "Kd", "7c"))
    )

    assert raise_event.contribution_delta == Decimal("2.5")
    assert raise_event.raise_to == Decimal("3")
    assert raise_event.raise_increment == Decimal("2")
    assert call_event.contribution_delta == Decimal("2")
    assert flop_event.state_before.player_contributed_pot == Decimal("6")
    assert flop_event.state_after.players_reached_flop == frozenset({1, 2})
    assert [state.street_contribution for state in flop_event.state_after.player_states] == [
        Decimal("0"),
        Decimal("0"),
    ]
    assert [state.total_contribution for state in flop_event.state_after.player_states] == [
        Decimal("3"),
        Decimal("3"),
    ]


@pytest.mark.parametrize("verb", ["post_ante", "post_dead_blind"])
def test_reducer_keeps_ante_and_dead_blind_as_distinct_forced_events(verb: str) -> None:
    reducer = LedgerReducer(_hand())
    event = reducer.apply(_input(2, verb, actor_seat=1, action_amount=Decimal("0.25")))

    assert event.verb == verb
    assert event.contribution_delta == Decimal("0.25")
    assert event.state_after.player_contributed_pot == Decimal("0.25")
    hero_state = event.state_after.player_states[0]
    assert hero_state.total_contribution + hero_state.remaining_stack == Decimal("10")


def test_short_all_in_raise_does_not_reopen_action() -> None:
    reducer = LedgerReducer(_hand(villain_stack="3"))
    reducer.apply(_input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(_input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")))
    reducer.apply(
        _input(4, "raise", actor_seat=1, action_amount=Decimal("1.5"), raise_to=Decimal("2.5"))
    )
    short_raise = reducer.apply(
        _input(
            5,
            "raise",
            actor_seat=2,
            action_amount=Decimal("2"),
            raise_to=Decimal("3"),
            is_all_in=True,
        )
    )

    assert short_raise.is_all_in is True
    assert short_raise.raise_increment == Decimal("0.5")
    assert short_raise.state_after.legal_raise_bounds.action_reopened is False
    with pytest.raises(LedgerReductionError, match="has not reopened"):
        reducer.apply(
            _input(6, "raise", actor_seat=1, action_amount=Decimal("1"), raise_to=Decimal("4"))
        )
    reducer.apply(_input(7, "call", actor_seat=1, action_amount=Decimal("0.5")))


def test_return_splash_and_settlement_keep_promotional_money_out_of_player_pot() -> None:
    reducer = LedgerReducer(_hand())
    reducer.apply(_input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(_input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")))
    reducer.apply(_input(4, "call", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(
        _input(5, "street_transition", next_street="flop", board_cards=("As", "Kd", "7c"))
    )
    reducer.apply(_input(6, "bet", actor_seat=1, action_amount=Decimal("1")))
    reducer.apply(_input(7, "fold", actor_seat=2))
    returned = reducer.apply(_input(8, "return_uncalled", actor_seat=1, action_amount=Decimal("1")))
    splash = reducer.apply(_input(9, "splash_drop", promotional_delta=Decimal("0.4")))
    reducer.apply(_input(10, "fee_summary", observed_rake=Decimal("0.1"), splash_fee=Decimal("0")))
    reducer.apply(
        _input(11, "collect", actor_seat=1, pot_award_id="main", award_amount=Decimal("1.9"))
    )
    ledger = reducer.finalize(reported_total_pot=Decimal("2.4"))

    assert returned.returned_delta == Decimal("1")
    assert returned.state_after.player_contributed_pot == Decimal("2")
    assert splash.state_after.player_contributed_pot == Decimal("2")
    assert splash.state_after.promotional_drop == Decimal("0.4")
    assert ledger.settlement.reported_total_pot == Decimal("2.4")
    assert ledger.settlement.reconciliation_status == "unreconciled"


def test_reducer_rejects_check_facing_bet_and_non_all_in_short_call() -> None:
    reducer = LedgerReducer(_hand(villain_stack="1.25"))
    reducer.apply(_input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(_input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")))
    with pytest.raises(LedgerReductionError, match="check is illegal"):
        reducer.apply(_input(4, "check", actor_seat=1))

    reducer.apply(
        _input(5, "raise", actor_seat=1, action_amount=Decimal("1.5"), raise_to=Decimal("2"))
    )
    with pytest.raises(LedgerReductionError, match="short call"):
        reducer.apply(_input(6, "call", actor_seat=2, action_amount=Decimal("0.25")))
