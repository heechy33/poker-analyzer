"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { HandTable } from "@/components/HandTable";
import { QueryError } from "@/components/QueryError";
import { StatCard } from "@/components/StatCard";
import { TimeframeToggle } from "@/components/TimeframeToggle";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useBiggestLosers, useStatsByPosition, useStatsSummary } from "@/hooks/useStats";
import { formatBb100, formatPct, sortByPosition } from "@/lib/format";
import { useHandReviewStore } from "@/stores/hand-review";
import type { Timeframe } from "@/types/api";

function bbColor(bb: number): string | undefined {
  if (bb > 0) return "text-emerald-400";
  if (bb < 0) return "text-red-400";
  return undefined;
}

function barFill(bb: number): string {
  if (bb >= 0) return "hsl(160, 84%, 39%)";
  return "hsl(0, 84%, 60%)";
}

export default function DashboardPage() {
  const [timeframe, setTimeframe] = useState<Timeframe>("30d");
  const openHandReview = useHandReviewStore((s) => s.openHandReview);

  const stats = useStatsSummary(timeframe);
  const byPosition = useStatsByPosition(timeframe);
  const losers = useBiggestLosers(timeframe);

  const positionRows = sortByPosition(
    (byPosition.data ?? []).map((r) => ({ ...r, position: r.position })),
  );

  const chartData = positionRows.map((r) => ({
    position: r.position,
    bb_per_100: r.bb_per_100,
  }));

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground">Hero stats and biggest losing hands</p>
        </div>
        <TimeframeToggle value={timeframe} onChange={setTimeframe} />
      </div>

      {stats.isLoading && <StatsSkeleton />}
      {stats.isError && (
        <QueryError
          message={stats.error?.message ?? "Failed to load stats"}
          onRetry={() => stats.refetch()}
        />
      )}
      {stats.data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          <StatCard label="Hands" value={String(stats.data.hands_count)} />
          <StatCard label="VPIP" value={formatPct(stats.data.vpip_pct)} />
          <StatCard label="PFR" value={formatPct(stats.data.pfr_pct)} />
          <StatCard label="3-bet" value={formatPct(stats.data.three_bet_pct)} />
          <StatCard label="WTSD" value={formatPct(stats.data.wtsd_pct)} />
          <StatCard label="W$SD" value={formatPct(stats.data.wsd_pct)} />
          <StatCard
            label="BB/100"
            value={formatBb100(stats.data.bb_per_100)}
            valueClassName={bbColor(stats.data.bb_per_100)}
          />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="border-zinc-800 bg-zinc-950/50">
          <CardHeader>
            <CardTitle className="text-lg">BB/100 by position</CardTitle>
          </CardHeader>
          <CardContent>
            {byPosition.isLoading && <Skeleton className="h-64 w-full" />}
            {byPosition.isError && (
              <QueryError
                message={byPosition.error?.message ?? "Failed to load position stats"}
                onRetry={() => byPosition.refetch()}
              />
            )}
            {byPosition.data && chartData.length > 0 && (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(0,0%,18%)" />
                    <XAxis dataKey="position" tick={{ fill: "hsl(0,0%,64%)", fontSize: 12 }} />
                    <YAxis tick={{ fill: "hsl(0,0%,64%)", fontSize: 12 }} />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(0,0%,6%)",
                        border: "1px solid hsl(0,0%,18%)",
                        borderRadius: 8,
                      }}
                      formatter={(v: number) => [formatBb100(v), "BB/100"]}
                    />
                    <Bar dataKey="bb_per_100" radius={[4, 4, 0, 0]}>
                      {chartData.map((entry) => (
                        <Cell key={entry.position} fill={barFill(entry.bb_per_100)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
            {byPosition.data && positionRows.length > 0 && (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Pos</th>
                      <th className="pb-2 pr-4">Hands</th>
                      <th className="pb-2 pr-4">VPIP</th>
                      <th className="pb-2 pr-4">PFR</th>
                      <th className="pb-2 text-right">BB/100</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positionRows.map((row) => (
                      <tr key={row.position} className="border-t border-border/50">
                        <td className="py-1.5 pr-4 font-medium">{row.position}</td>
                        <td className="py-1.5 pr-4 tabular-nums">{row.hands}</td>
                        <td className="py-1.5 pr-4 tabular-nums">{formatPct(row.vpip_pct)}</td>
                        <td className="py-1.5 pr-4 tabular-nums">{formatPct(row.pfr_pct)}</td>
                        <td
                          className={`py-1.5 text-right tabular-nums ${bbColor(row.bb_per_100) ?? ""}`}
                        >
                          {formatBb100(row.bb_per_100)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {byPosition.data && positionRows.length === 0 && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No position data for this timeframe.
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="border-zinc-800 bg-zinc-950/50">
          <CardHeader>
            <CardTitle className="text-lg">Biggest losers</CardTitle>
          </CardHeader>
          <CardContent>
            {losers.isLoading && <Skeleton className="h-48 w-full" />}
            {losers.isError && (
              <QueryError
                message={losers.error?.message ?? "Failed to load losers"}
                onRetry={() => losers.refetch()}
              />
            )}
            {losers.data && (
              <HandTable
                hands={losers.data}
                onRowClick={(h) => openHandReview(h.id)}
                emptyMessage="No losing hands in this period."
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatsSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      {Array.from({ length: 7 }).map((_, i) => (
        <Skeleton key={i} className="h-24 rounded-xl" />
      ))}
    </div>
  );
}