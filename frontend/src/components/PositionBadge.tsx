import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const POSITION_STYLES: Record<string, string> = {
  BTN: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  CO: "border-sky-500/40 bg-sky-500/10 text-sky-400",
  HJ: "border-violet-500/40 bg-violet-500/10 text-violet-400",
  UTG: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  SB: "border-orange-500/40 bg-orange-500/10 text-orange-400",
  BB: "border-rose-500/40 bg-rose-500/10 text-rose-400",
};

export function PositionBadge({ position }: { position: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("font-mono text-xs", POSITION_STYLES[position] ?? "")}
    >
      {position}
    </Badge>
  );
}
