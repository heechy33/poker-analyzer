/**
 * Authenticated API client for the FastAPI backend.
 *
 * Client components: use `createClient()` from `@/lib/supabase/client` for the
 * session JWT on each request (see `authHeaders`).
 *
 * Server Components (future): read the session with `createClient()` from
 * `@/lib/supabase/server`, pass `access_token` into fetch helpers, or add a
 * server-side `apiFetchWithToken(path, token, init)` variant.
 */
import { createClient } from "@/lib/supabase/client";
import type {
  AnalysisListItem,
  AnalyzeDoneEvent,
  AnalyzeErrorEvent,
  AnalyzeHandRequest,
  AnalyzeTokenEvent,
  CompleteUploadRequest,
  FilterOptionsResponse,
  HandDetail,
  HandSummary,
  HandsListParams,
  HandsLosersParams,
  LeakTagRow,
  PositionStatsRow,
  PresignResponse,
  PresignUploadRequest,
  StatsSummaryResponse,
  Timeframe,
  UploadResponse,
} from "@/types/api";

/** Same-origin /api proxy in the browser (see next.config.js rewrites). */
function apiBaseUrl(): string {
  if (typeof window !== "undefined") {
    return "/api";
  }
  return process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
}

export type AnalyzeStreamEvent =
  | { event: "token"; data: AnalyzeTokenEvent }
  | { event: "done"; data: AnalyzeDoneEvent }
  | { event: "error"; data: AnalyzeErrorEvent };

async function authHeaders(): Promise<HeadersInit> {
  try {
    const supabase = createClient();
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const auth = await authHeaders();
  for (const [key, value] of Object.entries(auth)) {
    headers.set(key, value);
  }

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers,
    });
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    const target = `${apiBaseUrl()}${path}`;
    throw new Error(
      `Cannot reach API at ${target} (${reason}). ` +
        "Is the backend running (uvicorn on port 8000)? " +
        "If yes, try disabling ad blockers for this site — they often block /uploads URLs.",
    );
  }

  if (!response.ok) {
    const raw = await response.text();
    let detail = raw || response.statusText;
    try {
      const body = JSON.parse(raw) as { detail?: unknown };
      detail =
        typeof body.detail === "string"
          ? body.detail
          : body.detail !== undefined
            ? JSON.stringify(body.detail)
            : detail;
    } catch {
      // raw text is already in detail
    }
    throw new Error(`API ${response.status} ${response.statusText}: ${detail}`);
  }

  return (await response.json()) as T;
}

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export function fetchStatsSummary(
  timeframe: Timeframe,
  position?: string,
): Promise<StatsSummaryResponse> {
  return apiFetch<StatsSummaryResponse>(
    `/stats/summary${buildQuery({ timeframe, position })}`,
  );
}

export function fetchStatsByPosition(timeframe: Timeframe): Promise<PositionStatsRow[]> {
  return apiFetch<PositionStatsRow[]>(`/stats/by-position${buildQuery({ timeframe })}`);
}

export function fetchLeaks(timeframe: Timeframe): Promise<LeakTagRow[]> {
  return apiFetch<LeakTagRow[]>(`/stats/leaks${buildQuery({ timeframe })}`);
}

export function fetchHands(params: HandsListParams = {}): Promise<HandSummary[]> {
  return apiFetch<HandSummary[]>(
    `/hands${buildQuery({
      limit: params.limit,
      offset: params.offset,
      order: params.order,
      position: params.position,
      since: params.since,
      only_losses: params.only_losses,
      table_format: params.table_format,
      stakes: params.stakes,
    })}`,
  );
}

export function fetchBiggestLosers(params: HandsLosersParams = {}): Promise<HandSummary[]> {
  return apiFetch<HandSummary[]>(
    `/hands/losers${buildQuery({
      limit: params.limit,
      since: params.since,
      position: params.position,
      table_format: params.table_format,
      stakes: params.stakes,
    })}`,
  );
}

export function fetchFilterOptions(): Promise<FilterOptionsResponse> {
  return apiFetch<FilterOptionsResponse>("/hands/filter-options");
}

export function fetchHand(handId: string): Promise<HandDetail> {
  return apiFetch<HandDetail>(`/hands/${handId}`);
}

export function presignUpload(body: PresignUploadRequest): Promise<PresignResponse> {
  return apiFetch<PresignResponse>("/uploads/presign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function completeUpload(
  uploadId: string,
  body?: CompleteUploadRequest,
): Promise<UploadResponse> {
  return apiFetch<UploadResponse>(`/uploads/${uploadId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export function fetchUpload(uploadId: string): Promise<UploadResponse> {
  return apiFetch<UploadResponse>(`/uploads/${uploadId}`);
}

export function fetchHandAnalyses(handId: string): Promise<AnalysisListItem[]> {
  return apiFetch<AnalysisListItem[]>(`/hands/${handId}/analyses`);
}

/**
 * Minimal SSE parser for POST /hands/{id}/analyze?stream=true.
 * Emits parsed events: token | done | error.
 */
export async function streamAnalyzeHand(
  handId: string,
  body: AnalyzeHandRequest,
  onEvent: (event: AnalyzeStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  });
  const auth = await authHeaders();
  for (const [key, value] of Object.entries(auth)) {
    headers.set(key, value);
  }

  const response = await fetch(`${apiBaseUrl()}/hands/${handId}/analyze?stream=true`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    const raw = await response.text();
    let detail = raw || response.statusText;
    try {
      const errBody = JSON.parse(raw) as { detail?: unknown };
      detail =
        typeof errBody.detail === "string"
          ? errBody.detail
          : errBody.detail !== undefined
            ? JSON.stringify(errBody.detail)
            : detail;
    } catch {
      // raw text is already in detail
    }
    throw new Error(`API ${response.status} ${response.statusText}: ${detail}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("No response body for SSE stream");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];

  const flushEvent = () => {
    if (!eventName && dataLines.length === 0) {
      return;
    }
    const raw = dataLines.join("\n");
    dataLines = [];
    const name = eventName || "message";
    eventName = "";

    try {
      const parsed = JSON.parse(raw) as AnalyzeTokenEvent | AnalyzeDoneEvent | AnalyzeErrorEvent;
      if (name === "token") {
        onEvent({ event: "token", data: parsed as AnalyzeTokenEvent });
      } else if (name === "done") {
        onEvent({ event: "done", data: parsed as AnalyzeDoneEvent });
      } else if (name === "error") {
        onEvent({ event: "error", data: parsed as AnalyzeErrorEvent });
      }
    } catch {
      onEvent({ event: "error", data: { message: "Failed to parse SSE payload" } });
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      flushEvent();
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line === "") {
        flushEvent();
        continue;
      }
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
  }
}
