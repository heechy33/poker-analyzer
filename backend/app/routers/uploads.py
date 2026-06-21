from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.database import async_engine, get_session
from app.models import Upload
from app.schemas import (
    CompleteUploadRequest,
    PresignResponse,
    PresignUploadRequest,
    UploadResponse,
)
from app.services.ingest import run_upload_ingest
from app.services.storage import build_storage_path, create_signed_upload_url

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _upload_response(upload: Upload, **extra: object) -> UploadResponse:
    return UploadResponse(
        id=str(upload.id),
        filename=upload.filename,
        status=upload.status,
        hand_count=upload.hand_count,
        error_message=upload.error_message,
        parse_warnings=upload.parse_warnings,
        bytes=upload.bytes,
        uploaded_at=upload.uploaded_at,
        **extra,
    )


async def _background_ingest(upload_id: UUID, content: str | bytes | None) -> None:
    async with AsyncSession(async_engine) as session:
        await run_upload_ingest(session, upload_id, content)


@router.post("/presign", response_model=PresignResponse)
async def presign_upload(
    body: PresignUploadRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PresignResponse:
    user_uuid = UUID(user_id)
    existing = await session.exec(
        select(Upload).where(Upload.user_id == user_uuid, Upload.sha256 == body.sha256)
    )
    upload = existing.first()
    if upload is not None:
        extra: dict[str, object] = {"deduplicated": True}
        # Allow retry when a prior attempt created the row but never finished upload/parse.
        if upload.status in ("queued", "error") and upload.storage_path:
            settings = get_settings()
            try:
                signed = create_signed_upload_url(upload.storage_path)
                extra["signed_url"] = signed.get("signedUrl")
                extra["token"] = signed.get("token")
                extra["path"] = signed.get("path") or upload.storage_path
            except Exception:
                if settings.ENVIRONMENT != "development":
                    raise
        return PresignResponse(**_upload_response(upload).model_dump(), **extra)

    upload = Upload(
        user_id=user_uuid,
        filename=body.filename,
        storage_path="",
        sha256=body.sha256,
        bytes=body.bytes,
        status="queued",
    )
    session.add(upload)
    await session.flush()

    storage_path = build_storage_path(user_id, upload.id, body.filename)
    upload.storage_path = storage_path
    session.add(upload)
    await session.commit()
    await session.refresh(upload)

    settings = get_settings()
    signed: dict[str, object] | None = None
    if settings.ENVIRONMENT != "development":
        try:
            signed = create_signed_upload_url(storage_path)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
    else:
        try:
            signed = create_signed_upload_url(storage_path)
        except Exception:
            # Dev raw_content uploads skip Storage; signed URL is optional.
            signed = None

    return PresignResponse(
        **_upload_response(upload).model_dump(),
        signed_url=signed.get("signedUrl") if signed else None,
        token=signed.get("token") if signed else None,
        path=(signed.get("path") if signed else None) or storage_path,
        deduplicated=False,
    )


@router.post("/{upload_id}/complete", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def complete_upload(
    upload_id: UUID,
    background_tasks: BackgroundTasks,
    body: CompleteUploadRequest | None = None,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    upload = await session.get(Upload, upload_id)
    if upload is None or str(upload.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")

    if upload.status == "parsed":
        return _upload_response(upload)

    settings = get_settings()
    raw_content: str | bytes | None = None
    if body is not None and body.raw_content:
        if settings.ENVIRONMENT != "development":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="raw_content is only allowed in development",
            )
        raw_content = body.raw_content

    upload.status = "parsing"
    upload.error_message = None
    session.add(upload)
    await session.commit()
    await session.refresh(upload)

    background_tasks.add_task(_background_ingest, upload_id, raw_content)
    return _upload_response(upload)


@router.get("/{upload_id}", response_model=UploadResponse)
async def get_upload(
    upload_id: UUID,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    upload = await session.get(Upload, upload_id)
    if upload is None or str(upload.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return _upload_response(upload)
