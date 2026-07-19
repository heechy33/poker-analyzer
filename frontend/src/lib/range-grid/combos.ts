import type { SolverOutput } from "@/lib/solver/types";

export const RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"] as const;

export type Rank = (typeof RANKS)[number];

export interface CellAction {
  action: string;
  probability: number;
  ev?: number;
  aggregateFrequency?: number;
}

export interface CellStrategy {
  label: string;
  comboCount: number;
  sourceComboCount: number;
  isBlocked: boolean;
  actions: CellAction[];
}

const SUITS = ["c", "d", "h", "s"] as const;

function rankIndex(rank: string): number {
  return RANKS.indexOf(rank.toUpperCase() as Rank);
}

/**
 * Standard matrix label for grid position.
 * Diagonal = pairs, upper triangle = suited, lower = offsuit.
 */
export function cellLabelForPosition(row: number, col: number): string {
  const r1 = RANKS[row];
  const r2 = RANKS[col];
  if (row === col) return `${r1}${r2}`;
  if (row < col) return `${r1}${r2}s`;
  return `${r2}${r1}o`;
}

/** Map a concrete combo key like "AhKh" / "AsKd" to a matrix cell label. */
export function comboToCell(combo: string): string {
  const compact = combo.replace(/\s+/g, "");
  if (compact.length < 4) return "";

  const r1 = compact[0].toUpperCase();
  const s1 = compact[1].toLowerCase();
  const r2 = compact[2].toUpperCase();
  const s2 = compact[3].toLowerCase();

  const i1 = rankIndex(r1);
  const i2 = rankIndex(r2);
  if (i1 < 0 || i2 < 0) return "";

  if (r1 === r2) return `${r1}${r2}`;

  const high = i1 <= i2 ? r1 : r2;
  const low = i1 <= i2 ? r2 : r1;
  const suited = s1 === s2;
  return `${high}${low}${suited ? "s" : "o"}`;
}

function expandCellCombos(label: string): string[] {
  if (label.length === 2) {
    // Pair: AA → AcAd, AcAh, AcAs, AdAh, AdAs, AhAs
    const rank = label[0];
    const combos: string[] = [];
    for (let i = 0; i < SUITS.length; i++) {
      for (let j = i + 1; j < SUITS.length; j++) {
        const a = `${rank}${SUITS[i]}`;
        const b = `${rank}${SUITS[j]}`;
        combos.push(a < b ? `${a}${b}` : `${b}${a}`);
      }
    }
    return combos;
  }

  if (label.length !== 3) return [];

  const high = label[0];
  const low = label[1];
  const suited = label[2] === "s";
  const combos: string[] = [];

  if (suited) {
    for (const suit of SUITS) {
      const a = `${high}${suit}`;
      const b = `${low}${suit}`;
      combos.push(`${a}${b}`);
    }
  } else {
    for (const s1 of SUITS) {
      for (const s2 of SUITS) {
        if (s1 === s2) continue;
        const a = `${high}${s1}`;
        const b = `${low}${s2}`;
        combos.push(`${a}${b}`);
      }
    }
  }

  return combos;
}

function normalizeComboKey(combo: string): string {
  const compact = combo.replace(/\s+/g, "");
  if (compact.length < 4) return compact;
  const c1 = compact.slice(0, 2);
  const c2 = compact.slice(2, 4);
  // Match solver export ordering: higher rank first, then suit order within rank.
  const r1 = rankIndex(c1[0]);
  const r2 = rankIndex(c2[0]);
  if (r1 < r2) return `${c1}${c2}`;
  if (r2 < r1) return `${c2}${c1}`;
  return c1[1] <= c2[1] ? `${c1}${c2}` : `${c2}${c1}`;
}

function boardBlocksCombo(combo: string, board: readonly string[]): boolean {
  if (!board.length) return false;
  const boardSet = new Set(board.map((c) => c.trim().toLowerCase()));
  const c1 = combo.slice(0, 2).toLowerCase();
  const c2 = combo.slice(2, 4).toLowerCase();
  return boardSet.has(c1) || boardSet.has(c2);
}

/**
 * Average strategy (and optional EV) across all concrete combos in a matrix cell.
 */
export function aggregateCombosToCell(
  output: SolverOutput,
  label: string,
  options: { board?: readonly string[] } = {},
): CellStrategy {
  const board = options.board ?? [];
  const candidates = expandCellCombos(label);
  const sourceComboCount = candidates.length;

  const strategyKeys = new Map<string, string>();
  for (const key of Object.keys(output.combo_strategy)) {
    strategyKeys.set(normalizeComboKey(key), key);
  }

  let comboCount = 0;
  let blockedCount = 0;
  const freqSum: Record<string, number> = {};
  const evSum: Record<string, number> = {};
  const evWeight: Record<string, number> = {};

  for (const action of output.actions) {
    freqSum[action] = 0;
    evSum[action] = 0;
    evWeight[action] = 0;
  }

  for (const combo of candidates) {
    if (boardBlocksCombo(combo, board)) {
      blockedCount += 1;
      continue;
    }

    const key = strategyKeys.get(normalizeComboKey(combo));
    if (!key) continue;

    const freqs = output.combo_strategy[key];
    if (!freqs) continue;

    comboCount += 1;
    for (const action of output.actions) {
      const p = freqs[action] ?? 0;
      freqSum[action] += p;
      const ev = output.combo_ev?.[key]?.[action];
      if (typeof ev === "number") {
        evSum[action] += ev;
        evWeight[action] += 1;
      }
    }
  }

  const isBlocked = sourceComboCount > 0 && blockedCount === sourceComboCount;
  const divisor = comboCount || 1;

  const actions = output.actions.map((action) => {
    const probability = comboCount > 0 ? freqSum[action] / divisor : 0;
    const item: CellAction = {
      action,
      probability,
      aggregateFrequency: output.aggregate_frequencies?.[action],
    };
    if (evWeight[action] > 0) {
      item.ev = evSum[action] / evWeight[action];
    }
    return item;
  });

  // Normalize tiny float drift so probabilities sum ~1 when we have data.
  const total = actions.reduce((s, a) => s + a.probability, 0);
  if (total > 0 && Math.abs(total - 1) > 1e-6) {
    for (const a of actions) a.probability /= total;
  }

  return {
    label,
    comboCount,
    sourceComboCount,
    isBlocked,
    actions,
  };
}
