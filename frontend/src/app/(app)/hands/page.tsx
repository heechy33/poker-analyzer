"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown, DollarSign, Hash } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  HandAnalysisPane,
  handScoreFromSolves,
  type SolvesByStreet,
} from "@/components/hand-analysis/HandAnalysisPane";
import { HandListCard, type GradeCounts } from "@/components/hand-analysis/HandListCard";
import { QueryError } from "@/components/QueryError";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchFilterOptions, fetchHand, fetchHands } from "@/lib/api";
import { formatPot, POSITIONS } from "@/lib/format";
import { useAmountDisplay } from "@/stores/amount-display";
import type { FilterOptionsResponse, HandSummary } from "@/types/api";

const PAGE_SIZE = 50;

type SortKey = "played_at" | "hero_position" | "total_pot" | "hero_net_bb" | "hero_net";
type SortDir = "asc" | "desc";

interface CachedScore {
  counts: GradeCounts;
  score: number;
}

function dayKey(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function loadStored(key: string): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(key) ?? "";
}

function storeVal(key: string, value: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem(key, value);
  }
}

export default function HandsPage() {
  const [offset, setOffset] = useState(0);
  const [position, setPosition] = useState<string>("");
  const [onlyLosses, setOnlyLosses] = useState(false);
  const [since, setSince] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("played_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedHandId, setSelectedHandId] = useState<string | null>(null);
  const [scoresByHand, setScoresByHand] = useState<Record<string, CachedScore>>({});

  // New filter state
  const [gameMode, setGameMode] = useState<string>(() => loadStored("hands:game_mode"));
  const [stakes, setStakes] = useState<string>(() => loadStored("hands:stakes"));
  const { unit, setUnit } = useAmountDisplay();

  // Fetch filter options for stakes dropdown
  const filterOptions = useQuery<FilterOptionsResponse>({
    queryKey: ["filter-options"],
    queryFn: fetchFilterOptions,
    staleTime: 60_000,
  });

  const query = useQuery({
    queryKey: ["hands", { offset, position, onlyLosses, since, gameMode, stakes }],
    queryFn: () =>
      fetchHands({
        limit: PAGE_SIZE,
        offset,
        order: "played_at.desc",
        position: position || undefined,
        since: since || undefined,
        only_losses: onlyLosses || undefined,
        game_mode: (gameMode || undefined) as "heads_up" | "multiway" | undefined,
        stakes: stakes || undefined,
      }),
  });

  const sortedHands = useMemo(() => {
    const rows = [...(query.data ?? [])];
    rows.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "played_at":
          cmp = new Date(a.played_at).getTime() - new Date(b.played_at).getTime();
          break;
        case "hero_position":
          cmp = a.hero_position.localeCompare(b.hero_position);
          break;
        case "total_pot":
          cmp = Number.parseFloat(a.total_pot) - Number.parseFloat(b.total_pot);
          break;
        case "hero_net_bb":
          cmp = Number.parseFloat(a.hero_net_bb) - Number.parseFloat(b.hero_net_bb);
          break;
        case "hero_net":
          cmp = Number.parseFloat(a.hero_net) - Number.parseFloat(b.hero_net);
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [query.data, sortKey, sortDir]);

  const groupedHands = useMemo(() => {
    const groups = new Map<string, HandSummary[]>();
    for (const hand of sortedHands) {
      const key = dayKey(hand.played_at);
      groups.set(key, [...(groups.get(key) ?? []), hand]);
    }
    return Array.from(groups.entries());
  }, [sortedHands]);

  const handQuery = useQuery({
    queryKey: ["hand", selectedHandId],
    queryFn: () => fetchHand(selectedHandId as string),
    enabled: Boolean(selectedHandId),
  });

  useEffect(() => {
    if (!selectedHandId && sortedHands.length > 0) {
      setSelectedHandId(sortedHands[0].id);
    }
    if (selectedHandId && sortedHands.length > 0 && !sortedHands.some((hand) => hand.id === selectedHandId)) {
      setSelectedHandId(sortedHands[0].id);
    }
  }, [selectedHandId, sortedHands]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  function resetFilters() {
    setPosition("");
    setOnlyLosses(false);
    setSince("");
    setGameMode("");
    storeVal("hands:game_mode", "");
    setStakes("");
    storeVal("hands:stakes", "");
    setOffset(0);
  }

  const handleSolvesChange = useCallback((handId: string, solves: SolvesByStreet) => {
    const nextScore = handScoreFromSolves(solves);
    setScoresByHand((previous) => ({
      ...previous,
      [handId]: nextScore,
    }));
  }, []);

  // Determine the effective sort key based on display unit
  const effectiveSortKey: SortKey = sortKey;

  // Build active filter summary
  const activeFilters: string[] = [];
  if (gameMode === "heads_up") activeFilters.push("Heads Up");
  if (gameMode === "multiway") activeFilters.push("Multiway");
  if (stakes) activeFilters.push(stakes);
  const hasFilters = Boolean(gameMode || stakes || position || onlyLosses || since);

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Hands</h1>
        <p className="text-muted-foreground">Browse hands with embedded postflop review</p>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-4 rounded-lg border border-border bg-card p-4">
        {/* Game mode segmented control */}
        <label className="space-y-1.5 text-sm">
          <span className="text-muted-foreground">Game Mode</span>
          <div className="flex rounded-md border border-input bg-background">
            {(["", "heads_up", "multiway"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => {
                  setGameMode(mode);
                  storeVal("hands:game_mode", mode);
                  setOffset(0);
                }}
                className={`px-3 py-1.5 text-xs font-medium first:rounded-l-md last:rounded-r-md transition-colors ${
                  gameMode === mode
                    ? "bg-zinc-700 text-zinc-100"
                    : "text-muted-foreground hover:bg-zinc-800"
                }`}
              >
                {mode === "" ? "All" : mode === "heads_up" ? "Heads Up" : "Multiway"}
              </button>
            ))}
          </div>
        </label>

        {/* Stakes dropdown */}
        <label className="space-y-1.5 text-sm">
          <span className="text-muted-foreground">Stakes</span>
          <select
            value={stakes}
            onChange={(e) => {
              setStakes(e.target.value);
              storeVal("hands:stakes", e.target.value);
              setOffset(0);
            }}
            className="flex h-10 min-w-[140px] rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">All stakes</option>
            {filterOptions.data?.stakes.map((s) => (
              <option key={s.label} value={`${s.sb}/${s.bb}`}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        {/* Position filter (existing) */}
        <label className="space-y-1.5 text-sm">
          <span className="text-muted-foreground">Position</span>
          <select
            value={position}
            onChange={(e) => {
              setPosition(e.target.value);
              setOffset(0);
            }}
            className="flex h-10 w-32 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">All</option>
            {POSITIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>

        {/* Since filter (existing) */}
        <label className="space-y-1.5 text-sm">
          <span className="text-muted-foreground">Since (date)</span>
          <Input
            type="date"
            value={since}
            onChange={(e) => {
              setSince(e.target.value);
              setOffset(0);
            }}
            className="w-40"
          />
        </label>

        {/* Losses only (existing) */}
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            checked={onlyLosses}
            onChange={(e) => {
              setOnlyLosses(e.target.checked);
              setOffset(0);
            }}
            className="h-4 w-4 rounded border-input accent-emerald-500"
          />
          Losses only
        </label>

        {/* BB / $ toggle */}
        <div className="flex items-center gap-2 pb-2">
          <span className="text-xs text-muted-foreground">Display</span>
          <div className="flex rounded-md border border-input bg-background">
            <button
              type="button"
              onClick={() => setUnit("bb")}
              className={`inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-l-md transition-colors ${
                unit === "bb" ? "bg-zinc-700 text-zinc-100" : "text-muted-foreground hover:bg-zinc-800"
              }`}
            >
              <Hash className="h-3 w-3" />
              BB
            </button>
            <button
              type="button"
              onClick={() => setUnit("chips")}
              className={`inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-r-md transition-colors ${
                unit === "chips" ? "bg-zinc-700 text-zinc-100" : "text-muted-foreground hover:bg-zinc-800"
              }`}
            >
              <DollarSign className="h-3 w-3" />
              $
            </button>
          </div>
        </div>

        <Button variant="ghost" size="sm" onClick={resetFilters}>
          Clear filters
        </Button>
      </div>

      {/* Active filter summary */}
      {hasFilters && (
        <p className="text-sm text-muted-foreground">
          {activeFilters.length > 0 && (
            <>
              {activeFilters.join(" · ")} ·{" "}
            </>
          )}
          displaying {unit === "bb" ? "BB" : "$"}
        </p>
      )}

      {/* Sort buttons */}
      <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
        <SortButton
          label="Date"
          active={sortKey === "played_at"}
          dir={sortDir}
          onClick={() => toggleSort("played_at")}
        />
        <SortButton
          label="Position"
          active={sortKey === "hero_position"}
          dir={sortDir}
          onClick={() => toggleSort("hero_position")}
        />
        <SortButton
          label="Pot"
          active={sortKey === "total_pot"}
          dir={sortDir}
          onClick={() => toggleSort("total_pot")}
        />
        <SortButton
          label={unit === "bb" ? "Net (bb)" : "Net ($)"}
          active={sortKey === (unit === "bb" ? "hero_net_bb" : "hero_net")}
          dir={sortDir}
          onClick={() => toggleSort(unit === "bb" ? "hero_net_bb" : "hero_net")}
        />
      </div>

      {query.isLoading && <Skeleton className="h-96 w-full rounded-lg" />}
      {query.isError && (
        <QueryError
          message={query.error?.message ?? "Failed to load hands"}
          onRetry={() => query.refetch()}
        />
      )}

      {query.data && (
        <div className="grid gap-4 lg:grid-cols-[minmax(360px,0.9fr)_minmax(0,1.1fr)] xl:grid-cols-[minmax(430px,0.95fr)_minmax(0,1.05fr)]">
          <aside className="max-h-[calc(100vh-220px)] overflow-y-auto pr-1">
            <div className="space-y-4">
              {groupedHands.length === 0 && (
                <p className="rounded-lg border border-border py-8 text-center text-sm text-muted-foreground">
                  {hasFilters
                    ? "No hands match these filters"
                    : "No hands found."}
                </p>
              )}
              {groupedHands.map(([date, hands]) => (
                <section key={date} className="space-y-2">
                  <h2 className="px-1 text-sm font-semibold text-muted-foreground">{date}</h2>
                  {hands.map((hand) => (
                    <HandListCard
                      key={hand.id}
                      hand={hand}
                      selected={hand.id === selectedHandId}
                      gradeCounts={scoresByHand[hand.id]?.counts}
                      score={scoresByHand[hand.id]?.score}
                      onSelect={() => setSelectedHandId(hand.id)}
                    />
                  ))}
                </section>
              ))}
            </div>

            <div className="sticky bottom-0 mt-3 flex items-center justify-between border-t border-border bg-background/95 py-3 backdrop-blur">
              <p className="text-sm text-muted-foreground">
                Showing {sortedHands.length ? offset + 1 : 0}-{offset + sortedHands.length}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={sortedHands.length < PAGE_SIZE}
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          </aside>

          <main className="min-w-0">
            {handQuery.isLoading && <Skeleton className="h-[720px] w-full rounded-lg" />}
            {handQuery.isError && (
              <QueryError
                message={handQuery.error?.message ?? "Failed to load hand"}
                onRetry={() => handQuery.refetch()}
              />
            )}
            {handQuery.data && (
              <HandAnalysisPane hand={handQuery.data} onSolvesChange={handleSolvesChange} />
            )}
            {!selectedHandId && !handQuery.isLoading && (
              <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
                Select a hand to review.
              </div>
            )}
          </main>
        </div>
      )}
    </div>
  );
}

function SortButton({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
}) {
  const Icon = active ? (dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 hover:bg-zinc-900"
    >
      {label}
      <Icon className="h-3 w-3" />
    </button>
  );
}