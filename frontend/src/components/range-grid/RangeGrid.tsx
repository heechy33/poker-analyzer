"use client";

import { useEffect, useMemo, useState } from "react";

import { RangeGridTooltip } from "./RangeGridTooltip";
import { RANGE_GRID_COLORS, actionColor } from "@/lib/range-grid/colors";
import {
  RANKS,
  aggregateCombosToCell,
  cellLabelForPosition,
  comboToCell,
  type CellStrategy,
} from "@/lib/range-grid/combos";
import type { SolverOutput } from "@/lib/solver/types";
import { cn } from "@/lib/utils";

export interface RangeGridProps {
  output: SolverOutput;
  heroCombo?: string;
  board?: string[];
  className?: string;
}

interface ActiveCell {
  cell: CellStrategy;
  rect: DOMRect;
}

interface CellModel {
  row: number;
  col: number;
  label: string;
  strategy: CellStrategy;
}

interface Segment {
  action: string;
  x: number;
  width: number;
}

function usePrefersReducedMotion(): boolean {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setPrefersReducedMotion(media.matches);

    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return prefersReducedMotion;
}

function toSegments(cell: CellStrategy): Segment[] {
  let x = 0;

  return cell.actions.map((item) => {
    const probability = Math.min(Math.max(item.probability, 0), 1);
    const width = Math.min(probability * 40, 40 - x);
    const segment = { action: item.action, x, width };

    x += width;
    return segment;
  });
}

export function RangeGrid({ output, heroCombo, board, className }: RangeGridProps) {
  const [activeCell, setActiveCell] = useState<ActiveCell | null>(null);
  const [mounted, setMounted] = useState(false);
  const prefersReducedMotion = usePrefersReducedMotion();
  const heroLabel = heroCombo ? comboToCell(heroCombo) : "";

  useEffect(() => {
    setMounted(false);

    if (!output?.actions?.length) return;
    if (prefersReducedMotion) {
      setMounted(true);
      return;
    }

    const frame = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(frame);
  }, [output, prefersReducedMotion]);

  const cells = useMemo<CellModel[]>(
    () =>
      RANKS.flatMap((_, row) =>
        RANKS.map((__, col) => {
          const label = cellLabelForPosition(row, col);
          return {
            row,
            col,
            label,
            strategy: aggregateCombosToCell(output, label, { board }),
          };
        }),
      ),
    [board, output],
  );

  return (
    <div className={cn("relative w-full max-w-3xl", className)} onMouseLeave={() => setActiveCell(null)}>
      <div className="grid grid-cols-[repeat(13,minmax(0,1fr))] gap-px rounded-md border border-zinc-800 bg-zinc-900 p-px">
        {cells.map((cell) => (
          <RangeGridCell
            key={cell.label}
            cell={cell}
            actions={output.actions}
            isHero={heroLabel === cell.label}
            mounted={mounted || prefersReducedMotion}
            onShow={(strategy, rect) => setActiveCell({ cell: strategy, rect })}
            onHide={() => setActiveCell(null)}
          />
        ))}
      </div>

      {activeCell && (
        <RangeGridTooltip
          cell={activeCell.cell}
          anchorRect={activeCell.rect}
          actions={output.actions}
        />
      )}
    </div>
  );
}

interface RangeGridCellProps {
  cell: CellModel;
  actions: readonly string[];
  isHero: boolean;
  mounted: boolean;
  onShow: (strategy: CellStrategy, rect: DOMRect) => void;
  onHide: () => void;
}

function RangeGridCell({ cell, actions, isHero, mounted, onShow, onHide }: RangeGridCellProps) {
  const isEmpty = cell.strategy.comboCount === 0;
  const segments = toSegments(cell.strategy);
  const delay = Math.min((cell.row + cell.col) * 8, 200);

  return (
    <button
      type="button"
      className="aspect-square min-w-0 bg-zinc-950 outline-none ring-offset-0 transition-transform focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-amber-400"
      aria-label={`${cell.label} strategy`}
      onMouseEnter={(event) => onShow(cell.strategy, event.currentTarget.getBoundingClientRect())}
      onFocus={(event) => onShow(cell.strategy, event.currentTarget.getBoundingClientRect())}
      onBlur={onHide}
      onClick={(event) => onShow(cell.strategy, event.currentTarget.getBoundingClientRect())}
    >
      <svg viewBox="0 0 40 40" className="h-full w-full" role="img" aria-hidden="true">
        <rect
          x="0"
          y="0"
          width="40"
          height="40"
          fill={RANGE_GRID_COLORS.empty}
          opacity={isEmpty ? 0.9 : 1}
        />

        {!isEmpty &&
          segments.map((segment) => (
            <rect
              key={segment.action}
              x={segment.x}
              y="0"
              width={mounted ? segment.width : 0}
              height="40"
              fill={actionColor(segment.action, actions)}
              style={{
                transition: mounted ? `width 300ms ease ${delay}ms` : undefined,
              }}
            />
          ))}

        {isEmpty && cell.strategy.isBlocked && (
          <path d="M4 36 L36 4" stroke="#71717a" strokeWidth="2" opacity="0.35" />
        )}

        <rect
          x={isHero ? 1 : 0.5}
          y={isHero ? 1 : 0.5}
          width={isHero ? 38 : 39}
          height={isHero ? 38 : 39}
          fill="none"
          stroke={isHero ? RANGE_GRID_COLORS.hero : RANGE_GRID_COLORS.border}
          strokeWidth={isHero ? 2 : 0.75}
        />

        <text
          x="20"
          y="22"
          textAnchor="middle"
          dominantBaseline="middle"
          fill={RANGE_GRID_COLORS.label}
          opacity={isEmpty ? 0.2 : 0.88}
          fontSize="9"
          fontWeight="700"
          fontFamily="system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
          paintOrder="stroke"
          stroke="#09090b"
          strokeWidth="2"
        >
          {cell.label}
        </text>
      </svg>
    </button>
  );
}
