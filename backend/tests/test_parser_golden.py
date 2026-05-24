from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.parser import ParsedHand, parse_hand, parse_hands


EXPECTED_PATH = Path(__file__).parent / "fixtures" / "coinpoker" / "expected" / "hands.json"
EXCLUDED_SINGLE_HAND_FILES = {"multi_hand_file.txt"}


def canonical_hand(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    return parse_hand(text.splitlines()).model_dump(mode="json")


def single_hand_fixture_paths(fixture_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in fixture_dir.glob("*.txt")
        if path.name not in EXCLUDED_SINGLE_HAND_FILES
    )


def rewrite_snapshots(fixture_dir: Path) -> dict[str, object]:
    snapshots = {
        path.name: canonical_hand(path) for path in single_hand_fixture_paths(fixture_dir)
    }
    EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPECTED_PATH.write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return snapshots


@pytest.fixture(scope="session")
def expected_snapshots(
    pytestconfig: pytest.Config,
    coinpoker_fixture_dir: Path,
) -> dict[str, object]:
    if pytestconfig.getoption("--update-snapshots"):
        return rewrite_snapshots(coinpoker_fixture_dir)
    with EXPECTED_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "fixture_path" not in metafunc.fixturenames:
        return
    fixture_dir = Path(__file__).parent / "fixtures" / "coinpoker"
    paths = single_hand_fixture_paths(fixture_dir)
    metafunc.parametrize("fixture_path", paths, ids=[path.name for path in paths])


def test_golden_fixture_matches_snapshot(
    fixture_path: Path,
    expected_snapshots: dict[str, object],
) -> None:
    canonical = canonical_hand(fixture_path)
    assert fixture_path.name in expected_snapshots
    assert canonical == expected_snapshots[fixture_path.name]
    assert ParsedHand.model_validate(expected_snapshots[fixture_path.name]).model_dump(
        mode="json"
    ) == canonical


@pytest.mark.parametrize(
    ("filename", "flag_name"),
    [
        ("all_in_preflop.txt", "all_in"),
        ("side_pot.txt", "side_pots"),
        ("split_pot.txt", "split_pot"),
        ("run_it_twice.txt", "run_it_twice"),
    ],
)
def test_named_edge_fixture_flags(
    coinpoker_fixture_dir: Path,
    filename: str,
    flag_name: str,
) -> None:
    hand = parse_hand(
        (coinpoker_fixture_dir / filename).read_text(encoding="utf-8").splitlines()
    )
    assert hand.flags[flag_name] is True


def test_uncalled_bet_fixture_reflects_return(coinpoker_fixture_dir: Path) -> None:
    hand = parse_hand(
        (coinpoker_fixture_dir / "uncalled_bet.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert hand.hero_net == hand.hero_collected - hand.hero_invested
    assert hand.hero_invested < hand.hero_collected


def test_three_bet_fixture_preserves_raise_increment_and_total(
    coinpoker_fixture_dir: Path,
) -> None:
    hand = parse_hand(
        (coinpoker_fixture_dir / "three_bet_raise_sizing.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    raises = [action for action in hand.actions if action.action == "raise"]
    assert any(action.amount is not None and action.raise_to is not None for action in raises)
    assert all(
        action.raise_to > action.amount
        for action in raises
        if action.amount is not None and action.raise_to is not None
    )


def test_parse_multi_hand_file(coinpoker_fixture_dir: Path) -> None:
    with (coinpoker_fixture_dir / "multi_hand_file.txt").open(encoding="utf-8") as file:
        hands = list(parse_hands(file))

    ids = [hand.coinpoker_hand_id for hand in hands]
    assert len(hands) == 3
    assert len(ids) == len(set(ids))
    assert ids == [9300000001, 9300000002, 9300000003]
