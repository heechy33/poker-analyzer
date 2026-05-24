/// <reference lib="webworker" />

import type { SolveProgress, SolveRequest, SolveResult, SolverOutput } from "./types";

type RpcMethod = "solve" | "cancel" | "ping";

interface RpcRequest {
  id: number;
  method: RpcMethod;
  params?: unknown;
}

interface WasmModule {
  default(moduleOrPath?: unknown): Promise<unknown>;
  init_game(scenarioJson: string): number;
  solve_step(handle: number, maxItersThisStep: number): string;
  export_strategy(handle: number, historyPathJson: string): string;
  free_game(handle: number): void;
  last_error(): string;
}

const ctx: DedicatedWorkerGlobalScope = self as DedicatedWorkerGlobalScope;

let wasmModulePromise: Promise<WasmModule> | null = null;
let cancelRequested = false;
const wasmUrl = "/wasm/solver_wasm.js";

function readableLastError(wasm: WasmModule, fallback: string): string {
  const detail = wasm.last_error();
  return detail ? `${fallback}: ${detail}` : fallback;
}

async function loadWasm(): Promise<WasmModule> {
  if (!wasmModulePromise) {
    wasmModulePromise = (import(
      /* webpackIgnore: true */
      wasmUrl
    ) as Promise<WasmModule>)
      .then(async (wasm: WasmModule) => {
        await wasm.default();
        return wasm;
      })
      .catch((error: unknown) => {
        wasmModulePromise = null;
        const reason = error instanceof Error ? error.message : String(error);
        throw new Error(
          `Solver WASM bundle is unavailable. Run "cd frontend && npm run build:wasm" and ensure /wasm/solver_wasm.js plus solver_wasm_bg.wasm exist. ${reason}`,
        );
      });
  }
  return wasmModulePromise;
}

function parseJsonObject<T>(json: string, wasm: WasmModule, context: string): T {
  const value = JSON.parse(json) as unknown;
  if (!value || typeof value !== "object" || Object.keys(value).length === 0) {
    throw new Error(readableLastError(wasm, `${context} returned an empty object`));
  }
  return value as T;
}

function modeOverrides(mode: SolveRequest["mode"]) {
  return mode === "quick"
    ? { max_iterations: 50, target_exploitability_bb: 1.0 }
    : { max_iterations: 200, target_exploitability_bb: 0.5 };
}

function heroPositionFrom(request: SolveRequest): string | undefined {
  const metadataHero = request.metadata?.hero_position;
  if (typeof metadataHero === "string" && metadataHero.length > 0) {
    return metadataHero;
  }
  return request.scenario.hero_position;
}

function toUiProgress(raw: {
  iterations_done: number;
  exploitability_bb: number;
  finished: boolean;
}): SolveProgress {
  return {
    iterations: raw.iterations_done,
    exploitability_bb: raw.exploitability_bb,
    finished: raw.finished,
  };
}

async function solve(request: SolveRequest, id: number): Promise<SolveResult> {
  const wasm = await loadWasm();
  cancelRequested = false;

  const initPayload = {
    ...request.scenario,
    hero_position: heroPositionFrom(request),
    ...modeOverrides(request.mode),
  };
  const handle = wasm.init_game(JSON.stringify(initPayload));
  if (!handle) {
    throw new Error(readableLastError(wasm, "init_game failed"));
  }

  let finalProgress: SolveProgress = {
    iterations: 0,
    exploitability_bb: Number.POSITIVE_INFINITY,
    finished: false,
  };

  try {
    while (!finalProgress.finished) {
      if (cancelRequested) {
        throw new Error("Solver run cancelled");
      }

      const rawProgress = parseJsonObject<{
        iterations_done: number;
        exploitability_bb: number;
        finished: boolean;
      }>(wasm.solve_step(handle, 10), wasm, "solve_step");
      finalProgress = toUiProgress(rawProgress);
      ctx.postMessage({ type: "progress", id, data: finalProgress });

      if (cancelRequested) {
        throw new Error("Solver run cancelled");
      }

      await new Promise((resolve) => setTimeout(resolve, 0));
    }

    // v1 exports the root node only. Future UI work can pass a concrete
    // postflop history path once hand-action-to-tree mapping is available.
    const output = parseJsonObject<SolverOutput>(
      wasm.export_strategy(handle, ""),
      wasm,
      "export_strategy",
    );
    return { output, progress: finalProgress };
  } finally {
    wasm.free_game(handle);
  }
}

async function handleRequest(message: RpcRequest): Promise<void> {
  const { id, method, params } = message;
  try {
    if (method === "ping") {
      ctx.postMessage({ id, result: "pong" });
      return;
    }
    if (method === "cancel") {
      cancelRequested = true;
      ctx.postMessage({ id, result: undefined });
      return;
    }
    if (method === "solve") {
      const result = await solve(params as SolveRequest, id);
      ctx.postMessage({ id, result });
      return;
    }
    throw new Error(`Unknown solver worker method: ${method satisfies never}`);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : String(error);
    ctx.postMessage({ id, error: messageText });
  }
}

ctx.addEventListener("message", (event: MessageEvent<RpcRequest>) => {
  void handleRequest(event.data);
});
