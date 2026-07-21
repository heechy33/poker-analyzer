"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAmountDisplay } from "@/stores/amount-display";
import { cn } from "@/lib/utils";
import type { HandActionOut, HandDetail } from "@/types/api";

const STREET_ORDER = ["preflop", "flop", "turn", "river"] as const;

function formatAction(action: HandActionOut, unit: "bb" | "chips", stakeBB: number): string {
  const pieces = [action.action];
  if (action.raise_to) {
    if (unit === "bb" && stakeBB > 0) {
      const bb = parseFloat(action.raise_to) / stakeBB;
      pieces.push(`to ${action.raise_to} (${Number.isFinite(bb) ? bb.toFixed(1) : "0.0"} bb)`);
    } else {
      pieces.push(`to ${action.raise_to}`);
    }
  } else if (action.amount) {
    if (unit === "bb" && stakeBB > 0) {
      const bb = parseFloat(action.amount) / stakeBB;
      pieces.push(`${action.amount} (${Number.isFinite(bb) ? bb.toFixed(1) : "0.0"} bb)`);
    } else {
      pieces.push(action.amount);
    }
  }
  return pieces.join(" ");
}

function formatPotLabel(chips: string, unit: "bb" | "chips", stakeBB: number): string {
  if (unit === "chips") return `₮${Number(chips).toFixed(2)}`;
  const bb = parseFloat(chips) / stakeBB;
  return `${Number.isFinite(bb) ? bb.toFixed(1) : chips} bb`;
}

export function ReplayerTab({ hand }: { hand: HandDetail }) {
  const [showRaw, setShowRaw] = useState(false);
  const { unit } = useAmountDisplay();
  const heroName = hand.players.find((player) => player.is_hero)?.screen_name;
  const stakeBB = parseFloat(hand.stake_bb);

  const grouped = useMemo(() => {
    const groups = new Map<string, HandActionOut[]>();
    for (const street of STREET_ORDER) groups.set(street, []);
    for (const action of hand.actions) {
      const street = action.street.toLowerCase();
      groups.set(street, [...(groups.get(street) ?? []), action]);
    }
    return groups;
  }, [hand.actions]);

  return (
    <div className="space-y-4">
      {hand.ledger_status !== "valid" && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          Replay fallback only — this hand has no valid canonical ledger and is not authoritative for statistics or any future solver review.
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_220px]">
        <div className="space-y-4">
          {STREET_ORDER.map((street) => {
            const actions = grouped.get(street) ?? [];
            if (actions.length === 0) return null;

            return (
              <section key={street} className="space-y-2">
                <h3 className="text-xs font-semibold uppercase text-muted-foreground">
                  {street}
                </h3>
                <ol className="space-y-1">
                  {actions
                    .slice()
                    .sort((a, b) => a.action_order - b.action_order)
                    .map((action) => {
                      const isHero =
                        action.seat === hand.hero_seat ||
                        (heroName !== undefined && action.screen_name === heroName);

                      return (
                        <li
                          key={`${action.street}-${action.action_order}-${action.seat}`}
                          className={cn(
                            "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md border px-3 py-2 text-sm",
                            isHero
                              ? "border-emerald-500/40 bg-emerald-500/10"
                              : "border-border bg-zinc-950/50",
                          )}
                        >
                          <div className="min-w-0">
                            <span className="font-medium">{action.screen_name}</span>
                            <span className="ml-2 text-muted-foreground">
                              seat {action.seat}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 font-mono text-xs">
                            <span>{formatAction(action, unit, stakeBB)}</span>
                            {action.is_all_in && (
                              <Badge variant="outline" className="border-amber-500/50 text-amber-300">
                                All-in
                              </Badge>
                            )}
                          </div>
                        </li>
                      );
                    })}
                </ol>
              </section>
            );
          })}
        </div>

        <aside className="h-fit rounded-lg border border-border bg-zinc-950/50 p-4 text-sm">
          <h3 className="mb-3 font-semibold">Summary</h3>
          <dl className="space-y-2 text-muted-foreground">
            <div className="flex justify-between gap-4">
              <dt>Total pot</dt>
              <dd className="font-mono text-foreground">{formatPotLabel(hand.total_pot, unit, stakeBB)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt>Rake</dt>
              <dd className="font-mono text-foreground">{unit === "chips" ? `₮${hand.rake}` : `${(parseFloat(hand.rake) / stakeBB).toFixed(1)} bb`}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt>Splash fee</dt>
              <dd className="font-mono text-foreground">{unit === "chips" ? `₮${hand.splash_fee}` : `${(parseFloat(hand.splash_fee) / stakeBB).toFixed(1)} bb`}</dd>
            </div>
          </dl>
        </aside>
      </div>

      {hand.raw_text && (
        <div className="space-y-2">
          <Button variant="outline" size="sm" onClick={() => setShowRaw((value) => !value)}>
            {showRaw ? "Hide raw" : "Show raw"}
          </Button>
          {showRaw && (
            <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-zinc-950 p-4 text-xs text-zinc-300">
              {hand.raw_text}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
