import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SolverClient, type WorkerLike } from "./client";
import type { SolveProgress, SolveResult } from "./types";
import type { SolverRunCreate } from "@/types/api";

/**
 * FakeWorker: supports onmessage, onerror, onmessageerror callbacks
 * and records all postMessage calls.
 */
class FakeWorker implements WorkerLike {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  onmessageerror: ((event: MessageEvent) => void) | null = null;
  posted: unknown[] = [];
  /**
   * When true, respond to "ping" synchronously via setTimeout(0)
   * instead of queueMicrotask so fake-timer tests can advance it.
   */
  syncPing = false;

  /**
   * Custom resolver: invoked when a "solve" message is posted.
   * Receives (id, postMessage, fireProgress).
   */
  private _solveResolver:
    | ((
        id: number,
        post: (data: unknown) => void,
        fireProgress: (data: SolveProgress) => void,
      ) => void)
    | null = null;

  setSolveResolver(
    fn: (
      id: number,
      post: (data: unknown) => void,
      fireProgress: (data: SolveProgress) => void,
    ) => void,
  ) {
    this._solveResolver = fn;
  }

  postMessage(message: { id: number; method: string; params?: unknown }): void {
    this.posted.push(message);
    const post = (data: unknown) => {
      this.onmessage?.({ data } as MessageEvent);
    };
    const fireProgress = (data: SolveProgress) => {
      post({ id: message.id, type: "progress", data });
    };

    if (message.method === "ping") {
      if (this.syncPing) {
        setTimeout(() => post({ id: message.id, result: "pong" }), 0);
      } else {
        queueMicrotask(() => post({ id: message.id, result: "pong" }));
      }
      return;
    }
    if (message.method === "cancel") {
      queueMicrotask(() => post({ id: message.id, result: undefined }));
      return;
    }
    if (message.method === "solve" && this._solveResolver) {
      queueMicrotask(() => this._solveResolver!(message.id, post, fireProgress));
    }
  }

  terminate(): void {
    this.posted.push({ method: "terminate" });
  }
}

/** Simulate a worker error event without the real DOM ErrorEvent class. */
function fakeErrorEvent(message: string): ErrorEvent {
  return { message } as ErrorEvent;
}

function makeRequest(overrides?: Partial<Parameters<SolverClient["solve"]>[0]>) {
  return {
    hand_id: "hand-1",
    street: "flop" as const,
    mode: "quick" as const,
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
    ...overrides,
  };
}

describe("SolverClient", () => {
  let worker: FakeWorker;

  beforeEach(() => {
    worker = new FakeWorker();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── basic RPC ────────────────────────────────────────────

  it("keeps progress events separate from final resolution", async () => {
    worker.setSolveResolver((id, post, fireProgress) => {
      fireProgress({ iterations: 10, exploitability_bb: 1.5, finished: false });
      post({
        id,
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
      });
    });

    const client = new SolverClient(worker);
    const progressEvents: number[] = [];

    const result = await client.solve(makeRequest(), (progress) =>
      progressEvents.push(progress.iterations),
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

  // ── ping ─────────────────────────────────────────────────

  it("ping returns 'pong'", async () => {
    const client = new SolverClient(worker);
    const result = await client.ping();
    expect(result).toBe("pong");
  });

  it("ping rejects when worker is dead", async () => {
    const client = new SolverClient(worker);
    // Simulate crash.
    worker.onerror?.(fakeErrorEvent("boom"));
    await expect(client.ping()).rejects.toMatchObject({
      error_class: "worker_crashed",
    });
  });

  // ── crash detection ──────────────────────────────────────

  it("marks worker as dead and rejects pending requests on onerror", async () => {
    worker.setSolveResolver(() => {
      // Never resolve — expect the error handler to reject.
    });

    const client = new SolverClient(worker);

    const solvePromise = client.solve(makeRequest());
    expect(client.isDead).toBe(false);

    // Simulate worker crash.
    worker.onerror?.(fakeErrorEvent("Worker died"));

    await expect(solvePromise).rejects.toMatchObject({
      error_class: "worker_crashed",
    });
    expect(client.isDead).toBe(true);
  });

  it("marks worker as dead and rejects pending requests on onmessageerror", async () => {
    worker.setSolveResolver(() => {
      // Never resolve.
    });

    const client = new SolverClient(worker);
    const solvePromise = client.solve(makeRequest());

    worker.onmessageerror?.(new MessageEvent("messageerror"));

    await expect(solvePromise).rejects.toMatchObject({
      error_class: "worker_crashed",
    });
    expect(client.isDead).toBe(true);
  });

  // ── heartbeat / timeout ──────────────────────────────────

  it("quick mode rejects after 30s total timeout", async () => {
    vi.useFakeTimers();
    worker.syncPing = true; // so ping resolves via setTimeout(0)

    worker.setSolveResolver(() => {
      // Never resolve — timeout should fire.
    });

    const client = new SolverClient(worker);
    const solvePromise = client.solve(makeRequest({ mode: "quick" }));

    // Advance past the ping microtask.
    await vi.advanceTimersByTimeAsync(0);
    // Advance past 30s total timeout — fires the setTimeout in request().
    await vi.advanceTimersByTimeAsync(31_000);

    await expect(solvePromise).rejects.toMatchObject({
      error_class: "timeout",
    });
    expect(client.isDead).toBe(true);
    expect(worker.posted.some((p) => (p as { method: string }).method === "terminate")).toBe(true);
  });

  it("full mode heartbeat fires before total timeout (no progress)", async () => {
    vi.useFakeTimers();
    worker.syncPing = true;

    worker.setSolveResolver(() => {
      // Never resolve and never send progress → heartbeat at 15s.
    });

    const client = new SolverClient(worker);
    const solvePromise = client.solve(makeRequest({ mode: "full" }));

    await vi.advanceTimersByTimeAsync(0); // ping
    // Advance past 15s heartbeat, but not 180s total.
    await vi.advanceTimersByTimeAsync(16_000);

    await expect(solvePromise).rejects.toMatchObject({
      error_class: "timeout",
    });
    expect(client.isDead).toBe(true);
  });

  it("full mode total timeout fires if heartbeat stays alive", async () => {
    vi.useFakeTimers();
    worker.syncPing = true;

    worker.setSolveResolver((_id, _post, fireProgress) => {
      // Fire progress every 10s — keeps heartbeat alive past 15s.
      fireProgress({ iterations: 5, exploitability_bb: 2, finished: false });
      setTimeout(() => {
        fireProgress({ iterations: 10, exploitability_bb: 1.8, finished: false });
      }, 10_000);
      setTimeout(() => {
        fireProgress({ iterations: 15, exploitability_bb: 1.5, finished: false });
      }, 20_000);
      // Total timeout at 180s should still fire.
    });

    const client = new SolverClient(worker);
    const solvePromise = client.solve(makeRequest({ mode: "full" }));

    // Advance past ping.
    await vi.advanceTimersByTimeAsync(0);

    // Advance 12s — first progress at 10s fired, heartbeat reset.
    // Heartbeat timer is now at ~2s (reset at 10s, now at 12s).
    await vi.advanceTimersByTimeAsync(12_000);

    // Advance to 22s — second progress at 20s fires and resets heartbeat.
    await vi.advanceTimersByTimeAsync(10_000);
    // Heartbeat now at ~2s from 20s.

    // Advance to 180s total — should fire total timeout.
    await vi.advanceTimersByTimeAsync(180_000 - 22_000);

    await expect(solvePromise).rejects.toMatchObject({
      error_class: "timeout",
    });
    expect(client.isDead).toBe(true);
  });

  // ── terminate ────────────────────────────────────────────

  it("terminate rejects pending requests with worker_crashed and calls worker.terminate", () => {
    worker.setSolveResolver(() => {
      // Never resolve.
    });

    const client = new SolverClient(worker);
    const promise = client.solve(makeRequest());

    client.terminate();

    expect(worker.posted.some((p) => (p as { method: string }).method === "terminate")).toBe(
      true,
    );
    return expect(promise).rejects.toMatchObject({ error_class: "worker_crashed" });
  });

  // ── cancel ───────────────────────────────────────────────

  it("cancel rejects when worker is dead", async () => {
    const client = new SolverClient(worker);
    worker.onerror?.(fakeErrorEvent("dead"));

    await expect(client.cancel()).rejects.toMatchObject({ error_class: "worker_crashed" });
  });

  // ── factory ──────────────────────────────────────────────

  it("independent clients do not share state", () => {
    const a = new SolverClient(new FakeWorker());
    const b = new SolverClient(new FakeWorker());
    expect(a).not.toBe(b);
    expect(a.isDead).toBe(false);
    expect(b.isDead).toBe(false);
  });

  // ── error_class propagation ──────────────────────────────

  it("propagates error_class from worker responses", async () => {
    worker.setSolveResolver((id, post) => {
      post({ id, error: "game tree too large", error_class: "Unreachable" });
    });

    const client = new SolverClient(worker);
    await expect(client.solve(makeRequest())).rejects.toMatchObject({
      message: "game tree too large",
      error_class: "Unreachable",
    });
  });
});