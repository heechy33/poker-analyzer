"""Hand-analysis service: cache lookup + Anthropic call + persistence.

Used by both the SSE and non-stream branches of
``POST /hands/{hand_id}/analyze``. Keeping the business logic out of the
router makes it directly testable with a mocked :class:`LLMClient` and an
in-memory or real Postgres session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import desc
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.llm import (
    GENERAL_COACHING_LABEL,
    SYSTEM_PROMPT,
    LLMClient,
    build_analysis_prompt,
    compute_prompt_hash,
    parse_llm_response,
)
from app.models import Hand, HandAction, HandPlayer, LlmAnalysis
from app.services.ingest import sort_actions


@dataclass
class AnalysisOutcome:
    analysis: LlmAnalysis
    cached: bool


async def load_hand_bundle(
    session: AsyncSession,
    hand_id: UUID,
    user_id: UUID,
) -> tuple[Hand, list[HandPlayer], list[HandAction]] | None:
    """Fetch hand + players + sorted actions, scoped to ``user_id``."""
    hand = await session.get(Hand, hand_id)
    if hand is None or hand.user_id != user_id:
        return None

    players_result = await session.exec(
        select(HandPlayer).where(HandPlayer.hand_id == hand_id).order_by(HandPlayer.seat)
    )
    actions_result = await session.exec(
        select(HandAction).where(HandAction.hand_id == hand_id)
    )
    players = list(players_result.all())
    actions = sort_actions(list(actions_result.all()))
    return hand, players, actions


async def find_cached_analysis(
    session: AsyncSession,
    user_id: UUID,
    hand_id: UUID,
    prompt_hash: str,
) -> LlmAnalysis | None:
    stmt = (
        select(LlmAnalysis)
        .where(LlmAnalysis.user_id == user_id)
        .where(LlmAnalysis.hand_id == hand_id)
        .where(LlmAnalysis.prompt_hash == prompt_hash)
        .limit(1)
    )
    result = await session.exec(stmt)
    return result.first()


async def list_analyses_for_hand(
    session: AsyncSession,
    user_id: UUID,
    hand_id: UUID,
) -> list[LlmAnalysis]:
    stmt = (
        select(LlmAnalysis)
        .where(LlmAnalysis.user_id == user_id)
        .where(LlmAnalysis.hand_id == hand_id)
        .where(LlmAnalysis.analysis_text.startswith(GENERAL_COACHING_LABEL))
        .order_by(desc(LlmAnalysis.created_at))
    )
    result = await session.exec(stmt)
    return list(result.all())


async def persist_analysis(
    session: AsyncSession,
    *,
    user_id: UUID,
    hand_id: UUID,
    model: str,
    prompt_hash: str,
    analysis_text: str,
    leak_tags: Sequence[str],
    input_tokens: int,
    output_tokens: int,
) -> LlmAnalysis:
    if not analysis_text.startswith(GENERAL_COACHING_LABEL):
        analysis_text = f"{GENERAL_COACHING_LABEL}\n\n{analysis_text}"

    row = LlmAnalysis(
        user_id=user_id,
        hand_id=hand_id,
        model=model,
        prompt_hash=prompt_hash,
        analysis_text=analysis_text,
        leak_tags=list(leak_tags),
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_or_create_analysis(
    session: AsyncSession,
    *,
    user_id: UUID,
    hand_id: UUID,
    street: str,
    llm: LLMClient,
) -> AnalysisOutcome:
    """Cache-aware non-stream analysis.

    Returns ``(LlmAnalysis, cached)``. Raises :class:`LookupError` if the
    hand doesn't exist or isn't owned by ``user_id``.
    """
    prompt_hash = compute_prompt_hash(hand_id, street)
    cached = await find_cached_analysis(session, user_id, hand_id, prompt_hash)
    if cached is not None:
        return AnalysisOutcome(analysis=cached, cached=True)

    bundle = await load_hand_bundle(session, hand_id, user_id)
    if bundle is None:
        raise LookupError(f"hand {hand_id} not found for user {user_id}")
    hand, players, actions = bundle

    user_prompt = build_analysis_prompt(
        hand,
        players,
        actions,
        street=street,
    )
    result = await llm.analyze(system=SYSTEM_PROMPT, user_prompt=user_prompt)
    analysis_text, leak_tags = parse_llm_response(result.text)

    row = await persist_analysis(
        session,
        user_id=user_id,
        hand_id=hand_id,
        model=result.model or llm.model,
        prompt_hash=prompt_hash,
        analysis_text=analysis_text,
        leak_tags=leak_tags,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return AnalysisOutcome(analysis=row, cached=False)
