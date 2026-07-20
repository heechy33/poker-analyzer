"""Unit contract for the privileged P0.9 database verifier."""

import importlib.util
from pathlib import Path
import sys


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "verify_p0_9_database",
    SCRIPTS / "verify_p0_9_database.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _manifests(version: str, count_key: str) -> list[dict[str, object]]:
    return [
        {
            "object_name": name,
            count_key: index,
            "migration_version": version,
        }
        for index, name in enumerate(MODULE.LEGACY_OBJECTS)
    ]


def test_complete_requires_all_payloads_absent_and_both_manifests() -> None:
    objects_absent = {
        f"{schema}.{name}": True
        for schema in ("public", "legacy_solver_archive")
        for name in MODULE.LEGACY_OBJECTS
    }
    remediation = _manifests("012", "archived_row_count")
    purge = _manifests("013", "purged_row_count")

    assert MODULE._is_complete(objects_absent, remediation, purge)

    objects_absent["legacy_solver_archive.range_library"] = False
    assert not MODULE._is_complete(objects_absent, remediation, purge)


def test_complete_rejects_missing_or_wrong_version_manifest_rows() -> None:
    objects_absent = {
        f"{schema}.{name}": True
        for schema in ("public", "legacy_solver_archive")
        for name in MODULE.LEGACY_OBJECTS
    }
    remediation = _manifests("012", "archived_row_count")
    purge = _manifests("013", "purged_row_count")

    assert not MODULE._is_complete(objects_absent, remediation[:-1], purge)
    purge[0]["migration_version"] = "012"
    assert not MODULE._is_complete(objects_absent, remediation, purge)
