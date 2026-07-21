"""Opt-in PostgreSQL acceptance tests for P1.2 migration and backfill.

Set ``P1_2_TEST_DATABASE_URL`` to a dedicated database whose name contains
``p1_2`` or ends in ``_test``.  This suite resets the public schema.
"""

from __future__ import annotations

import os
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Hand, HandAction, HandLedger, Upload
from app.parser.coinpoker import parse_hand
from app.services.ingest import (
    actions_from_parsed,
    backfill_canonical_ledgers,
    hand_from_parsed,
    players_from_parsed,
)
from app.stats.compute import compute_stats


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ROOT = Path(__file__).parents[1]
MIGRATIONS = ROOT / "migrations"
BASE_MIGRATIONS = ("001_uploads.sql", "002_hands.sql", "011_upload_parse_stats.sql")
P1_2_MIGRATION = "014_canonical_hand_ledgers.sql"
FIXTURE = ROOT / "tests" / "fixtures" / "coinpoker" / "hand_001.txt"
RLS_ROLE = "p1_2_ledger_test_client"


def _database_url() -> str:
    url = os.getenv("P1_2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set P1_2_TEST_DATABASE_URL to run destructive P1.2 PostgreSQL acceptance tests")
    database_name = (urlparse(url).path or "").rstrip("/").lower()
    if "p1_2" not in database_name and not database_name.endswith("_test"):
        pytest.fail("P1_2_TEST_DATABASE_URL must name a dedicated p1_2 or *_test database")
    return url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    engine = create_async_engine(_database_url(), future=True)
    yield engine
    await engine.dispose()


async def _reset_database(connection: AsyncConnection) -> None:
    await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
    await connection.execute(text("CREATE SCHEMA public"))
    await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    await connection.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
    await connection.execute(text("DROP TABLE IF EXISTS auth.users CASCADE"))
    await connection.execute(text("CREATE TABLE auth.users (id uuid PRIMARY KEY)"))
    await connection.execute(
        text(
            "CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid "
            "LANGUAGE sql STABLE AS $$ "
            "SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$"
        )
    )


async def _apply(connection: AsyncConnection, names: tuple[str, ...]) -> None:
    for name in names:
        sql = (MIGRATIONS / name).read_text(encoding="utf-8")
        # Strip line comments before splitting.  A comment in migration 002
        # documents a column with a semicolon, which is not a SQL delimiter.
        sql_without_comments = re.sub(r"--[^\r\n]*", "", sql)
        for statement in sql_without_comments.split(";"):
            statement = statement.strip()
            if not statement or statement.upper() in {"BEGIN", "COMMIT"}:
                continue
            await connection.execute(text(statement))


async def _insert_auth_user(connection: AsyncConnection, user_id: UUID) -> None:
    await connection.execute(
        text("INSERT INTO auth.users (id) VALUES (:user_id)"), {"user_id": user_id}
    )


async def _seed_legacy_hand(engine, user_id: UUID) -> UUID:
    parsed = parse_hand(FIXTURE.read_text(encoding="utf-8").splitlines())
    async with AsyncSession(engine) as session:
        upload = Upload(
            user_id=user_id,
            filename="p1_2_backfill.txt",
            storage_path=f"test/{user_id}/p1_2_backfill.txt",
            sha256="a" * 64,
            status="parsed",
        )
        session.add(upload)
        await session.flush()
        hand = hand_from_parsed(parsed, user_id, upload.id)
        # This phase intentionally represents a pre-014 database.  The current
        # ORM model knows about the additive ledger columns, but those columns
        # must not be mentioned while seeding the legacy row.
        legacy_values = {
            column.name: getattr(hand, column.name)
            for column in Hand.__table__.columns
            if column.name not in {"ledger_status", "ledger_version", "ledger_hash"}
        }
        legacy_values["flags"] = json.dumps(legacy_values["flags"])
        columns = ", ".join(legacy_values)
        parameters = ", ".join(f":{name}" for name in legacy_values)
        await session.execute(
            text(f"INSERT INTO hands ({columns}) VALUES ({parameters})"),
            legacy_values,
        )
        await session.flush()
        for player in players_from_parsed(parsed, hand.id, user_id):
            session.add(player)
        for action in actions_from_parsed(parsed, hand.id, user_id):
            legacy_action_values = {
                column.name: getattr(action, column.name)
                for column in HandAction.__table__.columns
                if column.name
                not in {"id", "ledger_event_index", "contribution_delta", "returned_delta", "raise_increment"}
            }
            columns = ", ".join(legacy_action_values)
            parameters = ", ".join(f":{name}" for name in legacy_action_values)
            await session.execute(
                text(f"INSERT INTO hand_actions ({columns}) VALUES ({parameters})"),
                legacy_action_values,
            )
        await session.commit()
        return hand.id


async def test_clean_install_and_upgrade_are_idempotent(engine) -> None:
    user_id = uuid4()
    async with engine.begin() as connection:
        await _reset_database(connection)
        await _apply(connection, BASE_MIGRATIONS + (P1_2_MIGRATION,))
        await _insert_auth_user(connection, user_id)
        columns = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'hand_ledgers'"
            )
        )
        assert {"payload", "ledger_hash", "summary_diff", "failure_reason"} <= set(columns.scalars())
        rls_enabled = await connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.hand_ledgers'::regclass")
        )
        assert rls_enabled.scalar_one() is True

    async with engine.begin() as connection:
        await _reset_database(connection)
        await _apply(connection, BASE_MIGRATIONS)
        await _insert_auth_user(connection, user_id)

    await _seed_legacy_hand(engine, user_id)
    async with engine.begin() as connection:
        await _apply(connection, (P1_2_MIGRATION, P1_2_MIGRATION))
        upgraded = await connection.execute(
            text("SELECT ledger_status, ledger_version, ledger_hash FROM hands")
        )
        assert upgraded.one() == ("legacy_unbackfilled", None, None)


async def test_backfill_is_idempotent_and_records_corrected_summary_diff(engine) -> None:
    user_id = uuid4()
    async with engine.begin() as connection:
        await _reset_database(connection)
        await _apply(connection, BASE_MIGRATIONS + (P1_2_MIGRATION,))
        await _insert_auth_user(connection, user_id)
    hand_id = await _seed_legacy_hand(engine, user_id)

    async with AsyncSession(engine) as session:
        before = await compute_stats(session, user_id)
        first = await backfill_canonical_ledgers(session, user_id=user_id)
        await session.commit()
        after = await compute_stats(session, user_id)
        ledger_row = await session.get(HandLedger, hand_id)
        assert first.scanned == 1
        assert first.valid == 1
        assert first.invalid == 0
        assert ledger_row is not None
        assert ledger_row.status == "valid"
        assert ledger_row.summary_diff["hero_invested"] == {
            "stored": "0.7500",
            "canonical": "0.9000",
        }
        # Action-rate statistics are preserved by the named projection; the
        # corrected monetary summary deliberately changes BB/100.
        for field in ("hands_count", "vpip_pct", "pfr_pct", "three_bet_pct", "wtsd_pct", "wsd_pct"):
            assert after[field] == before[field]
        assert after["bb_per_100"] != before["bb_per_100"]

        second = await backfill_canonical_ledgers(session, user_id=user_id)
        await session.commit()
        assert second.scanned == 0
        assert second.valid == 0


async def test_hand_ledgers_rls_isolates_owners(engine) -> None:
    first_user, second_user = uuid4(), uuid4()
    async with engine.begin() as connection:
        await _reset_database(connection)
        await _apply(connection, BASE_MIGRATIONS + (P1_2_MIGRATION,))
        await _insert_auth_user(connection, first_user)
        await _insert_auth_user(connection, second_user)
        await connection.execute(
            text(
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RLS_ROLE}') "
                f"THEN CREATE ROLE {RLS_ROLE} NOLOGIN; END IF; END $$"
            )
        )
        await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {RLS_ROLE}"))
        await connection.execute(text(f"GRANT SELECT ON hand_ledgers TO {RLS_ROLE}"))

        for user_id in (first_user, second_user):
            await connection.execute(
                text(
                    "INSERT INTO uploads (id, user_id, filename, storage_path, sha256) "
                    "VALUES (gen_random_uuid(), :user_id, 'rls.txt', :path, :sha)"
                ),
                {"user_id": user_id, "path": f"test/{user_id}", "sha": str(user_id).replace("-", "") * 2},
            )
            hand_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO hands (id, user_id, upload_id, coinpoker_hand_id, played_at, table_name, table_size, "
                    "stake_sb, stake_bb, button_seat, hero_seat, hero_position, hero_cards, total_pot, hero_net, hero_net_bb) "
                    "SELECT :hand_id, :user_id, id, :coinpoker_hand_id, now(), 'rls', 2, 0.01, 0.02, 1, 1, 'BTN/SB', "
                    "ARRAY['As','Kd'], 0, 0, 0 FROM uploads WHERE user_id = :user_id"
                ),
                {"hand_id": hand_id, "user_id": user_id, "coinpoker_hand_id": int(user_id.int % 10**12)},
            )
            await connection.execute(
                text(
                    "INSERT INTO hand_ledgers (hand_id, user_id, status, failure_reason) "
                    "VALUES (:hand_id, :user_id, 'invalid_ledger', 'test')"
                ),
                {"hand_id": hand_id, "user_id": user_id},
            )

        await connection.execute(text(f"SET LOCAL ROLE {RLS_ROLE}"))
        await connection.execute(
            text("SELECT set_config('request.jwt.claim.sub', :user_id, true)"),
            {"user_id": str(first_user)},
        )
        visible = await connection.execute(text("SELECT user_id FROM hand_ledgers"))
        assert visible.scalars().all() == [first_user]
