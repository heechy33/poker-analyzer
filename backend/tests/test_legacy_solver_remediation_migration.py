"""Safety contract for the Phase 0 legacy-database remediation."""

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations"
    / "012_archive_legacy_solver_objects.sql"
)
PURGE_MIGRATION = (
    Path(__file__).parents[1]
    / "migrations"
    / "013_purge_legacy_solver_data.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _purge_sql() -> str:
    return PURGE_MIGRATION.read_text(encoding="utf-8")


def test_migration_is_an_atomic_allowlisted_archive() -> None:
    sql = _sql()
    normalized = re.sub(r"\s+", " ", sql.lower()).strip()

    assert normalized.startswith("-- migration 012:")
    assert " begin;" in normalized
    assert normalized.endswith("commit;")
    assert "create schema if not exists legacy_solver_archive" in normalized

    allowlist = re.search(
        r"foreach legacy_name in array array\[(.*?)\]\s*loop",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert allowlist is not None
    assert re.findall(r"'([^']+)'", allowlist.group(1)) == [
        "range_library",
        "solver_runs",
        "solver_telemetry",
    ]

    assert "alter table public.%i set schema legacy_solver_archive" in normalized
    assert "both public and archive objects exist" in normalized
    assert "unexpected relkind" in normalized


def test_migration_revokes_access_before_moving_data() -> None:
    normalized = re.sub(r"\s+", " ", _sql().lower())

    revoke_position = normalized.index(
        "revoke all privileges on table public.%i from public"
    )
    move_position = normalized.index(
        "alter table public.%i set schema legacy_solver_archive"
    )
    assert revoke_position < move_position

    assert "revoke all privileges on schema legacy_solver_archive from public" in normalized
    assert "array['anon', 'authenticated']" in normalized
    assert "drop policy %i on legacy_solver_archive.%i" in normalized
    assert "enable row level security" in normalized
    assert "force row level security" in normalized


def test_migration_preserves_rows_and_records_the_archive() -> None:
    normalized = re.sub(r"\s+", " ", _sql().lower())

    destructive_table_sql = re.compile(
        r"\b(drop\s+table|truncate|delete\s+from)\b",
        flags=re.IGNORECASE,
    )
    assert destructive_table_sql.search(_sql()) is None
    assert "select count(*) from public.%i" in normalized
    assert "remediation_manifest" in normalized
    assert "on conflict (object_name) do nothing" in normalized


def test_purge_is_atomic_and_limited_to_retired_solver_objects() -> None:
    sql = _purge_sql()
    normalized = re.sub(r"\s+", " ", sql.lower()).strip()

    assert normalized.startswith("-- migration 013:")
    assert " begin;" in normalized
    assert normalized.endswith("commit;")
    assert "drop schema" not in normalized
    assert " cascade" not in normalized

    allowlist = re.search(
        r"foreach legacy_name in array array\[(.*?)\]\s*loop",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert allowlist is not None
    assert re.findall(r"'([^']+)'", allowlist.group(1)) == [
        "range_library",
        "solver_runs",
        "solver_telemetry",
    ]
    assert normalized.count("drop table") == 1
    assert "drop table legacy_solver_archive.%i" in normalized


def test_purge_requires_quarantine_and_keeps_a_private_audit_trail() -> None:
    normalized = re.sub(r"\s+", " ", _purge_sql().lower())

    refuse_position = normalized.index("refusing to purge public.%")
    drop_position = normalized.index("drop table legacy_solver_archive.%i")
    assert refuse_position < drop_position
    assert "apply migration 012 first" in normalized
    assert "select count(*) from legacy_solver_archive.%i" in normalized
    assert "legacy_solver_archive.purge_manifest" in normalized
    assert "purged_row_count" in normalized
    assert "array['anon', 'authenticated']" in normalized
    assert (
        "revoke all privileges on table legacy_solver_archive.purge_manifest"
        in normalized
    )
