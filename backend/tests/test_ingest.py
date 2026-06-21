from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from app.parser.coinpoker import ParseError, parse_hand
from app.services.ingest import (
    _build_parse_summary,
    _split_hand_blocks,
    hand_from_parsed,
    parsed_to_summary_dict,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coinpoker"
GOLDEN_PATH = FIXTURE_DIR / "expected" / "hands.json"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "hand_001.txt",
        "hand_002.txt",
        "all_in_preflop.txt",
        "side_pot.txt",
        "uncalled_bet.txt",
    ],
)
def test_parsed_hand_mapping_matches_parser(fixture_name: str) -> None:
    text = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
    parsed = parse_hand(text.splitlines())
    mapping = parsed_to_summary_dict(parsed)

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    expected = golden[fixture_name]

    assert mapping["coinpoker_hand_id"] == expected["coinpoker_hand_id"]
    assert mapping["hero_net"] == Decimal(expected["hero_net"])
    assert mapping["hero_net_bb"] == Decimal(expected["hero_net_bb"])
    assert mapping["hero_cards"] == expected["hero_cards"]
    assert mapping["players_count"] == len(expected["players"])
    assert mapping["actions_count"] == len(expected["actions"])


def test_split_hand_blocks_two_valid() -> None:
    text = (FIXTURE_DIR / "multi_hand_file.txt").read_text(encoding="utf-8")
    blocks = _split_hand_blocks(text)
    assert len(blocks) >= 2
    # Every block should parse successfully since these are known-good fixtures.
    for block in blocks:
        parsed = parse_hand(block)
        assert parsed.coinpoker_hand_id > 0
        assert parsed.hero_cards


def test_split_hand_blocks_empty() -> None:
    assert _split_hand_blocks("") == []
    assert _split_hand_blocks("   \n  \n") == []


def test_split_hand_blocks_non_coinpoker_text() -> None:
    assert _split_hand_blocks("just some random notes\nno header here") == []


def test_build_parse_summary_clean() -> None:
    assert _build_parse_summary(10, 0, []) is None


def test_build_parse_summary_errors_only() -> None:
    result = _build_parse_summary(2, 0, ["error a", "error b"])
    assert result == "Imported 2 hands (2 parse errors)"


def test_build_parse_summary_duplicates_only() -> None:
    result = _build_parse_summary(5, 3, [])
    assert result == "Imported 5 hands (3 duplicates skipped)"


def test_build_parse_summary_mixed() -> None:
    result = _build_parse_summary(142, 5, ["bad hand #1", "bad hand #2", "bad #3"])
    assert "Imported 142 hands" in result
    assert "3 parse errors" in result
    assert "5 duplicates skipped" in result


def test_build_parse_summary_singular() -> None:
    result = _build_parse_summary(1, 1, ["oops"])
    assert "Imported 1 hand" in result
    assert "1 parse error" in result
    assert "1 duplicate skipped" in result


def test_hand_flags_are_json_serializable() -> None:
    import json
    from decimal import Decimal
    from uuid import uuid4

    text = (FIXTURE_DIR / "hand_001.txt").read_text(encoding="utf-8")
    parsed = parse_hand(text.splitlines())
    parsed.flags["bomb_pot_ante"] = Decimal("0.50")
    hand = hand_from_parsed(parsed, uuid4(), uuid4())
    json.dumps(hand.flags)
    assert hand.flags["bomb_pot_ante"] == "0.50"


@pytest.mark.asyncio
async def test_existing_coinpoker_ids_returns_scalar_ints() -> None:
    """Regression: .all() returned Row tuples so duplicate checks never matched."""
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL or DATABASE_URL for integration ingest tests")

    pytest.importorskip("asyncpg")
    from sqlalchemy import select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.database import async_engine
    from app.models import Hand
    from app.services.ingest import existing_coinpoker_ids

    async with AsyncSession(async_engine) as session:
        sample = (
            await session.exec(select(Hand.user_id, Hand.coinpoker_hand_id).limit(1))
        ).first()
        if sample is None:
            pytest.skip("no hands in database")
        user_id, hand_id = sample
        existing = await existing_coinpoker_ids(session, user_id, [hand_id])

    assert hand_id in existing
    assert all(isinstance(value, int) for value in existing)


def test_split_hand_blocks_with_one_broken_block() -> None:
    """A block that is valid header + nonsense content should be split out
    and will fail inside parse_hand(), but _split_hand_blocks still returns it."""
    text = (
        "CoinPoker Hand #9300000001: NLH (₮0.10/₮0.25) - 2026/05/23 15:41:00 PDT\n"
        "Table 'multi-a' heads-up Seat #1 is the button\n"
        "Seat 1: Hero (₮25.00 in chips)\n"
        "Seat 2: Villain001 (₮25.00 in chips)\n"
        "Hero: posts small blind ₮0.10\n"
        "Villain001: posts big blind ₮0.25\n"
        "*** HOLE CARDS ***\n"
        "Dealt to Hero [Ad Kc]\n"
        "Hero: raises ₮0.65 to ₮0.90\n"
        "Villain001: folds\n"
        "Uncalled bet (₮0.65) returned to Hero\n"
        "Hero collected ₮0.25 from pot\n"
        "*** SUMMARY ***\n"
        "Total pot ₮0.50 | Rake ₮0.00\n"
        "\n"
        "CoinPoker Hand #9999999999: NLH (₮0.10/₮0.25) - 2026/05/23 15:42:00 PDT\n"
        "garbage line that will cause a parse error\n"
        "\n"
        "CoinPoker Hand #9300000002: NLH (₮0.10/₮0.25) - 2026/05/23 15:41:30 PDT\n"
        "Table 'multi-a' heads-up Seat #1 is the button\n"
        "Seat 1: Hero (₮25.00 in chips)\n"
        "Seat 2: Villain002 (₮25.00 in chips)\n"
        "Hero: posts small blind ₮0.10\n"
        "Villain002: posts big blind ₮0.25\n"
        "*** HOLE CARDS ***\n"
        "Dealt to Hero [Kh Qh]\n"
        "Hero: calls ₮0.15\n"
        "Villain002: checks\n"
        "*** FLOP *** [2d 7c Js]\n"
        "Villain002: bets ₮0.50\n"
        "Hero: folds\n"
        "Uncalled bet (₮0.50) returned to Villain002\n"
        "Villain002 collected ₮0.50 from pot\n"
        "*** SUMMARY ***\n"
        "Total pot ₮0.50 | Rake ₮0.00\n"
    )
    blocks = _split_hand_blocks(text)
    assert len(blocks) == 3

    parsed = []
    errors = []
    for block in blocks:
        try:
            parsed.append(parse_hand(block))
        except ParseError as exc:
            errors.append(str(exc))

    assert len(parsed) == 2
    assert len(errors) == 1
    assert parsed[0].coinpoker_hand_id == 9300000001
    assert parsed[0].hero_cards == ["Ad", "Kc"]
    assert parsed[1].coinpoker_hand_id == 9300000002
    assert parsed[1].hero_cards == ["Kh", "Qh"]
    assert "9999999999" in errors[0] or "line" in errors[0].lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_upload_integration() -> None:
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL or DATABASE_URL for integration ingest tests")

    pytest.importorskip("asyncpg")