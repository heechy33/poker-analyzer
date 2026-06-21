"use client";

/**
 * Manual demo script:
 * 1. Upload fixture hands (T13).
 * 2. Dashboard -> biggest loser -> Solve (full) on flop -> wait for grid.
 * 3. Explain -> watch stream -> tags appear.
 * 4. Re-open same hand/street -> cache hit + cached analysis.
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CoachTab } from "@/components/hand-review/CoachTab";
import { ReplayerTab } from "@/components/hand-review/ReplayerTab";
import { SolverTab } from "@/components/hand-review/SolverTab";
import { NetAmount } from "@/components/NetAmount";
import { PositionBadge } from "@/components/PositionBadge";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fetchHand } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useHandReviewStore, type HandReviewTab } from "@/stores/hand-review";
import type { SolverOutput } from "@/lib/solver/types";
import type { HandDetail, SolverSummary, Street } from "@/types/api";

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function boardCards(hand: HandDetail): { street: Street; cards: string[] }[] {
  const streets: { street: Street; cards: string[] }[] = [];
  if (hand.flop?.length) streets.push({ street: "flop", cards: hand.flop });
  if (hand.turn) streets.push({ street: "turn", cards: [hand.turn] });
  if (hand.river) streets.push({ street: "river", cards: [hand.river] });
  return streets;
}

function availableStreets(hand: HandDetail | undefined): Street[] {
  if (!hand?.flop?.length) return [];
  const streets: Street[] = ["flop"];
  if (hand.turn) streets.push("turn");
  if (hand.river) streets.push("river");
  return streets;
}

function HeaderMeta({ hand }: { hand: HandDetail }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>{formatDate(hand.played_at)}</span>
        <span>|</span>
        <span>{hand.table_name}</span>
        <span>|</span>
        <span>
          {hand.stake_sb}/{hand.stake_bb}
        </span>
        <PositionBadge position={hand.hero_position} />
        <NetAmount chips={hand.hero_net} bb={hand.hero_net_bb} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {hand.hero_cards.map((card) => (
          <Badge key={card} variant="outline" className="font-mono text-sm">
            {card}
          </Badge>
        ))}
        {boardCards(hand).map(({ street, cards }) => (
          <span key={street} className="inline-flex items-center gap-1">
            <span className="text-xs uppercase text-muted-foreground">{street}</span>
            {cards.map((card) => (
              <Badge key={`${street}-${card}`} variant="secondary" className="font-mono">
                {card}
              </Badge>
            ))}
          </span>
        ))}
      </div>
    </div>
  );
}

export function HandReviewModal() {
  const {
    handId,
    activeTab,
    selectedStreet,
    closeHandReview,
    setTab,
    setStreet,
  } = useHandReviewStore();
  const [scenarioHash, setScenarioHash] = useState<string | null>(null);
  const [solverSummary, setSolverSummary] = useState<SolverSummary | null>(null);

  const handQuery = useQuery({
    queryKey: ["hand", handId],
    queryFn: () => fetchHand(handId as string),
    enabled: Boolean(handId),
  });

  const hand = handQuery.data;
  const streets = useMemo(() => availableStreets(hand), [hand]);
  const solverDisabled = streets.length === 0;

  useEffect(() => {
    if (streets.length > 0 && !streets.includes(selectedStreet)) {
      setStreet(streets[0]);
    }
  }, [selectedStreet, setStreet, streets]);

  useEffect(() => {
    if (!handId) {
      setScenarioHash(null);
      setSolverSummary(null);
    }
  }, [handId]);

  const onSolved = useCallback(
    (payload: {
      scenarioHash: string | null;
      solverSummary: SolverSummary | null;
      output: SolverOutput | null;
    }) => {
      setScenarioHash(payload.scenarioHash);
      setSolverSummary(payload.solverSummary);
    },
    [],
  );

  function handleOpenChange(open: boolean) {
    if (!open) closeHandReview();
  }

  function handleTabChange(value: string) {
    setTab(value as HandReviewTab);
  }

  return (
    <Dialog open={Boolean(handId)} onOpenChange={handleOpenChange}>
      <DialogContent className="flex h-[90vh] w-[calc(100vw-1rem)] max-w-5xl grid-rows-[auto_minmax(0,1fr)] flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-6 py-5 pr-12">
          <DialogTitle>Hand {hand ? shortId(hand.id) : ""}</DialogTitle>
          <DialogDescription className="sr-only">
            Review hand action, solver output, and coach analysis.
          </DialogDescription>
          {hand ? <HeaderMeta hand={hand} /> : <Skeleton className="mt-3 h-12 w-full" />}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {handQuery.isLoading && <Skeleton className="h-72 w-full" />}
          {handQuery.isError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {handQuery.error?.message ?? "Failed to load hand"}
            </div>
          )}

          {hand && (
            <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <TabsList>
                  <TabsTrigger value="replayer">Replayer</TabsTrigger>
                  <TabsTrigger value="solver" disabled={solverDisabled}>
                    Solver
                  </TabsTrigger>
                  <TabsTrigger value="coach">Coach</TabsTrigger>
                </TabsList>
                {solverDisabled && (
                  <span className="text-sm text-muted-foreground">Hand ended preflop.</span>
                )}
              </div>

              <TabsContent value="replayer">
                <ReplayerTab hand={hand} />
              </TabsContent>

              <TabsContent value="solver">
                <SolverTab
                  hand={hand}
                  selectedStreet={selectedStreet}
                  availableStreets={streets}
                  onStreetChange={setStreet}
                  onSolved={onSolved}
                />
              </TabsContent>

              <TabsContent value="coach">
                <CoachTab
                  handId={hand.id}
                  selectedStreet={selectedStreet}
                  availableStreets={streets}
                  onStreetChange={setStreet}
                  scenarioHash={scenarioHash}
                  solverSummary={solverSummary}
                />
              </TabsContent>
            </Tabs>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}