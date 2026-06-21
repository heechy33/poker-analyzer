import { describe, expect, it } from "vitest";

import {
  computeHandScore,
  subReasonFromScenario,
  tierFromConfidence,
  type SolvesByStreet,
} from "../../lib/hand-analysis/confidence";

describe("tierFromConfidence", () => {
  it("returns 'high' for 'high'", () => {
    expect(tierFromConfidence("high")).toBe("high");
  });
  it("returns 'medium' for 'medium'", () => {
    expect(tierFromConfidence("medium")).toBe("medium");
  });
  it("returns 'low' for 'low'", () => {
    expect(tierFromConfidence("low")).toBe("low");
  });
  it("returns 'low' for undefined", () => {
    expect(tierFromConfidence(undefined)).toBe("low");
  });
  it("returns 'low' for unknown values", () => {
    expect(tierFromConfidence("unknown")).toBe("low");
  });
});

describe("subReasonFromScenario (P1.1 — medium sub-reason detection)", () => {
  it("returns 'borderline_spr' when SPR < 1.0 and confidence is medium", () => {
    const result = subReasonFromScenario({
      confidence: "medium",
      confidence_reasons: [],
      spr: 0.8,
    });
    expect(result).toBe("borderline_spr");
  });

  it("returns 'range_fallback' when confidence_reasons includes 'fallback'", () => {
    const result = subReasonFromScenario({
      confidence: "medium",
      confidence_reasons: ["range_fallback"],
      spr: 3.0,
    });
    expect(result).toBe("range_fallback");
  });

  it("returns 'range_fallback' as default for medium confidence", () => {
    const result = subReasonFromScenario({
      confidence: "medium",
      confidence_reasons: ["some_other_reason"],
      spr: 5.0,
    });
    expect(result).toBe("range_fallback");
  });

  it("returns null for high confidence", () => {
    const result = subReasonFromScenario({
      confidence: "high",
      confidence_reasons: [],
      spr: 0.8,
    });
    expect(result).toBeNull();
  });

  it("returns null for low confidence", () => {
    const result = subReasonFromScenario({
      confidence: "low",
      confidence_reasons: [],
      spr: 0.8,
    });
    expect(result).toBeNull();
  });

  it("returns null for undefined confidence", () => {
    const result = subReasonFromScenario({
      confidence: undefined,
      confidence_reasons: [],
      spr: 0.5,
    });
    expect(result).toBeNull();
  });
});

describe("computeHandScore (P2.7 — high confidence only)", () => {
  const baseState = {
    status: "ready" as const,
    output: null,
    progress: null,
    fromCache: false,
    error: null,
  };

  // Helper to build a minimal scenario
  function scenario(confidence: string, metadata?: Record<string, unknown>) {
    return {
      confidence,
      confidence_reasons: [],
      confidence_detail: "",
      scenario_hash: "abc",
      scenario: {} as never,
      metadata: metadata ?? {},
      cached: false,
      cached_output: null,
    };
  }

  function grade(quality: string) {
    return {
      quality,
      actualAction: "check",
      bestAction: "check",
      evGap: 0,
      label: quality === "solid" ? "Solid" : "Close",
      cells: [],
    };
  }

  it("aggregates grades from high confidence streets only", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("solid"),
      },
      turn: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("mistake"),
      },
    };

    const result = computeHandScore(solves);
    expect(result.counts.solid).toBe(1);
    expect(result.counts.mistake).toBe(1);
    expect(result.counts.mixed).toBe(0);
    expect(result.score).toBe(Math.round((100 + 15) / 2));
    expect(result.highConfidenceStreets).toBe(2);
    expect(result.excludedStreets).toBe(0);
  });

  it("excludes medium and low confidence streets from scoring", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("medium"),
        grade: grade("mistake"),
      },
      turn: {
        ...baseState,
        scenario: scenario("low"),
        grade: grade("solid"),
      },
    };

    const result = computeHandScore(solves);
    expect(result.highConfidenceStreets).toBe(0);
    expect(result.excludedStreets).toBe(2);
    expect(result.score).toBe(50);
    expect(result.counts.solid).toBe(0);
    expect(result.counts.mixed).toBe(0);
    expect(result.counts.mistake).toBe(0);
  });

  it("returns default score 50 when there are no graded streets", () => {
    const solves: SolvesByStreet = {};
    const result = computeHandScore(solves);
    expect(result.score).toBe(50);
    expect(result.highConfidenceStreets).toBe(0);
    expect(result.excludedStreets).toBe(0);
    expect(result.totalStreets).toBe(0);
  });

  it("filters out 'unknown' quality grades", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("unknown"),
      },
      turn: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("solid"),
      },
    };

    const result = computeHandScore(solves);
    expect(result.highConfidenceStreets).toBe(1);
    expect(result.score).toBe(100);
    expect(result.counts.solid).toBe(1);
  });

  it("excludes streets with null scenario", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: null,
        grade: grade("solid"),
      },
    };

    const result = computeHandScore(solves);
    expect(result.highConfidenceStreets).toBe(0);
    expect(result.excludedStreets).toBe(1);
  });

  it("handles mixed high/medium scenario with grades correctly", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("mixed"),
      },
      turn: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("solid"),
      },
      river: {
        ...baseState,
        scenario: scenario("medium"),
        grade: grade("mistake"),
      },
    };

    const result = computeHandScore(solves);
    expect(result.highConfidenceStreets).toBe(2);
    expect(result.excludedStreets).toBe(1);
    expect(result.totalStreets).toBe(3);
    expect(result.score).toBe(Math.round((60 + 100) / 2)); // 80
    expect(result.counts.solid).toBe(1);
    expect(result.counts.mixed).toBe(1);
    expect(result.counts.mistake).toBe(0); // mistake was on medium = excluded
  });

  it("returns default 50 when all high confidence streets have unknown quality", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("unknown"),
      },
    };

    const result = computeHandScore(solves);
    expect(result.score).toBe(50);
    expect(result.highConfidenceStreets).toBe(0);
    expect(result.totalStreets).toBe(0); // unknown quality excluded from graded count entirely
    expect(result.excludedStreets).toBe(0);
  });

  it("assigns solid=100, mixed=60, mistake=15 point values (regression guard)", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("solid"),
      },
      turn: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("mixed"),
      },
      river: {
        ...baseState,
        scenario: scenario("high"),
        grade: grade("mistake"),
      },
    };

    const result = computeHandScore(solves);
    // (100 + 60 + 15) / 3 = 58.33 → Math.round = 58
    expect(result.score).toBe(58);
    expect(result.counts.solid).toBe(1);
    expect(result.counts.mixed).toBe(1);
    expect(result.counts.mistake).toBe(1);
  });

  it("reports excludedStreets count correctly for multiway-only (all medium/low) hands", () => {
    const solves: SolvesByStreet = {
      flop: {
        ...baseState,
        scenario: scenario("medium"),
        grade: grade("mixed"),
      },
      turn: {
        ...baseState,
        scenario: scenario("low"),
        grade: grade("mistake"),
      },
      river: {
        ...baseState,
        scenario: scenario("medium"),
        grade: grade("solid"),
      },
    };

    const result = computeHandScore(solves);
    // All 3 streets have non-unknown grades but none are high confidence
    expect(result.score).toBe(50); // §4: zero eligible streets → score 50, NOT null
    expect(result.highConfidenceStreets).toBe(0);
    expect(result.totalStreets).toBe(3);
    expect(result.excludedStreets).toBe(3); // all three excluded as medium/low
    expect(result.counts.solid).toBe(0);
    expect(result.counts.mixed).toBe(0);
    expect(result.counts.mistake).toBe(0);
  });
});
