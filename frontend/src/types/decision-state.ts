/**
 * Wire contract for `hunl-decision-state/1`.
 *
 * This is a decision-time projection only. It deliberately has no eligibility,
 * settlement, solver, range, EV, frequency, or grading data.
 */

export type ChipAmount = string;
export type DecisionStreetV1 = "preflop" | "flop" | "turn" | "river";
export type DecisionActionV1 = "fold" | "check" | "call" | "bet" | "raise";
export type DecisionTableFormatV1 = "hu_2max" | "6max" | "9max";

export interface DecisionPlayerV1 {
  seat: number;
  alias: string;
  position: string;
  starting_stack: ChipAmount;
  is_hero: boolean;
  dealt_in: boolean;
}

export interface DecisionPlayerStateV1 {
  seat: number;
  active: boolean;
  folded: boolean;
  all_in: boolean;
  street_contribution: ChipAmount;
  total_contribution: ChipAmount;
  remaining_stack: ChipAmount;
}

export interface DecisionLegalRaiseBoundsV1 {
  min_raise_to: ChipAmount | null;
  max_raise_to: ChipAmount | null;
  action_reopened: boolean;
}

export interface DecisionStateV1 {
  schema_version: "hunl-decision-state/1";
  raw_hand_id: string;
  played_at: string;
  game: "NLHE";
  table_marker: string;
  table_format: DecisionTableFormatV1;
  button_seat: number;
  small_blind: ChipAmount;
  big_blind: ChipAmount;
  action_event_index: number;
  action_street_event_index: number | null;
  street: DecisionStreetV1;
  players: DecisionPlayerV1[];
  hero_seat: number;
  hero_position: string;
  hero_combo: [string, string];
  active_seats: number[];
  folded_seats: number[];
  all_in_seats: number[];
  players_reached_flop: number[];
  player_states: DecisionPlayerStateV1[];
  amount_to_call: ChipAmount;
  last_full_raise: ChipAmount;
  legal_raise_bounds: DecisionLegalRaiseBoundsV1;
  legal_actions: DecisionActionV1[];
  player_contributed_pot: ChipAmount;
  board_prefix: string[];
  rake_schedule_id: string | null;
}

const decisionStateFields = [
  "schema_version",
  "raw_hand_id",
  "played_at",
  "game",
  "table_marker",
  "table_format",
  "button_seat",
  "small_blind",
  "big_blind",
  "action_event_index",
  "action_street_event_index",
  "street",
  "players",
  "hero_seat",
  "hero_position",
  "hero_combo",
  "active_seats",
  "folded_seats",
  "all_in_seats",
  "players_reached_flop",
  "player_states",
  "amount_to_call",
  "last_full_raise",
  "legal_raise_bounds",
  "legal_actions",
  "player_contributed_pot",
  "board_prefix",
  "rake_schedule_id",
] as const satisfies readonly (keyof DecisionStateV1)[];

type Assert<T extends true> = T;
type _NoMissingDecisionStateFields = Assert<
  Exclude<keyof DecisionStateV1, (typeof decisionStateFields)[number]> extends never
    ? true
    : false
>;
type _NoUnknownDecisionStateFields = Assert<
  Exclude<(typeof decisionStateFields)[number], keyof DecisionStateV1> extends never
    ? true
    : false
>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertExactKeys(value: Record<string, unknown>, keys: readonly string[], name: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${name} has an unexpected wire shape`);
  }
}

function assertString(value: unknown, name: string): asserts value is string {
  if (typeof value !== "string") throw new Error(`${name} must be a string`);
}

function assertNumber(value: unknown, name: string): asserts value is number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`${name} must be an integer`);
  }
}

function assertBoolean(value: unknown, name: string): asserts value is boolean {
  if (typeof value !== "boolean") throw new Error(`${name} must be a boolean`);
}

function assertArray(value: unknown, name: string): asserts value is unknown[] {
  if (!Array.isArray(value)) throw new Error(`${name} must be an array`);
}

function parsePlayer(value: unknown): DecisionPlayerV1 {
  if (!isRecord(value)) throw new Error("players[] must be an object");
  assertExactKeys(value, ["seat", "alias", "position", "starting_stack", "is_hero", "dealt_in"], "players[]");
  assertNumber(value.seat, "players[].seat");
  assertString(value.alias, "players[].alias");
  assertString(value.position, "players[].position");
  assertString(value.starting_stack, "players[].starting_stack");
  assertBoolean(value.is_hero, "players[].is_hero");
  assertBoolean(value.dealt_in, "players[].dealt_in");
  return value as unknown as DecisionPlayerV1;
}

function parsePlayerState(value: unknown): DecisionPlayerStateV1 {
  if (!isRecord(value)) throw new Error("player_states[] must be an object");
  assertExactKeys(value, ["seat", "active", "folded", "all_in", "street_contribution", "total_contribution", "remaining_stack"], "player_states[]");
  assertNumber(value.seat, "player_states[].seat");
  assertBoolean(value.active, "player_states[].active");
  assertBoolean(value.folded, "player_states[].folded");
  assertBoolean(value.all_in, "player_states[].all_in");
  assertString(value.street_contribution, "player_states[].street_contribution");
  assertString(value.total_contribution, "player_states[].total_contribution");
  assertString(value.remaining_stack, "player_states[].remaining_stack");
  return value as unknown as DecisionPlayerStateV1;
}

/** Validate a JSON API payload without adding a solver-facing consumer. */
export function parseDecisionStateV1(value: unknown): DecisionStateV1 {
  if (!isRecord(value)) throw new Error("decision state must be an object");
  assertExactKeys(value, decisionStateFields, "decision state");

  const stringFields = [
    "schema_version", "raw_hand_id", "played_at", "game", "table_marker", "table_format",
    "small_blind", "big_blind", "street", "hero_position", "amount_to_call", "last_full_raise",
    "player_contributed_pot",
  ] as const;
  for (const field of stringFields) assertString(value[field], field);
  for (const field of ["button_seat", "action_event_index", "hero_seat"] as const) {
    assertNumber(value[field], field);
  }
  if (value.action_street_event_index !== null) {
    assertNumber(value.action_street_event_index, "action_street_event_index");
  }
  if (value.schema_version !== "hunl-decision-state/1") throw new Error("unsupported decision-state schema");
  if (value.game !== "NLHE") throw new Error("unsupported game");
  if (!["hu_2max", "6max", "9max"].includes(value.table_format as string)) throw new Error("invalid table format");
  if (!["preflop", "flop", "turn", "river"].includes(value.street as string)) throw new Error("invalid street");

  assertArray(value.players, "players");
  value.players.forEach(parsePlayer);
  assertArray(value.hero_combo, "hero_combo");
  if (value.hero_combo.length !== 2) throw new Error("hero_combo must contain two cards");
  value.hero_combo.forEach((card) => assertString(card, "hero_combo[]"));
  for (const field of ["active_seats", "folded_seats", "all_in_seats", "players_reached_flop"] as const) {
    assertArray(value[field], field);
    value[field].forEach((seat) => assertNumber(seat, `${field}[]`));
  }
  assertArray(value.player_states, "player_states");
  value.player_states.forEach(parsePlayerState);
  if (!isRecord(value.legal_raise_bounds)) throw new Error("legal_raise_bounds must be an object");
  assertExactKeys(value.legal_raise_bounds, ["min_raise_to", "max_raise_to", "action_reopened"], "legal_raise_bounds");
  for (const field of ["min_raise_to", "max_raise_to"] as const) {
    if (value.legal_raise_bounds[field] !== null) {
      assertString(value.legal_raise_bounds[field], `legal_raise_bounds.${field}`);
    }
  }
  assertBoolean(value.legal_raise_bounds.action_reopened, "legal_raise_bounds.action_reopened");
  assertArray(value.legal_actions, "legal_actions");
  value.legal_actions.forEach((action) => {
    if (typeof action !== "string" || !["fold", "check", "call", "bet", "raise"].includes(action)) {
      throw new Error("invalid legal action");
    }
  });
  assertArray(value.board_prefix, "board_prefix");
  value.board_prefix.forEach((card) => assertString(card, "board_prefix[]"));
  if (value.rake_schedule_id !== null) assertString(value.rake_schedule_id, "rake_schedule_id");
  return value as unknown as DecisionStateV1;
}
