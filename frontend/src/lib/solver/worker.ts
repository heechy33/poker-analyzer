/// <reference lib="webworker" />

import type { SolveProgress, SolveRequest, SolveResult, SolverOutput } from "./types";
import {
  buildDecisionNodeHistory,
  mapVillainActionToSolverLabel,
  type ActionBeforeHero,
} from "./hand-context";

type RpcMethod = "solve" | "cancel" | "ping";

interface RpcRequest {
  id: number;
  method: RpcMethod;
  params?: unknown;
}

/// Structured response from WASM exports that return a result/error envelope.
interface WasmEnvelope {
  ok: boolean;
  handle?: number;
  error_class?: string;
  message?: string;
}

interface WasmModule {
  default(moduleOrPath?: unknown): Promise<unknown>;
  init_game(scenarioJson: string): string;   // now returns JSON string of WasmEnvelope
  solve_step(handle: number, maxItersThisStep: number): string; // JSON string
  export_strategy(handle: number, historyPathJson: string): string; // JSON string
  get_actions_at(handle: number, historyPathJson: string): string; // JSON string
  preflight(envelopeJson: string): string;   // JSON string of WasmEnvelope
  free_game(handle: number): void;
  last_error(): string;
}

/// Response from get_actions_at.
interface ActionsAtResponse {
  ok: boolean;
  actions: string[];
  current_player: number | null;
  pot_chips: number;
  is_terminal: boolean;
  is_chance: boolean;
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

/// Parse a WASM export result that returns a `WasmEnvelope` JSON string.
/// On `ok: true` returns the parsed envelope.
/// On `ok: false` throws a structured error with `error_class`.
function unwrapWasmEnvelope(raw: string, wasm: WasmModule, context: string): WasmEnvelope {
  let envelope: WasmEnvelope;
  try {
    envelope = JSON.parse(raw) as WasmEnvelope;
  } catch {
    throw new Error(`${context}: WASM returned unparseable response: ${raw}`);
  }

  if (!envelope.ok) {
    const detail = readableLastError(wasm, context);
    const err = new Error(envelope.message ?? detail) as Error & {
      error_class?: string;
    };
    err.error_class = envelope.error_class ?? "unknown";
    throw err;
  }

  return envelope;
}

/// Parse a JSON object from WASM, handling the structured error fallback.
function parseWasmJson<T>(raw: string, wasm: WasmModule, context: string): T {
  // First check if this is a WasmEnvelope error.
  let envelope: WasmEnvelope | null = null;
  try {
    envelope = JSON.parse(raw) as WasmEnvelope;
  } catch {
    // Not JSON — fall through to throw.
  }

  if (envelope && typeof envelope.ok === "boolean" && !envelope.ok) {
    const detail = readableLastError(wasm, context);
    const err = new Error(envelope.message ?? detail) as Error & {
      error_class?: string;
    };
    err.error_class = envelope.error_class ?? "unknown";
    throw err;
  }

  // Not a structured error — parse as the expected type.
  const value = JSON.parse(raw) as unknown;
  if (!value || typeof value !== "object" || Object.keys(value).length === 0) {
    // If the envelope was ok:true but empty, treat as unexpected.
    if (envelope?.ok) {
      throw new Error(readableLastError(wasm, `${context} returned empty object on success`));
    }
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

/**
 * Determine whether hero is OOP (player 0) or IP (player 1) based on
 * scenario metadata positions.
 */
function heroIsOop(request: SolveRequest): boolean {
  const meta = request.metadata ?? {};
  const heroPos = typeof meta.hero_position === "string" ? meta.hero_position : null;
  const oopPos  = typeof meta.oop_position  === "string" ? meta.oop_position  : null;
  if (!heroPos || !oopPos) return false;
  return heroPos === oopPos;
}

/**
 * Fetch the available action names at an already-navigated node without
 * computing the full strategy.  Returns null if the WASM call fails or the
 * game module doesn't expose `get_actions_at` yet (graceful fallback).
 */
function fetchActionsAt(
  wasm: WasmModule,
  handle: number,
  history: number[],
): ActionsAtResponse | null {
  if (typeof wasm.get_actions_at !== "function") return null;
  try {
    const raw = wasm.get_actions_at(handle, JSON.stringify(history));
    const parsed = JSON.parse(raw) as ActionsAtResponse;
    if (!parsed.ok) return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Navigate to the hero's exact decision node and export the strategy there.
 *
 * Strategy:
 * 1. Export root to get action labels at the opening node.
 * 2. Use `buildDecisionNodeHistory` (with intermediate `get_actions_at` calls
 *    for depth > 1) to build the numeric history path.
 * 3. Export the hero-node strategy.  If history reconstruction fails at any
 *    step, fall back to the deepest navigable node or root.
 */
async function exportAtHeroNode(
  wasm: WasmModule,
  handle: number,
  request: SolveRequest,
  finalProgress: SolveProgress,
): Promise<SolveResult> {
  const meta = request.metadata ?? {};
  const actionsBeforeHero = Array.isArray(meta.actions_before_hero)
    ? (meta.actions_before_hero as ActionBeforeHero[])
    : [];

  // Always export root first — gives us the opening-node action labels.
  const rootOutput = parseWasmJson<SolverOutput>(
    wasm.export_strategy(handle, ""),
    wasm,
    "export_strategy(root)",
  );

  // If hero is OOP they open the action — root IS the decision node.
  const isOop = heroIsOop(request);
  if (isOop || actionsBeforeHero.length === 0) {
    return {
      output: rootOutput,
      progress: finalProgress,
      historyPath: [],
      historyIncomplete: false,
      nodeDepth: 0,
    };
  }

  // Hero is IP (or unknown) — navigate past pre-hero actions.
  const rootActions = rootOutput.actions as string[];

  // For depth > 1 we need the action labels at each intermediate node.
  // Fetch them lazily via get_actions_at.
  const intermediateActionLists: string[][] = [];
  const partialHistory: number[] = [];

  for (let step = 0; step < actionsBeforeHero.length; step++) {
    const entry = actionsBeforeHero[step];
    const currentActions = step === 0 ? rootActions : intermediateActionLists[step - 1];

    const label = mapVillainActionToSolverLabel(entry, currentActions);
    if (label === null) break;

    const idx = currentActions.indexOf(label);
    if (idx === -1) break;

    partialHistory.push(idx);

    // Pre-fetch actions at the NEXT node only if we still have more steps.
    if (step < actionsBeforeHero.length - 1) {
      const nextNode = fetchActionsAt(wasm, handle, partialHistory);
      if (!nextNode || nextNode.is_terminal || nextNode.is_chance) break;
      intermediateActionLists.push(nextNode.actions);
    }
  }

  const incomplete = partialHistory.length < actionsBeforeHero.length;

  // Build the context object (for description and depth).
  const nodeCtx = buildDecisionNodeHistory(
    actionsBeforeHero.slice(0, partialHistory.length),
    rootActions,
    intermediateActionLists,
  );

  if (partialHistory.length === 0) {
    // Could not navigate at all — fall back to root.
    return {
      output: rootOutput,
      progress: finalProgress,
      historyPath: [],
      historyIncomplete: true,
      nodeDepth: 0,
    };
  }

  // Export the hero-node strategy.
  const heroHistoryJson = JSON.stringify(partialHistory);
  let heroOutput: SolverOutput;
  try {
    heroOutput = parseWasmJson<SolverOutput>(
      wasm.export_strategy(handle, heroHistoryJson),
      wasm,
      `export_strategy(${heroHistoryJson})`,
    );
  } catch {
    // If the deep export fails (e.g., the node is terminal/chance), fall back.
    return {
      output: rootOutput,
      progress: finalProgress,
      historyPath: [],
      historyIncomplete: true,
      nodeDepth: 0,
    };
  }

  return {
    output: heroOutput,
    progress: finalProgress,
    historyPath: partialHistory,
    historyIncomplete: incomplete,
    nodeDepth: nodeCtx.depth,
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
  const initResult = unwrapWasmEnvelope(
    wasm.init_game(JSON.stringify(initPayload)),
    wasm,
    "init_game",
  );
  const handle = initResult.handle;
  if (!handle) {
    throw new Error(readableLastError(wasm, "init_game returned ok but no handle"));
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

      const rawProgress = parseWasmJson<{
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

    return await exportAtHeroNode(wasm, handle, request, finalProgress);
  } finally {
    wasm.free_game(handle);
  }
}

async function handleRequest(message: RpcRequest): Promise<void> {
  const { id, method } = message;
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
      const result = await solve(message.params as SolveRequest, id);
      ctx.postMessage({ id, result });
      return;
    }
    throw new Error(`Unknown solver worker method: ${method satisfies never}`);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : String(error);
    const errorClass = (error as { error_class?: string }).error_class;
    ctx.postMessage({
      id,
      error: messageText,
      ...(errorClass ? { error_class: errorClass } : {}),
    });
  }
}

ctx.addEventListener("message", (event: MessageEvent<RpcRequest>) => {
  void handleRequest(event.data);
});
