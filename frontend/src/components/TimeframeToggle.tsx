"use client";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Timeframe } from "@/types/api";

const OPTIONS: Timeframe[] = ["lifetime", "7d", "30d"];

const LABELS: Record<Timeframe, string> = {
  lifetime: "Lifetime",
  "7d": "7d",
  "30d": "30d",
};

export function TimeframeToggle({
  value,
  onChange,
}: {
  value: Timeframe;
  onChange: (tf: Timeframe) => void;
}) {
  return (
    <Tabs value={value} onValueChange={(v) => onChange(v as Timeframe)}>
      <TabsList className="bg-zinc-900">
        {OPTIONS.map((tf) => (
          <TabsTrigger
            key={tf}
            value={tf}
            className="data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-400"
          >
            {LABELS[tf]}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
