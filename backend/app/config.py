import base64
import json
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    SUPABASE_URL: str = Field(
        validation_alias=AliasChoices("SUPABASE_URL", "supabase_url", "NEXT_PUBLIC_SUPABASE_URL"),
    )
    SUPABASE_SERVICE_ROLE_KEY: str
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_MAX_TOKENS: int = 1024
    DATABASE_URL: str
    ENVIRONMENT: str = "development"
    SUPABASE_STORAGE_BUCKET: str = "hand-histories"

    model_config = SettingsConfigDict(
        # Backend .env wins; repo-root .env is fallback only (e.g. when keys differ).
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("SUPABASE_SERVICE_ROLE_KEY", mode="after")
    @classmethod
    def validate_service_role_key(cls, value: str) -> str:
        """Reject the anon key — a common copy-paste mistake that breaks Storage uploads."""
        parts = value.split(".")
        if len(parts) != 3:
            return value
        try:
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        except (json.JSONDecodeError, ValueError):
            return value
        role = payload.get("role")
        if role != "service_role":
            raise ValueError(
                f"SUPABASE_SERVICE_ROLE_KEY has JWT role '{role}', not 'service_role'. "
                "In Supabase Dashboard → Project Settings → API, copy the service_role "
                "secret (not the anon public key) into backend/.env."
            )
        return value

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Supabase URIs are often postgresql://; async SQLAlchemy needs postgresql+asyncpg://."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgres://")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
