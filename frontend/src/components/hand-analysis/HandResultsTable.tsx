import { Trophy, Users } from "lucide-react";

import { useAmountDisplay } from "@/stores/amount-display";
import { cn } from "@/lib/utils";
import type { HandActionOut, HandDetail } from "@/types/api";

function amount(action: HandActionOut): number {
  const raw = action.amount ?? action.raise_to ?? "0";
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function signedChips(value: number): string {
  if (value > 0) return `+ ${value.toFixed(2)}`;
  if (value < 0) return `- ${Math.abs(value).toFixed(2)}`;
  return "0.00";
}

function playerNet(hand: HandDetail, seat: number, screenName: string): number | null {
  const heroNet = Number.parseFloat(hand.hero_net);
  if (seat === hand.hero_seat && Number.isFinite(heroNet)) return heroNet;

  let invested = 0;
  let collected = 0;
  for (const action of hand.actions) {
    if (action.screen_name !== screenName) continue;
    if (["post_sb", "post_bb", "call", "bet", "raise"].includes(action.action)) {
      invested += amount(action);
    } else if (action.action === "collect") {
      collected += amount(action);
    }
  }

  if (invested === 0 && collected === 0) return null;
  return collected - invested;
}

export function HandResultsTable({ hand }: { hand: HandDetail }) {
  const { unit } = useAmountDisplay();
  const stakeBB = parseFloat(hand.stake_bb);

  function formatNet(chips: number): string {
    if (unit === "chips") {
      const abs = Math.abs(chips);
      const sign = chips > 0 ? "+" : chips < 0 ? "-" : "";
      return `${sign}₮${abs.toFixed(2)}`;
    }
    const bb = stakeBB > 0 ? chips / stakeBB : 0;
    const abs = Math.abs(bb);
    const sign = bb > 0 ? "+" : bb < 0 ? "-" : "";
    return `${sign}${abs.toFixed(1)} bb`;
  }

  function formatStack(chips: number): string {
    if (unit === "chips") return chips.toFixed(2);
    const bb = stakeBB > 0 ? chips / stakeBB : chips;
    return `${bb.toFixed(1)} bb`;
  }

  return (
    <section className="overflow-hidden rounded-lg border border-slate-700 bg-slate-900/80">
      <div className="flex items-center justify-between border-b border-slate-700 bg-slate-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <Trophy className="h-4 w-4 text-slate-300" />
          <h3 className="font-semibold text-zinc-100">Results</h3>
        </div>
        <span className="inline-flex items-center gap-1 text-sm text-slate-300">
          <Users className="h-4 w-4" />
          {hand.players.length} Players
        </span>
      </div>

      <div className="divide-y divide-slate-800">
        {hand.players.map((player) => {
          const net = playerNet(hand, player.seat, player.screen_name);
          const starting = Number.parseFloat(player.starting_stack);
          const finalStack = net !== null && Number.isFinite(starting) ? starting + net : null;

          return (
            <div
              key={player.seat}
              className="grid grid-cols-[64px_minmax(0,1fr)_auto_auto] items-center gap-3 px-4 py-2 text-sm"
            >
              <span className="w-fit rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs font-semibold text-slate-300">
                {player.position ?? `S${player.seat}`}
              </span>
              <span className={cn("truncate text-slate-300", player.is_hero && "font-semibold text-zinc-100")}>
                {player.is_hero ? "You" : player.screen_name}
              </span>
              <span
                className={cn(
                  "font-mono text-xs",
                  net === null
                    ? "text-slate-500"
                    : net >= 0
                      ? "text-emerald-300"
                      : "text-red-300",
                )}
              >
                {net === null ? "net n/a" : formatNet(net)}
              </span>
              <span className="min-w-14 rounded-md bg-slate-700 px-2 py-1 text-right font-mono text-xs font-semibold text-slate-100">
                {finalStack === null
                  ? formatStack(starting)
                  : formatStack(finalStack)}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}