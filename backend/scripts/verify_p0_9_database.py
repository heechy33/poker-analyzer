"""Privileged, read-only verification for the P0.9 database purge.

The command prints only the redacted target fingerprint, object-absence flags,
and migration row counts. It never prints the database URL or credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import asyncpg

from audit_legacy_solver_objects import _read_env_file, _target_fingerprint


LEGACY_OBJECTS = ("range_library", "solver_runs", "solver_telemetry")


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _is_complete(
    objects_absent: dict[str, bool],
    remediation: list[dict[str, object]],
    purge: list[dict[str, object]],
) -> bool:
    expected = set(LEGACY_OBJECTS)
    remediation_names = {str(row["object_name"]) for row in remediation}
    purge_names = {str(row["object_name"]) for row in purge}
    return (
        set(objects_absent) == {
            f"{schema}.{name}"
            for schema in ("public", "legacy_solver_archive")
            for name in LEGACY_OBJECTS
        }
        and all(objects_absent.values())
        and remediation_names == expected
        and purge_names == expected
        and all(row["migration_version"] == "012" for row in remediation)
        and all(row["migration_version"] == "013" for row in purge)
    )


async def verify(env_file: Path) -> tuple[dict[str, object], bool]:
    values = _read_env_file(env_file)
    missing = [
        name for name in ("DATABASE_URL", "SUPABASE_URL") if not values.get(name)
    ]
    if missing:
        raise ValueError(f"missing required settings: {', '.join(missing)}")

    connection = await asyncpg.connect(
        _asyncpg_dsn(values["DATABASE_URL"]),
        command_timeout=15,
        statement_cache_size=0,
    )
    try:
        objects_absent: dict[str, bool] = {}
        for schema in ("public", "legacy_solver_archive"):
            for name in LEGACY_OBJECTS:
                qualified_name = f"{schema}.{name}"
                objects_absent[qualified_name] = (
                    await connection.fetchval(
                        "SELECT to_regclass($1) IS NULL",
                        qualified_name,
                    )
                )

        remediation = [
            dict(row)
            for row in await connection.fetch(
                """
                SELECT object_name, archived_row_count, migration_version
                FROM legacy_solver_archive.remediation_manifest
                ORDER BY object_name
                """
            )
        ]
        purge = [
            dict(row)
            for row in await connection.fetch(
                """
                SELECT object_name, purged_row_count, migration_version
                FROM legacy_solver_archive.purge_manifest
                ORDER BY object_name
                """
            )
        ]
    finally:
        await connection.close()

    result: dict[str, object] = {
        "target": _target_fingerprint(values["SUPABASE_URL"]),
        "objects_absent": objects_absent,
        "remediation_manifest": remediation,
        "purge_manifest": purge,
    }
    return result, _is_complete(objects_absent, remediation, purge)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing DATABASE_URL and SUPABASE_URL.",
    )
    args = parser.parse_args()

    try:
        result, complete = asyncio.run(verify(args.env_file))
    except (OSError, ValueError, asyncpg.PostgresError) as error:
        print(f"verification failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
