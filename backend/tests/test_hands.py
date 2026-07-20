"""
Integration tests for the /hands API endpoints (table_format + stakes filters).

Requires a PostgreSQL database accessible via TEST_DATABASE_URL or DATABASE_URL.
SQLite is NOT supported — the models use PostgreSQL-native types (ARRAY, JSONB).

Run with a local Postgres:
    TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/test_poker" pytest -m integration tests/test_hands.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.main import app
from app.models.tables import Hand, Upload

# The module-scoped engine owns asyncpg connections, so its tests and async
# fixtures must all run on the same module-scoped event loop.
pytestmark = pytest.mark.asyncio(loop_scope="module")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_db() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL (PostgreSQL) to run hands integration tests")
    return url


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _create_hand(
    session: AsyncSession,
    user_id: UUID,
    upload_id: UUID,
    *,
    table_size: int,
    stake_sb: str,
    stake_bb: str,
    hero_position: str = "BTN",
    hero_net: str = "0",
    hero_net_bb: str = "0",
    coinpoker_hand_id: int | None = None,
    played_at: datetime | None = None,
) -> UUID:
    """Insert a minimal Hand row and return its id."""
    hand = Hand(
        user_id=user_id,
        upload_id=upload_id,
        coinpoker_hand_id=coinpoker_hand_id or abs(hash(str(uuid4()))) % (10**9),
        played_at=played_at or _utc_now(),
        table_name="Test Table",
        table_size=table_size,
        stake_sb=Decimal(stake_sb),
        stake_bb=Decimal(stake_bb),
        button_seat=3,
        hero_seat=1,
        hero_position=hero_position,
        hero_cards=["Ac", "Kd"],
        hero_invested=Decimal("0"),
        hero_collected=Decimal("0"),
        hero_net=Decimal(hero_net),
        hero_net_bb=Decimal(hero_net_bb),
        total_pot=Decimal("0"),
    )
    session.add(hand)
    await session.flush()
    hand_id: UUID = hand.id  # type: ignore[assignment]
    return hand_id


# ---------------------------------------------------------------------------
# Pytest fixtures (module scope for engine, function scope for data isolation)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    url = _require_db()
    eng = create_async_engine(url, echo=False, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def session(engine):
    async with AsyncSession(engine) as s:
        yield s


@pytest_asyncio.fixture(loop_scope="module")
async def client(engine, session):
    """Return an httpx AsyncClient that talks directly to the FastAPI app.

    The app's get_session dependency is overridden to use *this* session
    so we can seed data and query it from within the same transaction.
    """
    from app.database import get_session

    async def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


@pytest_asyncio.fixture(loop_scope="module")
async def upload_id(session: AsyncSession, user_id: UUID) -> UUID:
    """Persist the upload referenced by seeded hands."""
    upload = Upload(
        user_id=user_id,
        filename="test_hands.txt",
        storage_path=f"test/{user_id}/test_hands.txt",
        sha256="b" * 64,
        status="parsed",
    )
    session.add(upload)
    await session.flush()
    return upload.id  # type: ignore[return-value]


@pytest_asyncio.fixture
def auth_headers(user_id: UUID):
    """Mock auth headers — we override get_current_user in tests, so just use
    the real user_id UUID in headers. The test overrides the auth dep below."""
    return {"Authorization": f"Bearer fake-token-for-{user_id}"}


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------

SEED_HANDS = [
    # table_size=2 (2-max table format)
    {"table_size": 2, "stake_sb": "0.01", "stake_bb": "0.02", "hero_position": "BTN", "hero_net": "-1"},
    {"table_size": 2, "stake_sb": "0.01", "stake_bb": "0.02", "hero_position": "BTN", "hero_net": "2"},
    {"table_size": 2, "stake_sb": "0.05", "stake_bb": "0.10", "hero_position": "BB", "hero_net": "-5"},
    # table_size=6 (6-max table format; postflop player count is independent)
    {"table_size": 6, "stake_sb": "0.10", "stake_bb": "0.25", "hero_position": "BTN", "hero_net": "-10"},
    {"table_size": 6, "stake_sb": "0.10", "stake_bb": "0.25", "hero_position": "CO", "hero_net": "3"},
    {"table_size": 6, "stake_sb": "0.50", "stake_bb": "1.00", "hero_position": "HJ", "hero_net": "-1"},
    # table_size=9 (9-max table format; postflop player count is independent)
    {"table_size": 9, "stake_sb": "0.10", "stake_bb": "0.25", "hero_position": "UTG", "hero_net": "-2"},
]


@pytest_asyncio.fixture(loop_scope="module")
async def seeded_hands(
    session: AsyncSession, user_id: UUID, upload_id: UUID
):
    """Insert the SEED_HANDS set and return the list of hand ids."""
    ids: list[UUID] = []
    for i, h in enumerate(SEED_HANDS):
        hid = await _create_hand(
            session,
            user_id,
            upload_id,
            table_size=h["table_size"],
            stake_sb=h["stake_sb"],
            stake_bb=h["stake_bb"],
            hero_position=h["hero_position"],
            hero_net=h["hero_net"],
            coinpoker_hand_id=1000 + i,
        )
        ids.append(hid)
    await session.commit()
    return ids


# ---------------------------------------------------------------------------
# Override auth dependency so we don't need real Supabase tokens
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def override_auth(client, user_id: UUID):
    """Make every request authenticate as `user_id`."""
    from app.auth import get_current_user

    async def _fake_user():
        return str(user_id)

    app.dependency_overrides[get_current_user] = _fake_user
    # We need to re-apply both overrides. The client fixture above already
    # set get_session. This fixture is autouse and will run after client,
    # so both overrides are active.
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: table_format filter
# ---------------------------------------------------------------------------


async def test_table_format_hu_2max_returns_only_table_size_2(
    client: AsyncClient, seeded_hands
):
    """GET /hands?table_format=hu_2max returns only 2-max hands."""
    resp = await client.get("/hands", params={"table_format": "hu_2max"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    for hand in data:
        assert hand["table_format"] == "hu_2max"
        assert "table_size" not in hand


async def test_table_format_6max_returns_only_table_size_6(
    client: AsyncClient, seeded_hands
):
    """GET /hands?table_format=6max does not conflate 6-max with multiway."""
    resp = await client.get("/hands", params={"table_format": "6max"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    for hand in data:
        assert hand["table_format"] == "6max"


async def test_table_format_9max_returns_only_table_size_9(
    client: AsyncClient, seeded_hands
):
    resp = await client.get("/hands", params={"table_format": "9max"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["table_format"] == "9max"


async def test_table_format_omitted_returns_all(
    client: AsyncClient, seeded_hands
):
    """Without table_format, all 7 hands are returned."""
    resp = await client.get("/hands")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 7


async def test_table_format_invalid_returns_422(
    client: AsyncClient, seeded_hands
):
    """Legacy aggregate values are invalid table formats."""
    resp = await client.get("/hands", params={"table_format": "multiway"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: stakes filter
# ---------------------------------------------------------------------------


async def test_stakes_filter_exact_match_001_002(
    client: AsyncClient, seeded_hands
):
    """GET /hands?stakes=0.01/0.02 returns only 0.01/0.02 hands."""
    resp = await client.get("/hands", params={"stakes": "0.01/0.02"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for hand in data:
        # Stake values come back as strings from JSON
        assert hand["stake_sb"] == "0.01"
        assert hand["stake_bb"] == "0.02"


async def test_stakes_filter_exact_match_010_025(
    client: AsyncClient, seeded_hands
):
    """GET /hands?stakes=0.10/0.25 returns only 0.10/0.25 hands (3 of them)."""
    resp = await client.get("/hands", params={"stakes": "0.10/0.25"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    for hand in data:
        assert hand["stake_sb"] == "0.10"
        assert hand["stake_bb"] == "0.25"


async def test_stakes_filter_no_match_returns_empty(
    client: AsyncClient, seeded_hands
):
    """Stakes with no matching hands returns empty list."""
    resp = await client.get("/hands", params={"stakes": "5.00/10.00"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 0


async def test_stakes_filter_invalid_format_returns_422(
    client: AsyncClient, seeded_hands
):
    """Invalid stakes format returns 422."""
    resp = await client.get("/hands", params={"stakes": "invalid"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: combined filters
# ---------------------------------------------------------------------------


async def test_combined_hu_2max_and_stakes(
    client: AsyncClient, seeded_hands
):
    """Exact table format and stakes filters compose."""
    resp = await client.get(
        "/hands",
        params={"table_format": "hu_2max", "stakes": "0.01/0.02"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for hand in data:
        assert hand["table_format"] == "hu_2max"
        assert hand["stake_sb"] == "0.01"
        assert hand["stake_bb"] == "0.02"


async def test_combined_6max_and_stakes(
    client: AsyncClient, seeded_hands
):
    """A 6-max filter does not include 9-max hands at the same stakes."""
    resp = await client.get(
        "/hands",
        params={"table_format": "6max", "stakes": "0.10/0.25"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    for hand in data:
        assert hand["table_format"] == "6max"
        assert hand["stake_sb"] == "0.10"
        assert hand["stake_bb"] == "0.25"


async def test_combined_no_results_when_mismatch(
    client: AsyncClient, seeded_hands
):
    """An exact 2-max format plus unmatched stakes returns no hands."""
    resp = await client.get(
        "/hands",
        params={"table_format": "hu_2max", "stakes": "0.50/1.00"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 0


# ---------------------------------------------------------------------------
# Tests: find_biggest_losers with filters
# ---------------------------------------------------------------------------


async def test_losers_hu_2max_only(
    client: AsyncClient, seeded_hands
):
    """GET /hands/losers?table_format=hu_2max returns losing 2-max hands."""
    resp = await client.get("/hands/losers", params={"table_format": "hu_2max"})
    assert resp.status_code == 200
    data = resp.json()
    # HU seed hands: -1, +2 (win), -5 => losers: -1 and -5 (since -5 < -1, should be first)
    assert len(data) == 2
    for hand in data:
        assert hand["table_format"] == "hu_2max"
        assert float(hand["hero_net"]) < 0


async def test_losers_with_stakes_filter(
    client: AsyncClient, seeded_hands
):
    """GET /hands/losers?stakes=0.10/0.25 returns only losing hands at those stakes."""
    resp = await client.get("/hands/losers", params={"stakes": "0.10/0.25"})
    assert resp.status_code == 200
    data = resp.json()
    # 0.10/0.25 hands: -10 (6-max), +3 (6-max, win), -2 (9-max) => losers: -10, -2
    assert len(data) == 2
    for hand in data:
        assert hand["stake_sb"] == "0.10"
        assert hand["stake_bb"] == "0.25"
        assert float(hand["hero_net"]) < 0


async def test_losers_combined_table_format_and_stakes(
    client: AsyncClient, seeded_hands
):
    """GET /hands/losers combines exact 6-max and stakes filters."""
    resp = await client.get(
        "/hands/losers",
        params={"table_format": "6max", "stakes": "0.10/0.25"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    for hand in data:
        assert hand["table_format"] == "6max"
        assert hand["stake_sb"] == "0.10"
        assert hand["stake_bb"] == "0.25"
        assert float(hand["hero_net"]) < 0


# ---------------------------------------------------------------------------
# Tests: filter-options endpoint
# ---------------------------------------------------------------------------


async def test_filter_options_returns_distinct_stakes(
    client: AsyncClient, seeded_hands
):
    """GET /hands/filter-options returns stakes and exact table formats."""
    resp = await client.get("/hands/filter-options")
    assert resp.status_code == 200
    data = resp.json()

    assert "stakes" in data
    assert "table_formats" in data

    stakes = data["stakes"]
    assert isinstance(stakes, list)
    assert len(stakes) == 4  # 0.01/0.02, 0.05/0.10, 0.10/0.25, 0.50/1.00

    # Check one specific entry
    found_010_025 = [s for s in stakes if s["sb"] == "0.10" and s["bb"] == "0.25"]
    assert len(found_010_025) == 1
    assert found_010_025[0]["label"] == "0.10/0.25"

    # Should be ordered by bb ascending, then sb
    bb_values = [float(s["bb"]) for s in stakes]
    assert bb_values == sorted(bb_values)

    assert data["table_formats"] == ["hu_2max", "6max", "9max"]
    assert "game_modes" not in data


async def test_filter_options_empty_for_new_user(
    client: AsyncClient, session: AsyncSession
):
    """A user with no hands still gets the supported table formats."""
    # This test doesn't use seeded_hands — a different user_id
    resp = await client.get("/hands/filter-options")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stakes"] == []
    assert data["table_formats"] == ["hu_2max", "6max", "9max"]


# ---------------------------------------------------------------------------
# Tests: existing query params still work alongside new filters
# ---------------------------------------------------------------------------


async def test_existing_position_filter_still_works(
    client: AsyncClient, seeded_hands
):
    """Position filtering composes with the table-format filter."""
    resp = await client.get(
        "/hands",
        params={"table_format": "hu_2max", "position": "BTN"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # HU BTN hands: only the two 0.01/0.02 BTN hands
    assert len(data) == 2
    for hand in data:
        assert hand["table_format"] == "hu_2max"
        assert hand["hero_position"] == "BTN"


async def test_only_losses_with_table_format(
    client: AsyncClient, seeded_hands
):
    """only_losses=true combines with an exact table format."""
    resp = await client.get(
        "/hands",
        params={"table_format": "hu_2max", "only_losses": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2  # -1 and -5
    for hand in data:
        assert float(hand["hero_net"]) < 0
        assert hand["table_format"] == "hu_2max"
        assert "table_size" not in hand
