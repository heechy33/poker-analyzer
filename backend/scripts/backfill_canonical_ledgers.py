"""Backfill versioned canonical ledgers from stored CoinPoker raw text.

Run only against an approved database, for example:
    python scripts/backfill_canonical_ledgers.py --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import async_engine
from app.services.ingest import backfill_canonical_ledgers


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=UUID, help="Limit to one owner UUID")
    parser.add_argument("--limit", type=int, help="Maximum hands to process")
    return parser.parse_args()


async def _run() -> None:
    args = _arguments()
    async with AsyncSession(async_engine) as session:
        result = await backfill_canonical_ledgers(
            session,
            user_id=args.user_id,
            limit=args.limit,
        )
        await session.commit()
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run())
