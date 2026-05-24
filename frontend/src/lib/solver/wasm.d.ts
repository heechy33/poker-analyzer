declare module "/wasm/solver_wasm.js" {
  export default function init(moduleOrPath?: unknown): Promise<unknown>;
  export function init_game(scenarioJson: string): number;
  export function solve_step(handle: number, maxItersThisStep: number): string;
  export function export_strategy(handle: number, historyPathJson: string): string;
  export function free_game(handle: number): void;
  export function last_error(): string;
}

