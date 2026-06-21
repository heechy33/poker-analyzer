"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchHandAnalyses, streamAnalyzeHand } from "@/lib/api";
import { humanizeLeakTag } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AnalysisListItem, SolverSummary, Street } from "@/types/api";

interface CoachTabProps {
  handId: string;
  selectedStreet: Street;
  availableStreets: Street[];
  onStreetChange: (street: Street) => void;
  scenarioHash: string | null;
  solverSummary: SolverSummary | null;
  solverConfidence?: string | null;
}

function formatAnalysisDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function AlertBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
      {children}
    </div>
  );
}

function solverConfidenceBadgeClass(tier: string): string {
  if (tier === "high") return "border-emerald-500/50 text-emerald-300";
  if (tier === "medium") return "border-yellow-500/50 text-yellow-300";
  return "border-amber-500/50 text-amber-300";
}

export function CoachTab({
  handId,
  selectedStreet,
  availableStreets,
  onStreetChange,
  scenarioHash,
  solverSummary,
  solverConfidence,
}: CoachTabProps) {
  const abortRef = useRef<AbortController | null>(null);
  const [analysis, setAnalysis] = useState("");
  const [leakTags, setLeakTags] = useState<string[]>([]);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [cached, setCached] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const analyses = useQuery({
    queryKey: ["hand-analyses", handId],
    queryFn: () => fetchHandAnalyses(handId),
  });

  async function explain() {
    const controller = new AbortController();
    abortRef.current = controller;
    setAnalysis("");
    setLeakTags([]);
    setAnalysisId(null);
    setCached(false);
    setError(null);
    setIsStreaming(true);

    try {
      await streamAnalyzeHand(
        handId,
        {
          street: selectedStreet,
          scenario_hash: scenarioHash,
          solver_summary: solverSummary,
        },
        (event) => {
          if (event.event === "token") {
            setAnalysis((current) => current + event.data.text);
          } else if (event.event === "done") {
            setLeakTags(event.data.leak_tags);
            setAnalysisId(event.data.analysis_id);
            setCached(event.data.cached);
          } else if (event.event === "error") {
            setError(event.data.message);
          }
        },
        controller.signal,
      );
      await analyses.refetch();
    } catch (streamError) {
      if (controller.signal.aborted) {
        setError("Analysis cancelled.");
      } else {
        setError(streamError instanceof Error ? streamError.message : String(streamError));
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }

  function cancel() {
    abortRef.current?.abort();
  }

  function loadPrevious(item: AnalysisListItem) {
    abortRef.current?.abort();
    setAnalysis(item.analysis);
    setLeakTags(item.leak_tags);
    setAnalysisId(item.id);
    setCached(true);
    setError(null);
    setIsStreaming(false);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-zinc-950/50 p-4">
          <label className="space-y-1.5 text-sm">
            <span className="text-muted-foreground">Street</span>
            <select
              value={selectedStreet}
              onChange={(event) => onStreetChange(event.target.value as Street)}
              disabled={isStreaming || availableStreets.length === 0}
              className="flex h-9 rounded-md border border-input bg-background px-3 text-sm"
            >
              {(availableStreets.length > 0 ? availableStreets : ["flop"]).map((street) => (
                <option key={street} value={street}>
                  {street}
                </option>
              ))}
            </select>
          </label>

          <Button onClick={explain} disabled={isStreaming}>
            {isStreaming && <Loader2 className="h-4 w-4 animate-spin" />}
            Explain
          </Button>
          <Button variant="ghost" onClick={cancel} disabled={!isStreaming}>
            Cancel
          </Button>
          {solverSummary ? (
            <Badge
              variant="outline"
              className={solverConfidenceBadgeClass(solverConfidence ?? "low")}
            >
              Solver data:{" "}
              {solverConfidence === "high"
                ? "high"
                : solverConfidence === "medium"
                  ? "medium"
                  : "low"}{" "}
              confidence
            </Badge>
          ) : (
            <Badge variant="outline" className="border-zinc-600 text-zinc-300">
              LLM-only
            </Badge>
          )}
          {cached && (
            <Badge variant="outline" className="border-emerald-500/50 text-emerald-300">
              Cached analysis
            </Badge>
          )}
        </div>

        {error && <AlertBox>{error}</AlertBox>}

        <div
          className={cn(
            "min-h-72 whitespace-pre-wrap rounded-lg border border-border bg-zinc-950 p-4 text-sm leading-6 text-zinc-100",
            !analysis && "text-muted-foreground",
          )}
        >
          {analysis || "Coach analysis will stream here."}
        </div>

        {leakTags.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {leakTags.map((tag) => (
              <Badge key={tag} variant="outline" className="border-amber-500/40 text-amber-200">
                {humanizeLeakTag(tag)}
              </Badge>
            ))}
          </div>
        )}

        {analysisId && (
          <p className="text-xs text-muted-foreground">Analysis id: {analysisId}</p>
        )}
      </div>

      <aside className="h-fit rounded-lg border border-border bg-zinc-950/50 p-4">
        <h3 className="mb-3 text-sm font-semibold">Previous analyses</h3>
        {analyses.isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}
        {analyses.isError && (
          <p className="text-sm text-destructive">
            {analyses.error?.message ?? "Failed to load analyses"}
          </p>
        )}
        {analyses.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">No saved analysis yet.</p>
        )}
        <div className="space-y-2">
          {analyses.data?.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => loadPrevious(item)}
              className="w-full rounded-md border border-border px-3 py-2 text-left text-sm transition-colors hover:bg-zinc-900"
            >
              <span className="block truncate font-medium">{formatAnalysisDate(item.created_at)}</span>
              <span className="mt-1 block truncate text-xs text-muted-foreground">
                {item.leak_tags.length > 0
                  ? item.leak_tags.map(humanizeLeakTag).join(", ")
                  : "No tags"}
              </span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}