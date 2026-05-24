import type { SolveProgress, SolveRequest, SolveResult } from "./types";

/**
 * Manual smoke checklist:
 * 1. Run `npm run build:wasm`, then `npm run dev`.
 * 2. Call `getSolverClient().ping()` from a browser-only path and expect "pong".
 * 3. Start a quick solve and confirm progress events arrive at 10, 20, ...
 * 4. Call `cancel()` during a solve and confirm it rejects within one chunk.
 * 5. Rename `frontend/public/wasm` temporarily and confirm the error mentions `npm run build:wasm`.
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
  type?: "progress";
  data?: SolveProgress;
}

export interface WorkerLike {
  onmessage: ((event: MessageEvent<RpcResponse>) => void) | null;
  postMessage(message: RpcMessage): void;
  terminate(): void;
}

interface PendingRequest<T> {
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
  onProgress?: (progress: SolveProgress) => void;
  timeout: ReturnType<typeof setTimeout>;
}

const DEFAULT_TIMEOUT_MS = 120_000;

export class SolverClient {
  private worker: WorkerLike;
  private nextId = 1;
  private pending = new Map<number, PendingRequest<unknown>>();

  constructor(worker?: WorkerLike) {
    this.worker = worker ?? this.createWorker();
    this.worker.onmessage = (event) => this.handleMessage(event.data);
  }

  ping(): Promise<"pong"> {
    return this.request<"pong">("ping");
  }

  solve(
    request: SolveRequest,
    onProgress?: (progress: SolveProgress) => void,
  ): Promise<SolveResult> {
    return this.request<SolveResult>("solve", request, onProgress);
  }

  cancel(): Promise<void> {
    return this.request<void>("cancel");
  }

  terminate(): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timeout);
      pending.reject(new Error("Solver worker terminated"));
      this.pending.delete(id);
    }
    this.worker.terminate();
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
    timeoutMs = DEFAULT_TIMEOUT_MS,
  ): Promise<T> {
    const id = this.nextId;
    this.nextId += 1;

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Solver worker ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);

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
      pending.reject(new Error(message.error));
      return;
    }
    pending.resolve(message.result);
  }
}

let singleton: SolverClient | null = null;

export function getSolverClient(): SolverClient {
  if (!singleton) {
    singleton = new SolverClient();
  }
  return singleton;
}

export function terminateSolverClient(): void {
  singleton?.terminate();
  singleton = null;
}

