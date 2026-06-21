"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { QueryError } from "@/components/QueryError";
import { TimeframeToggle } from "@/components/TimeframeToggle";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useLeaks } from "@/hooks/useStats";
import { humanizeLeakTag } from "@/lib/format";
import type { Timeframe } from "@/types/api";

export default function LeaksPage() {
  const [timeframe, setTimeframe] = useState<Timeframe>("30d");
  const leaks = useLeaks(timeframe);

  const chartData = (leaks.data ?? [])
    .slice()
    .sort((a, b) => b.count - a.count)
    .map((row) => ({
      tag: humanizeLeakTag(row.tag),
      count: row.count,
      pct: row.pct_of_analyses,
      rawTag: row.tag,
    }));

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Leaks</h1>
          <p className="text-muted-foreground">Patterns from LLM hand analysis</p>
        </div>
        <TimeframeToggle value={timeframe} onChange={setTimeframe} />
      </div>

      {leaks.isLoading && <Skeleton className="h-80 w-full rounded-xl" />}
      {leaks.isError && (
        <QueryError
          message={leaks.error?.message ?? "Failed to load leaks"}
          onRetry={() => leaks.refetch()}
        />
      )}

      {leaks.data && leaks.data.length === 0 && (
        <Card className="border-zinc-800 bg-zinc-950/50">
          <CardContent className="py-12 text-center text-muted-foreground">
            Run hand analysis from a reviewed hand to populate leaks
          </CardContent>
        </Card>
      )}

      {leaks.data && leaks.data.length > 0 && (
        <>
          <Card className="border-zinc-800 bg-zinc-950/50">
            <CardHeader>
              <CardTitle className="text-lg">Leak frequency</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={chartData}
                    layout="vertical"
                    margin={{ top: 8, right: 16, left: 8, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(0,0%,18%)" />
                    <XAxis type="number" tick={{ fill: "hsl(0,0%,64%)", fontSize: 12 }} />
                    <YAxis
                      type="category"
                      dataKey="tag"
                      width={140}
                      tick={{ fill: "hsl(0,0%,64%)", fontSize: 11 }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(0,0%,6%)",
                        border: "1px solid hsl(0,0%,18%)",
                        borderRadius: 8,
                      }}
                      formatter={(v: number, _name, props) => {
                        const payload = props.payload as { pct: number };
                        return [`${v} (${payload.pct.toFixed(1)}% of analyses)`, "Count"];
                      }}
                    />
                    <Bar dataKey="count" fill="hsl(160, 84%, 39%)" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-zinc-900/50 text-left text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-3 font-medium">Leak</th>
                  <th className="px-4 py-3 font-medium text-right">Count</th>
                  <th className="px-4 py-3 font-medium text-right">% of analyses</th>
                </tr>
              </thead>
              <tbody>
                {chartData.map((row) => (
                  <tr key={row.rawTag} className="border-b border-border/60">
                    <td className="px-4 py-2.5">{row.tag}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{row.count}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-emerald-400/90">
                      {row.pct.toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
