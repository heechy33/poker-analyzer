import { CardStrip } from "@/components/hand-analysis/CardFace";
import { NetAmount } from "@/components/NetAmount";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { HandSummary } from "@/types/api";

export function HandListCard({
  hand,
  selected,
  onSelect,
}: {
  hand: HandSummary;
  selected: boolean;
  onSelect: () => void;
}) {
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

        <span className="shrink-0 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs font-semibold text-slate-300">
          {hand.hero_position}
        </span>
      </div>
    </button>
  );
}
