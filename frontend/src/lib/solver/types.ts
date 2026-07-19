import type { Street } from "@/types/api";

/**
 * Quarantined low-level wrapper input. This is not a product solve contract and
 * must be replaced by the versioned HUNL solve specification before reuse.
 */
export interface LegacyScenarioEnvelope {
  board: string[];
  pot_bb: number;
  effective_stack_bb: number;
  oop_player: string;
  ip_player: string;
  hero_range: Record<string, number>;
  villain_range: Record<string, number>;
  bet_tree: {
    flop: string[];
    turn: string[];
    river: string[];
    allin_always?: boolean;
  };
  hero_position?: string;
  oop_range?: Record<string, number>;
  ip_range?: Record<string, number>;
}

export type SolveMode = "quick" | "full";

export interface SolveProgress {
  iterations: number;
  exploitability_bb: number;
  finished: boolean;
}

export interface SolverOutput extends Record<string, unknown> {
  solver_version: string;
  iterations: number;
  exploitability_bb: number;
  actions: string[];
  combo_strategy: Record<string, Record<string, number>>;
  combo_ev?: Record<string, Record<string, number>>;
  aggregate_frequencies: Record<string, number>;
}

export interface SolveRequest {
  scenario: LegacyScenarioEnvelope;
  scenario_hash: string;
  street: Street;
  hand_id: string;
  mode: SolveMode;
  metadata?: Record<string, unknown>;
}

export interface SolveResult {
  output: SolverOutput;
  progress: SolveProgress;
  /** Numeric action-index path used in `apply_history` to reach the graded node. */
  historyPath?: number[];
  /**
   * `true` when the decision-node path could not be fully reconstructed
   * from the hand action log — the exported strategy is from a shallower
   * node than intended (or the root).
   */
  historyIncomplete?: boolean;
  /** 0 = root node; 1+ = depth navigated past villain/hero pre-actions. */
  nodeDepth?: number;
  /**
   * `true` if the original "full" solve timed out and the result is from a
   * quick approximation. The UI should surface a note:
   * "Full solve timed out — showing quick approximation".
   */
  downgradedToQuick?: boolean;
}
