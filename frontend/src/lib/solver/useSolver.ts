"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { fetchScenario, postSolverRun, postSolverTelemetry } from "@/lib/api";
import type { ScenarioResponse, SolverTelemetryCreate, Street } from "@/types/api";

import { createSolverClient, type SolverClient } from "./client";
import type { SolveMode, SolveProgress, SolveRequest, SolveResult, SolverOutput } from "./types";

function progressFromOutput(output: SolverOutput): SolveProgress {
  return {
    iterations: output.iterations,
    exploitability_bb: output.exploitability_bb,
    finished: true,
  };
}

/**
 * Fire-and-forget telemetry beacon.  Extracts scenario snapshot fields from
 * the builder's metadata block so the server can build reliability dashboards.
 */
function _fireTelemetry(
  scenarioResponse: ScenarioResponse,
  street: Street,
  handId: string,
  mode: SolveMode,
  errorClass: string,
  durationMs: number,
  message?: string,
): void {
  const meta = scenarioResponse.metadata as Record<string, unknown>;
  const payload: SolverTelemetryCreate = {
    hand_id: handId,
    street,
    scenario_hash: scenarioResponse.scenario_hash,
    error_class: errorClass,
    message: message ?? null,
    confidence: (meta.confidence as string) ?? null,
    spr: (meta.spr as number) ?? null,
    pot_bb: (meta.pot_bb_telemetry as number) ?? null,
    eff_bb: (meta.eff_bb_telemetry as number) ?? null,
    multiway_alive_count: (meta.multiway_alive_count as number) ?? null,
    hero_lookup_hit: (meta.hero_lookup_hit as boolean) ?? null,
    villain_lookup_hit: (meta.villain_lookup_hit as boolean) ?? null,
    pot_error_pct: (meta.pot_error_pct as number) ?? null,
    effective_bet_sizes_flop: (meta.effective_bet_sizes_flop as string[]) ?? null,
    effective_bet_sizes_turn: (meta.effective_bet_sizes_turn as string[]) ?? null,
    effective_bet_sizes_river: (meta.effective_bet_sizes_river as string[]) ?? null,
    solver_mode: mode,
    duration_ms: durationMs,
    wasm_memory_used: null,
  };
  postSolverTelemetry(payload);
}

export function useSolver() {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<SolveProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSolving, setIsSolving] = useState(false);
  /** Track the currently active client so cancel() targets the right worker. */
  const clientRef = useRef<SolverClient | null>(null);

  const solve = useCallback(
    async (handId: string, street: Street, mode: SolveMode): Promise<SolveResult> => {
      setError(null);
      setIsSolving(true);
      setProgress(null);

      const startMs = performance.now();
      const client = createSolverClient();
      clientRef.current = client;

      try {
        const scenarioResponse = await queryClient.fetchQuery({
          queryKey: ["scenario", handId, street],
          queryFn: () => fetchScenario(handId, street),
        });

        if (scenarioResponse.cached && scenarioResponse.cached_output) {
          const output = scenarioResponse.cached_output as unknown as SolverOutput;
          const cachedProgress = progressFromOutput(output);
          setProgress(cachedProgress);

          // Telemetry for cache hit (instant success).
          const durationMs = Math.round(performance.now() - startMs);
          _fireTelemetry(scenarioResponse, street, handId, mode, "success", durationMs);

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

        const result = await client.solve(request, setProgress);
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

        // Telemetry for successful solve.
        const durationMs = Math.round(performance.now() - startMs);
        _fireTelemetry(scenarioResponse, street, handId, mode, "success", durationMs);

        return result;
      } catch (solveError) {
        const message = solveError instanceof Error ? solveError.message : String(solveError);
        setError(message);

        // Extract error_class from the error object if present.
        const errorClass =
          (solveError as Error & { error_class?: string }).error_class || "unknown";

        // Fire telemetry on failure with minimal payload (scenario may not be available).
        const durationMs = Math.round(performance.now() - startMs);
        postSolverTelemetry({
          hand_id: handId,
          street,
          error_class: errorClass,
          message,
          solver_mode: mode,
          duration_ms: durationMs,
        });
        throw solveError;
      } finally {
        client.terminate();
        if (clientRef.current === client) {
          clientRef.current = null;
        }
        setIsSolving(false);
      }
    },
    [queryClient],
  );

  const cancel = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      await client.cancel();
    } catch {
      // If the worker is already dead, cancel itself fails — that's fine.
    } finally {
      client.terminate();
      clientRef.current = null;
      setIsSolving(false);
    }
  }, []);

  return { solve, cancel, progress, error, isSolving };
}