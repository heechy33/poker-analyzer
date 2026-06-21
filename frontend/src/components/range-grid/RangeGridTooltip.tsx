"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { actionColor } from "@/lib/range-grid/colors";
import type { CellStrategy } from "@/lib/range-grid/combos";

interface TooltipPosition {
  left: number;
  top: number;
}

interface RangeGridTooltipProps {
  cell: CellStrategy;
  anchorRect: DOMRect;
  actions: readonly string[];
}

const VIEWPORT_MARGIN = 8;

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function RangeGridTooltip({ cell, anchorRect, actions }: RangeGridTooltipProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<TooltipPosition | null>(null);

  useLayoutEffect(() => {
    const tooltip = ref.current;
    if (!tooltip) return;

    const tooltipRect = tooltip.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const centeredLeft = anchorRect.left + anchorRect.width / 2 - tooltipRect.width / 2;
    const belowTop = anchorRect.bottom + VIEWPORT_MARGIN;
    const aboveTop = anchorRect.top - tooltipRect.height - VIEWPORT_MARGIN;
    const left = Math.min(
      Math.max(centeredLeft, VIEWPORT_MARGIN),
      viewportWidth - tooltipRect.width - VIEWPORT_MARGIN,
    );
    const top =
      belowTop + tooltipRect.height <= viewportHeight - VIEWPORT_MARGIN
        ? belowTop
        : Math.max(aboveTop, VIEWPORT_MARGIN);

    setPosition({ left, top });
  }, [anchorRect, cell]);

  return (
    <div
      ref={ref}
      className="fixed z-50 w-56 rounded-md border border-zinc-700 bg-zinc-950/95 p-3 text-xs text-zinc-100 shadow-xl backdrop-blur"
      style={{
        left: position?.left ?? 0,
        top: position?.top ?? 0,
        visibility: position ? "visible" : "hidden",
      }}
      role="status"
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-zinc-50">{cell.label}</span>
        <span className="text-[11px] text-zinc-400">
          {cell.comboCount}/{cell.sourceComboCount || cell.comboCount} combos
        </span>
      </div>

      <div className="space-y-1.5">
        {cell.actions.map((item) => (
          <div key={item.action} className="grid grid-cols-[1fr_auto] items-baseline gap-3">
            <div className="flex min-w-0 items-center gap-1.5">
              <span
                className="h-2 w-2 shrink-0 rounded-sm"
                style={{ backgroundColor: actionColor(item.action, actions) }}
              />
              <span className="truncate text-zinc-200">{item.action}</span>
            </div>
            <div className="text-right tabular-nums text-zinc-100">
              {formatPercent(item.probability)}
              {typeof item.ev === "number" && (
                <span className="ml-1 text-zinc-400">{item.ev.toFixed(2)}bb</span>
              )}
            </div>
            {typeof item.aggregateFrequency === "number" && (
              <div className="col-span-2 pl-3.5 text-[11px] text-zinc-500">
                range {formatPercent(item.aggregateFrequency)}
              </div>
            )}
          </div>
        ))}
      </div>

      {cell.isBlocked && (
        <div className="mt-2 border-t border-zinc-800 pt-2 text-[11px] text-zinc-500">
          Blocked by board cards
        </div>
      )}
    </div>
  );
}
