-- Migration 001: uploads
-- Tracks every .txt file a user submits for parsing.
-- Dedup is enforced per-user by SHA-256 content hash.

CREATE TABLE uploads (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  filename        text        NOT NULL,
  storage_path    text        NOT NULL,           -- path inside Supabase Storage bucket "hand-histories"
  sha256          text        NOT NULL,           -- SHA-256 of file bytes, for dedup
  bytes           integer,
  hand_count      integer,                        -- populated after parsing
  status          text        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'parsing', 'parsed', 'error')),
  error_message   text,
  uploaded_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE uploads ADD CONSTRAINT uploads_user_sha256_unique UNIQUE (user_id, sha256);

ALTER TABLE uploads ENABLE ROW LEVEL SECURITY;

CREATE POLICY uploads_rls ON uploads
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
