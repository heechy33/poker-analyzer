from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import get_current_user
from app.database import get_session
from app.models import Hand, SolverRun
from app.schemas import SolverRunCreate, SolverRunResponse

router = APIRouter(prefix="/solver-runs", tags=["solver"])


@router.post("", response_model=SolverRunResponse, status_code=status.HTTP_201_CREATED)
async def create_solver_run(
    body: SolverRunCreate,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SolverRunResponse:
    hand_id: UUID | None = None
    if body.hand_id is not None:
        try:
            hand_id = UUID(body.hand_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid hand_id",
            ) from exc
        hand = await session.get(Hand, hand_id)
        if hand is None or str(hand.user_id) != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hand not found")

    existing = await session.exec(
        select(SolverRun).where(SolverRun.scenario_hash == body.scenario_hash)
    )
    cached = existing.first()
    if cached is not None:
        return SolverRunResponse(
            id=str(cached.id),
            scenario_hash=cached.scenario_hash,
            street=cached.street,
            created_at=cached.created_at,
        )

    run = SolverRun(
        user_id=UUID(user_id),
        hand_id=hand_id,
        street=body.street,
        scenario_hash=body.scenario_hash,
        solver_version=body.solver_version,
        iterations=body.iterations,
        exploitability_bb=body.exploitability_bb,
        output_jsonb=body.output_jsonb,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    return SolverRunResponse(
        id=str(run.id),
        scenario_hash=run.scenario_hash,
        street=run.street,
        created_at=run.created_at,
    )
