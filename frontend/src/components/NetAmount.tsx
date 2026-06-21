import { useAmountDisplay } from "@/stores/amount-display";
import { cn } from "@/lib/utils";

function parseAmount(value: string): number {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : 0;
}

export function NetAmount({
  chips,
  bb,
  unit: unitProp,
  className,
}: {
  chips: string;
  bb: string;
  /** Override the global amount display unit. Defaults to the persisted store value. */
  unit?: "bb" | "chips";
  className?: string;
}) {
  const { unit: storeUnit } = useAmountDisplay();
  const unit = unitProp ?? storeUnit;

  if (unit === "chips") {
    const chipsNum = parseAmount(chips);
    const color =
      chipsNum > 0 ? "text-emerald-400" : chipsNum < 0 ? "text-red-400" : "text-muted-foreground";
    const abs = Math.abs(chipsNum);
    const sign = chipsNum > 0 ? "+" : chipsNum < 0 ? "-" : "";
    return (
      <span className={cn("tabular-nums", color, className)}>
        {sign}₮{abs.toFixed(2)}
      </span>
    );
  }

  // BB mode (default)
  const bbNum = parseAmount(bb);
  const color =
    bbNum > 0 ? "text-emerald-400" : bbNum < 0 ? "text-red-400" : "text-muted-foreground";
  const abs = Math.abs(bbNum);
  const sign = bbNum > 0 ? "+" : bbNum < 0 ? "-" : "";
  return (
    <span className={cn("tabular-nums", color, className)}>
      {sign}{abs.toFixed(1)} bb
    </span>
  );
}