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


def test_parse_hand_with_anonymized_hero_id() -> None:
    """CoinPoker exports use hashed player ids instead of the literal name Hero."""
    hand = parse_hand(
        [
            "CoinPoker Hand #5305071014: NLH (₮0.10/₮0.25) 2026/05/23 02:31:43 PDT",
            "Table '201122' 6-max Seat #3 is the button",
            "Seat 1: alpha (₮25.00 in chips)",
            "Seat 2: bravo (₮25.00 in chips)",
            "Seat 3: charlie (₮25.00 in chips)",
            "Seat 4: f75b28c7 (₮26.85 in chips)",
            "Seat 5: villain (₮25.00 in chips)",
            "Seat 6: foxtrot (₮25.00 in chips)",
            "f75b28c7: posts small blind ₮0.10",
            "villain: posts big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to f75b28c7",
            "[Kc 9d]",
            "foxtrot: folds",
            "alpha: folds",
            "bravo: folds",
            "charlie: folds",
            "f75b28c7: raises ₮0.65 to ₮0.90",
            "villain: folds",
            "Uncalled bet (₮0.50) returned to f75b28c7",
            "f75b28c7 collected ₮1.80 from pot",
            "*** SUMMARY ***",
            "Total pot ₮2.30 | Rake ₮0.48 | Splash Fee ₮0.02",
        ]
    )

    assert hand.coinpoker_hand_id == 5305071014
    assert hand.hero_cards == ["Kc", "9d"]
    assert hand.hero_seat == 4
    hero = next(player for player in hand.players if player.is_hero)
    assert hero.screen_name == "f75b28c7"
    assert hand.hero_net > 0


def test_parse_hand_header_without_dash_before_timestamp() -> None:
    """Real CoinPoker exports often omit the dash before the played-at timestamp."""
    hand = parse_hand(
        [
            "CoinPoker Hand #5305071014: NLH (₮0.10/₮0.25) 2026/05/23 02:31:43 PDT",
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
            "villain: folds",
            "Uncalled bet (₮0.50) returned to Hero",
            "Hero collected ₮1.80 from pot",
            "*** SUMMARY ***",
            "Total pot ₮2.30 | Rake ₮0.48 | Splash Fee ₮0.02",
        ]
    )

    assert hand.coinpoker_hand_id == 5305071014
    assert hand.played_at.year == 2026
    assert hand.played_at.month == 5
    assert hand.played_at.day == 23


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


def test_parse_hand_with_placeholder_dealt_lines_for_other_players() -> None:
    """CoinPoker lists anonymized 'Dealt to <id>' lines before Hero's cards."""
    hand = parse_hand(
        [
            "CoinPoker Hand #5305071014: NLH (₮0.10/₮0.25) 2026/05/23 02:31:43 PDT",
            "Table '201122' 6-max Seat #2 is the button",
            "Seat 1: f75b28c7 (₮20.92 in chips)",
            "Seat 2: d0df9435 (₮33.05 in chips)",
            "Seat 3: 3493fca0 (₮33.48 in chips)",
            "Seat 4: Hero (₮25 in chips)",
            "Seat 5: ab2a4e05 (₮25.50 in chips)",
            "3493fca0: posts small blind ₮0.10",
            "Hero: posts big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to f75b28c7",
            "Dealt to d0df9435",
            "Dealt to 3493fca0",
            "Dealt to Hero [Js 8h]",
            "Dealt to ab2a4e05",
            "ab2a4e05: folds",
            "Hero: folds",
            "3493fca0 collected ₮9.01 from pot",
            "*** SUMMARY ***",
            "Total pot ₮9.51 | Rake ₮0.48 | Splash Fee ₮0.02",
            "Hand was run once",
            "Board [ 6h 9d As Kd ]",
            "Game ended: 2026/05/23 02:33:16 PDT",
        ]
    )

    assert hand.hero_cards == ["Js", "8h"]
    hero = next(player for player in hand.players if player.is_hero)
    assert hero.screen_name == "Hero"


def test_parse_return_and_auto_big_blind() -> None:
    hand = parse_hand(
        [
            "CoinPoker Hand #5331371054: NLH (₮0.10/₮0.25) 2026/05/23 15:19:41 PDT",
            "Table '201122' 6-max Seat #2 is the button",
            "Seat 1: button (₮25 in chips)",
            "Seat 2: villain (₮18.68 in chips)",
            "Seat 3: bigblind (₮69 in chips)",
            "Seat 4: Hero (₮25 in chips)",
            "villain: posts small blind ₮0.10",
            "bigblind: posts big blind ₮0.25",
            "Hero: posts auto big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to villain",
            "Dealt to bigblind",
            "Dealt to Hero [Ah 6h]",
            "Hero: checks",
            "villain: raises ₮0.56 to ₮0.81",
            "bigblind: calls ₮0.56",
            "Hero: calls ₮0.56",
            "*** FLOP *** [Js Qc 6d]",
            "Hero: checks",
            "bigblind: bets ₮1.19",
            "Hero: folds",
            "bigblind: RETURN ₮1.19",
            "bigblind collected ₮2.29 from pot",
            "*** SUMMARY ***",
            "Total pot ₮2.43 | Rake ₮0.12 | Splash Fee ₮0.02",
        ]
    )

    assert hand.hero_cards == ["Ah", "6h"]
    assert any(action.action == "post_bb" and action.screen_name == "Hero" for action in hand.actions)


def test_parse_auto_bb_abbreviated() -> None:
    """AUTOBB is the abbreviated form of 'posts auto big blind' found in newer exports."""
    hand = parse_hand(
        [
            "CoinPoker Hand #65918100215: NLH (₮0.10/₮0.25) 2026/06/11 00:14:49 PDT",
            "Table '201371' 6-max Seat #1 is the button",
            "Seat 1: button (₮25.00 in chips)",
            "Seat 2: smallblind (₮25.00 in chips)",
            "Seat 3: bigblind (₮25.00 in chips)",
            "Seat 4: Hero (₮25.00 in chips)",
            "smallblind: posts small blind ₮0.10",
            "bigblind: posts big blind ₮0.25",
            "Hero: AUTOBB ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to Hero [Ac Ks]",
            "Hero: checks",
            "button: folds",
            "smallblind: folds",
            "bigblind: checks",
            "*** FLOP *** [2h 7d As]",
            "bigblind: checks",
            "Hero: bets ₮0.35",
            "bigblind: folds",
            "Hero collected ₮0.70 from pot",
            "*** SUMMARY ***",
            "Total pot ₮0.70 | Rake ₮0.00 | Splash Fee ₮0.00",
        ]
    )

    auto_action = next(
        action for action in hand.actions
        if action.action == "post_bb" and action.screen_name == "Hero"
    )
    assert auto_action.amount == Decimal("0.25")
    assert hand.hero_invested == Decimal("0.6000")  # 0.25 AUTOBB + 0.35 bet
    assert hand.stake_bb == Decimal("0.25")


def test_return_uncalled_bet_regression() -> None:
    """RETURN lines for uncalled bets that aren't returned to Hero are handled."""
    hand = parse_hand(
        [
            "CoinPoker Hand #67390700001: NLH (₮0.01/₮0.02) 2026/06/12 18:44:55 PDT",
            "Table '200589' 2-max Seat #1 is the button",
            "Seat 1: Hero (₮2.00 in chips)",
            "Seat 2: villain (₮2.00 in chips)",
            "Hero: posts small blind ₮0.01",
            "villain: posts big blind ₮0.02",
            "*** HOLE CARDS ***",
            "Dealt to Hero [Ts Js]",
            "Hero: raises ₮0.05 to ₮0.06",
            "villain: calls ₮0.04",
            "*** FLOP *** [9s 8s 2c]",
            "villain: checks",
            "Hero: bets ₮0.08",
            "villain: folds",
            "Hero: RETURN ₮0.05",
            "Hero collected ₮0.15 from pot",
            "*** SUMMARY ***",
            "Total pot ₮0.15 | Rake ₮0.00 | Splash Fee ₮0.00",
        ]
    )

    assert hand.table_size == 2
    assert hand.hero_position == "BTN/SB"
    # Hero invested: 0.01 (sb) + 0.05 (raise) + 0.08 (bet) - 0.05 (return) = 0.09
    assert hand.hero_invested == Decimal("0.0900")


def test_summary_seat_without_hole_cards() -> None:
    """Summary lines like 'Seat 3: 8141684d won (₮1.12)' should not throw."""
    hand = parse_hand(
        [
            "CoinPoker Hand #5312345001: NLH (₮0.10/₮0.25) 2026/06/11 01:00:00 PDT",
            "Table '201122' 6-max Seat #2 is the button",
            "Seat 1: aaa (₮25 in chips)",
            "Seat 2: bbb (₮25 in chips)",
            "Seat 3: Hero (₮25 in chips)",
            "Seat 4: ccc (₮25 in chips)",
            "ccc: posts small blind ₮0.10",
            "Hero: posts big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to Hero [7h 2c]",
            "aaa: folds",
            "bbb: folds",
            "Hero: checks",
            "ccc: raises ₮0.65 to ₮0.90",
            "Hero: folds",
            "ccc collected ₮0.90 from pot",
            "*** SUMMARY ***",
            "Total pot ₮0.90 | Rake ₮0.00 | Splash Fee ₮0.00",
            "Seat 3: Hero didn't show",
            "Seat 4: ccc won (₮0.90)",
        ]
    )

    assert hand.hero_cards == ["7h", "2c"]


def test_bomb_pot_header_and_first_second_flop() -> None:
    """BombPot variant with three stake parts and FIRST/SECOND FLOP markers."""
    hand = parse_hand(
        [
            "CoinPoker Hand #65918100216: NLH BombPot (₮0.10/₮0.25/₮0.50) "
            "2026/06/11 00:15:34 PDT",
            "Table '201371' 6-max Seat #1 is the button",
            "Seat 1: button (₮25 in chips)",
            "Seat 2: smallblind (₮25 in chips)",
            "Seat 3: bigblind (₮25 in chips)",
            "Seat 4: Hero (₮25 in chips)",
            "smallblind: posts ante ₮0.50",
            "bigblind: posts ante ₮0.50",
            "Hero: posts ante ₮0.50",
            "button: posts ante ₮0.50",
            "smallblind: posts small blind ₮0.10",
            "bigblind: posts big blind ₮0.25",
            "*** HOLE CARDS ***",
            "Dealt to Hero [Ah 9d]",
            "*** FIRST FLOP *** [3h 7c Ad]",
            "*** SECOND FLOP *** [Qs 5d 8c]",
            "Hero: bets ₮1.00",
            "button: folds",
            "smallblind: folds",
            "bigblind: folds",
            "Hero collected ₮5.00 from pot",
            "*** SUMMARY ***",
            "Total pot ₮5.00 | Rake ₮0.25 | Splash Fee ₮0.00",
        ]
    )

    assert hand.flags["bomb_pot"] is True
    assert hand.flags["bomb_pot_ante"] == Decimal("0.50")
    assert hand.stake_sb == Decimal("0.10")
    assert hand.stake_bb == Decimal("0.25")
    assert hand.flags["run_it_twice"] is True
    assert hand.flop == ["3h", "7c", "Ad"]
    # second flop cards stored in second_board
    assert hand.flags["second_board"] == ["Qs", "5d", "8c"]


def test_unsupported_variant_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="unsupported"):
        parse_hand(
            [
                "CoinPoker Hand #8000000001: PLO (₮0.10/₮0.25) - "
                "2026/05/23 18:00:00 PDT",
            ]
        )
