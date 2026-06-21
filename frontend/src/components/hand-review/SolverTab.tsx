"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Eye,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { RangeGrid } from "@/components/range-grid";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { actionColor } from "@/lib/range-grid/colors";
import { heroCardsToComboKey, inferHeroActionOnStreet } from "@/lib/solver/hand-context";
import { computeSolverSummary } from "@/lib/solver/summary";
import type { SolveMode, SolverOutput } from "@/lib/solver/types";
import { useSolver } from "@/lib/solver/useSolver";
import { cn } from "@/lib/utils";
import type { HandDetail, ScenarioResponse, SolverSummary, Street } from "@/types/api";

type ConfidenceTier = "high" | "medium" | "low";

interface SolverTabProps {
  hand: HandDetail;
  selectedStreet: Street;
  availableStreets: Street[];
  onStreetChange: (street: Street) => void;
  onSolved: (payload: {
    scenarioHash: string | null;
    solverSummary: SolverSummary | null;
    output: SolverOutput | null;
  }) => void;
}

const MAX_ITERATIONS: Record<SolveMode, number> = {
  quick: 50,
  full: 200,
};

// ─── Honest labels (P1.6) — matches HandAnalysisPane.tsx ───
const CONFIDENCE_LABEL: Record<ConfidenceTier, string> = {
  high: "GTO Analysis — High Confidence",
  medium: "Approximate GTO — Medium Confidence",
  low: "Not GTO — Multiway Approximation",
};

function confidenceBadgeClass(tier: ConfidenceTier): string {
  if (tier === "high") return "border-emerald-400/50 text-emerald-200";
  if (tier === "medium") return "border-yellow-400/50 text-yellow-200";
  return "border-amber-300/60 text-amber-100";
}

function tierFromConfidence(confidence: string | undefined): ConfidenceTier {
  if (confidence === "high") return "high";
  if (confidence === "medium") return "medium";
  return "low";
}

function subReasonFromScenario(scenario: ScenarioResponse | null): string | null {
  if (!scenario) return null;
  if (scenario.confidence !== "medium") return null;
  const spr = typeof scenario.metadata?.spr === "number" ? scenario.metadata.spr : null;
  if (spr !== null && spr < 1.0) return "borderline_spr";
  if (scenario.confidence_reasons.some((r) => r.includes("fallback"))) return "range_fallback";
  return "range_fallback";
}

function AlertBox({
  tone = "destructive",
  children,
}: {
  tone?: "destructive" | "amber";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3 text-sm",
        tone === "amber"
          ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
          : "border-destructive/40 bg-destructive/10 text-destructive",
      )}
    >
      {children}
    </div>
  );
}

function boardForStreet(hand: HandDetail, street: Street): string[] {
  const cards = [...(hand.flop ?? [])];
  if ((street === "turn" || street === "river") && hand.turn) cards.push(hand.turn);
  if (street === "river" && hand.river) cards.push(hand.river);
  return cards;
}

function formatActionLabel(action: string): string {
  return action.replaceAll("_", " ");
}

export function SolverTab({
  hand,
  selectedStreet,
  availableStreets,
  onStreetChange,
  onSolved,
}: SolverTabProps) {
  const queryClient = useQueryClient();
  const { solve, cancel, progress, error, isSolving } = useSolver();
  const [output, setOutput] = useState<SolverOutput | null>(null);
  const [scenario, setScenario] = useState<ScenarioResponse | null>(null);
  const [summary, setSummary] = useState<SolverSummary | null>(null);
  const [mode, setMode] = useState<SolveMode>("quick");
  const [loadedFromCache, setLoadedFromCache] = useState(false);
  const [solveError, setSolveError] = useState<string | null>(null);
  const [showConfidence, setShowConfidence] = useState(false);
  // P1.1: Low confidence opt-in
  const [lowConfidenceOptIn, setLowConfidenceOptIn] = useState(false);

  const heroCombo = useMemo(() => heroCardsToComboKey(hand.hero_cards), [hand.hero_cards]);
  const heroName = hand.players.find((player) => player.is_hero)?.screen_name ?? null;
  const board = boardForStreet(hand, selectedStreet);
  const maxIterations = MAX_ITERATIONS[mode];
  const progressPct = progress
    ? Math.min(100, Math.round((progress.iterations / maxIterations) * 100))
    : 0;

  useEffect(() => {
    setOutput(null);
    setScenario(null);
    setSummary(null);
    setLoadedFromCache(false);
    setSolveError(null);
    setShowConfidence(false);
    setLowConfidenceOptIn(false);
    onSolved({ scenarioHash: null, solverSummary: null, output: null });
  }, [hand.id, selectedStreet, onSolved]);

  if (availableStreets.length === 0) {
    return (
      <AlertBox tone="amber">
        Hand ended preflop. Solver review starts once a flop exists.
      </AlertBox>
    );
  }

  async function runSolve(nextMode: SolveMode, targetStreet = selectedStreet) {
    setMode(nextMode);
    setSolveError(null);
    setLoadedFromCache(false);
    setLowConfidenceOptIn(false);

    try {
      const result = await solve(hand.id, targetStreet, nextMode);
      const nextScenario =
        queryClient.getQueryData<ScenarioResponse>(["scenario", hand.id, targetStreet]) ?? null;
      const actualAction = inferHeroActionOnStreet(hand.actions, targetStreet, result.output.actions, {
        heroSeat: hand.hero_seat,
        heroName,
      });
      const nextSummary =
        heroCombo && actualAction
          ? computeSolverSummary({
              heroCombo,
              actualAction,
              output: result.output,
            })
          : null;

      setOutput(result.output);
      setScenario(nextScenario);
      setSummary(nextSummary);
      setLoadedFromCache(Boolean(nextScenario?.cached && nextScenario.cached_output));
      onSolved({
        scenarioHash: nextScenario?.scenario_hash ?? null,
        solverSummary: nextSummary,
        output: result.output,
      });
    } catch (solveFailure) {
      const message = solveFailure instanceof Error ? solveFailure.message : String(solveFailure);
      setSolveError(message);
    }
  }

  function handleStreetChange(street: Street) {
    onStreetChange(street);
    void runSolve(mode, street);
  }

  const evDiff = summary?.ev_diff_bb;

  const isMultiwayApprox =
    scenario?.metadata?.is_multiway_approximation === true;
  const villainScreenName =
    typeof scenario?.metadata?.villain_screen_name === "string"
      ? scenario.metadata.villain_screen_name
      : null;
  const alivePlayers =
    typeof scenario?.metadata?.alive_players === "number"
      ? scenario.metadata.alive_players
      : null;

  const confidenceTier = tierFromConfidence(scenario?.confidence);
  const subReason = subReasonFromScenario(scenario);
  const fallbackRange =
    typeof scenario?.metadata?.fallback_range_label === "string"
      ? scenario.metadata.fallback_range_label
      : null;

  // P1.1: Low confidence — hide output by default, require opt-in
  const isLowConfidence = confidenceTier === "low";
  const isMediumConfidence = confidenceTier === "medium";
  const showSolverOutput =
    !isLowConfidence || lowConfidenceOptIn;

  const ConfidenceIcon =
    confidenceTier === "high"
      ? ShieldCheck
      : confidenceTier === "medium"
        ? ShieldQuestion
        : ShieldAlert;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-zinc-950/50 p-4">
        <label className="space-y-1.5 text-sm">
          <span className="text-muted-foreground">Street</span>
          <select
            value={selectedStreet}
            onChange={(event) => handleStreetChange(event.target.value as Street)}
            disabled={isSolving}
            className="flex h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            {availableStreets.map((street) => (
              <option key={street} value={street}>
                {street}
              </option>
            ))}
          </select>
        </label>

        <Button onClick={() => runSolve("quick")} disabled={isSolving}>
          {isSolving && mode === "quick" && <Loader2 className="h-4 w-4 animate-spin" />}
          Quick solve
        </Button>
        <Button variant="outline" onClick={() => runSolve("full")} disabled={isSolving}>
          {isSolving && mode === "full" && <Loader2 className="h-4 w-4 animate-spin" />}
          Full solve
        </Button>
        <Button variant="ghost" onClick={() => void cancel()} disabled={!isSolving}>
          Cancel
        </Button>

        {scenario && (
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowConfidence((v) => !v)}
              className="cursor-pointer"
            >
              <Badge
                variant="outline"
                className={cn(
                  "inline-flex items-center gap-1",
                  confidenceBadgeClass(confidenceTier),
                )}
              >
                <ConfidenceIcon className="h-3 w-3" />
                {CONFIDENCE_LABEL[confidenceTier]}
              </Badge>
            </button>
            {showConfidence && (
              <div className="absolute left-0 top-full z-20 mt-1 w-80 rounded-lg border border-zinc-600 bg-zinc-900 p-3 text-xs text-zinc-200 shadow-xl">
                <p className="font-medium text-zinc-100">{scenario.confidence_detail}</p>
                {scenario.confidence_reasons.length > 0 && (
                  <ul className="mt-2 list-inside list-disc space-y-1 text-zinc-400">
                    {scenario.confidence_reasons.map((reason) => (
                      <li key={reason} className="font-mono text-[11px]">
                        {reason}
                      </li>
                    ))}
                  </ul>
                )}
                {isMultiwayApprox && villainScreenName && alivePlayers && (
                  <p className="mt-2 text-amber-300/80">
                    {alivePlayers}-way pot modeled as heads-up vs {villainScreenName}.
                    Strategy is approximate, not true multiway GTO.
                  </p>
                )}
                {subReason === "range_fallback" && fallbackRange && (
                  <p className="mt-2 text-yellow-300/80">
                    Fallback range used:{" "}
                    <span className="font-mono text-yellow-200">{fallbackRange}</span>
                  </p>
                )}
              </div>
            )}
          </div>
        )}
        {loadedFromCache && (
          <Badge variant="outline" className="border-emerald-500/50 text-emerald-300">
            Loaded from cache
          </Badge>
        )}
        {isLowConfidence && lowConfidenceOptIn && (
          <Badge variant="outline" className="border-amber-400/60 text-amber-200/80">
            Shown by request
          </Badge>
        )}
      </div>

      {/* P1.1: Low confidence — hide solver output behind opt-in banner */}
      {isLowConfidence && !lowConfidenceOptIn && scenario && (
        <AlertBox tone="amber">
          <div className="flex items-start gap-3">
            <Ban className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold">
                Solver analysis hidden — Not GTO (Multiway Approximation)
              </p>
              <p className="mt-1">
                This was a multiway pot (3+ players). The solver cannot compute true
                multiway GTO. The frequencies and EV below are an inaccurate heads-up
                approximation with tightened ranges and estimated pot size.
              </p>
              <p className="mt-1">
                We recommend using the <span className="font-semibold text-white">Coach tab</span>{" "}
                for narrative analysis on multiway pots.
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setLowConfidenceOptIn(true)}
                className="mt-3"
              >
                <Eye className="mr-1.5 h-3.5 w-3.5" />
                Show approximate analysis
              </Button>
            </div>
          </div>
        </AlertBox>
      )}

      {/* P1.1: Medium confidence amber warning banner */}
      {isMediumConfidence && scenario && (
        <AlertBox tone="amber">
          <div className="flex items-start gap-3">
            <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold">
                Approximate GTO — Medium Confidence
                <span className="ml-2 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-normal uppercase">
                  {subReason === "borderline_spr" ? "borderline SPR" : "range fallback"}
                </span>
              </p>
              {subReason === "borderline_spr" ? (
                <p className="mt-1 text-amber-200/80">
                  {"The effective stack is very shallow (SPR < 1.0). At this depth, most"}
                  bet sizes force all-in, and the solver's bet tree is simplified.
                  Push/fold decisions are directionally reliable, but exact frequencies
                  are coarse.
                </p>
              ) : (
                <>
                  {fallbackRange && (
                    <p className="mt-1">
                      Fallback range:{" "}
                      <span className="font-mono">{fallbackRange}</span>
                    </p>
                  )}
                  <p className="mt-1 text-amber-200/80">
                    We couldn't match your opponent's exact preflop range. The solver
                    used a fallback range that may differ significantly from your
                    opponent's actual range. Frequencies and EV may be wrong.
                    Use only for general concepts, not specific action frequencies.
                  </p>
                </>
              )}
            </div>
          </div>
        </AlertBox>
      )}

      {/* P1.6: High confidence guidance banner (collapsible) */}
      {confidenceTier === "high" && scenario && (
        <details className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-emerald-200">
            <ShieldCheck className="h-4 w-4" />
            GTO Analysis — High Confidence
            <span className="text-emerald-300/50">(click for details)</span>
          </summary>
          <p className="mt-2 text-xs leading-relaxed text-emerald-200/80">
            This is a clean heads-up spot with precise range data and validated game state.
            The solver computed true GTO frequencies for these exact ranges. Use this to
            study optimal play.
          </p>
          <p className="mt-1 text-xs text-emerald-300/70">
            Precision note: Quick solves use 50 iterations (1.0 bb exploitability). Action
             EVs may vary ±0.3–0.8 bb vs a full 200-iteration solve. Frequencies {" > 10%"}
             {"are directionally reliable. Don't split hairs over <0.5 bb EV differences."}
          </p>
        </details>
      )}

      {/* P2.2: HU pot transparency for multiway */}
      {isMultiwayApprox && scenario && (
        <AlertBox tone="amber">
          {scenario.scenario.pot_bb !== undefined && hand.total_pot ? (
            <>
              Pot modeled as {scenario.scenario.pot_bb.toFixed(1)} bb (actual table pot:{" "}
              {hand.total_pot} bb). Multiway dead money from folded players is excluded,
              making the pot smaller and SPR larger than reality.
            </>
          ) : (
            "Multiway pot collapsed to heads-up for CFR solving. Pot size and SPR are approximate."
          )}
        </AlertBox>
      )}

      {(solveError || error) && (
        <AlertBox>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{solveError ?? error}</span>
            <Button variant="outline" size="sm" onClick={() => runSolve(mode)}>
              Retry
            </Button>
          </div>
        </AlertBox>
      )}

      {(isSolving || progress) && (
        <div className="rounded-lg border border-border bg-zinc-950/50 p-4">
          <div className="mb-2 flex items-center justify-between text-sm">
            <span>
              {progress?.iterations ?? 0} / {maxIterations} iterations
            </span>
            <span className="font-mono text-muted-foreground">
              {progress?.exploitability_bb?.toFixed(3) ?? "0.000"} bb
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
            <div
              className="h-full bg-emerald-500 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {mode === "full" && (
            <p className="mt-2 text-xs text-muted-foreground">{"Target <= 0.5 bb"}</p>
          )}
        </div>
      )}

      {output && showSolverOutput && (
        <div className={cn("space-y-4", isMediumConfidence && "opacity-90")}>
          {evDiff !== null && evDiff !== undefined && (
            <div
              className={cn(
                "rounded-lg border px-4 py-3 text-sm",
                confidenceTier === "high"
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                  : "border-yellow-500/40 bg-yellow-500/10 text-yellow-100",
              )}
            >
              <p>
                Hero combo {heroCombo}: actual {summary?.hero_action}, solver prefers{" "}
                {summary?.solver_best_action}. EV difference {evDiff.toFixed(2)} bb.
              </p>
              {isMediumConfidence && (
                <p className="mt-1 text-xs text-yellow-300/70">
                  Approximate GTO — EV may be ±0.5+ bb off due to range or SPR uncertainty.
                </p>
              )}
            </div>
          )}

          {!summary && (
            <AlertBox tone="amber">
              Solver output loaded, but the hero action could not be matched exactly to the solver
              action labels.
            </AlertBox>
          )}

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_220px]">
            <RangeGrid output={output} heroCombo={heroCombo} board={board} className="max-w-none" />
            <div className="h-fit rounded-lg border border-border bg-zinc-950/50 p-4">
              <h3 className="mb-3 text-sm font-semibold">Aggregate strategy</h3>
              <div className="space-y-3">
                {output.actions.map((action) => {
                  const frequency = output.aggregate_frequencies[action] ?? 0;
                  return (
                    <div key={action} className="space-y-1">
                      <div className="flex justify-between gap-3 text-xs">
                        <span>{formatActionLabel(action)}</span>
                        <span className="font-mono text-muted-foreground">
                          {(frequency * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
                        <div
                          className="h-full"
                          style={{
                            width: `${Math.min(100, frequency * 100)}%`,
                            backgroundColor: actionColor(action, output.actions),
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}