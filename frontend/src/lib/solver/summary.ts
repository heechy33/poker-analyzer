import type { SolverSummary } from "@/types/api";

import type { SolverOutput } from "./types";

export interface SolverSummaryInput {
  heroCombo: string;
  actualAction: string;
  output: SolverOutput;
}

function bestActionByValue(values: Record<string, number>): string | null {
  let bestAction: string | null = null;
  let bestValue = Number.NEGATIVE_INFINITY;
  for (const [action, value] of Object.entries(values)) {
    if (value > bestValue) {
      bestAction = action;
      bestValue = value;
    }
  }
  return bestAction;
}

export function computeSolverSummary({
  heroCombo,
  actualAction,
  output,
}: SolverSummaryInput): SolverSummary {
  const actionFrequencies =
    output.combo_strategy[heroCombo] ?? output.aggregate_frequencies ?? {};
  const comboEv = output.combo_ev?.[heroCombo];
  const solverBestAction =
    (comboEv && bestActionByValue(comboEv)) ?? bestActionByValue(actionFrequencies);

  const actualEv = comboEv?.[actualAction];
  const bestEv = solverBestAction ? comboEv?.[solverBestAction] : undefined;

  return {
    hero_action: actualAction,
    solver_best_action: solverBestAction,
    ev_diff_bb:
      actualEv !== undefined && bestEv !== undefined
        ? Math.max(0, bestEv - actualEv)
        : null,
    action_frequencies: actionFrequencies,
  };
}

