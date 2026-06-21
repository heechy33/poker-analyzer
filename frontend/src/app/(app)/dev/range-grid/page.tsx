import { notFound } from "next/navigation";

import { RangeGrid } from "@/components/range-grid";
import fixture from "@/lib/range-grid/__fixtures__/strategy_export_min.json";
import type { SolverOutput } from "@/lib/solver/types";

export default function DevRangeGridPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Range Grid</h1>
        <p className="text-sm text-muted-foreground">Fixture strategy export</p>
      </div>

      <RangeGrid
        output={fixture as SolverOutput}
        heroCombo="AhKh"
        board={["As", "7d", "2c"]}
        className="max-w-4xl"
      />
    </div>
  );
}
