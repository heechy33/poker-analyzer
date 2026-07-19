/** Palette for the 13×13 range grid SVG. */
export const RANGE_GRID_COLORS = {
  empty: "#18181b",
  border: "#3f3f46",
  hero: "#34d399",
  label: "#fafafa",
} as const;

const ACTION_PALETTE = [
  "#34d399", // emerald — check / passive
  "#fbbf24", // amber — small bet
  "#f97316", // orange — medium bet
  "#ef4444", // red — large bet / raise
  "#a78bfa", // violet — all-in / other
  "#38bdf8", // sky
  "#fb7185", // rose
  "#4ade80", // green
];

const NAMED: Record<string, string> = {
  fold: "#71717a",
  check: "#34d399",
  call: "#22d3ee",
  allin: "#a78bfa",
};

/**
 * Stable color for a solver action label within the current action list.
 */
export function actionColor(action: string, actions: readonly string[]): string {
  const lower = action.toLowerCase();
  if (NAMED[lower]) return NAMED[lower];

  if (lower.startsWith("bet")) {
    const pct = Number.parseInt(lower.replace(/\D/g, ""), 10);
    if (pct <= 40) return ACTION_PALETTE[1];
    if (pct <= 75) return ACTION_PALETTE[2];
    return ACTION_PALETTE[3];
  }
  if (lower.startsWith("raise")) {
    return ACTION_PALETTE[3];
  }

  const index = Math.max(0, actions.indexOf(action));
  return ACTION_PALETTE[index % ACTION_PALETTE.length];
}
