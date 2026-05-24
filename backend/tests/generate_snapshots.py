from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.parser import parse_hand  # noqa: E402


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"
EXPECTED_DIR = FIXTURE_DIR / "expected"
EXPECTED_PATH = EXPECTED_DIR / "hands.json"
EXCLUDED_SINGLE_HAND_FILES = {"multi_hand_file.txt"}
TETHER_SIGN = "\u20ae"
MOJIBAKE_TETHER_SIGN = "\u00e2\u201a\u00ae"


def canonical_fixture(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    hand = parse_hand(text.splitlines())
    return hand.model_dump(mode="json")


def single_hand_fixture_paths() -> list[Path]:
    return sorted(
        path
        for path in FIXTURE_DIR.glob("*.txt")
        if path.name not in EXCLUDED_SINGLE_HAND_FILES
    )


def rewrite_snapshots() -> None:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = {
        path.name: canonical_fixture(path) for path in single_hand_fixture_paths()
    }
    EXPECTED_PATH.write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalize_currency(text: str) -> str:
    return text.replace(MOJIBAKE_TETHER_SIGN, TETHER_SIGN)


def seed_synthetic_fixtures(*, force: bool = False) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    readme = FIXTURE_DIR / "README.md"
    if force or not readme.exists():
        readme.write_text(
            "Anonymized real CoinPoker hands; do not commit PII\n\n"
            "Current corpus is synthetic but format-faithful until real "
            "anonymized exports are available.\n",
            encoding="utf-8",
        )

    fixtures = synthetic_fixtures()
    for filename, text in fixtures.items():
        path = FIXTURE_DIR / filename
        if force or not path.exists():
            path.write_text(normalize_currency(text.strip()) + "\n", encoding="utf-8")


def synthetic_fixtures() -> dict[str, str]:
    fixtures: dict[str, str] = {}
    card_pairs = [
        ("As", "Kd"),
        ("Qh", "Qs"),
        ("Jc", "Tc"),
        ("9d", "9s"),
        ("Ah", "5h"),
        ("Kc", "Qc"),
        ("7s", "6s"),
        ("Ad", "Ac"),
        ("Th", "Td"),
        ("8c", "8h"),
    ]
    board_sets = [
        ("2c", "7d", "Jh", "Ks", "3c"),
        ("Kd", "9c", "Td", "2s", "4h"),
        ("Ac", "Kc", "Qd", "Jd", "2h"),
        ("5s", "6s", "7d", "8h", "9c"),
        ("Qc", "3d", "3s", "Ah", "Ts"),
    ]

    for idx in range(1, 31):
        hand_id = 9100000000 + idx
        hero_cards = card_pairs[(idx - 1) % len(card_pairs)]
        board = board_sets[(idx - 1) % len(board_sets)]
        if idx % 5 == 0:
            text = showdown_loss_hand(
                hand_id=hand_id,
                table_name=f"hand-{idx:03d}",
                hero_cards=hero_cards,
                board=board,
                minute=idx,
            )
        elif idx % 4 == 0:
            text = cbet_uncalled_hand(
                hand_id=hand_id,
                table_name=f"hand-{idx:03d}",
                hero_cards=hero_cards,
                board=board[:3],
                minute=idx,
            )
        elif idx % 3 == 0:
            text = three_bet_hand(
                hand_id=hand_id,
                table_name=f"hand-{idx:03d}",
                hero_cards=hero_cards,
                board=board[:3],
                minute=idx,
            )
        else:
            text = preflop_win_hand(
                hand_id=hand_id,
                table_name=f"hand-{idx:03d}",
                hero_cards=hero_cards,
                minute=idx,
            )
        fixtures[f"hand_{idx:03d}.txt"] = text

    fixtures.update(
        {
            "all_in_preflop.txt": all_in_preflop_hand(),
            "side_pot.txt": side_pot_hand(),
            "split_pot.txt": split_pot_hand(),
            "run_it_twice.txt": run_it_twice_hand(),
            "sitout_timeout.txt": sitout_timeout_hand(),
            "uncalled_bet.txt": cbet_uncalled_hand(
                hand_id=9200000006,
                table_name="uncalled-bet",
                hero_cards=("Kc", "9d"),
                board=("Kd", "9c", "Td"),
                minute=36,
            ),
            "muck_no_showdown.txt": muck_hand(),
            "dead_blind_post.txt": dead_blind_post_hand(),
            "three_bet_raise_sizing.txt": three_bet_hand(
                hand_id=9200000009,
                table_name="three-bet-sizing",
                hero_cards=("Ah", "Kh"),
                board=("Ac", "7s", "2d"),
                minute=39,
            ),
            "splash_fee_summary.txt": splash_fee_hand(),
        }
    )

    multi = "\n\n".join(
        [
            preflop_win_hand(
                hand_id=9300000001,
                table_name="multi-a",
                hero_cards=("Ad", "Kc"),
                minute=41,
            ),
            cbet_uncalled_hand(
                hand_id=9300000002,
                table_name="multi-b",
                hero_cards=("Qd", "Qh"),
                board=("Qs", "8d", "2c"),
                minute=42,
            ),
            showdown_loss_hand(
                hand_id=9300000003,
                table_name="multi-c",
                hero_cards=("Jd", "Td"),
                board=("2h", "7c", "Js", "4d", "9s"),
                minute=43,
            ),
        ]
    )
    fixtures["multi_hand_file.txt"] = multi
    return fixtures


def header(hand_id: int, table_name: str, minute: int, stake: str = "₮0.10/₮0.25") -> str:
    return (
        f"CoinPoker Hand #{hand_id}: NLH ({stake}) - "
        f"2026/05/23 15:{minute:02d}:00 PDT\n"
        f"Table '{table_name}' heads-up Seat #1 is the button"
    )


def preflop_win_hand(
    *,
    hand_id: int,
    table_name: str,
    hero_cards: tuple[str, str],
    minute: int,
) -> str:
    return f"""
{header(hand_id, table_name, minute)}
Seat 1: Hero (₮25.00 in chips)
Seat 2: Villain{hand_id % 1000:03d} (₮25.00 in chips)
Hero: posts small blind ₮0.10
Villain{hand_id % 1000:03d}: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [{hero_cards[0]} {hero_cards[1]}]
Hero: raises ₮0.65 to ₮0.90
Villain{hand_id % 1000:03d}: folds
Hero collected ₮1.15 from pot
*** SUMMARY ***
Total pot ₮1.15 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def cbet_uncalled_hand(
    *,
    hand_id: int,
    table_name: str,
    hero_cards: tuple[str, str],
    board: tuple[str, str, str],
    minute: int,
) -> str:
    return f"""
{header(hand_id, table_name, minute)}
Seat 1: Hero (₮26.85 in chips)
Seat 2: Villain{hand_id % 1000:03d} (₮25.00 in chips)
Hero: posts small blind ₮0.10
Villain{hand_id % 1000:03d}: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [{hero_cards[0]} {hero_cards[1]}]
Hero: raises ₮0.65 to ₮0.90
Villain{hand_id % 1000:03d}: calls ₮0.65
*** FLOP *** [{board[0]} {board[1]} {board[2]}]
Hero: bets ₮1.20
Villain{hand_id % 1000:03d}: folds
Uncalled bet (₮0.50) returned to Hero
Hero collected ₮1.80 from pot
*** SUMMARY ***
Total pot ₮2.30 | Rake ₮0.48 | Splash Fee ₮0.02
"""


def showdown_loss_hand(
    *,
    hand_id: int,
    table_name: str,
    hero_cards: tuple[str, str],
    board: tuple[str, str, str, str, str],
    minute: int,
) -> str:
    villain = f"Villain{hand_id % 1000:03d}"
    return f"""
{header(hand_id, table_name, minute)}
Seat 1: Hero (₮25.00 in chips)
Seat 2: {villain} (₮25.00 in chips)
Hero: posts small blind ₮0.10
{villain}: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [{hero_cards[0]} {hero_cards[1]}]
Hero: calls ₮0.15
{villain}: checks
*** FLOP *** [{board[0]} {board[1]} {board[2]}]
{villain}: checks
Hero: checks
*** TURN *** [{board[0]} {board[1]} {board[2]}] [{board[3]}]
{villain}: bets ₮0.50
Hero: calls ₮0.50
*** RIVER *** [{board[0]} {board[1]} {board[2]} {board[3]}] [{board[4]}]
{villain}: checks
Hero: checks
*** SHOWDOWN ***
Hero: shows [{hero_cards[0]} {hero_cards[1]}] (High Card)
{villain}: shows [Jc Td] (Pair)
{villain} collected ₮1.75 from pot
*** SUMMARY ***
Total pot ₮1.75 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def three_bet_hand(
    *,
    hand_id: int,
    table_name: str,
    hero_cards: tuple[str, str],
    board: tuple[str, str, str],
    minute: int,
) -> str:
    return f"""
CoinPoker Hand #{hand_id}: NLH (₮0.10/₮0.25) - 2026/05/23 15:{minute:02d}:00 PDT
Table '{table_name}' 6-max Seat #3 is the button
Seat 1: Opener (₮25.00 in chips)
Seat 2: Hero (₮25.00 in chips)
Seat 3: Button (₮25.00 in chips)
Seat 4: SmallBlind (₮25.00 in chips)
Seat 5: BigBlind (₮25.00 in chips)
SmallBlind: posts small blind ₮0.10
BigBlind: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [{hero_cards[0]} {hero_cards[1]}]
Opener: raises ₮0.65 to ₮0.90
Hero: raises ₮2.10 to ₮3.00
Button: folds
SmallBlind: folds
BigBlind: folds
Opener: calls ₮2.10
*** FLOP *** [{board[0]} {board[1]} {board[2]}]
Opener: checks
Hero: bets ₮4.00
Opener: folds
Uncalled bet (₮4.00) returned to Hero
Hero collected ₮6.35 from pot
*** SUMMARY ***
Total pot ₮6.35 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def all_in_preflop_hand() -> str:
    return """
CoinPoker Hand #9200000001: NLH (₮0.50/₮1.00) - 2026/05/23 16:01:00 PDT
Table 'all-in-preflop' heads-up Seat #1 is the button
Seat 1: Hero (₮5.00 in chips)
Seat 2: AllinCaller (₮20.00 in chips)
Hero: posts small blind ₮0.50
AllinCaller: posts big blind ₮1.00
*** HOLE CARDS ***
Dealt to Hero [As Ah]
Hero: raises ₮4.50 to ₮5.00 and is all-in
AllinCaller: calls ₮4.00
*** FLOP *** [Ac Kd Qh]
*** TURN *** [Ac Kd Qh] [2s]
*** RIVER *** [Ac Kd Qh 2s] [2d]
*** SHOWDOWN ***
Hero: shows [As Ah] (Full House)
AllinCaller: shows [Ks Kh] (Full House)
Hero collected ₮10.00 from pot
*** SUMMARY ***
Total pot ₮10.00 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def side_pot_hand() -> str:
    return """
CoinPoker Hand #9200000002: NLH (₮0.50/₮1.00) - 2026/05/23 16:02:00 PDT
Table 'side-pot' 6-max Seat #1 is the button
Seat 1: Hero (₮20.00 in chips)
Seat 2: ShortStack (₮5.00 in chips)
Seat 3: BigStack (₮30.00 in chips)
Hero: posts small blind ₮0.50
ShortStack: posts big blind ₮1.00
*** HOLE CARDS ***
Dealt to Hero [Ad Ac]
BigStack: raises ₮3.00 to ₮4.00
Hero: calls ₮3.50
ShortStack: calls ₮4.00 and is all-in
*** FLOP *** [2c 7d Jh]
Hero: bets ₮10.00
BigStack: calls ₮10.00
*** TURN *** [2c 7d Jh] [Ks]
Hero: checks
BigStack: checks
*** RIVER *** [2c 7d Jh Ks] [3c]
Hero: checks
BigStack: checks
*** SHOWDOWN ***
Hero: shows [Ad Ac] (Pair)
ShortStack: shows [Kc Qc] (Pair)
BigStack: shows [Jc Tc] (Pair)
ShortStack collected ₮15.00 from main pot
Hero collected ₮20.00 from side pot
*** SUMMARY ***
Total pot ₮35.00 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def split_pot_hand() -> str:
    return """
CoinPoker Hand #9200000003: NLH (₮0.50/₮1.00) - 2026/05/23 16:03:00 PDT
Table 'split-pot' heads-up Seat #1 is the button
Seat 1: Hero (₮20.00 in chips)
Seat 2: ChopShop (₮20.00 in chips)
Hero: posts small blind ₮0.50
ChopShop: posts big blind ₮1.00
*** HOLE CARDS ***
Dealt to Hero [Ah Qh]
Hero: raises ₮2.50 to ₮3.00
ChopShop: calls ₮2.00
*** FLOP *** [Ad Kd 2c]
ChopShop: checks
Hero: bets ₮2.00
ChopShop: calls ₮2.00
*** TURN *** [Ad Kd 2c] [2s]
ChopShop: checks
Hero: checks
*** RIVER *** [Ad Kd 2c 2s] [Kc]
ChopShop: checks
Hero: checks
*** SHOWDOWN ***
Hero: shows [Ah Qh] (Two Pair)
ChopShop: shows [As Qs] (Two Pair)
Hero collected ₮5.00 from pot
ChopShop collected ₮5.00 from pot
*** SUMMARY ***
Total pot ₮10.00 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def run_it_twice_hand() -> str:
    return """
CoinPoker Hand #9200000004: NLH (₮0.50/₮1.00) - 2026/05/23 16:04:00 PDT
Table 'run-it-twice' heads-up Seat #1 is the button
Seat 1: Hero (₮50.00 in chips)
Seat 2: TwiceVillain (₮50.00 in chips)
Hero: posts small blind ₮0.50
TwiceVillain: posts big blind ₮1.00
*** HOLE CARDS ***
Dealt to Hero [As Ah]
Hero: raises ₮2.50 to ₮3.00
TwiceVillain: raises ₮47.00 to ₮50.00 and is all-in
Hero: calls ₮47.00 and is all-in
*** FLOP *** [Ac Kd Qh]
*** TURN *** [Ac Kd Qh] [2s]
*** FIRST RIVER *** [Ac Kd Qh 2s] [2d]
*** SECOND RIVER *** [Ac Kd Qh 2s] [Jc]
*** SHOWDOWN ***
Hero: shows [As Ah] (Full House)
TwiceVillain: shows [Tc Js] (Straight)
Hero collected ₮50.00 from main pot
TwiceVillain collected ₮50.00 from main pot
Hero collected ₮10.00 from side pot
*** SUMMARY ***
Total pot ₮110.00 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def sitout_timeout_hand() -> str:
    return """
CoinPoker Hand #9200000005: NLH (₮0.10/₮0.25) - 2026/05/23 16:05:00 PDT
Table 'sitout-timeout' 6-max Seat #2 is the button
Seat 1: Hero (₮25.00 in chips)
Seat 2: Button (₮25.00 in chips)
Seat 3: SmallBlind (₮25.00 in chips)
Seat 4: BigBlind (₮25.00 in chips)
SmallBlind: posts small blind ₮0.10
BigBlind: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [Qs Qh]
Hero: raises ₮0.60 to ₮0.85
Button: has timed out
SmallBlind: is sitting out
BigBlind: folds
Hero collected ₮1.15 from pot
*** SUMMARY ***
Total pot ₮1.15 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def muck_hand() -> str:
    return """
CoinPoker Hand #9200000007: NLH (₮0.10/₮0.25) - 2026/05/23 16:07:00 PDT
Table 'muck-no-showdown' heads-up Seat #1 is the button
Seat 1: Hero (₮25.00 in chips)
Seat 2: Mucker (₮25.00 in chips)
Hero: posts small blind ₮0.10
Mucker: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [8c 8d]
Hero: calls ₮0.15
Mucker: checks
*** FLOP *** [2s 3d 4h]
Mucker: checks
Hero: checks
*** TURN *** [2s 3d 4h] [5c]
Mucker: bets ₮0.50
Hero: calls ₮0.50
*** RIVER *** [2s 3d 4h 5c] [6s]
Mucker: bets ₮1.00
Hero: calls ₮1.00
*** SHOWDOWN ***
Hero: mucks hand
Mucker collected ₮3.30 from pot
*** SUMMARY ***
Total pot ₮3.30 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def dead_blind_post_hand() -> str:
    return """
CoinPoker Hand #9200000008: NLH (₮0.10/₮0.25) - 2026/05/23 16:08:00 PDT
Table 'dead-blind-post' 6-max Seat #1 is the button
Seat 1: Hero (₮25.00 in chips)
Seat 2: SmallBlind (₮25.00 in chips)
Seat 4: PostedOutOfOrder (₮25.00 in chips)
SmallBlind: posts small blind ₮0.10
PostedOutOfOrder: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [Ac Js]
Hero: raises ₮0.75 to ₮1.00
SmallBlind: folds
PostedOutOfOrder: folds
Hero collected ₮1.35 from pot
*** SUMMARY ***
Total pot ₮1.35 | Rake ₮0.00 | Splash Fee ₮0.00
"""


def splash_fee_hand() -> str:
    return """
CoinPoker Hand #9200000010: NLH (₮0.10/₮0.25) - 2026/05/23 16:10:00 PDT
Table 'splash-fee' heads-up Seat #1 is the button
Seat 1: Hero (₮25.00 in chips)
Seat 2: Splashy (₮25.00 in chips)
Hero: posts small blind ₮0.10
Splashy: posts big blind ₮0.25
*** HOLE CARDS ***
Dealt to Hero [Kc Qc]
Hero: raises ₮0.65 to ₮0.90
Splashy: calls ₮0.65
*** FLOP *** [Kh 7d 2s]
Splashy: checks
Hero: bets ₮1.00
Splashy: calls ₮1.00
*** TURN *** [Kh 7d 2s] [3c]
Splashy: checks
Hero: checks
*** RIVER *** [Kh 7d 2s 3c] [9h]
Splashy: checks
Hero: checks
*** SHOWDOWN ***
Hero: shows [Kc Qc] (Pair)
Splashy: shows [Qh Jh] (High Card)
Hero collected ₮3.60 from pot
*** SUMMARY ***
Total pot ₮3.80 | Rake ₮0.18 | Splash Fee ₮0.02
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate CoinPoker parser snapshots from fixture files."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Rewrite expected/hands.json from current fixtures.",
    )
    parser.add_argument(
        "--seed-synthetic-fixtures",
        action="store_true",
        help="Create the synthetic fixture corpus if fixture files are missing.",
    )
    parser.add_argument(
        "--force-fixtures",
        action="store_true",
        help="Overwrite existing synthetic fixture files when seeding.",
    )
    args = parser.parse_args()

    if args.seed_synthetic_fixtures:
        seed_synthetic_fixtures(force=args.force_fixtures)
    if args.update:
        rewrite_snapshots()
    if not args.seed_synthetic_fixtures and not args.update:
        parser.print_help()


if __name__ == "__main__":
    main()
