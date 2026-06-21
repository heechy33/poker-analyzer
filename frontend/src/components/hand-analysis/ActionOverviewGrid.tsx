import { AlertTriangle, Check, HelpCircle, Info, X } from "lucide-react";

import { DecisionGradeBadge } from "@/components/hand-analysis/DecisionGradeBadge";
import type { DecisionGrade, DecisionQuality } from "@/lib/hand-analysis/grading";
import { cn } from "@/lib/utils";

function qualityIcon(quality: DecisionQuality) {
  if (quality === "solid") return Check;
  if (quality === "mistake") return X;
  return HelpCircle;
}

function qualityClass(quality: DecisionQuality): string {
  if (quality === "solid") return "bg-emerald-300 text-zinc-950";
  if (quality === "mistake") return "bg-red-400 text-zinc-950";
  return "bg-amber-200 text-zinc-950";
}

/** Derive a one-line node context description from depth / incomplete flags. */
function nodeCaption(
  nodeDepth: number | undefined,
  historyIncomplete: boolean | undefined,
  confidence: "high" | "medium" | "low" | undefined,
): { text: string; className: string } {
  if (historyIncomplete) {
    return {
      text: "Solver line: partial history — showing approximate node",
      className: "text-amber-300/80",
    };
  }
  if (nodeDepth === undefined || nodeDepth === 0) {
    if (confidence === "medium") {
      return {
        text: "Solver line: approximate — medium confidence",
        className: "text-yellow-300/80",
      };
    }
    return { text: "Solver line: hero opens (root node)", className: "text-slate-400" };
  }
  return {
    text: `Solver line: hero responds (depth ${nodeDepth})`,
    className: "text-emerald-300/70",
  };
}

export function ActionOverviewGrid({
  grade,
  context,
  caption,
  className,
  dimmed = false,
  confidence,
  nodeDepth,
  historyIncomplete,
}: {
  grade: DecisionGrade;
  context: string;
  caption?: string;
  className?: string;
  /** When true, visually dim the entire grid to indicate reduced reliability. */
  dimmed?: boolean;
  /** Confidence tier for caption override. */
  confidence?: "high" | "medium" | "low";
  /** Depth of the solver node used for grading. 0 = root, 1+ = response node. */
  nodeDepth?: number;
  /** True when history reconstruction was incomplete. */
  historyIncomplete?: boolean;
}) {
  const derived = nodeCaption(nodeDepth, historyIncomplete, confidence);
  const captionText = caption ?? derived.text;
  const captionClass = caption ? "text-slate-400" : derived.className;

  const showMediumApprox = confidence === "medium" && !historyIncomplete;
  const showHistoryWarning = historyIncomplete === true;
  const showResponseInfo = !historyIncomplete && nodeDepth !== undefined && nodeDepth > 0;

  return (
    <div
      className={cn(
        "rounded-lg border border-slate-700 bg-slate-800/70 p-3 shadow-sm",
        dimmed && "opacity-60",
        className,
      )}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-zinc-100">Action Overview</h4>
          <p className={cn("mt-0.5 text-xs", captionClass)}>{captionText}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-300">{context}</span>
          <DecisionGradeBadge
            quality={grade.quality}
            label={dimmed ? `${grade.label ?? grade.quality} (approx)` : grade.label}
          />
        </div>
      </div>

      {/* Medium-confidence approximate warning */}
      {showMediumApprox && (
        <div className="mb-3 flex items-start gap-2 rounded bg-amber-500/10 border border-amber-500/20 px-2 py-1.5">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-300" />
          <p className="text-[10px] leading-relaxed text-amber-200/80">
            Frequencies and EV are approximate. Use only for directional guidance,
            not specific action selection.
          </p>
        </div>
      )}

      {/* History-incomplete warning — shown when decision node could not be resolved */}
      {showHistoryWarning && (
        <div className="mb-3 flex items-start gap-2 rounded bg-amber-500/10 border border-amber-500/20 px-2 py-1.5">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-300" />
          <p className="text-[10px] leading-relaxed text-amber-200/80">
            Could not reconstruct full action history — frequencies shown are from
            a parent node and may not match this exact spot. Use for directional
            guidance only.
          </p>
        </div>
      )}

      {/* Response-node info badge — shown when we successfully navigated to hero's node */}
      {showResponseInfo && (
        <div className="mb-3 flex items-start gap-2 rounded bg-emerald-500/8 border border-emerald-500/15 px-2 py-1.5">
          <Info className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400/70" />
          <p className="text-[10px] leading-relaxed text-emerald-200/70">
            Frequencies from hero&apos;s exact response node (depth {nodeDepth}).
          </p>
        </div>
      )}

      <div className="grid gap-1.5 md:grid-cols-2 xl:grid-cols-3">
        {grade.cells.map((cell) => {
          const Icon = qualityIcon(cell.quality);
          return (
            <div
              key={cell.action}
              className={cn(
                "min-w-0 rounded-md border bg-slate-700/75 p-2 transition-colors",
                cell.isTaken
                  ? "border-amber-300 shadow-[0_0_0_1px_rgba(252,211,77,0.45)]"
                  : "border-slate-900/80",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span
                    className={cn(
                      "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded",
                      qualityClass(cell.quality),
                    )}
                  >
                    <Icon className="h-3 w-3" />
                  </span>
                  <span className="truncate text-sm font-semibold text-zinc-100">
                    {cell.label}
                  </span>
                </div>
                <span className="font-mono text-sm font-bold text-zinc-100">
                  {(cell.frequency * 100).toFixed(0)}%
                </span>
              </div>

              <div className="mt-2 flex items-center justify-between gap-3 rounded bg-slate-600/80 px-2 py-1">
                <div className="flex items-center gap-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-300">EV</span>
                  {Array.from({ length: 5 }).map((_, index) => (
                    <span
                      key={index}
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        index < cell.dots ? "bg-emerald-300" : "bg-slate-400/50",
                      )}
                    />
                  ))}
                </div>
                <span className="text-[10px] font-semibold text-slate-100">
                  {cell.qualityLabel}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
