import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import type {
  ScenarioResponse,
  SolverRunCreate,
  SolverRunResponse,
  Street,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

let supabase: SupabaseClient | null = null;

function getSupabaseClient(): SupabaseClient | null {
  if (supabase) {
    return supabase;
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) {
    return null;
  }

  supabase = createClient(url, key);
  return supabase;
}

async function authHeaders(): Promise<HeadersInit> {
  const client = getSupabaseClient();
  if (!client) {
    return {};
  }

  const { data } = await client.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const auth = await authHeaders();
  for (const [key, value] of Object.entries(auth)) {
    headers.set(key, value);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      detail = await response.text();
    }
    throw new Error(`API ${response.status} ${response.statusText}: ${detail}`);
  }

  return (await response.json()) as T;
}

export function fetchScenario(handId: string, street: Street): Promise<ScenarioResponse> {
  const params = new URLSearchParams({ street });
  return apiFetch<ScenarioResponse>(`/hands/${handId}/scenario?${params.toString()}`);
}

export function postSolverRun(body: SolverRunCreate): Promise<SolverRunResponse> {
  return apiFetch<SolverRunResponse>("/solver-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

