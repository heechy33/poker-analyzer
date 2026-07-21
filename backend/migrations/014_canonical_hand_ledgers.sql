-- Migration 014: versioned canonical ledger persistence for Phase 1.
--
-- Raw hand text remains the reparsable source.  `hand_ledgers` stores one
-- immutable, hash-addressed ledger result and `hand_actions` becomes a
-- compatibility projection for replay and statistics; it is never an
-- independent accounting source.

BEGIN;

ALTER TABLE hands
  ADD COLUMN IF NOT EXISTS ledger_status text NOT NULL DEFAULT 'legacy_unbackfilled',
  ADD COLUMN IF NOT EXISTS ledger_version text,
  ADD COLUMN IF NOT EXISTS ledger_hash text;

ALTER TABLE hands DROP CONSTRAINT IF EXISTS hands_ledger_status_check;
ALTER TABLE hands ADD CONSTRAINT hands_ledger_status_check CHECK (
  ledger_status IN ('valid', 'invalid_ledger', 'legacy_unbackfilled')
);

CREATE INDEX IF NOT EXISTS idx_hands_user_ledger_status
  ON hands (user_id, ledger_status);

CREATE TABLE IF NOT EXISTS hand_ledgers (
  hand_id        uuid PRIMARY KEY REFERENCES hands(id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  status         text NOT NULL CHECK (status IN ('valid', 'invalid_ledger')),
  schema_version text,
  ledger_hash    text,
  payload        jsonb,
  summary_diff   jsonb NOT NULL DEFAULT '{}'::jsonb,
  failure_reason text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT hand_ledgers_valid_payload CHECK (
    (status = 'valid' AND schema_version IS NOT NULL AND ledger_hash IS NOT NULL
      AND payload IS NOT NULL AND failure_reason IS NULL)
    OR
    (status = 'invalid_ledger' AND schema_version IS NULL AND ledger_hash IS NULL
      AND payload IS NULL AND failure_reason IS NOT NULL)
  )
);

ALTER TABLE hand_ledgers
  ADD COLUMN IF NOT EXISTS summary_diff jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_hand_ledgers_user_status
  ON hand_ledgers (user_id, status);
DROP INDEX IF EXISTS idx_hand_ledgers_hash;
CREATE INDEX idx_hand_ledgers_hash
  ON hand_ledgers (ledger_hash) WHERE ledger_hash IS NOT NULL;

ALTER TABLE hand_ledgers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS hand_ledgers_rls ON hand_ledgers;
CREATE POLICY hand_ledgers_rls ON hand_ledgers
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

ALTER TABLE hand_actions
  ADD COLUMN IF NOT EXISTS ledger_event_index integer,
  ADD COLUMN IF NOT EXISTS contribution_delta numeric(12,4),
  ADD COLUMN IF NOT EXISTS returned_delta numeric(12,4),
  ADD COLUMN IF NOT EXISTS raise_increment numeric(12,4);

ALTER TABLE hand_actions DROP CONSTRAINT IF EXISTS hand_actions_action_check;
ALTER TABLE hand_actions ADD CONSTRAINT hand_actions_action_check CHECK (action IN (
  'post_sb', 'post_bb', 'post_ante', 'post_dead_blind', 'post_straddle',
  'fold', 'check', 'call', 'bet', 'raise', 'return_uncalled',
  'all_in', 'show', 'muck', 'collect'
));

CREATE UNIQUE INDEX IF NOT EXISTS idx_hand_actions_ledger_event
  ON hand_actions (hand_id, ledger_event_index)
  WHERE ledger_event_index IS NOT NULL;

COMMIT;
