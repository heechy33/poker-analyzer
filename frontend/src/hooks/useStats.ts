"use client";

import { useQuery } from "@tanstack/react-query";

import {
  fetchBiggestLosers,
  fetchLeaks,
  fetchStatsByPosition,
  fetchStatsSummary,
} from "@/lib/api";
import { sinceDaysAgo } from "@/lib/format";
import type { Timeframe } from "@/types/api";

export function useStatsSummary(timeframe: Timeframe) {
  return useQuery({
    queryKey: ["stats", timeframe],
    queryFn: () => fetchStatsSummary(timeframe),
  });
}

export function useStatsByPosition(timeframe: Timeframe) {
  return useQuery({
    queryKey: ["stats-by-position", timeframe],
    queryFn: () => fetchStatsByPosition(timeframe),
  });
}

export function useBiggestLosers(timeframe: Timeframe) {
  return useQuery({
    queryKey: ["losers", timeframe],
    queryFn: () =>
      fetchBiggestLosers({
        limit: 10,
        since: sinceDaysAgo(30),
      }),
  });
}

export function useLeaks(timeframe: Timeframe) {
  return useQuery({
    queryKey: ["leaks", timeframe],
    queryFn: () => fetchLeaks(timeframe),
  });
}
