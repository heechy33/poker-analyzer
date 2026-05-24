from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from app.parser.coinpoker import parse_hand, parse_hands
from app.services.ingest import parsed_to_summary_dict

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


def test_multi_hand_fixture_parse_count() -> None:
    text = (FIXTURE_DIR / "multi_hand_file.txt").read_text(encoding="utf-8")
    hands = list(parse_hands(text.splitlines()))
    assert len(hands) >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_upload_integration() -> None:
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL or DATABASE_URL for integration ingest tests")

    pytest.importorskip("asyncpg")
