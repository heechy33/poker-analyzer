from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import get_current_user
from app.database import get_session
from app.schemas import LeakTagRow, PositionStatsRow, StatsSummaryResponse
from app.stats.compute import compute_by_position, compute_leaks, compute_stats

router = APIRouter(prefix="/stats", tags=["stats"])

Timeframe = Literal["lifetime", "7d", "30d"]


@router.get(
    "/summary",
    operation_id="get_stats",
    response_model=StatsSummaryResponse,
)
async def get_stats_summary(
    timeframe: Timeframe = Query(default="lifetime"),
    position: str | None = Query(default=None),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StatsSummaryResponse:
    """Return hero win-rate and volume stats for a given timeframe and position.

    Returns VPIP%, PFR%, 3-bet%, WTSD%, W$SD%, BB/100, and total hand count.
    Use ``timeframe="30d"`` for recent form or ``"lifetime"`` for the full
    sample; supply ``position`` (BTN, CO, HJ, UTG, SB, BB) to drill into a
    specific seat.  Prefer ``get_stats`` over ``list_recent_hands`` when you
    only need aggregate numbers, not individual hand records.
    """
    data = await compute_stats(
        session,
        UUID(user_id),
        timeframe=timeframe,
        position=position,
    )
    return StatsSummaryResponse(**data)


@router.get(
    "/by-position",
    operation_id="get_stats_by_position",
    response_model=list[PositionStatsRow],
)
async def get_stats_by_position(
    timeframe: Timeframe = Query(default="lifetime"),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PositionStatsRow]:
    """Aggregate hero stats broken down by position for the given timeframe.

    Returns one entry per position that has at least one hand in the
    filtered set. Positions are ordered alphabetically.
    """
    rows = await compute_by_position(session, UUID(user_id), timeframe=timeframe)
    return [PositionStatsRow(**row) for row in rows]


@router.get(
    "/leaks",
    operation_id="find_leaks",
    response_model=list[LeakTagRow],
)
async def get_leaks(
    timeframe: Timeframe = Query(default="30d"),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[LeakTagRow]:
    """Aggregate recurring leak patterns from all LLM hand analyses in the timeframe.

    Each item in the returned list is a distinct leak tag (e.g.
    ``overfold_turn``, ``thin_value_river``) with a raw ``count`` of how many
    analyses carried that tag and a ``pct_of_analyses`` percentage of the total
    analysis set.  Results are sorted by frequency descending.  Use this tool
    to identify the user's *single biggest exploitable pattern* before drilling
    into specific hands with ``list_recent_hands`` or ``analyze_hand``.
    Backed by the GIN index ``idx_llm_analyses_user_tags`` — fast even for
    thousands of analyses.  ``timeframe`` accepts ``"7d"``, ``"30d"``, or
    ``"lifetime"``; defaults to ``"30d"``.
    """
    rows = await compute_leaks(session, UUID(user_id), timeframe=timeframe)
    return [LeakTagRow(**row) for row in rows]


@router.get("", include_in_schema=False)
async def get_stats_redirect() -> RedirectResponse:
    """Backward-compatible alias — redirects to /stats/summary."""
    return RedirectResponse(url="/stats/summary", status_code=307)
