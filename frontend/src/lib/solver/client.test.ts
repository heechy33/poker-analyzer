import { describe, expect, it } from "vitest";

import { SolverClient, type WorkerLike } from "./client";
import type { SolveResult } from "./types";
import type { SolverRunCreate } from "@/types/api";

class FakeWorker implements WorkerLike {
  onmessage: ((event: MessageEvent) => void) | null = null;
  posted: unknown[] = [];

  postMessage(message: { id: number; method: string }): void {
    this.posted.push(message);
    if (message.method === "solve") {
      queueMicrotask(() => {
        this.onmessage?.({
          data: {
            id: message.id,
            type: "progress",
            data: { iterations: 10, exploitability_bb: 1.5, finished: false },
          },
        } as MessageEvent);
        this.onmessage?.({
          data: {
            id: message.id,
            result: {
              progress: { iterations: 20, exploitability_bb: 0.9, finished: true },
              output: {
                solver_version: "postflop-solver@test",
                iterations: 20,
                exploitability_bb: 0.9,
                actions: ["check", "bet_33"],
                combo_strategy: { AsKs: { check: 0.25, bet_33: 0.75 } },
                aggregate_frequencies: { check: 0.4, bet_33: 0.6 },
              },
            } satisfies SolveResult,
          },
        } as MessageEvent);
      });
    }
  }

  terminate(): void {
    this.posted.push({ method: "terminate" });
  }
}

describe("SolverClient pending-map RPC", () => {
  it("keeps progress events separate from final resolution", async () => {
    const worker = new FakeWorker();
    const client = new SolverClient(worker);
    const progressEvents: number[] = [];

    const result = await client.solve(
      {
        hand_id: "hand-1",
        street: "flop",
        mode: "quick",
        scenario_hash: "hash-1",
        scenario: {
          board: ["As", "Kd", "2c"],
          pot_bb: 10,
          effective_stack_bb: 90,
          oop_player: "BB",
          ip_player: "BTN",
          hero_range: { AKs: 1 },
          villain_range: { QQ: 1 },
          bet_tree: { flop: ["33%"], turn: ["50%"], river: ["75%"] },
        },
      },
      (progress) => progressEvents.push(progress.iterations),
    );

    expect(progressEvents).toEqual([10]);
    expect(result.progress.iterations).toBe(20);
    expect(result.output.solver_version).toBe("postflop-solver@test");

    const body: SolverRunCreate = {
      street: "flop",
      scenario_hash: "hash-1",
      output_jsonb: result.output,
    };
    expect(body.output_jsonb).toBe(result.output);
  });
});
