-- Migration 004: solver_runs
-- Cache of CFR solver outputs. scenario_hash is GLOBALLY unique (not per-user).
-- The same board+ranges+pot always produces the same equilibrium regardless of who solved it,
-- so user A solving a spot for the first time populates the cache for user B.

CREATE TABLE solver_runs (
  id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid          NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  hand_id             uuid          REFERENCES hands(id) ON DELETE SET NULL,
  street              text          NOT NULL,
  scenario_hash       text          NOT NULL UNIQUE,  -- SHA-256 of canonical scenario JSON (globally unique)
  solver_version      text          NOT NULL,         -- "postflop-solver@<commit-sha>"
  iterations          integer       NOT NULL,
  exploitability_bb   numeric(8,4)  NOT NULL,         -- convergence proof; target <= 0.5 bb
  output_jsonb        jsonb         NOT NULL,         -- strategy vectors per combo (TOAST-compressed)
  created_at          timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX idx_solver_runs_hash ON solver_runs (scenario_hash);  -- O(1) cache lookup

ALTER TABLE solver_runs ENABLE ROW LEVEL SECURITY;

-- Anyone can read cached solver results (shared knowledge, like range_library)
CREATE POLICY solver_runs_read   ON solver_runs FOR SELECT USING (true);
-- Only the user who ran the solve can insert their result
CREATE POLICY solver_runs_insert ON solver_runs FOR INSERT WITH CHECK (user_id = auth.uid());
