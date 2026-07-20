"""
Integration tests for the stats engine (T06).

Requires a PostgreSQL database accessible via TEST_DATABASE_URL or DATABASE_URL.
SQLite is NOT supported — the models use PostgreSQL-native types (ARRAY, JSONB).

Run with a local Postgres:
    TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/test_poker" pytest -m integration

Hand matrix used (hero_seat=1, stake_bb=1.0, all positions explicit):

  Hand | Position | VPIP | PFR | 3bet_opp | 3bet | saw_flop | WTSD | W$SD | net_bb
  -----+----------+------+-----+----------+------+----------+------+------+-------
    1  | BTN      |  T   |  F  |    F     |  F   |    T     |  F   |  –   |  -1.0
    2  | BTN      |  T   |  T  |    F     |  F   |    F     |  F   |  –   |  +1.5
    3  | BTN      |  T   |  T  |    T     |  T   |    F     |  F   |  –   |  +3.0
    4  | BB       |  F   |  F  |    T     |  F   |    F     |  F   |  –   |   0.0
    5  | BB       |  T   |  F  |    F     |  F   |    T     |  T   |  T   |  +5.0
    6  | BB       |  T   |  F  |    F     |  F   |    T     |  T   |  F   |  -5.0
    7  | BB       |  T   |  F  |    F     |  F   |    T     |  F   |  –   |  -1.0

Expected summary over all 7 hands:
  hands_count  = 7
  vpip_pct     = 6/7 ≈ 85.71
  pfr_pct      = 2/7 ≈ 28.57
  three_bet_pct= 1/2 = 50.00   (1 three_bet / 2 opportunities)
  wtsd_pct     = 2/4 = 50.00   (saw_flop: hands 1,5,6,7)
  wsd_pct      = 1/2 = 50.00
  bb_per_100   = (2.5 / 7) * 100 ≈ 35.71

BTN breakdown (hands 1–3):
  hands=3, vpip=3/3=100%, pfr=2/3≈66.67%, 3bet_opp=1, 3bet=1, 3bet_pct=100%
  saw_flop=1 (hand 1), wtsd=0/1=0%, wsd=N/A→0%
  bb_per_100 = ((-1+1.5+3)/3)*100 ≈ 116.67

BB breakdown (hands 4–7):
  hands=4, vpip=3/4=75%, pfr=0/4=0%, 3bet_opp=1, 3bet=0, 3bet_pct=0%
  saw_flop=3 (5,6,7), wtsd=2/3≈66.67%, wsd=1/2=50%
  bb_per_100 = ((0+5-5-1)/4)*100 = -25.00
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Hand, HandAction, HandPlayer, Upload
from app.stats.compute import compute_by_position, compute_stats

# ---------------------------------------------------------------------------
# Pytest marks
# ---------------------------------------------------------------------------

# The module-scoped engine owns asyncpg connections, so every test using it
# must run on the same module-scoped event loop.
pytestmark = pytest.mark.asyncio(loop_scope="module")


def _require_db() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL (PostgreSQL) to run stats integration tests")
    return url


# ---------------------------------------------------------------------------
# Fixtures
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
async def test_user_id() -> UUID:
    """Return a fresh UUID so tests never collide with existing data."""
    return uuid4()


@pytest_asyncio.fixture
async def upload_id(session: AsyncSession, test_user_id: UUID) -> UUID:
    uid = test_user_id
    upload = Upload(
        user_id=uid,
        filename="test_stats.txt",
        storage_path=f"test/{uid}/test_stats.txt",
        sha256="a" * 64,
        status="parsed",
    )
    session.add(upload)
    await session.flush()
    return upload.id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Helper: insert one hand + players + preflop actions
# ---------------------------------------------------------------------------

def _utc(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


async def _insert_hand(
    session: AsyncSession,
    user_id: UUID,
    upload_id: UUID,
    *,
    hero_position: str,
    hero_net_bb: Decimal,
    flop: list[str] | None = None,
    went_to_showdown: bool = False,
    won_at_showdown: bool | None = None,
    played_at: datetime | None = None,
    preflop_actions: list[dict],
    hero_seat: int = 1,
    other_seat: int = 2,
) -> Hand:
    hand = Hand(
        user_id=user_id,
        upload_id=upload_id,
        coinpoker_hand_id=abs(hash(str(uuid4()))) % (10**9),
        played_at=played_at or _utc(),
        table_name="Test Table",
        table_size=6,
        stake_sb=Decimal("0.50"),
        stake_bb=Decimal("1.00"),
        button_seat=3,
        hero_seat=hero_seat,
        hero_position=hero_position,
        hero_cards=["Ac", "Kd"],
        flop=flop,
        hero_invested=Decimal("0"),
        hero_collected=Decimal("0"),
        hero_net=hero_net_bb,
        hero_net_bb=hero_net_bb,
        total_pot=Decimal("10"),
        went_to_showdown=went_to_showdown,
        won_at_showdown=won_at_showdown,
    )
    session.add(hand)
    await session.flush()

    # Hero player record — is_hero=True is required for the stats CTE.
    hero_player = HandPlayer(
        hand_id=hand.id,
        user_id=user_id,
        seat=hero_seat,
        screen_name="Hero",
        position=hero_position,
        starting_stack=Decimal("100"),
        is_hero=True,
    )
    # Villain
    villain_player = HandPlayer(
        hand_id=hand.id,
        user_id=user_id,
        seat=other_seat,
        screen_name="Villain",
        position="SB",
        starting_stack=Decimal("100"),
        is_hero=False,
    )
    session.add(hero_player)
    session.add(villain_player)
    await session.flush()

    for order, act in enumerate(preflop_actions, start=1):
        action = HandAction(
            hand_id=hand.id,
            user_id=user_id,
            street="preflop",
            action_order=order,
            seat=act["seat"],
            screen_name=act.get("screen_name", "Hero" if act["seat"] == hero_seat else "Villain"),
            action=act["action"],
            amount=act.get("amount"),
            raise_to=act.get("raise_to"),
            is_all_in=act.get("is_all_in", False),
        )
        session.add(action)

    await session.commit()
    return hand


# ---------------------------------------------------------------------------
# Shared fixture: insert all 7 synthetic hands + 2 old hands
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seeded_hands(session: AsyncSession, test_user_id: UUID, upload_id: UUID) -> dict:
    uid = test_user_id
    up = upload_id

    # Hand 1 — BTN, limp preflop (VPIP, no PFR), sees flop, folds
    h1 = await _insert_hand(
        session, uid, up,
        hero_position="BTN",
        hero_net_bb=Decimal("-1.0"),
        flop=["Ah", "2d", "3c"],
        preflop_actions=[
            {"seat": 2, "action": "post_sb", "amount": Decimal("0.5")},
            {"seat": 3, "action": "post_bb", "amount": Decimal("1.0")},
            {"seat": 1, "action": "call",    "amount": Decimal("1.0")},
        ],
    )

    # Hand 2 — BTN, open raise (VPIP + PFR), wins blinds, no flop
    h2 = await _insert_hand(
        session, uid, up,
        hero_position="BTN",
        hero_net_bb=Decimal("1.5"),
        preflop_actions=[
            {"seat": 2, "action": "post_sb",  "amount": Decimal("0.5")},
            {"seat": 3, "action": "post_bb",  "amount": Decimal("1.0")},
            {"seat": 1, "action": "raise",    "amount": Decimal("2.5"), "raise_to": Decimal("2.5")},
            {"seat": 2, "action": "fold"},
            {"seat": 3, "action": "fold"},
        ],
    )

    # Hand 3 — BTN, 3-bet (VPIP + PFR + 3bet_opp + 3bet), no flop
    h3 = await _insert_hand(
        session, uid, up,
        hero_position="BTN",
        hero_net_bb=Decimal("3.0"),
        preflop_actions=[
            {"seat": 2, "action": "post_sb",  "amount": Decimal("0.5")},
            {"seat": 3, "action": "post_bb",  "amount": Decimal("1.0")},
            # Villain open-raises first
            {"seat": 2, "action": "raise",    "amount": Decimal("3.0"), "raise_to": Decimal("3.0")},
            # Hero 3-bets
            {"seat": 1, "action": "raise",    "amount": Decimal("9.0"), "raise_to": Decimal("9.0")},
            {"seat": 2, "action": "fold"},
        ],
    )

    # Hand 4 — BB, fold facing a raise (3bet_opp=T, 3bet=F, no VPIP)
    h4 = await _insert_hand(
        session, uid, up,
        hero_position="BB",
        hero_net_bb=Decimal("0.0"),
        preflop_actions=[
            {"seat": 2, "action": "post_sb",  "amount": Decimal("0.5")},
            # Hero posts BB
            {"seat": 1, "action": "post_bb",  "amount": Decimal("1.0")},
            # Villain raises
            {"seat": 2, "action": "raise",    "amount": Decimal("3.0"), "raise_to": Decimal("3.0")},
            # Hero folds — faced_raise_before = TRUE here
            {"seat": 1, "action": "fold"},
        ],
        hero_seat=1,
        other_seat=2,
    )

    # Hand 5 — BB, call preflop, goes to showdown and WINS
    h5 = await _insert_hand(
        session, uid, up,
        hero_position="BB",
        hero_net_bb=Decimal("5.0"),
        flop=["Kh", "Qd", "Jc"],
        went_to_showdown=True,
        won_at_showdown=True,
        preflop_actions=[
            {"seat": 2, "action": "post_sb",  "amount": Decimal("0.5")},
            {"seat": 1, "action": "post_bb",  "amount": Decimal("1.0")},
            {"seat": 2, "action": "call",     "amount": Decimal("0.5")},  # SB completes
            {"seat": 1, "action": "check"},
        ],
    )

    # Hand 6 — BB, call preflop, goes to showdown and LOSES
    h6 = await _insert_hand(
        session, uid, up,
        hero_position="BB",
        hero_net_bb=Decimal("-5.0"),
        flop=["2h", "7d", "9c"],
        went_to_showdown=True,
        won_at_showdown=False,
        preflop_actions=[
            {"seat": 2, "action": "post_sb",  "amount": Decimal("0.5")},
            {"seat": 1, "action": "post_bb",  "amount": Decimal("1.0")},
            {"seat": 2, "action": "call",     "amount": Decimal("0.5")},
            {"seat": 1, "action": "check"},
        ],
    )

    # Hand 7 — BB, call preflop, sees flop, folds on flop (WTSD denom, not num)
    h7 = await _insert_hand(
        session, uid, up,
        hero_position="BB",
        hero_net_bb=Decimal("-1.0"),
        flop=["5s", "6d", "7h"],
        preflop_actions=[
            {"seat": 2, "action": "post_sb",  "amount": Decimal("0.5")},
            {"seat": 1, "action": "post_bb",  "amount": Decimal("1.0")},
            {"seat": 2, "action": "call",     "amount": Decimal("0.5")},
            {"seat": 1, "action": "check"},
        ],
    )

    # Two extra hands played 60 days ago — outside all timeframe windows
    old_ts = _utc(days_ago=60)
    h_old1 = await _insert_hand(
        session, uid, up,
        hero_position="BTN",
        hero_net_bb=Decimal("10.0"),
        played_at=old_ts,
        preflop_actions=[
            {"seat": 2, "action": "post_sb", "amount": Decimal("0.5")},
            {"seat": 3, "action": "post_bb", "amount": Decimal("1.0")},
            {"seat": 1, "action": "raise",   "raise_to": Decimal("2.5")},
            {"seat": 2, "action": "fold"},
            {"seat": 3, "action": "fold"},
        ],
    )
    h_old2 = await _insert_hand(
        session, uid, up,
        hero_position="CO",
        hero_net_bb=Decimal("-2.0"),
        played_at=old_ts,
        preflop_actions=[
            {"seat": 2, "action": "post_sb", "amount": Decimal("0.5")},
            {"seat": 3, "action": "post_bb", "amount": Decimal("1.0")},
            {"seat": 1, "action": "fold"},
        ],
    )

    return {
        "h1": h1, "h2": h2, "h3": h3, "h4": h4,
        "h5": h5, "h6": h6, "h7": h7,
        "h_old1": h_old1, "h_old2": h_old2,
    }


# ---------------------------------------------------------------------------
# Tests: lifetime summary (all 9 hands)
# ---------------------------------------------------------------------------

async def test_hands_count_lifetime(session, test_user_id, seeded_hands):
    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    assert result["hands_count"] == 9


async def test_vpip_pct(session, test_user_id, seeded_hands):
    # Lifetime: vpip in hands 1,2,3,5,6,7 + h_old1 = 7/9 ≈ 77.78%
    # (h_old1 raises preflop → VPIP; h_old2 folds → no VPIP)
    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    # 7 vpip out of 9
    assert result["vpip_pct"] == round(7 / 9 * 100, 2)


async def test_pfr_pct(session, test_user_id, seeded_hands):
    # PFR: hands 2, 3, h_old1 = 3/9 ≈ 33.33%
    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    assert result["pfr_pct"] == round(3 / 9 * 100, 2)


async def test_three_bet_pct(session, test_user_id, seeded_hands):
    # 3bet_opp: hands 3, 4 = 2 (h_old1 is an open-raise — no prior non-hero raise)
    # 3bet: hand 3 only = 1
    # three_bet_pct = 1/2 = 50.00%
    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    assert result["three_bet_pct"] == 50.00


async def test_wtsd_pct(session, test_user_id, seeded_hands):
    # saw_flop: hands 1, 5, 6, 7 = 4 (old hands have no flop)
    # went_to_showdown: hands 5, 6 = 2
    # wtsd_pct = 2/4 = 50.00%
    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    assert result["wtsd_pct"] == 50.00


async def test_wsd_pct(session, test_user_id, seeded_hands):
    # won_at_showdown: hand 5 = 1 / 2 showdowns = 50.00%
    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    assert result["wsd_pct"] == 50.00


async def test_bb_per_100_7hand_slice(session, test_user_id, seeded_hands):
    # 7d timeframe excludes old hands; 7 recent hands only
    # sum_net_bb = -1 + 1.5 + 3 + 0 + 5 - 5 - 1 = 2.5
    # bb_per_100 = 2.5 / 7 * 100 ≈ 35.71
    result = await compute_stats(session, test_user_id, timeframe="7d")
    assert result["bb_per_100"] == round(Decimal("2.5") / 7 * 100, 2)


# ---------------------------------------------------------------------------
# Tests: timeframe filtering
# ---------------------------------------------------------------------------

async def test_timeframe_7d_excludes_old_hands(session, test_user_id, seeded_hands):
    result_7d = await compute_stats(session, test_user_id, timeframe="7d")
    result_30d = await compute_stats(session, test_user_id, timeframe="30d")
    result_life = await compute_stats(session, test_user_id, timeframe="lifetime")

    assert result_7d["hands_count"] == 7
    assert result_30d["hands_count"] == 7   # old hands are 60d ago
    assert result_life["hands_count"] == 9


async def test_timeframe_7d_vpip(session, test_user_id, seeded_hands):
    # Recent 7 hands: vpip in 1,2,3,5,6,7 = 6/7 ≈ 85.71%
    result = await compute_stats(session, test_user_id, timeframe="7d")
    assert result["vpip_pct"] == round(6 / 7 * 100, 2)


async def test_timeframe_7d_pfr(session, test_user_id, seeded_hands):
    # Recent 7 hands: pfr in 2, 3 = 2/7 ≈ 28.57%
    result = await compute_stats(session, test_user_id, timeframe="7d")
    assert result["pfr_pct"] == round(2 / 7 * 100, 2)


# ---------------------------------------------------------------------------
# Tests: position filter
# ---------------------------------------------------------------------------

async def test_position_filter_btn(session, test_user_id, seeded_hands):
    result = await compute_stats(session, test_user_id, timeframe="7d", position="BTN")
    # Recent BTN hands: 1, 2, 3
    assert result["hands_count"] == 3
    assert result["vpip_pct"] == 100.00        # all 3 are VPIP
    assert result["pfr_pct"] == round(2 / 3 * 100, 2)
    assert result["three_bet_pct"] == 100.00   # 1/1 opportunity


async def test_position_filter_bb(session, test_user_id, seeded_hands):
    result = await compute_stats(session, test_user_id, timeframe="7d", position="BB")
    # Recent BB hands: 4, 5, 6, 7
    assert result["hands_count"] == 4
    assert result["vpip_pct"] == 75.00         # 3/4
    assert result["pfr_pct"] == 0.00
    assert result["three_bet_pct"] == 0.00     # 0/1 opportunity
    assert result["wtsd_pct"] == round(2 / 3 * 100, 2)   # 2 WTSD / 3 saw flop
    assert result["wsd_pct"] == 50.00


# ---------------------------------------------------------------------------
# Tests: by-position breakdown
# ---------------------------------------------------------------------------

async def test_by_position_returns_all_positions(session, test_user_id, seeded_hands):
    rows = await compute_by_position(session, test_user_id, timeframe="7d")
    positions = {r["position"] for r in rows}
    assert "BTN" in positions
    assert "BB" in positions


async def test_by_position_btn_stats(session, test_user_id, seeded_hands):
    rows = await compute_by_position(session, test_user_id, timeframe="7d")
    btn = next(r for r in rows if r["position"] == "BTN")

    assert btn["hands"] == 3
    assert btn["vpip_pct"] == 100.00
    assert btn["pfr_pct"] == round(2 / 3 * 100, 2)
    assert btn["three_bet_pct"] == 100.00
    # Only hand 1 saw flop; hand 1 folded on flop → wtsd=0/1=0%
    assert btn["wtsd_pct"] == 0.00
    # No showdowns → wsd=0
    assert btn["wsd_pct"] == 0.00
    # net_bb: -1 + 1.5 + 3 = 3.5 / 3 * 100 ≈ 116.67
    assert btn["bb_per_100"] == round(Decimal("3.5") / 3 * 100, 2)


async def test_by_position_bb_stats(session, test_user_id, seeded_hands):
    rows = await compute_by_position(session, test_user_id, timeframe="7d")
    bb = next(r for r in rows if r["position"] == "BB")

    assert bb["hands"] == 4
    assert bb["vpip_pct"] == 75.00
    assert bb["pfr_pct"] == 0.00
    assert bb["three_bet_pct"] == 0.00
    assert bb["wtsd_pct"] == round(2 / 3 * 100, 2)
    assert bb["wsd_pct"] == 50.00
    # net_bb: 0 + 5 - 5 - 1 = -1 / 4 * 100 = -25.00
    assert bb["bb_per_100"] == -25.00


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------

async def test_empty_user_returns_zero_stats(session, engine):
    """A user with no hands should receive all-zero stats, not a server error."""
    fresh_user = uuid4()
    result = await compute_stats(session, fresh_user, timeframe="lifetime")
    assert result["hands_count"] == 0
    assert result["vpip_pct"] == 0.0
    assert result["pfr_pct"] == 0.0
    assert result["three_bet_pct"] == 0.0
    assert result["wtsd_pct"] == 0.0
    assert result["wsd_pct"] == 0.0
    assert result["bb_per_100"] == 0.0


async def test_empty_user_by_position_returns_empty_list(session, engine):
    fresh_user = uuid4()
    rows = await compute_by_position(session, fresh_user, timeframe="lifetime")
    assert rows == []


async def test_user_isolation(session, test_user_id, upload_id, seeded_hands):
    """Stats for one user must not bleed into another user's results."""
    other_user = uuid4()

    # Give other_user their own upload + 1 hand
    other_upload = Upload(
        user_id=other_user,
        filename="other.txt",
        storage_path=f"test/{other_user}/other.txt",
        sha256="b" * 64,
        status="parsed",
    )
    session.add(other_upload)
    await session.flush()

    await _insert_hand(
        session, other_user, other_upload.id,
        hero_position="BTN",
        hero_net_bb=Decimal("999.0"),
        preflop_actions=[
            {"seat": 2, "action": "post_sb"},
            {"seat": 3, "action": "post_bb"},
            {"seat": 1, "action": "raise", "raise_to": Decimal("2.5")},
        ],
    )

    result = await compute_stats(session, test_user_id, timeframe="lifetime")
    # test_user's net_bb must not be contaminated by other_user's 999bb hand
    assert result["bb_per_100"] != pytest.approx(999.0, abs=1.0)

    other_result = await compute_stats(session, other_user, timeframe="lifetime")
    assert other_result["hands_count"] == 1


async def test_fold_preflop_not_vpip(session, test_user_id, seeded_hands):
    """Hand 4: hero posts BB then folds to a raise — must NOT count as VPIP."""
    # 7d slice has 7 hands; hand 4 is a fold after posting BB
    result = await compute_stats(session, test_user_id, timeframe="7d")
    # hands 1,2,3,5,6,7 are VPIP; hand 4 is not → 6/7
    assert result["vpip_pct"] == round(6 / 7 * 100, 2)


async def test_showdown_win_loss_tracked_independently(session, test_user_id, seeded_hands):
    """W$SD denominator is WTSD hands, not all hands; WTSD denom is saw-flop hands."""
    result = await compute_stats(session, test_user_id, timeframe="7d")
    # 4 saw flop → WTSD denom=4; 2 went_to_showdown → WTSD=50%
    assert result["wtsd_pct"] == 50.00
    # 2 went_to_showdown → W$SD denom=2; 1 won → W$SD=50%
    assert result["wsd_pct"] == 50.00
