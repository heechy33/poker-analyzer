import { describe, expect, it } from "vitest";

import {
  buildDecisionNodeHistory,
  heroCardsToComboKey,
  inferHeroActionOnStreet,
  mapBetFractionToLabel,
  mapVillainActionToSolverLabel,
  type ActionBeforeHero,
} from "./hand-context";
import type { HandActionOut } from "@/types/api";

// ---------------------------------------------------------------------------
// heroCardsToComboKey
// ---------------------------------------------------------------------------

describe("heroCardsToComboKey", () => {
  it("keeps descending rank order for offsuit combos", () => {
    expect(heroCardsToComboKey(["Kc", "9d"])).toBe("Kc9d");
  });

  it("normalizes rank and suit case before sorting", () => {
    expect(heroCardsToComboKey(["9D", "ac"])).toBe("Ac9d");
  });

  it("breaks pocket-pair ties by suit for stable combo keys", () => {
    expect(heroCardsToComboKey(["Ts", "Tc"])).toBe("TcTs");
  });
});

// ---------------------------------------------------------------------------
// mapBetFractionToLabel (P2.5)
// ---------------------------------------------------------------------------

describe("mapBetFractionToLabel", () => {
  const solverActions = ["check", "bet_33", "bet_75", "allin"];
  const bettingOnly = ["bet_33", "bet_75", "allin"];

  it("maps 33% to bet_33 (exact)", () => {
    expect(mapBetFractionToLabel(33, bettingOnly)).toBe("bet_33");
  });

  it("maps 75% to bet_75 (exact)", () => {
    expect(mapBetFractionToLabel(75, bettingOnly)).toBe("bet_75");
  });

  it("maps 25% to bet_33 (closer than bet_75)", () => {
    // |25-33| = 8, |25-75| = 50 → bet_33 wins
    expect(mapBetFractionToLabel(25, bettingOnly)).toBe("bet_33");
  });

  it("maps 50% to bet_33 (closer: |50-33|=17 < |50-75|=25)", () => {
    expect(mapBetFractionToLabel(50, bettingOnly)).toBe("bet_33");
  });

  it("maps 60% to bet_75 (closer: |60-75|=15 < |60-33|=27)", () => {
    expect(mapBetFractionToLabel(60, bettingOnly)).toBe("bet_75");
  });

  it("prefers allin when pctOfPot > max label + 50", () => {
    // max label = 75; 75+50 = 125; bet of 150% → prefers allin
    expect(mapBetFractionToLabel(150, bettingOnly)).toBe("allin");
  });

  it("does NOT prefer allin for moderate overbets below threshold", () => {
    // 120% > 75% but 120 < 75+50=125 → bet_75 wins
    expect(mapBetFractionToLabel(120, bettingOnly)).toBe("bet_75");
  });

  it("returns null when no betting actions are available", () => {
    expect(mapBetFractionToLabel(50, ["fold", "check", "call"])).toBeNull();
  });

  it("falls back to allin when no labelled size matches and allin is available", () => {
    expect(mapBetFractionToLabel(500, ["allin"])).toBe("allin");
  });

  it("handles raise labels (bet and raise share the same logic)", () => {
    const withRaise = ["fold", "call", "raise_250"];
    expect(mapBetFractionToLabel(250, withRaise)).toBe("raise_250");
    expect(mapBetFractionToLabel(100, withRaise)).toBe("raise_250"); // only raise available
  });

  it("ignores non-betting actions when finding the best label", () => {
    // fold/check/call should not be counted as betting actions
    expect(mapBetFractionToLabel(33, solverActions)).toBe("bet_33");
  });
});

// ---------------------------------------------------------------------------
// mapVillainActionToSolverLabel
// ---------------------------------------------------------------------------

describe("mapVillainActionToSolverLabel", () => {
  const rootActions = ["check", "bet_33", "bet_75", "allin"];

  function entry(
    action: string,
    amount_bb: number | null = null,
    pot_bb_before = 10,
  ): ActionBeforeHero {
    return { player_is_hero: false, action, amount_bb, raise_to_bb: null, pot_bb_before };
  }

  it("maps villain check to 'check'", () => {
    expect(mapVillainActionToSolverLabel(entry("check"), rootActions)).toBe("check");
  });

  it("maps villain fold to 'fold'", () => {
    const withFold = ["fold", "check", "bet_33"];
    expect(mapVillainActionToSolverLabel(entry("fold"), withFold)).toBe("fold");
  });

  it("maps villain call to 'call'", () => {
    const withCall = ["fold", "call", "raise_250", "allin"];
    expect(mapVillainActionToSolverLabel(entry("call"), withCall)).toBe("call");
  });

  it("maps villain 33% c-bet to bet_33", () => {
    // villain bets 3.3bb into 10bb → 33%
    expect(
      mapVillainActionToSolverLabel(entry("bet", 3.3, 10), rootActions),
    ).toBe("bet_33");
  });

  it("maps villain 50% c-bet to nearest (bet_33 at 33/75 tree)", () => {
    // 50% → |50-33|=17 < |50-75|=25 → bet_33
    expect(
      mapVillainActionToSolverLabel(entry("bet", 5, 10), rootActions),
    ).toBe("bet_33");
  });

  it("maps villain 75% c-bet to bet_75", () => {
    expect(
      mapVillainActionToSolverLabel(entry("bet", 7.5, 10), rootActions),
    ).toBe("bet_75");
  });

  it("maps villain all-in overbet to allin", () => {
    expect(
      mapVillainActionToSolverLabel(entry("bet", 50, 10), rootActions),
    ).toBe("allin");
  });

  it("falls back to first betting action when no pot info", () => {
    const noAmount = entry("bet", null, 0);
    const result = mapVillainActionToSolverLabel(noAmount, rootActions);
    // Should return first betting action available
    expect(result).toBe("bet_33");
  });
});

// ---------------------------------------------------------------------------
// buildDecisionNodeHistory (P2.4) — Finding 8 scenarios
// ---------------------------------------------------------------------------

describe("buildDecisionNodeHistory", () => {
  const rootActionsOop = ["check", "bet_33", "bet_75", "allin"];
  // After villain checks, hero (IP) sees: check, bet_33, bet_75, allin
  const actionsAfterCheck = ["check", "bet_33", "bet_75", "allin"];
  // After villain bets 33%, hero (IP) sees: fold, call, raise_250, allin
  const actionsAfterBet33 = ["fold", "call", "raise_250", "allin"];

  it("returns empty history when actionsBeforeHero is empty (hero opens, depth 0)", () => {
    const result = buildDecisionNodeHistory([], rootActionsOop);
    expect(result.history).toEqual([]);
    expect(result.depth).toBe(0);
    expect(result.incomplete).toBe(false);
    expect(result.description).toContain("hero acts first");
  });

  it("navigates past villain check (depth 1 — hero responds to check)", () => {
    const actions: ActionBeforeHero[] = [
      { player_is_hero: false, action: "check", amount_bb: null, raise_to_bb: null, pot_bb_before: 10 },
    ];
    const result = buildDecisionNodeHistory(actions, rootActionsOop);
    // "check" is index 0 in rootActionsOop
    expect(result.history).toEqual([0]);
    expect(result.depth).toBe(1);
    expect(result.incomplete).toBe(false);
  });

  it("navigates past villain c-bet 33% (depth 1 — AUDIT FINDING 8)", () => {
    // Villain bets 3.3bb into 10bb = 33% — maps to bet_33 (index 1)
    const actions: ActionBeforeHero[] = [
      { player_is_hero: false, action: "bet", amount_bb: 3.3, raise_to_bb: null, pot_bb_before: 10 },
    ];
    const result = buildDecisionNodeHistory(actions, rootActionsOop);
    expect(result.history).toEqual([1]); // index of "bet_33"
    expect(result.depth).toBe(1);
    expect(result.incomplete).toBe(false);
    expect(result.description).toContain("villain bet");
  });

  it("navigates past villain c-bet 75% (depth 1)", () => {
    // 7.5bb into 10bb = 75% → bet_75 (index 2)
    const actions: ActionBeforeHero[] = [
      { player_is_hero: false, action: "bet", amount_bb: 7.5, raise_to_bb: null, pot_bb_before: 10 },
    ];
    const result = buildDecisionNodeHistory(actions, rootActionsOop);
    expect(result.history).toEqual([2]); // index of "bet_75"
    expect(result.depth).toBe(1);
  });

  it("handles depth-2 with intermediate node actions (hero check, villain c-bet)", () => {
    // hero checks (index 0 in rootActionsOop), villain bets (index 1 in actionsAfterCheck)
    const actions: ActionBeforeHero[] = [
      { player_is_hero: true,  action: "check", amount_bb: null, raise_to_bb: null, pot_bb_before: 10 },
      { player_is_hero: false, action: "bet",   amount_bb: 3.3, raise_to_bb: null,  pot_bb_before: 10 },
    ];
    const result = buildDecisionNodeHistory(actions, rootActionsOop, [actionsAfterCheck]);
    expect(result.history).toEqual([0, 1]); // check→bet_33
    expect(result.depth).toBe(2);
    expect(result.incomplete).toBe(false);
  });

  it("marks incomplete when intermediate node actions not provided for depth-2", () => {
    const actions: ActionBeforeHero[] = [
      { player_is_hero: true,  action: "check", amount_bb: null, raise_to_bb: null, pot_bb_before: 10 },
      { player_is_hero: false, action: "bet",   amount_bb: 3.3, raise_to_bb: null,  pot_bb_before: 10 },
    ];
    // No nodeActionsList provided — depth 2 cannot be resolved
    const result = buildDecisionNodeHistory(actions, rootActionsOop);
    expect(result.incomplete).toBe(true);
    // Partial history: at least depth 1 (hero's check)
    expect(result.history).toEqual([0]);
  });

  it("marks incomplete and falls back when action not found in solver actions", () => {
    const actions: ActionBeforeHero[] = [
      { player_is_hero: false, action: "bet", amount_bb: 3.3, raise_to_bb: null, pot_bb_before: 10 },
    ];
    // Root actions have NO betting option
    const nobet = ["fold", "check", "call"];
    const result = buildDecisionNodeHistory(actions, nobet);
    expect(result.incomplete).toBe(true);
    expect(result.history).toEqual([]);
    expect(result.depth).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// inferHeroActionOnStreet — with potAtHeroActionBb (P2.5)
// ---------------------------------------------------------------------------

describe("inferHeroActionOnStreet — pot-fraction bet mapping", () => {
  function makeAction(
    action: string,
    amount: string | null = null,
    raise_to: string | null = null,
  ): HandActionOut {
    return {
      street: "flop",
      action_order: 1,
      seat: 1,
      screen_name: "Hero",
      action,
      amount,
      raise_to,
      is_all_in: false,
    };
  }

  const solverActions = ["fold", "call", "bet_33", "bet_75", "allin"];
  const opts = { heroSeat: 1 };

  it("maps hero fold regardless of pot", () => {
    const actions = [makeAction("fold")];
    expect(inferHeroActionOnStreet(actions, "flop", solverActions, opts)).toBe("fold");
  });

  it("maps hero check to 'check'", () => {
    const responseActions = ["fold", "check", "raise_250", "allin"];
    const actions = [makeAction("check")];
    expect(inferHeroActionOnStreet(actions, "flop", responseActions, opts)).toBe("check");
  });

  it("maps hero call to 'call'", () => {
    const actions = [makeAction("call", "5")];
    expect(inferHeroActionOnStreet(actions, "flop", solverActions, opts)).toBe("call");
  });

  it("maps hero 33% bet to bet_33 using pot fraction", () => {
    // hero bets 3.3bb into 10bb → 33%
    const actions = [makeAction("bet", "3.3")];
    expect(
      inferHeroActionOnStreet(actions, "flop", solverActions, { ...opts, potAtHeroActionBb: 10 }),
    ).toBe("bet_33");
  });

  it("maps hero 25% bet to bet_33 (closer than bet_75)", () => {
    // 25% → |25-33|=8 < |25-75|=50 → bet_33
    const actions = [makeAction("bet", "2.5")];
    expect(
      inferHeroActionOnStreet(actions, "flop", solverActions, { ...opts, potAtHeroActionBb: 10 }),
    ).toBe("bet_33");
  });

  it("maps hero 75% bet to bet_75 using pot fraction", () => {
    const actions = [makeAction("bet", "7.5")];
    expect(
      inferHeroActionOnStreet(actions, "flop", solverActions, { ...opts, potAtHeroActionBb: 10 }),
    ).toBe("bet_75");
  });

  it("maps hero large overbet to allin (>125% pot)", () => {
    // 200% > 75+50=125 → allin
    const actions = [makeAction("bet", "20")];
    expect(
      inferHeroActionOnStreet(actions, "flop", solverActions, { ...opts, potAtHeroActionBb: 10 }),
    ).toBe("allin");
  });

  it("falls back to first betting action when no pot info (regression guard)", () => {
    // Without potAtHeroActionBb, should NOT crash — returns first betting action
    const actions = [makeAction("bet", "5")];
    const result = inferHeroActionOnStreet(actions, "flop", solverActions, opts);
    // Fallback picks first available betting action from the list
    expect(["bet_33", "bet_75", "allin"]).toContain(result);
  });

  it("uses raise_to field for raises when computing fraction", () => {
    // hero raises to 15bb, pot was 13.3bb → 15/13.3*100 ≈ 113% → closer to bet_75(75) than allin
    const actions = [makeAction("raise", "5", "15")];
    // |113-75|=38 < |113-125|... wait: allin threshold = 75+50=125; 113<125 → bet_75
    expect(
      inferHeroActionOnStreet(actions, "flop", solverActions, { ...opts, potAtHeroActionBb: 13.3 }),
    ).toBe("bet_75");
  });
});

// ---------------------------------------------------------------------------
// Integration test — villain c-bet scenario (audit Finding 8)
// ---------------------------------------------------------------------------

describe("Finding 8 — villain c-bet grading uses response node actions", () => {
  /**
   * Scenario: BTN (hero) vs BB (villain) on flop.
   * BB c-bets 5bb into 10bb pot → hero responds.
   *
   * Pre-fix behaviour: export_strategy(handle, "") → OOP/BB opening strategy
   *   (check/bet options) — WRONG, grade against wrong node.
   *
   * Post-fix behaviour:
   *   1. Root: OOP actions = ["check","bet_33","bet_75","allin"]
   *   2. Villain bet 5bb/10bb → 50% → nearest = bet_33 (idx 1)
   *   3. History = [1]
   *   4. Hero response actions at [1] = ["fold","call","raise_250","allin"]
   *   5. Hero called → mapped to "call" ✓
   */
  it("builds history [1] for villain 50% c-bet on 33/75 tree", () => {
    const actionsBeforeHero: ActionBeforeHero[] = [
      { player_is_hero: false, action: "bet", amount_bb: 5, raise_to_bb: null, pot_bb_before: 10 },
    ];
    const rootActions = ["check", "bet_33", "bet_75", "allin"];
    const ctx = buildDecisionNodeHistory(actionsBeforeHero, rootActions);

    expect(ctx.history).toEqual([1]);        // navigates to bet_33 branch
    expect(ctx.incomplete).toBe(false);
    expect(ctx.depth).toBe(1);
  });

  it("hero call maps correctly on the response node actions", () => {
    const responseActions = ["fold", "call", "raise_250", "allin"];
    const heroHandAction: HandActionOut = {
      street: "flop",
      action_order: 2,
      seat: 2,
      screen_name: "Hero",
      action: "call",
      amount: "5",
      raise_to: null,
      is_all_in: false,
    };
    const result = inferHeroActionOnStreet([heroHandAction], "flop", responseActions, {
      heroSeat: 2,
      potAtHeroActionBb: 15, // pot after villain c-bet
    });
    expect(result).toBe("call");
  });
});
