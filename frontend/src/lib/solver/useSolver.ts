"use client";

import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { fetchScenario, postSolverRun } from "@/lib/api";
import type { Street } from "@/types/api";

import { getSolverClient } from "./client";
import type { SolveMode, SolveProgress, SolveRequest, SolveResult, SolverOutput } from "./types";

function progressFromOutput(output: SolverOutput): SolveProgress {
  return {
    iterations: output.iterations,
    exploitability_bb: output.exploitability_bb,
    finished: true,
  };
}

export function useSolver() {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<SolveProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSolving, setIsSolving] = useState(false);

  const solve = useCallback(
    async (handId: string, street: Street, mode: SolveMode): Promise<SolveResult> => {
      setError(null);
      setIsSolving(true);
      setProgress(null);

      try {
        const scenarioResponse = await queryClient.fetchQuery({
          queryKey: ["scenario", handId, street],
          queryFn: () => fetchScenario(handId, street),
        });

        if (scenarioResponse.cached && scenarioResponse.cached_output) {
          const output = scenarioResponse.cached_output as unknown as SolverOutput;
          const cachedProgress = progressFromOutput(output);
          setProgress(cachedProgress);
          return { output, progress: cachedProgress };
        }

        const request: SolveRequest = {
          scenario: scenarioResponse.scenario,
          scenario_hash: scenarioResponse.scenario_hash,
          street,
          hand_id: handId,
          mode,
          metadata: scenarioResponse.metadata,
        };

        const result = await getSolverClient().solve(request, setProgress);
        void postSolverRun({
          hand_id: handId,
          street,
          scenario_hash: scenarioResponse.scenario_hash,
          solver_version: result.output.solver_version,
          iterations: result.output.iterations,
          exploitability_bb: String(result.output.exploitability_bb),
          output_jsonb: result.output,
        }).catch((postError: unknown) => {
          console.warn("Failed to cache solver output", postError);
        });
        return result;
      } catch (solveError) {
        const message = solveError instanceof Error ? solveError.message : String(solveError);
        setError(message);
        throw solveError;
      } finally {
        setIsSolving(false);
      }
    },
    [queryClient],
  );

  const cancel = useCallback(async () => {
    await getSolverClient().cancel();
    setIsSolving(false);
  }, []);

  return { solve, cancel, progress, error, isSolving };
}
