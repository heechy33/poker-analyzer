"""Static P1.2 safety assertions for migration 014."""

from __future__ import annotations

import re
from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / "014_canonical_hand_ledgers.sql"


def test_migration_014_is_atomic_and_keeps_canonical_ledger_separate() -> None:
    normalized = re.sub(r"\s+", " ", MIGRATION.read_text(encoding="utf-8").lower()).strip()

    assert normalized.startswith("-- migration 014:")
    assert " begin;" in normalized
    assert normalized.endswith("commit;")
    assert "create table if not exists hand_ledgers" in normalized
    assert "ledger_status in ('valid', 'invalid_ledger', 'legacy_unbackfilled')" in normalized
    assert "summary_diff jsonb not null default '{}'::jsonb" in normalized
    assert "alter table hand_ledgers add column if not exists summary_diff" in normalized
    assert "alter table hand_actions add column if not exists ledger_event_index" in normalized
    assert "add column if not exists contribution_delta" in normalized
    assert "drop index if exists idx_hand_ledgers_hash" in normalized


def test_migration_014_applies_row_level_security_and_has_no_destructive_rewrite() -> None:
    normalized = re.sub(r"\s+", " ", MIGRATION.read_text(encoding="utf-8").lower())

    assert "alter table hand_ledgers enable row level security" in normalized
    assert "create policy hand_ledgers_rls on hand_ledgers" in normalized
    assert "using (user_id = auth.uid())" in normalized
    assert "with check (user_id = auth.uid())" in normalized
    assert "drop table" not in normalized
    assert "truncate" not in normalized
    assert "delete from" not in normalized
