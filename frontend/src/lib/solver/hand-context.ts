import type { HandActionOut, Street } from "@/types/api";

// ---------------------------------------------------------------------------
// Card combo helpers
// ---------------------------------------------------------------------------

const RANK_ORDER = new Map(
  ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"].map((rank, index) => [
    rank,
    index,
  ]),
);

function normalizeCard(card: string): string | null {
  const compact = card.trim();
  if (compact.length !== 2) return null;

  const rank = compact[0].toUpperCase();
  const suit = compact[1].toLowerCase();

  if (!RANK_ORDER.has(rank) || !["c", "d", "h", "s"].includes(suit)) {
    return null;
  }

  return `${rank}${suit}`;
}

export function heroCardsToComboKey(cards: readonly string[]): string {
  const normalized = cards.map(normalizeCard).filter((card): card is string => Boolean(card));
  if (normalized.length < 2) return "";

  return normalized
    .slice(0, 2)
    .sort((a, b) => {
      const rankDiff = (RANK_ORDER.get(a[0]) ?? 99) - (RANK_ORDER.get(b[0]) ?? 99);
      return rankDiff || a[1].localeCompare(b[1]);
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Pot-fraction bet label mapping (P2.5)
// ---------------------------------------------------------------------------

/**
 * Parse the pot-percentage numeric value out of a solver action label.
 *
 * - `"bet_33"` → 33
 * - `"raise_250"` → 250
 * - `"allin"` → Infinity  (treat as very large for closest-match)
 * - anything else → NaN
 */
function parseLabelPct(label: string): number {
  if (label === "allin") return Infinity;
  const m = label.match(/^(?:bet|raise)_(\d+)$/);
  if (!m) return Number.NaN;
  return parseInt(m[1], 10);
}

/**
 * Given a bet as a fraction of the pot (e.g. 25 = 25% pot), find the nearest
 * solver action label among `solverActions`.
 *
 * Rules:
 * - Labels like `"bet_33"`, `"raise_250"` are matched by |labelPct − pctOfPot|.
 * - `"allin"` is preferred when the actual bet exceeds the largest label pct by
 *   more than 50 percentage points (e.g. pot is 10 bb, bet is 20 bb = 200%).
 * - Returns `null` if no betting action is available.
 */
export function mapBetFractionToLabel(
  pctOfPot: number,
  solverActions: readonly string[],
): string | null {
  const bettingActions = solverActions.filter(
    (a) => a.startsWith("bet") || a.startsWith("raise") || a === "allin",
  );
  if (bettingActions.length === 0) return null;

  let best: string | null = null;
  let bestDiff = Infinity;
  let maxLabelPct = 0;

  for (const label of bettingActions) {
    if (label === "allin") continue;
    const labelPct = parseLabelPct(label);
    if (Number.isNaN(labelPct)) continue;
    maxLabelPct = Math.max(maxLabelPct, labelPct);
    const diff = Math.abs(labelPct - pctOfPot);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = label;
    }
  }

  // If the actual bet is much larger than the largest tree size, prefer allin.
  const hasAllin = bettingActions.includes("allin");
  if (hasAllin && maxLabelPct > 0 && pctOfPot > maxLabelPct + 50) {
    return "allin";
  }

  return best ?? (hasAllin ? "allin" : null);
}

/**
 * Map a single hand action to its nearest solver action label.
 *
 * @param action - raw `HandActionOut` from the parsed hand history
 * @param solverActions - action labels available at the current solver node
 * @param potAtActionBb - pot size in bb **at the moment this action was made**;
 *   required for accurate bet-fraction matching.  When absent, falls back to
 *   the first available betting action.
 */
function nearestSizeAction(
  action: HandActionOut,
  solverActions: readonly string[],
  potAtActionBb?: number,
): string | null {
  const verb = action.action.toLowerCase();

  if (verb.includes("fold")) return solverActions.find((c) => c === "fold") ?? null;
  if (verb.includes("check")) return solverActions.find((c) => c === "check") ?? null;
  if (verb.includes("call")) return solverActions.find((c) => c === "call") ?? null;

  if (verb.includes("bet") || verb.includes("raise")) {
    const bettingActions = solverActions.filter(
      (c) => c.startsWith("bet") || c.startsWith("raise") || c === "allin",
    );
    if (bettingActions.length === 0) return null;

    // Prefer raise_to for raises (total committed), amount for bets.
    const rawAmount = action.raise_to ?? action.amount;
    const amount = rawAmount !== null && rawAmount !== undefined
      ? Number.parseFloat(rawAmount)
      : Number.NaN;

    if (Number.isFinite(amount) && potAtActionBb !== undefined && potAtActionBb > 0) {
      const pctOfPot = (amount / potAtActionBb) * 100;
      return mapBetFractionToLabel(pctOfPot, solverActions);
    }

    // Fallback when pot info is unavailable: use first available action.
    return bettingActions[0];
  }

  return null;
}

// ---------------------------------------------------------------------------
// Hero action inference
// ---------------------------------------------------------------------------

export function inferHeroActionOnStreet(
  actions: readonly HandActionOut[],
  street: Street,
  solverActions: readonly string[],
  options: {
    heroSeat?: number;
    heroName?: string | null;
    /** Pot size in bb when hero is to act — used for accurate bet-fraction mapping. */
    potAtHeroActionBb?: number;
  } = {},
): string | null {
  const heroActions = actions.filter((action) => {
    if (action.street.toLowerCase() !== street) return false;
    if (options.heroSeat !== undefined && action.seat === options.heroSeat) return true;
    if (options.heroName && action.screen_name === options.heroName) return true;
    return false;
  });

  // Grade the last hero action on the street (the "decision" being reviewed).
  for (const action of heroActions.reverse()) {
    const mapped = nearestSizeAction(action, solverActions, options.potAtHeroActionBb);
    if (mapped) return mapped;
  }

  return null;
}

// ---------------------------------------------------------------------------
// Decision-node history builder (P2.4)
// ---------------------------------------------------------------------------

/**
 * One action in the pre-hero action log, as emitted by the backend
 * `actions_before_hero` metadata field.
 */
export interface ActionBeforeHero {
  /** `true` when this is one of hero's own intermediate actions (not villain's). */
  player_is_hero: boolean;
  /** Action verb: "bet", "check", "call", "raise", "fold". */
  action: string;
  /** Chips committed by this action in bb (null for checks/folds). */
  amount_bb: number | null;
  /** Total raise-to amount in bb (non-null only for raises). */
  raise_to_bb: number | null;
  /** Total pot size in bb *before* this action was made. */
  pot_bb_before: number;
}

/**
 * Result from `buildDecisionNodeHistory`.
 */
export interface DecisionNodeContext {
  /** Numeric action indices for `apply_history` — empty = root node. */
  history: number[];
  /** 0 = root node (hero acts first); 1+ = depth after navigating past villain actions. */
  depth: number;
  /**
   * `true` when we could not fully reconstruct the path (e.g., an action
   * didn't match any available solver label).  The consumer should display
   * a reduced-confidence warning.
   */
  incomplete: boolean;
  /** Human-readable description of the node context (shown in the UI). */
  description: string;
}

/**
 * Map one `ActionBeforeHero` entry to a solver label at a given node.
 * Returns `null` when no suitable label is found.
 */
export function mapVillainActionToSolverLabel(
  entry: ActionBeforeHero,
  solverActions: readonly string[],
): string | null {
  const verb = entry.action.toLowerCase();

  if (verb === "check") return solverActions.find((a) => a === "check") ?? null;
  if (verb === "fold")  return solverActions.find((a) => a === "fold")  ?? null;
  if (verb === "call")  return solverActions.find((a) => a === "call")  ?? null;

  if (verb === "bet" || verb === "raise") {
    // Use raise_to_bb for raises (total commitment), amount_bb for bets.
    const amount = entry.raise_to_bb ?? entry.amount_bb;
    const pot = entry.pot_bb_before;

    if (amount !== null && amount > 0 && pot > 0) {
      const pctOfPot = (amount / pot) * 100;
      return mapBetFractionToLabel(pctOfPot, solverActions);
    }

    // No pot info — return first available betting action.
    return (
      solverActions.find((a) => a.startsWith("bet") || a.startsWith("raise") || a === "allin") ??
      null
    );
  }

  return null;
}

/**
 * Build the numeric action-index path needed to navigate the solver tree to
 * hero's exact decision node.
 *
 * The algorithm:
 * 1. For depth-0 (no pre-hero actions): return empty history immediately.
 * 2. For depth-1 (single villain action first): use the already-available
 *    `rootActions` to resolve the label → index.
 * 3. For depth-2+ (hero or villain acted multiple times): each extra step
 *    requires the caller to have queried `get_actions_at(handle, history)`
 *    for the intermediate node's action list and pass them as `nodeActionsList`.
 *    If `nodeActionsList` is shorter than needed, the remaining steps fall back
 *    and `incomplete` is set.
 *
 * @param actionsBeforeHero - ordered list from backend `actions_before_hero`
 * @param rootActions - solver action labels at the root node
 *   (from the first `export_strategy(handle, "")` call)
 * @param nodeActionsList - optional pre-fetched action label arrays for
 *   intermediate nodes at depth 2, 3, … (index 0 = actions after rootActions[idx])
 */
export function buildDecisionNodeHistory(
  actionsBeforeHero: readonly ActionBeforeHero[],
  rootActions: readonly string[],
  nodeActionsList: readonly (readonly string[])[] = [],
): DecisionNodeContext {
  if (actionsBeforeHero.length === 0) {
    return {
      history: [],
      depth: 0,
      incomplete: false,
      description: "hero acts first (root node)",
    };
  }

  const history: number[] = [];
  let incomplete = false;

  for (let step = 0; step < actionsBeforeHero.length; step++) {
    const entry = actionsBeforeHero[step];

    // Determine the action list available at this step.
    let currentActions: readonly string[];
    if (step === 0) {
      currentActions = rootActions;
    } else if (step - 1 < nodeActionsList.length) {
      currentActions = nodeActionsList[step - 1];
    } else {
      // No pre-fetched action list for this depth — mark incomplete and stop.
      incomplete = true;
      break;
    }

    const label = mapVillainActionToSolverLabel(entry, currentActions);
    if (label === null) {
      incomplete = true;
      break;
    }

    const idx = currentActions.indexOf(label);
    if (idx === -1) {
      incomplete = true;
      break;
    }

    history.push(idx);
  }

  if (incomplete && history.length === 0) {
    return {
      history: [],
      depth: 0,
      incomplete: true,
      description: "could not reconstruct history — showing root node",
    };
  }

  // Build a human-readable description of the node.
  const descParts = actionsBeforeHero.slice(0, history.length).map((entry) => {
    const who = entry.player_is_hero ? "hero" : "villain";
    const verb = entry.action;
    if (entry.amount_bb !== null) {
      const pct =
        entry.pot_bb_before > 0
          ? ` (${Math.round((entry.amount_bb / entry.pot_bb_before) * 100)}% pot)`
          : "";
      return `${who} ${verb} ${entry.amount_bb.toFixed(1)}bb${pct}`;
    }
    return `${who} ${verb}`;
  });

  const suffix = incomplete ? " [partial — showing deepest available node]" : "";

  return {
    history,
    depth: history.length,
    incomplete,
    description: `hero responds after: ${descParts.join(" → ")}${suffix}`,
  };
}
