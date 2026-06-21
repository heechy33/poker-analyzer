import logging
import socket
import sys
from collections.abc import AsyncGenerator
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def _warn_if_db_host_ipv6_only(database_url: str) -> None:
    """Direct Supabase hosts (db.*.supabase.co) are often IPv6-only; Windows often cannot reach them."""
    host = urlparse(database_url).hostname
    if not host or not host.endswith(".supabase.co") or host.startswith("aws-"):
        return
    has_ipv4 = has_ipv6 = False
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.getaddrinfo(host, 5432, family, socket.SOCK_STREAM)
            if family == socket.AF_INET:
                has_ipv4 = True
            else:
                has_ipv6 = True
        except OSError:
            pass
    if has_ipv6 and not has_ipv4:
        logger.warning(
            "DATABASE_URL host %s resolves to IPv6 only. On many Windows networks this "
            "causes connect timeouts (errno 10060). Use the Supabase Session pooler URI "
            "instead (Dashboard → Connect → Session pooler, port 5432, user postgres.PROJECT_REF).",
            host,
        )
        if sys.platform == "win32":
            logger.warning(
                "Example: postgresql+asyncpg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
            )


_warn_if_db_host_ipv6_only(settings.DATABASE_URL)

async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    future=True,
)

_SQLITE_PREFIXES = ("sqlite", "sqlite+aiosqlite")


def database_url_uses_sqlite(url: str) -> bool:
    return url.split("://", 1)[0].lower() in _SQLITE_PREFIXES


async def init_db() -> None:
    """Dev-only table bootstrap. Production schema comes from SQL migrations."""
    if settings.ENVIRONMENT != "development":
        return

    if database_url_uses_sqlite(settings.DATABASE_URL):
        raise RuntimeError(
            "SQLite is not supported. Models use PostgreSQL ARRAY/JSONB and "
            "stats queries require PostgreSQL. Set DATABASE_URL in backend/.env "
            "to your Supabase connection string "
            "(Dashboard → Project Settings → Database → Connection string, "
            "URI mode, async driver: postgresql+asyncpg://...)."
        )

    # Register table models on SQLModel.metadata before create_all.
    import app.models  # noqa: F401

    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # create_all does not alter existing tables; apply additive column patches.
        await conn.execute(
            text("ALTER TABLE uploads ADD COLUMN IF NOT EXISTS parse_warnings text")
        )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(async_engine) as session:
        yield session
