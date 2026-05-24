import type { ScenarioEnvelope, Street } from "@/types/api";

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
  scenario: ScenarioEnvelope;
  scenario_hash: string;
  street: Street;
  hand_id: string;
  mode: SolveMode;
  metadata?: Record<string, unknown>;
}

export interface SolveResult {
  output: SolverOutput;
  progress: SolveProgress;
}
