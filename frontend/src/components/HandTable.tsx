"use client";

import { Eye } from "lucide-react";

import { NetAmount } from "@/components/NetAmount";
import { PositionBadge } from "@/components/PositionBadge";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { HandSummary } from "@/types/api";

export function HandTable({
  hands,
  onRowClick,
  emptyMessage = "No hands found.",
}: {
  hands: HandSummary[];
  onRowClick?: (hand: HandSummary) => void;
  emptyMessage?: string;
}) {
  if (hands.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">{emptyMessage}</p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-border bg-zinc-900/50 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-3 font-medium">Date</th>
            <th className="px-4 py-3 font-medium">Position</th>
            <th className="px-4 py-3 font-medium">Cards</th>
            <th className="px-4 py-3 font-medium text-right">Pot</th>
            <th className="px-4 py-3 font-medium text-right">Net</th>
            <th className="px-4 py-3 font-medium text-center">WTSD</th>
          </tr>
        </thead>
        <tbody>
          {hands.map((hand) => (
            <tr
              key={hand.id}
              onClick={() => onRowClick?.(hand)}
              className={cn(
                "border-b border-border/60 transition-colors",
                onRowClick && "cursor-pointer hover:bg-zinc-900/60",
              )}
            >
              <td className="px-4 py-2.5 whitespace-nowrap text-muted-foreground">
                {formatDate(hand.played_at)}
              </td>
              <td className="px-4 py-2.5">
                <PositionBadge position={hand.hero_position} />
              </td>
              <td className="px-4 py-2.5 font-mono text-xs">
                {hand.hero_cards.join(" ") || "—"}
              </td>
              <td className="px-4 py-2.5 text-right tabular-nums">{hand.total_pot}</td>
              <td className="px-4 py-2.5 text-right">
                <NetAmount
                  chips={hand.hero_net}
                  bb={hand.hero_net_bb}
                />
              </td>
              <td className="px-4 py-2.5 text-center">
                {hand.went_to_showdown ? (
                  <Eye className="mx-auto h-4 w-4 text-emerald-400" aria-label="Went to showdown" />
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}