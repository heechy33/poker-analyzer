from __future__ import annotations

from decimal import Decimal

import pytest

from app.parser import ParseError, parse_hand, parse_hands


def test_parse_basic_hand_with_raise_increment_and_uncalled_return() -> None:
    hand = parse_hand(
        [
            "CoinPoker Hand #5331371084: NLH (₮0.10/₮0.25) - "
            "2026/05/23 15:50:17 PDT",
            "Table '201122' 6-max Seat #3 is the button",
            "Seat 1: alpha (₮25.00 in chips)",
            "Seat 2: bravo (₮25.00 in chips)",
            "Seat 3: charlie (₮25.00 in chips)",
            "Seat 4: Hero (₮26.85 in chips)",
            "Seat 5: villain (₮25.00 in chips)",
            "Seat 6: foxtrot (₮25.00 in chips)",
            "Hero: posts small blind ₮0.10",
            "villain: posts big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to Hero [Kc 9d]",
            "foxtrot: folds",
            "alpha: folds",
            "bravo: folds",
            "charlie: folds",
            "Hero: raises ₮0.65 to ₮0.90",
            "villain: calls ₮0.65",
            "*** FLOP *** [Kd 9c Td]",
            "Hero: bets ₮1.20",
            "villain: folds",
            "Uncalled bet (₮0.50) returned to Hero",
            "Hero collected ₮1.80 from pot",
            "*** SUMMARY ***",
            "Total pot ₮2.30 | Rake ₮0.48 | Splash Fee ₮0.02",
        ]
    )

    assert hand.coinpoker_hand_id == 5331371084
    assert hand.played_at.tzinfo is not None
    assert hand.table_size == 6
    assert hand.hero_seat == 4
    assert hand.hero_position == "SB"
    assert hand.hero_cards == ["Kc", "9d"]
    assert hand.flop == ["Kd", "9c", "Td"]
    assert hand.turn is None
    assert hand.river is None
    assert hand.total_pot == Decimal("2.3000")
    assert hand.rake == Decimal("0.4800")
    assert hand.splash_fee == Decimal("0.0200")
    assert hand.hero_invested == Decimal("1.4500")
    assert hand.hero_collected == Decimal("1.8000")
    assert hand.hero_net == Decimal("0.3500")
    assert hand.hero_net_bb == Decimal("1.4000")
    assert hand.went_to_showdown is False
    assert hand.won_at_showdown is None

    hero_raise = next(action for action in hand.actions if action.action == "raise")
    assert hero_raise.amount == Decimal("0.6500")
    assert hero_raise.raise_to == Decimal("0.9000")


def test_parse_all_in_side_split_and_run_it_twice() -> None:
    hand = parse_hand(
        [
            "CoinPoker Hand #6000000001: NLH (₮0.50/₮1.00) - "
            "2026/05/23 16:05:00 PDT",
            "Table 'HU-1' heads-up Seat #1 is the button",
            "Seat 1: Hero (₮50.00 in chips)",
            "Seat 2: villain (₮50.00 in chips)",
            "Hero: posts small blind ₮0.50",
            "villain: posts big blind ₮1.00",
            "*** HOLE CARDS ***",
            "Dealt to Hero [As Ah]",
            "Hero: raises ₮2.00 to ₮3.00",
            "villain: raises ₮47.00 to ₮50.00 and is all-in",
            "Hero: calls ₮47.00 and is all-in",
            "*** FLOP *** [Ac Kd Qh]",
            "*** TURN *** [Ac Kd Qh] [2s]",
            "*** FIRST RIVER *** [Ac Kd Qh 2s] [2d]",
            "*** SECOND RIVER *** [Ac Kd Qh 2s] [Jc]",
            "*** SHOWDOWN ***",
            "Hero: shows [As Ah] (Full House)",
            "villain: shows [Tc Js] (Straight)",
            "Hero collected ₮50.00 from main pot",
            "villain collected ₮50.00 from main pot",
            "Hero collected ₮10.00 from side pot",
            "*** SUMMARY ***",
            "Total pot ₮110.00 | Rake ₮0.00 | Splash Fee ₮0.00",
        ]
    )

    assert hand.hero_position == "BTN/SB"
    assert hand.flop == ["Ac", "Kd", "Qh"]
    assert hand.turn == "2s"
    assert hand.river == "2d"
    assert hand.flags["all_in"] is True
    assert hand.flags["run_it_twice"] is True
    assert hand.flags["split_pot"] is True
    assert hand.flags["side_pots"] is True
    assert hand.flags["second_board"] == ["Ac", "Kd", "Qh", "2s", "Jc"]
    assert hand.hero_invested == Decimal("49.5000")
    assert hand.hero_collected == Decimal("60.0000")
    assert hand.hero_net == Decimal("10.5000")
    assert hand.went_to_showdown is True
    assert hand.won_at_showdown is True
    assert any(action.is_all_in for action in hand.actions)
    assert hand.players[0].final_cards == ["As", "Ah"]


def test_timeout_and_sit_out_are_recorded_as_folds() -> None:
    hand = parse_hand(
        [
            "CoinPoker Hand #6000000002: NLH (₮0.10/₮0.25) - "
            "2026/05/23 17:00:00 PDT",
            "Table 'short' 6-max Seat #2 is the button",
            "Seat 1: Hero (₮25.00 in chips)",
            "Seat 2: button (₮25.00 in chips)",
            "Seat 3: smallblind (₮25.00 in chips)",
            "Seat 4: bigblind (₮25.00 in chips)",
            "smallblind: posts small blind ₮0.10",
            "bigblind: posts big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to Hero [Qs Qh]",
            "Hero: raises ₮0.60 to ₮0.85",
            "button: has timed out",
            "smallblind: is sitting out",
            "bigblind: folds",
            "Hero collected ₮1.15 from pot",
            "*** SUMMARY ***",
            "Total pot ₮1.15 | Rake ₮0.00 | Splash Fee ₮0.00",
        ]
    )

    fold_names = [
        action.screen_name for action in hand.actions if action.action == "fold"
    ]
    assert fold_names == ["button", "smallblind", "bigblind"]
    assert hand.hero_position == "CO"


def test_parse_hands_splits_multiple_blocks() -> None:
    first = (
        "CoinPoker Hand #7000000001: NLH (₮0.10/₮0.25) - "
        "2026/05/23 18:00:00 PDT\n"
        "Table 'a' heads-up Seat #1 is the button\n"
        "Seat 1: Hero (₮25.00 in chips)\n"
        "Seat 2: villain (₮25.00 in chips)\n"
        "Hero: posts small blind ₮0.10\n"
        "villain: posts big blind ₮0.25\n"
        "*** HOLE CARDS ***\n"
        "Dealt to Hero [Ad Ac]\n"
        "Hero: folds\n"
        "villain collected ₮0.35 from pot\n"
        "*** SUMMARY ***\n"
        "Total pot ₮0.35 | Rake ₮0.00 | Splash Fee ₮0.00\n"
    )
    second = first.replace("#7000000001", "#7000000002").replace(
        "Table 'a'", "Table 'b'"
    )

    hands = list(parse_hands((first + "\n" + second).splitlines(keepends=True)))

    assert [hand.coinpoker_hand_id for hand in hands] == [7000000001, 7000000002]
    assert hands[0].raw_text.startswith("CoinPoker Hand #7000000001")


def test_unsupported_variant_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="unsupported"):
        parse_hand(
            [
                "CoinPoker Hand #8000000001: PLO (₮0.10/₮0.25) - "
                "2026/05/23 18:00:00 PDT",
            ]
        )
