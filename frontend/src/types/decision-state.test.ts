import { expect, test } from "vitest";

import fieldContract from "../../../contracts/hunl-decision-state-v1.fields.json";
import realHunlFlopState from "../../../contracts/hunl-decision-state-v1.real-hunl-flop.json";
import {
  parseDecisionStateV1,
  type DecisionStateV1,
} from "./decision-state";

test("P1.11 accepts the shared Python decision-state serialization", () => {
  const state: DecisionStateV1 = parseDecisionStateV1(realHunlFlopState);

  expect(state).toEqual(realHunlFlopState);
  expect(fieldContract.schema_version).toBe(state.schema_version);
  expect([...fieldContract.required].sort()).toEqual(Object.keys(state).sort());
});

test("P1.11 rejects drift at the JSON boundary", () => {
  const missingRequiredField = { ...realHunlFlopState } as Record<string, unknown>;
  delete missingRequiredField.rake_schedule_id;
  expect(() => parseDecisionStateV1(missingRequiredField)).toThrow("wire shape");

  expect(() => parseDecisionStateV1({ ...realHunlFlopState, future_value: "no" })).toThrow(
    "wire shape",
  );
  expect(() => parseDecisionStateV1({ ...realHunlFlopState, small_blind: 0.02 })).toThrow(
    "small_blind must be a string",
  );
  expect(() => parseDecisionStateV1({ ...realHunlFlopState, legal_actions: ["grade"] })).toThrow(
    "invalid legal action",
  );
});
