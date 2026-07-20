import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function source(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

describe("P0.7 coaching and offline-study surface", () => {
  it("shows the offline-study notice during sign-in onboarding", () => {
    const login = source("../app/login/login-form.tsx");
    expect(login).toContain("<OfflineStudyNotice compact />");
  });

  it("blocks uploads until the closed-client confirmation is checked", () => {
    const upload = source("../app/(app)/upload/page.tsx");
    expect(upload).toContain("requireConfirmation");
    expect(upload).toContain("busy || !canStartStudyAction(clientClosed)");
  });

  it("labels Coach as general coaching and sends only the selected street", () => {
    const coach = source("../components/hand-review/CoachTab.tsx");
    expect(coach).toContain("General coaching—no verified solver result");
    expect(coach).toContain("{ street: selectedStreet }");
    expect(coach).not.toContain("solver_summary:");
    expect(coach).not.toContain("scenario_hash:");
  });

  it("shows the offline-study notice at the hand-review entry point", () => {
    const review = source("../components/hand-review/HandReviewModal.tsx");
    expect(review).toContain("<OfflineStudyNotice compact />");
  });
});
