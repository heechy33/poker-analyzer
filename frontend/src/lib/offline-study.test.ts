import { describe, expect, it } from "vitest";

import {
  CLIENT_CLOSED_CONFIRMATION,
  OFFLINE_STUDY_MESSAGE,
  canStartStudyAction,
} from "./offline-study";

describe("offline study guard", () => {
  it("blocks study actions until the CoinPoker client is confirmed closed", () => {
    expect(canStartStudyAction(false)).toBe(false);
    expect(canStartStudyAction(true)).toBe(true);
  });

  it("uses explicit policy language", () => {
    expect(OFFLINE_STUDY_MESSAGE).toContain("after your CoinPoker session");
    expect(OFFLINE_STUDY_MESSAGE).toContain("client fully closed");
    expect(CLIENT_CLOSED_CONFIRMATION).toBe(
      "I confirm the CoinPoker client is closed.",
    );
  });
});
