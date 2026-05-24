-- Migration 003: range_library
-- Static GTO preflop range reference, loaded once via migration.
-- Public read (no user_id). No RLS write access from the app — only service role inserts.

CREATE TABLE range_library (
  id                    serial      PRIMARY KEY,
  table_size            smallint    NOT NULL,           -- 2, 6, or 9
  effective_stack_bb    smallint    NOT NULL DEFAULT 100,
  position              text        NOT NULL,           -- BTN, CO, HJ, UTG, SB, BB
  action_sequence       text        NOT NULL,           -- e.g. "open", "vs_BTN_open_call", "vs_CO_open_3bet"
  range_string          text        NOT NULL,           -- PIO-style: "22+,A2s+,K9s+,..."
  combo_weights         jsonb,                          -- optional: {"AKs": 1.0, "JTs": 0.75} for mixed strategies
  source                text        NOT NULL,           -- "GTOWizard-free-tier", "rivers-app", etc.
  version               text        NOT NULL DEFAULT 'v1',
  created_at            timestamptz NOT NULL DEFAULT now(),

  UNIQUE (table_size, effective_stack_bb, position, action_sequence, version)
);

-- Ranges are shared knowledge — any authenticated or anonymous user can read
ALTER TABLE range_library ENABLE ROW LEVEL SECURITY;

CREATE POLICY range_library_read ON range_library FOR SELECT USING (true);
-- No INSERT/UPDATE policy from the app layer — only service role key can write
