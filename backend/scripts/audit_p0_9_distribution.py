"""Fail when release inputs contain retired or prohibited range sources."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    ROOT / "backend" / "app",
    ROOT / "backend" / "migrations",
    ROOT / "frontend" / "src",
    ROOT / "solver-wasm" / "src",
    ROOT / "solver-wasm" / "tests",
    ROOT / "postflop-solver" / "src",
    ROOT / "postflop-solver" / "tests",
    ROOT / "postflop-solver" / "examples",
)
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".mjs",
    ".py",
    ".rs",
    ".sql",
    ".ts",
    ".tsx",
}
PROHIBITED_PROVIDER_MARKERS = (
    "gto wizard",
    "gtowizard",
    "riverodds",
    "rivers-app",
)
ENGINE_COMMIT = "a67bf3d9f43b9998871a5c999717c1b72bd9e2ef"
WASM_ARTIFACT_NAMES = {
    "solver_wasm_bg.wasm",
    "solver_wasm.js",
    "solver_wasm.d.ts",
    "solver_wasm_bg.wasm.d.ts",
    "LICENSE.txt",
    "SOURCE-OFFER.txt",
}


def _text_files() -> list[Path]:
    return sorted(
        path
        for root in SCAN_ROOTS
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
    )


def audit() -> list[str]:
    errors: list[str] = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        for marker in PROHIBITED_PROVIDER_MARKERS:
            if marker in lowered:
                errors.append(f"{path.relative_to(ROOT)} contains {marker!r}")
        if path.suffix.lower() == ".sql" and re.search(
            r"\binsert\s+into\s+(?:public\.)?range_library\b",
            lowered,
        ):
            errors.append(f"{path.relative_to(ROOT)} seeds retired range_library")

    root_license = (ROOT / "LICENSE").read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    ).strip()
    engine_license = (
        (ROOT / "postflop-solver" / "LICENSE")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .strip()
    )
    if root_license != engine_license:
        errors.append("root LICENSE must match the retained engine AGPL text")

    engine_source = json.loads(
        (ROOT / "solver-wasm" / "ENGINE_SOURCE.json").read_text(encoding="utf-8")
    )
    if engine_source.get("commit") != ENGINE_COMMIT:
        errors.append("ENGINE_SOURCE.json does not record the approved engine commit")
    if engine_source.get("license") != "AGPL-3.0-or-later":
        errors.append("ENGINE_SOURCE.json must record AGPL-3.0-or-later")

    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    if ENGINE_COMMIT not in notice:
        errors.append("THIRD_PARTY_NOTICES.md is missing the exact engine commit")

    copy_script = (ROOT / "frontend" / "scripts" / "copy-wasm.mjs").read_text(
        encoding="utf-8"
    )
    for required in (
        "LICENSE.txt",
        "SOURCE-OFFER.txt",
        "release build requires a clean source tree",
        "does not match gitlink",
    ):
        if required not in copy_script:
            errors.append(f"copy-wasm.mjs is missing release guard {required!r}")

    purge_sql = (
        ROOT / "backend" / "migrations" / "013_purge_legacy_solver_data.sql"
    ).read_text(encoding="utf-8")
    for object_name in ("range_library", "solver_runs", "solver_telemetry"):
        if f"'{object_name}'" not in purge_sql:
            errors.append(f"migration 013 does not allowlist {object_name}")
    if "DROP TABLE legacy_solver_archive.%I" not in purge_sql:
        errors.append("migration 013 does not purge quarantined solver payloads")

    artifact_dir = ROOT / "frontend" / "public" / "wasm"
    if artifact_dir.exists():
        artifact_files = {path.name for path in artifact_dir.iterdir() if path.is_file()}
        unexpected = artifact_files - WASM_ARTIFACT_NAMES
        if unexpected:
            errors.append(
                "WASM artifact directory contains non-allowlisted files: "
                + ", ".join(sorted(unexpected))
            )
        code_artifacts = artifact_files - {"LICENSE.txt", "SOURCE-OFFER.txt"}
        if code_artifacts:
            for required in ("LICENSE.txt", "SOURCE-OFFER.txt"):
                if required not in artifact_files:
                    errors.append(f"WASM artifacts are missing {required}")
            for path in artifact_dir.iterdir():
                if not path.is_file():
                    continue
                lowered = path.read_bytes().lower()
                for marker in PROHIBITED_PROVIDER_MARKERS:
                    if marker.encode("utf-8") in lowered:
                        errors.append(f"{path.relative_to(ROOT)} contains {marker!r}")

    return errors


def main() -> int:
    errors = audit()
    if errors:
        for error in errors:
            print(f"P0.9 audit failed: {error}", file=sys.stderr)
        return 1
    print("P0.9 distribution/source audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
