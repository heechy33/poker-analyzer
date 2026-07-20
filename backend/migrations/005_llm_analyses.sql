-- Migration 005: llm_analyses
-- Cache of Anthropic-generated hand commentary.
-- prompt_hash enables cache lookup without re-calling Claude.

CREATE TABLE llm_analyses (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  hand_id         uuid        REFERENCES hands(id) ON DELETE SET NULL,
  model           text        NOT NULL,               -- e.g. "claude-sonnet-4-6"
  prompt_hash     text        NOT NULL,               -- versioned SHA-256(hand_id + street + coaching mode)
  analysis_text   text        NOT NULL,
  leak_tags       text[]      NOT NULL DEFAULT '{}',  -- enumerated tags: {"overfold_vs_cbet", ...}
  input_tokens    integer,
  output_tokens   integer,
  created_at      timestamptz NOT NULL DEFAULT now(),

  UNIQUE (user_id, hand_id, prompt_hash)
);

-- GIN index for fast leak tag aggregation queries
CREATE INDEX idx_llm_analyses_user_tags ON llm_analyses USING GIN (leak_tags);

ALTER TABLE llm_analyses ENABLE ROW LEVEL SECURITY;

CREATE POLICY llm_analyses_rls ON llm_analyses
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
