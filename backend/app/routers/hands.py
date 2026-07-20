from __future__ import annotations

import json
import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import get_current_user
from app.database import get_session
from app.llm import (
    SYSTEM_PROMPT,
    LLMClient,
    build_analysis_prompt,
    compute_prompt_hash,
    get_llm_client,
    parse_llm_response,
)
from app.models import (
    Hand,
    HandAction,
    HandPlayer,
    LlmAnalysis,
)
from app.schemas import (
    AnalysisListItem,
    AnalyzeHandRequest,
    AnalyzeHandResponse,
    FilterOptionsResponse,
    HandActionOut,
    HandDetail,
    HandPlayerOut,
    HandSummary,
    StakeOption,
    TableFormat,
)
from app.services.analysis import (
    find_cached_analysis,
    list_analyses_for_hand,
    load_hand_bundle,
    persist_analysis,
)
from app.stakes import format_stake
from app.services.ingest import sort_actions
from app.table_formats import TABLE_SIZE_BY_FORMAT, table_format_from_size

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hands", tags=["hands"])

ORDER_COLUMNS = {
    "played_at": Hand.played_at,
    "hero_net": Hand.hero_net,
    "hero_net_bb": Hand.hero_net_bb,
    "total_pot": Hand.total_pot,
}


def _to_summary(hand: Hand) -> HandSummary:
    return HandSummary(
        id=str(hand.id),
        coinpoker_hand_id=hand.coinpoker_hand_id,
        played_at=hand.played_at,
        table_name=hand.table_name,
        table_format=table_format_from_size(hand.table_size),
        stake_sb=hand.stake_sb,
        stake_bb=hand.stake_bb,
        hero_position=hand.hero_position,
        hero_cards=hand.hero_cards,
        hero_net=hand.hero_net,
        hero_net_bb=hand.hero_net_bb,
        went_to_showdown=hand.went_to_showdown,
        total_pot=hand.total_pot,
    )


def _parse_order(order: str):
    field_name, direction = order.split(".", 1)
    column = ORDER_COLUMNS[field_name]
    return desc(column) if direction == "desc" else asc(column)


@router.get("", operation_id="list_recent_hands", response_model=list[HandSummary])
async def list_hands(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="played_at.desc"),
    position: str | None = Query(default=None),
    since: str | None = Query(default=None),
    only_losses: bool = Query(default=False),
    table_format: TableFormat | None = Query(default=None),
    stakes: str | None = Query(default=None, pattern=r"^\d+(\.\d+)?/\d+(\.\d+)?$"),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[HandSummary]:
    """Return a filtered, paginated list of hand summaries for the authenticated user.

    Each hand includes id, date, position, hero hole-cards, net profit in chips
    and big blinds, total pot, and whether hero went to showdown.  Use
    ``only_losses=true`` combined with ``order=hero_net.asc`` to surface the
    worst hands (see also ``find_biggest_losers`` which pre-applies these
    defaults).  ``since`` accepts an ISO-8601 date or datetime string
    (e.g. ``"2026-05-01"``).  ``position`` must be one of BTN, CO, HJ, UTG,
    SB, BB.  Returns at most 200 records per call; use ``offset`` for
    pagination.

    **Filters:**
    ``table_format`` — the exact table configuration: ``hu_2max``, ``6max``,
    or ``9max``. It does not describe how many players reached the flop or
    whether a hand is solver-eligible.
    ``stakes`` — exact ``"sb/bb"`` e.g. ``0.10/0.25``.
    """
    if order not in {f"{field}.{direction}" for field in ORDER_COLUMNS for direction in ("asc", "desc")}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid order parameter")

    stmt = select(Hand).where(Hand.user_id == UUID(user_id))
    if position:
        stmt = stmt.where(Hand.hero_position == position)
    if since:
        try:
            since_date = datetime.fromisoformat(since).date()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="since must be an ISO date",
            ) from exc
        since_dt = datetime.combine(since_date, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(Hand.played_at >= since_dt)
    if only_losses:
        stmt = stmt.where(Hand.hero_net < 0)
    if table_format is not None:
        stmt = stmt.where(Hand.table_size == TABLE_SIZE_BY_FORMAT[table_format])
    if stakes:
        sb_str, bb_str = stakes.split("/", 1)
        stake_sb = Decimal(sb_str)
        stake_bb = Decimal(bb_str)
        stmt = stmt.where(Hand.stake_sb == stake_sb).where(Hand.stake_bb == stake_bb)

    stmt = stmt.order_by(_parse_order(order)).offset(offset).limit(limit)
    result = await session.exec(stmt)
    return [_to_summary(hand) for hand in result.all()]


@router.get("/losers", operation_id="find_biggest_losers", response_model=list[HandSummary])
async def find_biggest_losers(
    limit: int = Query(default=10, ge=1, le=50),
    since: str | None = Query(default=None),
    position: str | None = Query(default=None),
    table_format: TableFormat | None = Query(default=None),
    stakes: str | None = Query(default=None, pattern=r"^\d+(\.\d+)?/\d+(\.\d+)?$"),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[HandSummary]:
    """Return the user's worst (most losing) hands, sorted by hero_net ascending.

    This is the fastest path to identifying high-stakes mistakes — it
    pre-applies ``only_losses=true`` and ``order=hero_net.asc`` so you do
    not need to set those yourself.  ``limit`` defaults to 10 (max 50).
    Supply ``since`` (ISO-8601 date, e.g. ``"2026-05-01"``) to restrict to
    a time window, or ``position`` (BTN, CO, HJ, UTG, SB, BB) to focus on
    a specific seat.  Prefer this tool over ``list_recent_hands`` whenever
    the goal is "show me the hands where I lost the most chips".

    Accepts ``table_format`` and ``stakes`` for consistency with
    ``list_recent_hands``.
    """
    stmt = select(Hand).where(Hand.user_id == UUID(user_id)).where(Hand.hero_net < 0)

    if position:
        stmt = stmt.where(Hand.hero_position == position)
    if since:
        try:
            since_date = datetime.fromisoformat(since).date()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="since must be an ISO date",
            ) from exc
        since_dt = datetime.combine(since_date, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(Hand.played_at >= since_dt)
    if table_format is not None:
        stmt = stmt.where(Hand.table_size == TABLE_SIZE_BY_FORMAT[table_format])
    if stakes:
        sb_str, bb_str = stakes.split("/", 1)
        stake_sb = Decimal(sb_str)
        stake_bb = Decimal(bb_str)
        stmt = stmt.where(Hand.stake_sb == stake_sb).where(Hand.stake_bb == stake_bb)

    stmt = stmt.order_by(asc(Hand.hero_net)).limit(limit)
    result = await session.exec(stmt)
    return [_to_summary(hand) for hand in result.all()]


@router.get("/filter-options", operation_id="filter_options", response_model=FilterOptionsResponse)
async def get_filter_options(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FilterOptionsResponse:
    """Return distinct (stake_sb, stake_bb) pairs and supported table formats.

    Used by the hands page to populate stake / table-format filters
    dynamically based on the authenticated user's uploaded hands.
    """
    rows = await session.exec(
        select(
            Hand.stake_sb,
            Hand.stake_bb,
        )
        .where(Hand.user_id == UUID(user_id))
        .distinct()
        .order_by(Hand.stake_bb, Hand.stake_sb)
    )
    stakes_options: list[StakeOption] = []
    for sb, bb in rows.all():
        sb_str = format_stake(sb)
        bb_str = format_stake(bb)
        stakes_options.append(
            StakeOption(
                sb=sb_str,
                bb=bb_str,
                label=f"{sb_str}/{bb_str}",
            )
        )
    return FilterOptionsResponse(
        stakes=stakes_options,
        table_formats=["hu_2max", "6max", "9max"],
    )


@router.get("/{hand_id}", operation_id="get_hand", response_model=HandDetail)
async def get_hand(
    hand_id: UUID,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HandDetail:
    """Return the full action log and player details for a single hand.

    Includes every street action in chronological order, all seat information,
    board cards, raw CoinPoker hand-history text, rake, and hero financials.
    Call this after ``list_recent_hands`` or ``find_biggest_losers`` to inspect
    the specific actions that led to a big loss.  ``hand_id`` is the UUID
    returned by list-style endpoints — not the CoinPoker numeric hand ID.
    """
    hand = await session.get(Hand, hand_id)
    if hand is None or str(hand.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hand not found")

    players_result = await session.exec(
        select(HandPlayer).where(HandPlayer.hand_id == hand_id).order_by(HandPlayer.seat)
    )
    actions_result = await session.exec(select(HandAction).where(HandAction.hand_id == hand_id))
    players = players_result.all()
    actions = sort_actions(list(actions_result.all()))

    summary = _to_summary(hand)
    return HandDetail(
        **summary.model_dump(),
        upload_id=str(hand.upload_id),
        session_id=str(hand.session_id) if hand.session_id else None,
        button_seat=hand.button_seat,
        hero_seat=hand.hero_seat,
        flop=hand.flop,
        turn=hand.turn,
        river=hand.river,
        rake=hand.rake,
        splash_fee=hand.splash_fee,
        hero_invested=hand.hero_invested,
        hero_collected=hand.hero_collected,
        won_at_showdown=hand.won_at_showdown,
        flags=hand.flags or {},
        raw_text=hand.raw_text,
        players=[
            HandPlayerOut(
                seat=player.seat,
                screen_name=player.screen_name,
                position=player.position,
                starting_stack=player.starting_stack,
                is_hero=player.is_hero,
                final_cards=player.final_cards,
            )
            for player in players
        ],
        actions=[
            HandActionOut(
                street=action.street,
                action_order=action.action_order,
                seat=action.seat,
                screen_name=action.screen_name,
                action=action.action,
                amount=action.amount,
                raise_to=action.raise_to,
                is_all_in=action.is_all_in,
            )
            for action in actions
        ],
    )


# ---------------------------------------------------------------------------
# LLM hand analysis
# ---------------------------------------------------------------------------


async def _load_hand_for_analysis(
    session: AsyncSession,
    hand_id: UUID,
    user_id: str,
) -> tuple[Hand, list[HandPlayer], list[HandAction]]:
    bundle = await load_hand_bundle(session, hand_id, UUID(user_id))
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Hand not found"
        )
    return bundle


def _analysis_to_response(
    analysis: LlmAnalysis, *, cached: bool
) -> AnalyzeHandResponse:
    return AnalyzeHandResponse(
        id=str(analysis.id),
        hand_id=str(analysis.hand_id) if analysis.hand_id else "",
        model=analysis.model,
        prompt_hash=analysis.prompt_hash,
        analysis=analysis.analysis_text,
        leak_tags=list(analysis.leak_tags or []),
        cached=cached,
        input_tokens=analysis.input_tokens,
        output_tokens=analysis.output_tokens,
        created_at=analysis.created_at,
    )


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post("/{hand_id}/analyze", operation_id="analyze_hand")
async def analyze_hand(
    hand_id: UUID,
    body: AnalyzeHandRequest,
    stream: bool = Query(default=True),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
):
    """Generate (or replay cached) Claude commentary and leak tags for a hand.

    Pass ``stream=false`` for non-streaming API usage — returns a synchronous
    :class:`AnalyzeHandResponse` JSON object containing the full
    plain-English analysis and an array of ``leak_tags`` (e.g.
    ``[\"overfold_turn\", \"thin_value_river\"]``).  Cache hits are
    returned instantly without re-calling Claude. During Phase 0 this is
    general coaching only: solver summaries and scenario hashes are rejected
    at the request boundary and no solver claims are supplied to the model.
    The ``street`` field (``\"flop\"`` | ``\"turn\"`` | ``\"river\"``) selects
    which decision point Claude focuses on.  Use ``get_hand`` first to
    retrieve the action log and determine the interesting street.

    For streaming (UI usage): ``?stream=true`` (default) returns
    ``text/event-stream`` with ``token`` events as Claude streams,
    followed by a terminating ``done`` event carrying the analysis id
    and leak tags.
    """
    user_uuid = UUID(user_id)
    hand, players, actions = await _load_hand_for_analysis(session, hand_id, user_id)

    prompt_hash = compute_prompt_hash(hand_id, body.street)
    cached = await find_cached_analysis(session, user_uuid, hand_id, prompt_hash)

    if cached is not None:
        response = _analysis_to_response(cached, cached=True)
        if not stream:
            return response
        return StreamingResponse(
            _replay_cached_stream(response),
            media_type="text/event-stream",
        )

    user_prompt = build_analysis_prompt(
        hand,
        players,
        actions,
        street=body.street,
    )

    if not stream:
        result = await llm.analyze(system=SYSTEM_PROMPT, user_prompt=user_prompt)
        analysis_text, leak_tags = parse_llm_response(result.text)
        row = await persist_analysis(
            session,
            user_id=user_uuid,
            hand_id=hand_id,
            model=result.model or llm.model,
            prompt_hash=prompt_hash,
            analysis_text=analysis_text,
            leak_tags=leak_tags,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return _analysis_to_response(row, cached=False)

    return StreamingResponse(
        _stream_analysis(
            llm=llm,
            session=session,
            user_id=user_uuid,
            hand_id=hand_id,
            prompt_hash=prompt_hash,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        ),
        media_type="text/event-stream",
    )


async def _replay_cached_stream(response: AnalyzeHandResponse):
    """Emit a cached analysis as a single ``token`` plus terminating ``done``.

    This keeps the wire format identical for cached vs fresh responses so
    the frontend SSE consumer does not need a separate branch.
    """
    yield _sse_event("token", {"text": response.analysis})
    yield _sse_event(
        "done",
        {
            "analysis_id": response.id,
            "leak_tags": response.leak_tags,
            "cached": True,
            "model": response.model,
        },
    )


async def _stream_analysis(
    *,
    llm: LLMClient,
    session: AsyncSession,
    user_id: UUID,
    hand_id: UUID,
    prompt_hash: str,
    system_prompt: str,
    user_prompt: str,
):
    buffer: list[str] = []
    final_text = ""
    input_tokens = 0
    output_tokens = 0
    model = llm.model

    try:
        async for chunk in llm.stream_analyze(
            system=system_prompt, user_prompt=user_prompt
        ):
            if chunk.type == "token" and chunk.text:
                buffer.append(chunk.text)
                yield _sse_event("token", {"text": chunk.text})
            elif chunk.type == "final" and chunk.result is not None:
                final_text = chunk.result.text or "".join(buffer)
                input_tokens = chunk.result.input_tokens
                output_tokens = chunk.result.output_tokens
                model = chunk.result.model or model
    except Exception as exc:  # noqa: BLE001 - propagate as SSE error event
        logger.exception("LLM stream failed for hand %s", hand_id)
        yield _sse_event("error", {"message": str(exc)})
        return

    if not final_text:
        final_text = "".join(buffer)

    analysis_text, leak_tags = parse_llm_response(final_text)

    try:
        row = await persist_analysis(
            session,
            user_id=user_id,
            hand_id=hand_id,
            model=model,
            prompt_hash=prompt_hash,
            analysis_text=analysis_text,
            leak_tags=leak_tags,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        analysis_id = str(row.id)
    except Exception:  # noqa: BLE001 - persistence failure shouldn't kill the stream
        logger.exception("Failed to persist analysis for hand %s", hand_id)
        analysis_id = ""

    yield _sse_event(
        "done",
        {
            "analysis_id": analysis_id,
            "leak_tags": leak_tags,
            "cached": False,
            "model": model,
            "input_tokens": input_tokens or None,
            "output_tokens": output_tokens or None,
        },
    )


@router.get("/{hand_id}/analyses", response_model=list[AnalysisListItem])
async def list_hand_analyses(
    hand_id: UUID,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AnalysisListItem]:
    """Return every cached analysis for ``hand_id`` (most recent first)."""
    hand = await session.get(Hand, hand_id)
    if hand is None or str(hand.user_id) != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Hand not found"
        )

    rows = await list_analyses_for_hand(session, UUID(user_id), hand_id)
    return [
        AnalysisListItem(
            id=str(row.id),
            hand_id=str(row.hand_id) if row.hand_id else "",
            model=row.model,
            prompt_hash=row.prompt_hash,
            analysis=row.analysis_text,
            leak_tags=list(row.leak_tags or []),
            created_at=row.created_at,
        )
        for row in rows
    ]
