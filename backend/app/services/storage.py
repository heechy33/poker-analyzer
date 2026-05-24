from __future__ import annotations

from functools import lru_cache
from typing import Any
from uuid import UUID

from supabase import Client, create_client

from app.config import Settings, get_settings


@lru_cache
def get_supabase_client() -> Client:
    settings = get_settings()
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def build_storage_path(user_id: str | UUID, upload_id: str | UUID, filename: str) -> str:
    return f"{user_id}/{upload_id}/{filename}"


def create_signed_upload_url(storage_path: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    client = get_supabase_client()
    bucket = settings.SUPABASE_STORAGE_BUCKET
    response = client.storage.from_(bucket).create_signed_upload_url(storage_path)
    if isinstance(response, dict) and "signedUrl" in response:
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return dict(response)


def download_storage_object(storage_path: str, settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    client = get_supabase_client()
    bucket = settings.SUPABASE_STORAGE_BUCKET
    data = client.storage.from_(bucket).download(storage_path)
    if isinstance(data, bytes):
        return data
    return bytes(data)
