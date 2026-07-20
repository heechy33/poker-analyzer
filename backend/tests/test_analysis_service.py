"""Integration tests for the hand-analysis service (T08).

These cover the acceptance criteria:

* First call invokes the (mocked) Anthropic client; second identical call
  returns the cached row without invoking it again.
* Invalid leak tags returned by the model are stripped before persistence.
* ``prompt_hash`` dedupes repeat general-coaching requests: changing the
  street produces a new analysis row, identical inputs do not.
* Persisted responses carry the required solver-free coaching label.

Requires a PostgreSQL database (the models use PG-native ARRAY / JSONB).
Set ``TEST_DATABASE_URL`` or ``DATABASE_URL`` to run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.llm import GENERAL_COACHING_LABEL, LEAK_TAGS, AnalysisResult, StreamChunk
from app.llm.prompts import compute_prompt_hash
from app.models import Hand, HandAction, HandPlayer, Upload
from app.services.analysis import (
    find_cached_analysis,
    get_or_create_analysis,
    list_analyses_for_hand,
)

pytestmark = pytest.mark.asyncio


def _require_db() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip(
            "Set TEST_DATABASE_URL (PostgreSQL) to run analysis service tests"
        )
    return url


# ---------------------------------------------------------------------------
# Fake LLM client
# ---------------------------------------------------------------------------


@dataclass
class FakeLLM:
    """LLMClient that records calls and returns canned text."""

    model: str = "claude-sonnet-fake"
    response_text: str = (
        '{"analysis": "Hero overbluffs the river here.", '
        '"leak_tags": ["overbluff_river", "made_up_tag_that_does_not_exist"]}'
    )
    call_count: int = 0
    captured_prompts: list[str] = field(default_factory=list)

    async def analyze(self, *, system: str, user_prompt: str) -> AnalysisResult:
        self.call_count += 1
        self.captured_prompts.append(user_prompt)
        return AnalysisResult(
            text=self.response_text,
            model=self.model,
            input_tokens=42,
            output_tokens=17,
        )

    async def stream_analyze(
        self, *, system: str, user_prompt: str
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover - not exercised here
        result = await self.analyze(system=system, user_prompt=user_prompt)
        yield StreamChunk(type="token", text=result.text)
        yield StreamChunk(type="final", result=result)


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def engine():
    url = _require_db()
    eng = create_async_engine(url, echo=False, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    async with AsyncSession(engine) as s:
        yield s


@pytest_asyncio.fixture
async def user_id() -> UUID:
    return uuid4()


@pytest_asyncio.fixture
async def upload_id(session: AsyncSession, user_id: UUID) -> UUID:
    upload = Upload(
        user_id=user_id,
        filename="analyze.txt",
        storage_path=f"test/{user_id}/analyze.txt",
        sha256="c" * 64,
        status="parsed",
    )
    session.add(upload)
    await session.flush()
    await session.commit()
    return upload.id  # type: ignore[return-value]


@pytest_asyncio.fixture
async def hand_id(
    session: AsyncSession, user_id: UUID, upload_id: UUID
) -> UUID:
    hand = Hand(
        user_id=user_id,
        upload_id=upload_id,
        coinpoker_hand_id=abs(hash(str(uuid4()))) % (10**9),
        played_at=datetime.now(timezone.utc),
        table_name="Analyze Table",
        table_size=6,
        stake_sb=Decimal("0.10"),
        stake_bb=Decimal("0.25"),
        button_seat=3,
        hero_seat=1,
        hero_position="BTN",
        hero_cards=["As", "Kd"],
        flop=["2h", "7c", "Td"],
        turn="3s",
        river="9d",
        total_pot=Decimal("5.00"),
        hero_invested=Decimal("2.00"),
        hero_collected=Decimal("0"),
        hero_net=Decimal("-2.00"),
        hero_net_bb=Decimal("-8.00"),
    )
    session.add(hand)
    await session.flush()

    session.add(
        HandPlayer(
            hand_id=hand.id,
            user_id=user_id,
            seat=1,
            screen_name="Hero",
            position="BTN",
            starting_stack=Decimal("25.00"),
            is_hero=True,
        )
    )
    session.add(
        HandPlayer(
            hand_id=hand.id,
            user_id=user_id,
            seat=2,
            screen_name="Villain",
            position="BB",
            starting_stack=Decimal("25.00"),
            is_hero=False,
        )
    )

    session.add(
        HandAction(
            hand_id=hand.id,
            user_id=user_id,
            street="preflop",
            action_order=1,
            seat=2,
            screen_name="Villain",
            action="post_bb",
            amount=Decimal("0.25"),
        )
    )
    session.add(
        HandAction(
            hand_id=hand.id,
            user_id=user_id,
            street="preflop",
            action_order=2,
            seat=1,
            screen_name="Hero",
            action="raise",
            amount=Decimal("0.75"),
            raise_to=Decimal("0.75"),
        )
    )
    session.add(
        HandAction(
            hand_id=hand.id,
            user_id=user_id,
            street="preflop",
            action_order=3,
            seat=2,
            screen_name="Villain",
            action="call",
            amount=Decimal("0.50"),
        )
    )
    await session.commit()
    return hand.id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_first_call_invokes_llm_and_persists(
    session: AsyncSession, user_id: UUID, hand_id: UUID
) -> None:
    llm = FakeLLM()
    outcome = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="river",
        llm=llm,
    )

    assert outcome.cached is False
    assert llm.call_count == 1
    assert outcome.analysis.analysis_text == (
        f"{GENERAL_COACHING_LABEL}\n\nHero overbluffs the river here."
    )
    assert outcome.analysis.input_tokens == 42
    assert outcome.analysis.output_tokens == 17
    assert outcome.analysis.model == "claude-sonnet-fake"


async def test_invalid_tags_stripped_on_persist(
    session: AsyncSession, user_id: UUID, hand_id: UUID
) -> None:
    llm = FakeLLM()
    outcome = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="river",
        llm=llm,
    )
    assert outcome.analysis.leak_tags == ["overbluff_river"]
    # And every persisted tag is in the whitelist.
    for tag in outcome.analysis.leak_tags:
        assert tag in LEAK_TAGS


async def test_second_call_with_same_hash_hits_cache(
    session: AsyncSession, user_id: UUID, hand_id: UUID
) -> None:
    llm = FakeLLM()

    first = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="river",
        llm=llm,
    )
    assert first.cached is False
    assert llm.call_count == 1

    second = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="river",
        llm=llm,
    )
    assert second.cached is True
    # CRITICAL: the LLM client was NOT invoked again.
    assert llm.call_count == 1
    assert second.analysis.id == first.analysis.id


async def test_different_street_does_not_collide(
    session: AsyncSession, user_id: UUID, hand_id: UUID
) -> None:
    llm = FakeLLM()
    flop = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="flop",
        llm=llm,
    )
    turn = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="turn",
        llm=llm,
    )
    assert flop.analysis.id != turn.analysis.id
    assert flop.analysis.prompt_hash != turn.analysis.prompt_hash
    assert llm.call_count == 2


async def test_general_coaching_cache_key_works(
    session: AsyncSession, user_id: UUID, hand_id: UUID
) -> None:
    llm = FakeLLM()
    first = await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="river",
        llm=llm,
    )
    assert first.cached is False

    # And cache key still works (None == None hashes the same).
    cached = await find_cached_analysis(
        session,
        user_id,
        hand_id,
        compute_prompt_hash(hand_id, "river"),
    )
    assert cached is not None
    assert cached.id == first.analysis.id


async def test_list_analyses_returns_recent_first(
    session: AsyncSession, user_id: UUID, hand_id: UUID
) -> None:
    llm = FakeLLM()
    await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="flop",
        llm=llm,
    )
    await get_or_create_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        street="turn",
        llm=llm,
    )

    rows = await list_analyses_for_hand(session, user_id, hand_id)
    assert len(rows) >= 2
    # Sorted DESC by created_at — the second insert should come first.
    assert rows[0].created_at >= rows[1].created_at


async def test_missing_hand_raises_lookup_error(
    session: AsyncSession, user_id: UUID
) -> None:
    llm = FakeLLM()
    with pytest.raises(LookupError):
        await get_or_create_analysis(
            session,
            user_id=user_id,
            hand_id=uuid4(),
            street="river",
            llm=llm,
        )
