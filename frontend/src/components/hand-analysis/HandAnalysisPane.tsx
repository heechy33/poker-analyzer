"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  ChevronDown,
  Eye,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ActionOverviewGrid } from "@/components/hand-analysis/ActionOverviewGrid";
import { CardStrip } from "@/components/hand-analysis/CardFace";
import { HandResultsTable } from "@/components/hand-analysis/HandResultsTable";
import { CoachTab } from "@/components/hand-review/CoachTab";
import { RangeGrid } from "@/components/range-grid";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  gradeStreetDecision,
  type DecisionGrade,
} from "@/lib/hand-analysis/grading";
import { computeHandScore } from "@/lib/hand-analysis/confidence";
import { formatPot } from "@/lib/format";
import { heroCardsToComboKey } from "@/lib/solver/hand-context";
import type { SolveProgress, SolverOutput } from "@/lib/solver/types";
import { useSolver } from "@/lib/solver/useSolver";
import { useAmountDisplay } from "@/stores/amount-display";
import { cn } from "@/lib/utils";
import type { HandActionOut, HandDetail, ScenarioResponse, SolverSummary, Street } from "@/types/api";

type ConfidenceTier = "high" | "medium" | "low";

const STREET_ORDER = ["preflop", "flop", "turn", "river"] as const;
type DisplayStreet = (typeof STREET_ORDER)[number];

type StreetSolveStatus = "idle" | "solving" | "ready" | "error";

export interface StreetSolveState {
  status: StreetSolveStatus;
  output: SolverOutput | null;
  scenario: ScenarioResponse | null;
  grade: DecisionGrade | null;
  error: string | null;
  progress: SolveProgress | null;
  fromCache: boolean;
  /** Depth of the decision node used for grading (0 = root). */
  nodeDepth?: number;
  /** True when the history path could not be fully reconstructed. */
  historyIncomplete?: boolean;
}

export type SolvesByStreet = Partial<Record<Street, StreetSolveState>>;

// ─── Honest labels (P1.6) ───
const CONFIDENCE_LABEL: Record<ConfidenceTier, string> = {
  high: "GTO Analysis — High Confidence",
  medium: "Approximate GTO — Medium Confidence",
  low: "Not GTO — Multiway Approximation",
};

const CONFIDENCE_BADGE_CLASS: Record<ConfidenceTier, string> = {
  high: "border-emerald-400/50 text-emerald-200",
  medium: "border-yellow-400/50 text-yellow-200",
  low: "border-amber-300/60 text-amber-100",
};

const CONFIDENCE_BG_CLASS: Record<ConfidenceTier, string> = {
  high: "bg-emerald-500/10 border-emerald-500/30",
  medium: "bg-yellow-500/10 border-yellow-500/30",
  low: "bg-amber-500/10 border-amber-500/30",
};

// ─── User guidance copy adapted from POKER_SOLVER_AUDIT_REPORT.md lines 463–489 ───
const HIGH_GUIDANCE = `This is a clean heads-up spot with precise range data and validated game state. The solver computed true GTO frequencies for these exact ranges. Use this to study optimal play.

Precision note: Quick solves use 50 iterations (1.0 bb exploitability). Action EVs may vary ±0.3–0.8 bb vs a full 200-iteration solve. Frequencies > 10% are directionally reliable. Don't split hairs over <0.5 bb EV differences.`;

const MEDIUM_RANGE_FALLBACK_GUIDANCE = `This is a heads-up spot, but we couldn't match your opponent's exact preflop range in our library. The solver used a fallback range: BB default calling range (~35% of hands). This may be very different from your opponent's actual range.

What this means: The solver ran correctly, but computed GTO against a range your opponent probably didn't have. Frequencies and EV may be significantly wrong. Use only for general concepts (e.g., "this board favors the aggressor"), not specific action frequencies.`;

const MEDIUM_SPR_GUIDANCE = `The effective stack is very shallow (SPR < 1.0). At this depth, most bet sizes force all-in, and the solver's bet tree is simplified. Push/fold decisions are directionally reliable, but exact frequencies are coarse.`;

const LOW_GUIDANCE = `⚠ This was a multiway pot (3+ players). The solver cannot compute true multiway GTO. What you see is an inaccurate heads-up approximation with tightened ranges and estimated pot size.

Do not trust these frequencies. They are mathematically valid for a different game state than your actual hand. The pot size, SPR, and opponent ranges are all approximate. Use the Coach tab for narrative analysis instead.

If you want to study this spot, focus on the action log and Coach commentary. The "Solver" numbers are not reliable for multiway pots.`;

function availableStreets(hand: HandDetail): Street[] {
  if (!hand.flop?.length) return [];
  const streets: Street[] = ["flop"];
  if (hand.turn) streets.push("turn");
  if (hand.river) streets.push("river");
  return streets;
}

function boardForStreet(hand: HandDetail, street: DisplayStreet): string[] {
  if (street === "preflop") return hand.hero_cards;
  const cards = [...(hand.flop ?? [])];
  if ((street === "turn" || street === "river") && hand.turn) cards.push(hand.turn);
  if (street === "river" && hand.river) cards.push(hand.river);
  return cards;
}

function formatActionLabel(action: HandActionOut, stakeBB: string): string {
  const bbVal = parseFloat(stakeBB);
  if (action.raise_to) {
    if (bbVal > 0) {
      const bb = parseFloat(action.raise_to) / bbVal;
      return `${action.action} to ${action.raise_to} (${Number.isFinite(bb) ? bb.toFixed(1) : "0.0"} bb)`;
    }
    return `${action.action} to ${action.raise_to}`;
  }
  if (action.amount) {
    if (bbVal > 0) {
      const bb = parseFloat(action.amount) / bbVal;
      return `${action.action} ${action.amount} (${Number.isFinite(bb) ? bb.toFixed(1) : "0.0"} bb)`;
    }
    return `${action.action} ${action.amount}`;
  }
  return action.action;
}

function isHeroAction(hand: HandDetail, heroName: string | null, action: HandActionOut): boolean {
  return action.seat === hand.hero_seat || Boolean(heroName && action.screen_name === heroName);
}

function activePlayersAtStreet(hand: HandDetail, street: DisplayStreet): number {
  const streetIndex = STREET_ORDER.indexOf(street);
  const active = new Set(hand.players.map((player) => player.seat));

  for (const action of hand.actions) {
    const actionStreet = action.street.toLowerCase() as DisplayStreet;
    if (!STREET_ORDER.includes(actionStreet)) continue;
    if (STREET_ORDER.indexOf(actionStreet) >= streetIndex) continue;
    if (action.action === "fold") active.delete(action.seat);
  }

  return active.size;
}

function contextLabel(actions: HandActionOut[], target: HandActionOut | null, hand: HandDetail): string {
  if (!target) return "Street start";
  const previous = actions
    .filter((action) => action.action_order < target.action_order && action.seat !== hand.hero_seat)
    .at(-1);

  if (!previous) return "Street start";
  if (previous.action === "check") return "vs Check";
  if (previous.action === "bet") return "vs Bet";
  if (previous.action === "raise") return "vs Raise";
  if (previous.action === "call") return "vs Call";
  return `vs ${previous.action}`;
}

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function normalizeSolverError(message: string, errorClass?: string): string {
  // Class-based messages first.
  if (errorClass === "worker_crashed") {
    return "Solver worker crashed unexpectedly. Try again — a fresh worker will be created.";
  }
  if (errorClass === "timeout") {
    return "Solver timed out. The game tree may be too large or the worker stalled. Try quick mode or retry.";
  }
  if (errorClass === "Unreachable") {
    return "Solver could not reach a solution. The scenario may be too complex or the hand history incomplete.";
  }
  if (errorClass === "wasm") {
    return `Solver engine error: ${message}`;
  }

  // Legacy string-based messages.
  if (message.includes("Solver WASM bundle is unavailable") || message.includes("build:wasm")) {
    return "Solver not built. Run cd frontend && npm run build:wasm, then restart the frontend dev server.";
  }
  if (message.includes("hero folded") || message.includes("hand has no")) {
    return `Scenario unavailable: ${message}`;
  }
  if (message.includes("fewer than two players")) {
    return "Scenario unavailable: the hand was no longer a postflop decision spot.";
  }
  if (message.includes("range is empty") || message.includes("non-empty hero")) {
    return "Scenario unavailable: preflop ranges could not be resolved for this spot.";
  }
  return message;
}

function solverSummaryFromState(state: StreetSolveState | undefined): SolverSummary | null {
  if (!state?.grade) return null;

  return {
    hero_action: state.grade.actualAction,
    solver_best_action: state.grade.bestAction,
    ev_diff_bb: state.grade.evGap,
    action_frequencies: Object.fromEntries(
      state.grade.cells.map((cell) => [cell.action, cell.frequency]),
    ),
  };
}

export function handScoreFromSolves(solves: SolvesByStreet) {
  return computeHandScore(solves);
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

function potTransparencyText(
  scenario: ScenarioResponse | null,
  hand: HandDetail,
): string | null {
  if (!scenario) return null;
  const isMultiway =
    scenario.metadata?.is_multiway_approximation === true;
  if (!isMultiway) return null;
  const modeledPot = scenario.scenario.pot_bb;
  const actualPot = hand.total_pot;
  if (modeledPot !== undefined && actualPot) {
    return `Pot modeled as ${modeledPot.toFixed(1)} bb (actual table pot: ${actualPot} bb)`;
  }
  return null;
}

/**
 * Per-street solve handler. Separated so we can call it from individual street sections.
 */
async function solveOneStreet(
  street: Street,
  hand: HandDetail,
  queryClient: ReturnType<typeof useQueryClient>,
  solve: ReturnType<typeof useSolver>["solve"],
  heroName: string | null,
  setSolves: React.Dispatch<React.SetStateAction<SolvesByStreet>>,
) {
  setSolves((previous) => ({
    ...previous,
    [street]: {
      status: "solving",
      output: null,
      scenario: null,
      grade: null,
      error: null,
      progress: null,
      fromCache: false,
    },
  }));

  try {
    const result = await solve(hand.id, street, "quick");
    const scenario =
      queryClient.getQueryData<ScenarioResponse>(["scenario", hand.id, street]) ?? null;
    const potAtHeroActionBb =
      typeof scenario?.metadata?.pot_at_hero_action_bb === "number"
        ? scenario.metadata.pot_at_hero_action_bb
        : undefined;
    const grade = gradeStreetDecision({
      actions: hand.actions,
      street,
      output: result.output,
      heroCards: hand.hero_cards,
      heroSeat: hand.hero_seat,
      heroName,
      potAtHeroActionBb,
    });

    setSolves((previous) => ({
      ...previous,
      [street]: {
        status: "ready",
        output: result.output,
        scenario,
        grade,
        error: null,
        progress: result.progress,
        fromCache: Boolean(scenario?.cached && scenario.cached_output),
        nodeDepth: result.nodeDepth,
        historyIncomplete: result.historyIncomplete,
      },
    }));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const errorClass = (error as { error_class?: string }).error_class;
    const friendly = normalizeSolverError(message, errorClass);
    setSolves((previous) => ({
      ...previous,
      [street]: {
        status: "error",
        output: null,
        scenario: null,
        grade: null,
        error: friendly,
        progress: null,
        fromCache: false,
      },
    }));
  }
}

export function HandAnalysisPane({
  hand,
  onSolvesChange,
}: {
  hand: HandDetail;
  onSolvesChange?: (handId: string, solves: SolvesByStreet) => void;
}) {
  const queryClient = useQueryClient();
  const { solve, cancel, progress } = useSolver();
  const [solves, setSolves] = useState<SolvesByStreet>({});
  const [currentStreet, setCurrentStreet] = useState<Street | null>(null);
  const [coachStreet, setCoachStreet] = useState<Street>("flop");
  // Per-street opt-in for low confidence reveals (P1.1)
  const [lowConfidenceOptIns, setLowConfidenceOptIns] = useState<Set<Street>>(new Set());

  const postflopStreets = useMemo(() => availableStreets(hand), [hand]);
  const heroName = hand.players.find((player) => player.is_hero)?.screen_name ?? null;
  const heroCombo = heroCardsToComboKey(hand.hero_cards);

  useEffect(() => {
    if (postflopStreets.length > 0 && !postflopStreets.includes(coachStreet)) {
      setCoachStreet(postflopStreets[0]);
    }
  }, [coachStreet, postflopStreets]);

  useEffect(() => {
    onSolvesChange?.(hand.id, solves);
  }, [hand.id, onSolvesChange, solves]);

  // Sync progress into the currently solving street.
  useEffect(() => {
    if (!currentStreet || !progress) return;
    setSolves((previous) => ({
      ...previous,
      [currentStreet]: {
        status: previous[currentStreet]?.status ?? "solving",
        output: previous[currentStreet]?.output ?? null,
        scenario: previous[currentStreet]?.scenario ?? null,
        grade: previous[currentStreet]?.grade ?? null,
        error: previous[currentStreet]?.error ?? null,
        fromCache: previous[currentStreet]?.fromCache ?? false,
        nodeDepth: previous[currentStreet]?.nodeDepth,
        historyIncomplete: previous[currentStreet]?.historyIncomplete,
        progress,
      },
    }));
  }, [currentStreet, progress]);

  // Clear solves when hand changes.
  useEffect(() => {
    setSolves({});
    setLowConfidenceOptIns(new Set());
  }, [hand.id]);

  // Cleanup: cancel any in-flight solve on unmount.
  useEffect(() => {
    return () => {
      void cancel();
    };
  }, [cancel]);

  const handleSolve = async (street: Street) => {
    setCurrentStreet(street);
    await solveOneStreet(street, hand, queryClient, solve, heroName, setSolves);
    setCurrentStreet(null);
  };

  const handleRetry = async (street: Street) => {
    setCurrentStreet(street);
    await solveOneStreet(street, hand, queryClient, solve, heroName, setSolves);
    setCurrentStreet(null);
  };

  const handleLowConfidenceOptIn = (street: Street) => {
    setLowConfidenceOptIns((prev) => new Set([...prev, street]));
  };

  const score = computeHandScore(solves);

  return (
    <div className="min-h-[720px] space-y-4">
      <header className="rounded-lg border border-slate-700 bg-slate-900/90 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-sm text-slate-400">
              <span>Hand {shortId(hand.id)}</span>
              <span>{new Date(hand.played_at).toLocaleString()}</span>
              <span>{hand.stake_sb}/{hand.stake_bb}</span>
              <Badge variant="outline" className="border-amber-500/50 text-amber-200">
                {hand.hero_position}
              </Badge>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <CardStrip cards={hand.hero_cards} />
              {hand.flop?.length ? <CardStrip cards={hand.flop} /> : null}
              {hand.turn ? <CardStrip cards={[hand.turn]} /> : null}
              {hand.river ? <CardStrip cards={[hand.river]} /> : null}
            </div>
          </div>

          <div className="text-right">
            <div className="text-xs font-semibold uppercase text-slate-400">Hand Score</div>
            <div className="mt-1 flex items-center gap-1">
              {Array.from({ length: 20 }).map((_, index) => (
                <span
                  key={index}
                  className={cn(
                    "h-5 w-1.5 rounded-full",
                    index < Math.round(score.score / 5) ? "bg-blue-400" : "bg-slate-700",
                  )}
                />
              ))}
            </div>
            <div className="mt-2 text-xs text-slate-400">
              {score.counts.solid} solid / {score.counts.mixed} close / {score.counts.mistake} mistakes
            </div>
            {score.excludedStreets > 0 && (
              <div className="mt-1 text-[10px] text-amber-300/80">
                {score.excludedStreets} approximate {score.excludedStreets === 1 ? "street" : "streets"} excluded
                {" — "}only high-confidence streets scored
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Insufficient data warning — §8 item 4 */}
      {score.highConfidenceStreets === 0 && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/10 p-4">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-semibold text-destructive">
              Insufficient data — no high-confidence streets to grade. Hand score requires clean heads-up spots.
            </p>
            <p className="text-xs text-muted-foreground">
              {score.excludedStreets > 0
                ? `${score.excludedStreets} street${score.excludedStreets !== 1 ? "s" : ""} excluded (low confidence, multiway, or unknown grade).`
                : "No streets were eligible for grading."}
            </p>
          </div>
        </div>
      )}

      <PlayersPanel hand={hand} />

      {STREET_ORDER.map((street) => (
        <StreetSection
          key={street}
          hand={hand}
          street={street}
          heroName={heroName}
          heroCombo={heroCombo}
          solveState={street === "preflop" ? undefined : solves[street]}
          onSolve={street !== "preflop" ? () => handleSolve(street) : undefined}
          onRetry={street !== "preflop" ? () => handleRetry(street) : undefined}
          lowConfidenceOptIn={street !== "preflop" ? lowConfidenceOptIns.has(street) : false}
          onLowConfidenceOptIn={street !== "preflop" ? () => handleLowConfidenceOptIn(street) : undefined}
        />
      ))}

      {postflopStreets.length > 0 && (
        <details className="rounded-lg border border-slate-700 bg-slate-900/80 p-4">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-slate-200">
            <ChevronDown className="h-4 w-4" />
            Coach
          </summary>
          <div className="mt-4">
            <CoachTab
              handId={hand.id}
              selectedStreet={coachStreet}
              availableStreets={postflopStreets}
              onStreetChange={setCoachStreet}
              scenarioHash={solves[coachStreet]?.scenario?.scenario_hash ?? null}
              solverSummary={solverSummaryFromState(solves[coachStreet])}
              solverConfidence={solves[coachStreet]?.scenario?.confidence ?? null}
            />
          </div>
        </details>
      )}

      <HandResultsTable hand={hand} />
    </div>
  );
}

function PlayersPanel({ hand }: { hand: HandDetail }) {
  return (
    <section className="rounded-lg border border-slate-700 bg-slate-900/80">
      <div className="flex items-center justify-between border-b border-slate-700 bg-slate-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <Users className="h-4 w-4 text-slate-300" />
          <h2 className="font-semibold text-zinc-100">Players</h2>
        </div>
        <span className="text-sm text-slate-300">{hand.players.length} Players</span>
      </div>
      <div className="grid gap-2 p-4 sm:grid-cols-2">
        {hand.players.map((player) => (
          <div key={player.seat} className="grid grid-cols-[52px_minmax(0,1fr)_auto] items-center gap-2 text-sm">
            <span className="rounded-md bg-slate-950 px-2 py-1 text-xs font-semibold text-slate-300">
              {player.position ?? `S${player.seat}`}
            </span>
            <span className="truncate text-slate-300">
              {player.is_hero ? "You" : player.screen_name}
            </span>
            <span className="rounded bg-slate-700 px-2 py-1 font-mono text-xs text-slate-100">
              {player.starting_stack}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function StreetSection({
  hand,
  street,
  heroName,
  heroCombo,
  solveState,
  onSolve,
  onRetry,
  lowConfidenceOptIn,
  onLowConfidenceOptIn,
}: {
  hand: HandDetail;
  street: DisplayStreet;
  heroName: string | null;
  heroCombo: string;
  solveState?: StreetSolveState;
  onSolve?: () => void;
  onRetry?: () => void;
  lowConfidenceOptIn?: boolean;
  onLowConfidenceOptIn?: () => void;
}) {
  const { unit } = useAmountDisplay();
  const actions = hand.actions
    .filter((action) => action.street.toLowerCase() === street)
    .sort((a, b) => a.action_order - b.action_order);

  if (actions.length === 0 && street !== "preflop") return null;

  const heroActions = actions.filter((action) => isHeroAction(hand, heroName, action));
  const overviewTarget = heroActions.at(-1) ?? null;

  const confidenceTier = solveState?.scenario?.confidence as ConfidenceTier | undefined;
  // P1.1: For low confidence, don't show overview by default (requires opt-in)
  const isLowConfidence = confidenceTier === "low";
  const isMediumConfidence = confidenceTier === "medium";
  const canShowOverview =
    street !== "preflop" &&
    solveState?.status === "ready" &&
    solveState.grade &&
    // Low confidence requires explicit opt-in
    (!isLowConfidence || lowConfidenceOptIn);
  // Dim grades on medium confidence
  const dimGrades = isMediumConfidence;

  const board = boardForStreet(hand, street);
  const potTransparency = potTransparencyText(solveState?.scenario ?? null, hand);

  const potBB = solveState?.scenario?.scenario.pot_bb;
  const potDisplay = potBB !== undefined
    ? (unit === "chips" ? `₮${hand.total_pot}` : `${potBB.toFixed(1)} bb`)
    : (unit === "chips" ? `₮${hand.total_pot}` : `${hand.total_pot}`);

  return (
    <section className="overflow-hidden rounded-lg border border-slate-700 bg-slate-900/80">
      <div className="flex items-center justify-between gap-3 border-b border-slate-700 bg-slate-800 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-20">
            <h3 className="text-lg font-semibold capitalize text-zinc-100">{street}</h3>
            <p className="text-xs text-slate-400">{activePlayersAtStreet(hand, street)} players</p>
          </div>
          <CardStrip cards={board} small />
        </div>
        <div className="shrink-0 text-right text-xs text-slate-400">
          <div>Total Pot</div>
          <div className="mt-1 rounded-md bg-slate-950 px-2 py-1 font-mono font-semibold text-amber-200">
            {potDisplay}
          </div>
          {potTransparency && (
            <div className="mt-1 text-[10px] text-amber-300/70">{potTransparency}</div>
          )}
        </div>
      </div>

      {street !== "preflop" && (
        <SolverStatus
          state={solveState}
          onSolve={onSolve}
          onRetry={onRetry}
          lowConfidenceOptIn={lowConfidenceOptIn ?? false}
          onLowConfidenceOptIn={onLowConfidenceOptIn}
        />
      )}

      {/* P1.1: Low confidence banner with Coach link (shown when not opted in) */}
      {isLowConfidence && !lowConfidenceOptIn && solveState?.status === "ready" && (
        <div className={cn("border-b border-amber-500/30 px-4 py-3", CONFIDENCE_BG_CLASS.low)}>
          <div className="flex items-start gap-3">
            <Ban className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-amber-100">Solver analysis hidden</p>
              <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
                This is a low-confidence (multiway approximation) spot. Action grades and
                solver frequencies are mathematically valid for a heads-up model, not your
                actual multiway hand. Pot size and SPR are approximate.
              </p>
              <p className="mt-1 text-xs text-amber-300/70">
                We recommend using the{" "}
                <span className="font-semibold text-amber-200">Coach tab</span>{" "}
                for narrative analysis on multiway pots.
              </p>
              <button
                type="button"
                onClick={onLowConfidenceOptIn}
                className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-amber-400/40 bg-amber-400/10 px-3 py-1.5 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-400/20"
              >
                <Eye className="h-3.5 w-3.5" />
                Show approximate analysis
              </button>
            </div>
          </div>
        </div>
      )}

      {/* P1.1: Medium confidence amber warning banner */}
      {isMediumConfidence && solveState?.status === "ready" && (
        <ConfidenceBanner tier="medium" scenario={solveState?.scenario ?? null} />
      )}

      {/* P1.1: High confidence guidance tooltip banner */}
      {confidenceTier === "high" && solveState?.status === "ready" && (
        <ConfidenceBanner tier="high" scenario={solveState?.scenario ?? null} />
      )}

      <ol className="divide-y divide-slate-800">
        {actions.map((action) => {
          const hero = isHeroAction(hand, heroName, action);
          const showOverview =
            canShowOverview && overviewTarget?.action_order === action.action_order && solveState?.grade;

          return (
            <li key={`${action.street}-${action.action_order}-${action.seat}`} className="px-4 py-2">
              <div
                className={cn(
                  "inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-sm",
                  hero
                    ? "border-emerald-300 bg-emerald-400/10 text-zinc-100"
                    : "border-slate-800 bg-slate-950/70 text-slate-300",
                )}
              >
                <span className="shrink-0 text-xs font-semibold text-slate-400">
                  {hero ? `${hand.hero_position} (You)` : action.screen_name}
                </span>
                <span className="truncate font-semibold capitalize">{formatActionLabel(action, hand.stake_bb)}</span>
              </div>

              {showOverview && solveState?.grade && (
                <ActionOverviewGrid
                  grade={solveState.grade}
                  context={contextLabel(actions, overviewTarget, hand)}
                  className={cn("mt-3", dimGrades && "opacity-60")}
                  dimmed={dimGrades}
                  confidence={confidenceTier}
                  nodeDepth={solveState.nodeDepth}
                  historyIncomplete={solveState.historyIncomplete}
                />
              )}
            </li>
          );
        })}
      </ol>

      {solveState?.status === "ready" && solveState.output && canShowOverview && (
        <details className="border-t border-slate-800 px-4 py-3">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-slate-300">
            <ChevronDown className="h-4 w-4" />
            Range map
          </summary>
          <div className="mt-3">
            <RangeGrid output={solveState.output} heroCombo={heroCombo} board={board} className="max-w-none" />
          </div>
        </details>
      )}
    </section>
  );
}

function ConfidenceBanner({
  tier,
  scenario,
}: {
  tier: ConfidenceTier;
  scenario: ScenarioResponse | null;
}) {
  if (tier === "high") {
    return (
      <details className={cn("border-b px-4 py-2", CONFIDENCE_BG_CLASS.high)}>
        <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-emerald-200">
          <ShieldCheck className="h-3.5 w-3.5" />
          GTO Analysis — High Confidence
          <span className="text-emerald-300/50">(click for details)</span>
        </summary>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-emerald-200/80">
          {HIGH_GUIDANCE}
        </p>
      </details>
    );
  }

  if (tier === "medium") {
    const subReason = subReasonFromScenario(scenario);
    const guidance =
      subReason === "borderline_spr" ? MEDIUM_SPR_GUIDANCE : MEDIUM_RANGE_FALLBACK_GUIDANCE;
    const subLabel =
      subReason === "borderline_spr"
        ? "borderline SPR"
        : "range fallback";

    // Build fallback range info if available
    const fallbackRange =
      typeof scenario?.metadata?.fallback_range_label === "string"
        ? scenario.metadata.fallback_range_label
        : null;

    return (
      <div className={cn("border-b px-4 py-3", CONFIDENCE_BG_CLASS.medium)}>
        <div className="flex items-start gap-3">
          <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0 text-yellow-300" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-yellow-100">
              Approximate GTO — Medium Confidence
              <span className="ml-2 rounded bg-yellow-500/20 px-1.5 py-0.5 text-[10px] font-normal uppercase text-yellow-300">
                {subLabel}
              </span>
            </p>
            {fallbackRange && (
              <p className="mt-1 text-xs text-yellow-300/70">
                Fallback range used:{" "}
                <span className="font-mono text-yellow-200">{fallbackRange}</span>
              </p>
            )}
            <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-yellow-200/80">
              {guidance}
            </p>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

function SolverStatus({
  state,
  onSolve,
  onRetry,
  lowConfidenceOptIn,
  onLowConfidenceOptIn,
}: {
  state?: StreetSolveState;
  onSolve?: () => void;
  onRetry?: () => void;
  lowConfidenceOptIn?: boolean;
  onLowConfidenceOptIn?: () => void;
}) {
  // Idle: show a Solve button.
  if (!state || state.status === "idle") {
    return (
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <span className="text-xs text-slate-400">Run solver for this street</span>
        <Button size="sm" variant="outline" onClick={onSolve} disabled={!onSolve}>
          <Play className="mr-1 h-3.5 w-3.5" />
          Solve
        </Button>
      </div>
    );
  }

  if (state.status === "solving") {
    const progressPct = state.progress
      ? Math.min(100, Math.round((state.progress.iterations / 50) * 100))
      : 8;
    return (
      <div className="border-b border-slate-800 px-4 py-2">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-300">
          <span className="inline-flex items-center gap-2">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Solving quick line
          </span>
          <span className="font-mono">{state.progress?.iterations ?? 0} / 50</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
          <div className="h-full bg-emerald-400 transition-all" style={{ width: `${progressPct}%` }} />
        </div>
      </div>
    );
  }

  if (state.status === "ready") {
    const confidenceTier = state.scenario?.confidence as ConfidenceTier | undefined;
    const tier = tierFromConfidence(state.scenario?.confidence);
    const label = CONFIDENCE_LABEL[tier] || "Solver ready";
    const Icon =
      tier === "high" ? ShieldCheck : tier === "medium" ? ShieldQuestion : ShieldAlert;

    // For low confidence with opt-in, show "Shown by user request" note
    const lowOptInNote = tier === "low" && lowConfidenceOptIn;

    return (
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-4 py-2 text-xs">
        <Badge
          variant="outline"
          className={cn("inline-flex items-center gap-1", CONFIDENCE_BADGE_CLASS[tier])}
          title={state.scenario?.confidence_detail || tier}
        >
          <Icon className="h-3 w-3" />
          {label}
        </Badge>
        {state.fromCache && (
          <Badge variant="outline" className="border-blue-400/50 text-blue-200">
            From cache
          </Badge>
        )}
        {lowOptInNote && (
          <Badge variant="outline" className="border-amber-400/60 text-amber-200/80">
            Shown by request
          </Badge>
        )}
        {tier !== "high" && state.scenario && (
          <span className="text-[10px] text-slate-400">
            {state.scenario.confidence_detail}
          </span>
        )}
      </div>
    );
  }

  // Error state.
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-amber-500/10 px-4 py-2 text-xs text-amber-100">
      <span className="inline-flex items-center gap-2">
        <AlertTriangle className="h-4 w-4" />
        {state.error ?? "Solver failed"}
      </span>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}