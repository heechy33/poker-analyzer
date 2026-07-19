import type { SolveMode, SolveProgress, SolveRequest, SolveResult, SolverOutput } from "./types";

/**
 * Manual smoke checklist:
 * 1. Run `npm run build:wasm`, then `npm run dev`.
 * 2. Call `createSolverClient().ping()` from a browser-only path and expect "pong".
 * 3. Start a quick solve and confirm progress events arrive at 10, 20, ...
 * 4. Call `cancel()` during a solve and confirm it rejects within one chunk.
 * 5. Rename `frontend/public/wasm` temporarily and confirm the error mentions `npm run build:wasm`.
 * 6. Kill the worker mid-solve (e.g. close the tab's worker in devtools) and confirm recovery with "worker_crashed".
 */

type RpcMethod = "solve" | "cancel" | "ping";

interface RpcMessage {
  id: number;
  method: RpcMethod;
  params?: unknown;
}

interface RpcResponse<T = unknown> {
  id?: number;
  result?: T;
  error?: string;
  error_class?: string;
  type?: "progress";
  data?: SolveProgress;
}

export interface WorkerLike {
  onmessage: ((event: MessageEvent<RpcResponse>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent) => void) | null;
  postMessage(message: RpcMessage): void;
  terminate(): void;
}

interface PendingRequest<T> {
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
  onProgress?: (progress: SolveProgress) => void;
  timeout: ReturnType<typeof setTimeout>;
}

/**
 * Timeout config per solve mode.
 * totalMs: overall deadline for the solve.
 * heartbeatMs: if no progress arrives within this window, we kill the worker
 *               and reject with error_class "timeout".
 */
const TIMEOUTS: Record<SolveMode, { totalMs: number; heartbeatMs: number }> = {
  quick: { totalMs: 30_000, heartbeatMs: 15_000 },
  full: { totalMs: 180_000, heartbeatMs: 15_000 },
};

export class SolverClient {
  private worker: WorkerLike;
  private readonly workerFactory: () => WorkerLike;
  private nextId = 1;
  private pending = new Map<number, PendingRequest<unknown>>();
  private dead = false;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatMs = 15_000;

  constructor(worker?: WorkerLike, workerFactory?: () => WorkerLike) {
    this.workerFactory = workerFactory ?? (() => this.createWorker());
    this.worker = worker ?? this.workerFactory();
    this.worker.onmessage = (event) => this.handleMessage(event.data);

    this.worker.onerror = (event: ErrorEvent) => {
      this.markDead();
      this.rejectAllPending(
        "worker_crashed",
        `Solver worker crashed: ${event.message || "unknown error"}`,
      );
    };

    this.worker.onmessageerror = (_event: MessageEvent) => {
      this.markDead();
      this.rejectAllPending(
        "worker_crashed",
        "Solver worker received a message it cannot deserialize",
      );
    };
  }

  get isDead(): boolean {
    return this.dead;
  }

  /**
   * Pre-flight health check. If the worker is dead or unresponsive,
   * throws so the caller can recreate.
   */
  ping(): Promise<"pong"> {
    if (this.dead) {
      return Promise.reject(
        Object.assign(new Error("Solver worker is dead"), { error_class: "worker_crashed" }),
      );
    }
    return this.request<"pong">("ping");
  }

  /**
   * Solve with mode-appropriate timeouts and heartbeat monitoring.
   * Calls ping() first to verify the worker is alive.
   *
   * Legacy preview behavior: if a "full" solve times out, the client spawns a
   * fresh Worker and retries in "quick" mode. Neither result is gradeable until
   * a rebuilt caller enforces the verified solve contract.
   */
  async solve(
    request: SolveRequest,
    onProgress?: (progress: SolveProgress) => void,
  ): Promise<SolveResult> {
    const mode: SolveMode = request.mode;
    const { totalMs, heartbeatMs } = TIMEOUTS[mode];
    this.heartbeatMs = heartbeatMs;

    // Pre-flight health check (quick — the request timeout below will catch hangs).
    await this.ping();

    // Reset the heartbeat timer before starting.
    this.resetHeartbeat();

    try {
      return await this.request<SolveResult>(
        "solve",
        request,
        (progress) => {
          this.resetHeartbeat();
          onProgress?.(progress);
        },
        totalMs,
      );
    } catch (err: unknown) {
      // Legacy preview-only auto-downgrade from full to quick on timeout.
      // Only downgrade once (don't retry quick timeouts).
      const typedErr = err as Error & { error_class?: string };
      if (mode === "full" && typedErr?.error_class === "timeout") {
        // Worker was terminated by the timeout path; spawn a fresh one.
        this.respawnWorker();

        // Attempt quick solve with the same envelope.
        const downgradedRequest: SolveRequest = { ...request, mode: "quick" };
        const { totalMs: quickMs, heartbeatMs: quickHb } = TIMEOUTS["quick"];
        this.heartbeatMs = quickHb;
        this.resetHeartbeat();

        try {
          const result = await this.request<SolveResult>(
            "solve",
            downgradedRequest,
            (progress) => {
              this.resetHeartbeat();
              onProgress?.(progress);
            },
            quickMs,
          );
          result.downgradedToQuick = true;
          return result;
        } catch (quickErr: unknown) {
          const quickTyped = quickErr as Error & { error_class?: string };
          const msg = quickTyped?.error_class === "timeout"
            ? `Full solve timed out (${TIMEOUTS.full.totalMs / 1000}s) — quick approximation also timed out`
            : `Full solve timed out — quick approximation failed: ${quickTyped?.message ?? quickErr}`;
          throw Object.assign(new Error(msg), { error_class: "downgrade_failed" });
        }
      }
      throw err;
    }
  }

  /**
   * Respawn the underlying Worker after it was terminated (e.g. by a timeout).
   * Clears dead flag and sets up fresh event handlers.
   */
  private respawnWorker(): void {
    this.worker = this.workerFactory();
    this.dead = false;
    this.worker.onmessage = (event) => this.handleMessage(event.data);
    this.worker.onerror = (event: ErrorEvent) => {
      this.markDead();
      this.rejectAllPending(
        "worker_crashed",
        `Solver worker crashed: ${event.message || "unknown error"}`,
      );
    };
    this.worker.onmessageerror = (_event: MessageEvent) => {
      this.markDead();
      this.rejectAllPending(
        "worker_crashed",
        "Solver worker received a message it cannot deserialize",
      );
    };
  }

  cancel(): Promise<void> {
    if (this.dead) {
      return Promise.reject(
        Object.assign(new Error("Solver worker is dead"), { error_class: "worker_crashed" }),
      );
    }
    return this.request<void>("cancel");
  }

  terminate(): void {
    this.clearHeartbeat();
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timeout);
      pending.reject(
        Object.assign(new Error("Solver worker terminated"), { error_class: "worker_crashed" }),
      );
      this.pending.delete(id);
    }
    this.dead = true;
    try {
      this.worker.terminate();
    } catch {
      // Worker may already be dead — ignore.
    }
  }

  // ── private ──────────────────────────────────────────────

  private markDead(): void {
    this.dead = true;
    this.clearHeartbeat();
  }

  private rejectAllPending(errorClass: string, message: string): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timeout);
      const err = new Error(message) as Error & { error_class?: string };
      err.error_class = errorClass;
      pending.reject(err);
      this.pending.delete(id);
    }
  }

  private resetHeartbeat(): void {
    this.clearHeartbeat();
    this.heartbeatTimer = setTimeout(() => {
      this.markDead();
      this.rejectAllPending(
        "timeout",
        `Solver heartbeat lost — no progress for ${this.heartbeatMs}ms`,
      );
      try {
        this.worker.terminate();
      } catch {
        // ignore
      }
    }, this.heartbeatMs);
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private createWorker(): WorkerLike {
    if (typeof Worker === "undefined") {
      throw new Error("Solver workers are only available in the browser");
    }
    return new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  }

  private request<T>(
    method: RpcMethod,
    params?: unknown,
    onProgress?: (progress: SolveProgress) => void,
    timeoutMs?: number,
  ): Promise<T> {
    const id = this.nextId;
    this.nextId += 1;

    const effectiveTimeout = timeoutMs ?? 120_000;

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        this.markDead();
        try {
          this.worker.terminate();
        } catch {
          // ignore
        }
        const err = new Error(
          `Solver worker ${method} timed out after ${effectiveTimeout}ms`,
        ) as Error & { error_class?: string };
        err.error_class = "timeout";
        reject(err);
      }, effectiveTimeout);

      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
        onProgress,
        timeout,
      });

      this.worker.postMessage({ id, method, params });
    });
  }

  private handleMessage(message: RpcResponse): void {
    if (message.id === undefined) {
      return;
    }

    const pending = this.pending.get(message.id);
    if (!pending) {
      return;
    }

    if (message.type === "progress") {
      if (message.data) {
        pending.onProgress?.(message.data);
      }
      return;
    }

    clearTimeout(pending.timeout);
    this.pending.delete(message.id);

    if (message.error) {
      const err = new Error(message.error) as Error & { error_class?: string };
      if (message.error_class) {
        err.error_class = message.error_class;
      }
      pending.reject(err);
      return;
    }
    pending.resolve(message.result);
  }
}

/**
 * Factory: creates a fresh Worker per solve so crashes don't cascade.
 *
 * Usage (per-solve lifecycle):
 *   const client = createSolverClient();
 *   try {
 *     const result = await client.solve(request, onProgress);
 *   } finally {
 *     client.terminate();
 *   }
 *
 * The old singleton `getSolverClient()` is removed — callers should use
 * `createSolverClient()` and manage the lifecycle per solve.
 */
export function createSolverClient(): SolverClient {
  return new SolverClient();
}
