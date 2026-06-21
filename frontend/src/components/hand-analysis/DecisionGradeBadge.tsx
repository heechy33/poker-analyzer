import { Check, HelpCircle, X } from "lucide-react";

import type { DecisionQuality } from "@/lib/hand-analysis/grading";
import { cn } from "@/lib/utils";

const BADGE_META: Record<
  DecisionQuality,
  { label: string; className: string; icon: typeof Check }
> = {
  solid: {
    label: "Solid",
    className: "border-emerald-400/50 bg-emerald-400/15 text-emerald-200",
    icon: Check,
  },
  mixed: {
    label: "Close",
    className: "border-amber-300/60 bg-amber-300/15 text-amber-100",
    icon: HelpCircle,
  },
  mistake: {
    label: "Mistake",
    className: "border-red-400/50 bg-red-400/15 text-red-200",
    icon: X,
  },
  unknown: {
    label: "Unmatched",
    className: "border-zinc-500 bg-zinc-800 text-zinc-300",
    icon: HelpCircle,
  },
};

export function DecisionGradeBadge({
  quality,
  label,
  className,
}: {
  quality: DecisionQuality;
  label?: string;
  className?: string;
}) {
  const meta = BADGE_META[quality];
  const Icon = meta.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold",
        meta.className,
        className,
      )}
    >
      <Icon className="h-3 w-3" />
      {label ?? meta.label}
    </span>
  );
}
