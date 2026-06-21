-- Solver telemetry table: persist every solve attempt (success/failure)
-- for reliability dashboards and failure prioritization.
CREATE TABLE IF NOT EXISTS solver_telemetry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    hand_id UUID REFERENCES hands(id) ON DELETE SET NULL,
    street VARCHAR(16),
    scenario_hash VARCHAR(128),

    -- Outcome
    error_class VARCHAR(64) NOT NULL DEFAULT 'success',
    message TEXT,

    -- Scenario snapshot (from builder metadata)
    confidence VARCHAR(16),
    spr DOUBLE PRECISION,
    pot_bb DOUBLE PRECISION,
    eff_bb DOUBLE PRECISION,
    multiway_alive_count INTEGER,
    hero_lookup_hit BOOLEAN,
    villain_lookup_hit BOOLEAN,
    pot_error_pct DOUBLE PRECISION,

    -- Bet tree shape (effective sizes after force_allin dedup)
    effective_bet_sizes_flop VARCHAR(16)[],
    effective_bet_sizes_turn VARCHAR(16)[],
    effective_bet_sizes_river VARCHAR(16)[],

    -- Solver run metadata
    solver_mode VARCHAR(16),
    duration_ms INTEGER,
    wasm_memory_used INTEGER,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS ix_solver_telemetry_user_id ON solver_telemetry(user_id);
CREATE INDEX IF NOT EXISTS ix_solver_telemetry_error_class ON solver_telemetry(error_class);
CREATE INDEX IF NOT EXISTS ix_solver_telemetry_scenario_hash ON solver_telemetry(scenario_hash);
CREATE INDEX IF NOT EXISTS ix_solver_telemetry_created_at ON solver_telemetry(created_at);