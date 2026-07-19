"""Read-only Supabase audit for the Phase 0 legacy solver tables.

This intentionally uses the PostgREST surface rather than a PostgreSQL driver:
it verifies whether the retired public objects are still reachable through the
same API boundary the application used. Credentials and project identifiers are
never printed; the target is represented by a short one-way fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


LEGACY_OBJECTS = ("range_library", "solver_runs", "solver_telemetry")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _target_fingerprint(supabase_url: str) -> str:
    host = (urlparse(supabase_url).hostname or "unknown").lower()
    project_ref = re.sub(r"\.supabase\.co$", "", host)
    digest = hashlib.sha256(project_ref.encode("utf-8")).hexdigest()
    return f"supabase/{digest[:12]}"


def _request_status(
    *, supabase_url: str, api_key: str, table: str
) -> dict[str, object]:
    query = urlencode({"select": "id", "limit": 1})
    request = Request(
        f"{supabase_url.rstrip('/')}/rest/v1/{table}?{query}",
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Prefer": "count=exact",
            "Range": "0-0",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - audited URL
            return {
                "status": response.status,
                "content_range": response.headers.get("Content-Range"),
            }
    except HTTPError as error:
        # Do not print server bodies: they can contain schema or deployment data.
        return {"status": error.code, "content_range": None}
    except URLError as error:
        return {
            "status": "unreachable",
            "error": type(error.reason).__name__,
        }


def audit(env_file: Path) -> dict[str, object]:
    values = _read_env_file(env_file)
    required = (
        "SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ValueError(f"missing required settings: {', '.join(missing)}")

    url = values["SUPABASE_URL"]
    anon_key = values["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
    service_key = values["SUPABASE_SERVICE_ROLE_KEY"]
    objects: dict[str, object] = {}
    for table in LEGACY_OBJECTS:
        objects[table] = {
            "anonymous": _request_status(
                supabase_url=url,
                api_key=anon_key,
                table=table,
            ),
            "service_role": _request_status(
                supabase_url=url,
                api_key=service_key,
                table=table,
            ),
        }

    return {
        "target": _target_fingerprint(url),
        "audit": "read_only_postgrest",
        "objects": objects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing Supabase URL and API keys.",
    )
    args = parser.parse_args()

    try:
        result = audit(args.env_file)
    except (OSError, ValueError) as error:
        print(f"audit failed: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    statuses = [
        result["objects"][name][access]["status"]  # type: ignore[index]
        for name in LEGACY_OBJECTS
        for access in ("anonymous", "service_role")
    ]
    return 2 if "unreachable" in statuses else 0


if __name__ == "__main__":
    raise SystemExit(main())
