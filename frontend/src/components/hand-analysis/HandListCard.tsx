import { Check, HelpCircle, X } from "lucide-react";

import { CardStrip } from "@/components/hand-analysis/CardFace";
import { NetAmount } from "@/components/NetAmount";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { HandSummary } from "@/types/api";

export interface GradeCounts {
  solid: number;
  mixed: number;
  mistake: number;
}

export function HandListCard({
  hand,
  selected,
  gradeCounts,
  score,
  onSelect,
}: {
  hand: HandSummary;
  selected: boolean;
  gradeCounts?: GradeCounts;
  score?: number;
  onSelect: () => void;
}) {
  const safeScore = Math.max(0, Math.min(100, score ?? 50));

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-lg border p-3 text-left transition-colors",
        selected
          ? "border-blue-500 bg-slate-800"
          : "border-transparent bg-slate-900/80 hover:border-slate-700 hover:bg-slate-800/70",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="rounded-md bg-slate-950/80 px-2 py-1 text-xs text-slate-300">
          NLHE {hand.table_size}-max, {hand.stake_sb}/{hand.stake_bb}
        </span>
        <NetAmount chips={hand.hero_net} bb={hand.hero_net_bb} />
      </div>

      <div className="mt-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <CardStrip cards={hand.hero_cards} small />
          <div className="mt-2 text-xs text-slate-400">{formatDate(hand.played_at)}</div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-3">
          <div className="flex items-center gap-3 text-xs font-semibold">
            <span className="inline-flex items-center gap-1 text-emerald-300">
              <Check className="h-3.5 w-3.5 rounded bg-emerald-300 p-0.5 text-slate-950" />
              {gradeCounts?.solid ?? 0}
            </span>
            <span className="inline-flex items-center gap-1 text-amber-200">
              <HelpCircle className="h-3.5 w-3.5 rounded bg-amber-200 p-0.5 text-slate-950" />
              {gradeCounts?.mixed ?? 0}
            </span>
            <span className="inline-flex items-center gap-1 text-red-300">
              <X className="h-3.5 w-3.5 rounded bg-red-400 p-0.5 text-slate-950" />
              {gradeCounts?.mistake ?? 0}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-300">Hand Score</span>
            <span className="flex items-center gap-0.5" aria-label={`Hand score ${safeScore}`}>
              {Array.from({ length: 20 }).map((_, index) => (
                <span
                  key={index}
                  className={cn(
                    "h-4 w-1.5 rounded-full",
                    index < Math.round(safeScore / 5) ? "bg-blue-400" : "bg-slate-700",
                  )}
                />
              ))}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}
