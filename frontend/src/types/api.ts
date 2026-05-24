export interface HandPlayerOut {
  seat: number;
  screen_name: string;
  position: string | null;
  starting_stack: string;
  is_hero: boolean;
  final_cards: string[] | null;
}

export interface HandActionOut {
  street: string;
  action_order: number;
  seat: number;
  screen_name: string;
  action: string;
  amount: string | null;
  raise_to: string | null;
  is_all_in: boolean;
}

export interface HandSummary {
  id: string;
  coinpoker_hand_id: number;
  played_at: string;
  table_name: string;
  table_size: number;
  stake_sb: string;
  stake_bb: string;
  hero_position: string;
  hero_cards: string[];
  hero_net: string;
  hero_net_bb: string;
  went_to_showdown: boolean;
  total_pot: string;
}

export interface HandDetail extends HandSummary {
  upload_id: string;
  session_id: string | null;
  button_seat: number;
  hero_seat: number;
  flop: string[] | null;
  turn: string | null;
  river: string | null;
  rake: string;
  splash_fee: string;
  hero_invested: string;
  hero_collected: string;
  won_at_showdown: boolean | null;
  flags: Record<string, unknown>;
  raw_text: string | null;
  players: HandPlayerOut[];
  actions: HandActionOut[];
}

export interface StatsSummaryResponse {
  hands_count: number;
  vpip_pct: number;
  pfr_pct: number;
  three_bet_pct: number;
  wtsd_pct: number;
  wsd_pct: number;
  bb_per_100: number;
}

export interface PositionStatsRow {
  position: string;
  hands: number;
  vpip_pct: number;
  pfr_pct: number;
  three_bet_pct: number;
  wtsd_pct: number;
  wsd_pct: number;
  bb_per_100: number;
}

/** @deprecated Use StatsSummaryResponse. Kept for backward compatibility. */
export type StatsResponse = StatsSummaryResponse;

export type Timeframe = "lifetime" | "7d" | "30d";

export interface UploadResponse {
  id: string;
  filename: string;
  status: string;
  hand_count?: number | null;
  error_message?: string | null;
  bytes?: number | null;
  uploaded_at?: string | null;
}

export interface PresignResponse extends UploadResponse {
  signed_url?: string | null;
  token?: string | null;
  path?: string | null;
  deduplicated?: boolean;
}

export interface PresignUploadRequest {
  filename: string;
  bytes?: number;
  sha256: string;
}

export interface CompleteUploadRequest {
  raw_content?: string;
}

export type Street = "flop" | "turn" | "river";

export interface ScenarioResult {
  hand_id: string;
  street: Street;
  ev_bb: number | null;
  strategy: Record<string, number>;
  message: string;
  confidence: string;
}

export interface SolverRunCreate {
  hand_id?: string;
  street: string;
  scenario_hash: string;
  solver_version?: string;
  iterations?: number;
  exploitability_bb?: string;
  output_jsonb?: Record<string, unknown>;
}

export interface SolverRunResponse {
  id: string;
  scenario_hash: string;
  street: string;
  created_at: string;
}

export interface SolverSummary {
  hero_action?: string | null;
  solver_best_action?: string | null;
  ev_diff_bb?: number | null;
  action_frequencies?: Record<string, number>;
  notes?: string | null;
}

export interface AnalyzeHandRequest {
  street: Street;
  scenario_hash?: string | null;
  solver_summary?: SolverSummary | null;
}

export interface AnalyzeHandResponse {
  id: string;
  hand_id: string;
  model: string;
  prompt_hash: string;
  analysis: string;
  leak_tags: string[];
  cached: boolean;
  input_tokens?: number | null;
  output_tokens?: number | null;
  created_at: string;
}

export interface AnalysisListItem {
  id: string;
  hand_id: string;
  model: string;
  prompt_hash: string;
  analysis: string;
  leak_tags: string[];
  created_at: string;
}

/**
 * SSE wire format emitted by POST /hands/{hand_id}/analyze.
 *
 *  event: token
 *  data:  { "text": "..." }
 *
 *  event: done
 *  data:  {
 *           "analysis_id": "...",
 *           "leak_tags": [...],
 *           "cached": false,
 *           "model": "...",
 *           "input_tokens": 123,
 *           "output_tokens": 456
 *         }
 *
 *  event: error
 *  data:  { "message": "..." }
 *
 * The token event fires repeatedly while Claude streams. A single done
 * event terminates the stream. Cached responses emit one token (the
 * full analysis text) followed immediately by done.
 */
export interface AnalyzeTokenEvent {
  text: string;
}

export interface AnalyzeDoneEvent {
  analysis_id: string;
  leak_tags: string[];
  cached: boolean;
  model: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

export interface AnalyzeErrorEvent {
  message: string;
}

export interface HandsListParams {
  limit?: number;
  offset?: number;
  order?: string;
  position?: string;
  since?: string;
  only_losses?: boolean;
}
