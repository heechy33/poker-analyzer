"""P1.4 property and timeline tests for the canonical ledger reducer.

These tests intentionally construct normalized ledger inputs rather than
adapting ``ParsedHand``.  Wiring parser output to the ledger is P1.2; keeping
that boundary here makes a reducer failure shrink to a small action sequence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from hypothesis import assume, given, settings, strategies as st

from app.ledger.models import DealtProvenanceV1, LedgerHandV1, LedgerPlayerV1, SourceProvenanceV1
from app.ledger.reducer import LedgerReducer, ReductionInputV1


_ZERO = Decimal("0")


def _source(line_number: int) -> SourceProvenanceV1:
    return SourceProvenanceV1(
        line_number=line_number,
        line_type="action",
        raw_tokens={"raw_line": f"fixture line {line_number}"},
    )


def _input(line_number: int, verb: str, **kwargs: object) -> ReductionInputV1:
    return ReductionInputV1(source=_source(line_number), verb=verb, **kwargs)


def _hand(
    stacks: tuple[Decimal, ...], *, table_format: str = "hu_2max", big_blind: Decimal = Decimal("1")
) -> LedgerHandV1:
    dealt = DealtProvenanceV1(dealt_in=True, source=_source(1))
    positions = ("BTN/SB", "BB", "UTG", "CO", "BTN", "SB")
    return LedgerHandV1(
        raw_hand_id="p1-4-golden",
        played_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        game="NLHE",
        table_marker="p1-4-fixture",
        table_format=table_format,  # type: ignore[arg-type]
        button_seat=1,
        small_blind=big_blind / 2,
        big_blind=big_blind,
        players=tuple(
            LedgerPlayerV1(
                seat=index,
                alias="hero" if index == 1 else f"villain-{index}",
                position=positions[index - 1],
                starting_stack=stack,
                is_hero=index == 1,
                dealt=dealt,
                decision_cards=("As", "Kd") if index == 1 else None,
            )
            for index, stack in enumerate(stacks, start=1)
        ),
    )


def _assert_event_invariants(reducer: LedgerReducer, stacks: tuple[Decimal, ...]) -> None:
    """Assert the conservation equations after every action boundary."""
    expected_starting = sum(stacks, start=_ZERO)
    for expected_index, event in enumerate(reducer.events):
        assert event.event_index == expected_index
        state = event.state_after
        assert state.player_contributed_pot == sum(
            (player.total_contribution for player in state.player_states), start=_ZERO
        )
        assert expected_starting == sum(
            (player.total_contribution + player.remaining_stack for player in state.player_states),
            start=_ZERO,
        )
        assert all(player.remaining_stack >= _ZERO for player in state.player_states)
        assert state.active_seats.isdisjoint(state.folded_seats)
        assert state.all_in_seats <= state.active_seats


@settings(max_examples=80, deadline=None)
@given(
    stack=st.integers(min_value=4, max_value=200),
    open_to=st.integers(min_value=2, max_value=200),
    flop_bet=st.integers(min_value=1, max_value=100),
)
def test_property_full_call_line_preserves_chips_at_each_boundary(
    stack: int, open_to: int, flop_bet: int
) -> None:
    """Posts, raises, calls, transitions, bets, folds, and returns conserve chips."""
    assume(open_to <= stack)
    assume(flop_bet <= stack - open_to)
    stacks = (Decimal(stack), Decimal(stack))
    reducer = LedgerReducer(_hand(stacks))
    reducer.apply(_input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(_input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")))
    raised = reducer.apply(
        _input(4, "raise", actor_seat=1, action_amount=Decimal(open_to - 1), raise_to=Decimal(open_to))
    )
    reducer.apply(_input(5, "call", actor_seat=2, action_amount=Decimal(open_to - 1)))
    reducer.apply(
        _input(6, "street_transition", next_street="flop", board_cards=("As", "Kd", "7c"))
    )
    reducer.apply(_input(7, "bet", actor_seat=1, action_amount=Decimal(flop_bet)))
    reducer.apply(_input(8, "fold", actor_seat=2))
    reducer.apply(_input(9, "return_uncalled", actor_seat=1, action_amount=Decimal(flop_bet)))

    assert raised.contribution_delta == Decimal(open_to) - Decimal("0.5")
    assert raised.raise_increment == Decimal(open_to - 1)
    _assert_event_invariants(reducer, stacks)


@settings(max_examples=60, deadline=None)
@given(short_stack=st.integers(min_value=3, max_value=9))
def test_property_short_and_full_all_in_raises_preserve_conservation(short_stack: int) -> None:
    """A short all-in may not reopen action, while all legal variants conserve stacks."""
    stacks = (Decimal("20"), Decimal(short_stack))
    reducer = LedgerReducer(_hand(stacks))
    reducer.apply(_input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")))
    reducer.apply(_input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")))
    reducer.apply(_input(4, "raise", actor_seat=1, action_amount=Decimal("1.5"), raise_to=Decimal("2.5")))
    all_in = reducer.apply(
        _input(
            5,
            "raise",
            actor_seat=2,
            action_amount=Decimal(short_stack - 1),
            raise_to=Decimal(short_stack),
            is_all_in=True,
        )
    )
    reducer.apply(
        _input(6, "call", actor_seat=1, action_amount=Decimal(short_stack) - Decimal("2.5"))
    )

    assert all_in.is_all_in is True
    assert all_in.state_after.all_in_seats == frozenset({2})
    if short_stack < 4:
        assert all_in.state_after.legal_raise_bounds.action_reopened is False
    _assert_event_invariants(reducer, stacks)


def _timeline(reducer: LedgerReducer) -> list[tuple[str, str, str, str]]:
    return [
        (
            event.verb,
            str(event.contribution_delta),
            str(event.returned_delta),
            str(event.state_after.player_contributed_pot),
        )
        for event in reducer.events
    ]


def test_golden_hunl_all_in_timeline() -> None:
    """The retained shallow-SPR HUNL fixture's valid action boundaries are exact."""
    reducer = LedgerReducer(_hand((Decimal("12"), Decimal("15"))))
    inputs = (
        _input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")),
        _input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")),
        _input(4, "raise", actor_seat=1, action_amount=Decimal("9.5"), raise_to=Decimal("10")),
        _input(5, "call", actor_seat=2, action_amount=Decimal("9")),
        _input(6, "street_transition", next_street="flop", board_cards=("Js", "Tc", "4d")),
        _input(7, "bet", actor_seat=1, action_amount=Decimal("2"), is_all_in=True),
        _input(8, "call", actor_seat=2, action_amount=Decimal("2")),
        _input(9, "street_transition", next_street="turn", board_cards=("8s",)),
        _input(10, "street_transition", next_street="river", board_cards=("2c",)),
        _input(11, "street_transition", next_street="showdown"),
        _input(12, "fee_summary", observed_rake=_ZERO, splash_fee=_ZERO),
        _input(13, "collect", actor_seat=2, pot_award_id="main", award_amount=Decimal("24")),
    )
    for item in inputs:
        reducer.apply(item)

    assert _timeline(reducer) == [
        ("post_small_blind", "0.5", "0", "0.5"),
        ("post_big_blind", "1", "0", "1.5"),
        ("raise", "9.5", "0", "11.0"),
        ("call", "9", "0", "20.0"),
        ("street_transition", "0", "0", "20.0"),
        ("bet", "2", "0", "22.0"),
        ("call", "2", "0", "24.0"),
        ("street_transition", "0", "0", "24.0"),
        ("street_transition", "0", "0", "24.0"),
        ("street_transition", "0", "0", "24.0"),
        ("fee_summary", "0", "0", "24.0"),
        ("collect", "0", "0", "24.0"),
    ]
    assert reducer.events[4].state_after.players_reached_flop == frozenset({1, 2})
    _assert_event_invariants(reducer, (Decimal("12"), Decimal("15")))
    assert reducer.finalize(reported_total_pot=Decimal("24")).settlement.reconciliation_status == "reconciled"


def test_golden_sixmax_multiway_side_pot_and_dead_blind_boundaries() -> None:
    """Folded money remains in the pot and a short stack creates a distinct side-pot award."""
    stacks = (Decimal("20"), Decimal("5"), Decimal("30"))
    reducer = LedgerReducer(_hand(stacks, table_format="6max"))
    inputs = (
        _input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")),
        _input(3, "post_dead_blind", actor_seat=2, action_amount=Decimal("1")),
        _input(4, "raise", actor_seat=3, action_amount=Decimal("4"), raise_to=Decimal("5")),
        _input(5, "call", actor_seat=1, action_amount=Decimal("4.5")),
        _input(6, "call", actor_seat=2, action_amount=Decimal("4"), is_all_in=True),
        _input(7, "street_transition", next_street="flop", board_cards=("2c", "7d", "Jh")),
        _input(8, "bet", actor_seat=1, action_amount=Decimal("10")),
        _input(9, "call", actor_seat=3, action_amount=Decimal("10")),
        _input(10, "street_transition", next_street="turn", board_cards=("Ks",)),
        _input(11, "check", actor_seat=1),
        _input(12, "check", actor_seat=3),
        _input(13, "street_transition", next_street="river", board_cards=("3c",)),
        _input(14, "check", actor_seat=1),
        _input(15, "check", actor_seat=3),
        _input(16, "street_transition", next_street="showdown"),
        _input(17, "fee_summary", observed_rake=_ZERO, splash_fee=_ZERO),
        _input(18, "collect", actor_seat=2, pot_award_id="main", award_amount=Decimal("15")),
        _input(19, "collect", actor_seat=1, pot_award_id="side", award_amount=Decimal("20")),
    )
    for item in inputs:
        reducer.apply(item)

    assert reducer.events[5].state_after.players_reached_flop == frozenset({1, 2, 3})
    assert reducer.events[1].verb == "post_dead_blind"
    assert reducer.events[-1].state_after.player_contributed_pot == Decimal("35")
    _assert_event_invariants(reducer, stacks)
    assert reducer.finalize(reported_total_pot=Decimal("35")).settlement.reconciliation_status == "reconciled"


def test_golden_return_splash_fee_drop_and_second_run_boundaries() -> None:
    """Returns reverse only player money; splash money stays outside the rake basis."""
    stacks = (Decimal("50"), Decimal("50"))
    reducer = LedgerReducer(_hand(stacks))
    inputs = (
        _input(2, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")),
        _input(3, "post_big_blind", actor_seat=2, action_amount=Decimal("1")),
        _input(4, "call", actor_seat=1, action_amount=Decimal("0.5")),
        _input(5, "street_transition", next_street="flop", board_cards=("Ac", "Kd", "Qh")),
        _input(6, "bet", actor_seat=1, action_amount=Decimal("2")),
        _input(7, "fold", actor_seat=2),
        _input(8, "return_uncalled", actor_seat=1, action_amount=Decimal("2")),
        _input(9, "splash_drop", promotional_delta=Decimal("0.4")),
        _input(10, "fee_summary", observed_rake=Decimal("0.1"), splash_fee=Decimal("0.02")),
        _input(11, "collect", actor_seat=1, pot_award_id="main", award_amount=Decimal("1.88")),
    )
    for item in inputs:
        reducer.apply(item)

    assert reducer.events[6].state_after.player_contributed_pot == Decimal("2")
    assert reducer.events[7].state_after.promotional_drop == Decimal("0.4")
    assert reducer.events[8].state_after.fee_metadata.splash_fee == Decimal("0.02")
    _assert_event_invariants(reducer, stacks)
    ledger = reducer.finalize(reported_total_pot=Decimal("2.4"))
    assert ledger.settlement.reconciliation_status == "unreconciled"

    run_reducer = LedgerReducer(_hand((Decimal("50"), Decimal("50"))))
    for item in (
        _input(20, "post_small_blind", actor_seat=1, action_amount=Decimal("0.5")),
        _input(21, "post_big_blind", actor_seat=2, action_amount=Decimal("1")),
        _input(22, "raise", actor_seat=1, action_amount=Decimal("2.5"), raise_to=Decimal("3")),
        _input(23, "raise", actor_seat=2, action_amount=Decimal("49"), raise_to=Decimal("50"), is_all_in=True),
        _input(24, "call", actor_seat=1, action_amount=Decimal("47"), is_all_in=True),
        _input(25, "street_transition", next_street="flop", board_cards=("Ac", "Kd", "Qh")),
        _input(26, "street_transition", next_street="turn", board_cards=("2s",)),
        _input(27, "street_transition", next_street="river", board_cards=("2d",)),
        _input(28, "street_transition", next_street="river", board_cards=("Jc",), board_run="second_run"),
    ):
        run_reducer.apply(item)
    assert run_reducer.events[-1].state_after.board.first_run == ("Ac", "Kd", "Qh", "2s", "2d")
    assert run_reducer.events[-1].state_after.board.second_run == ("Jc",)
    _assert_event_invariants(run_reducer, (Decimal("50"), Decimal("50")))
