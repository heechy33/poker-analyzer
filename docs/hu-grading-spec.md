---
title: HU Grading Spec v1
status: draft
last_verified: 2026-06-13
supersedes: [POKER_SOLVER_AUDIT_REPORT.md (for implementation decisions only)]
---

## 1. Purpose & non-goals

This document is the **single decision source** for the poker-analyzer heads-up grading pipeline. It encodes every architectural choice that affects what the product calls "GTO", when a hand score is computed, and what the user sees. It replaces the audit documents' catalogues of options with concrete decisions.

**Explicit non-goals:**
- Multiway GTO equilibrium solving (true 3+ player CFR)
- Preflop solver or preflop grading
- User-configurable bet tree UI
- Server-side solver deployment
- Coach / LLM pipeline design
- Any new solver engine — the project uses `b-inary/postflop-solver` via `solver-wasm` and this is fixed

---

## 2. Definitions

### 2.1 Gradeable HU spot

A postflop street (flop, turn, or river) is **gradeable** when ALL of the following hold:

1. Hero is alive at street start and took at least one action on the street.
2. Exactly 2 players are alive postflop (true heads-up).
3. `build_scenario` returns `confidence = "high"` (see §2.3).
4. The WASM solver completes `init_game` → `solve_step` → `export_strategy` without error.
5. Hero's last action on that street can be mapped to a solver action label via `inferHeroActionOnStreet` (fold/check/call exact; bet/raise via pot-fraction match).

**Decision:** Only `high`-confidence solves produce grades. Medium/low solves never contribute a `solid`/`mixed`/`mistake` label to the hand score.

**Rationale:** Medium confidence means range fallback or borderline SPR — the solver is computing GTO for ranges the opponent didn't have or on a near-degenerate tree. Calling that a "grade" erodes trust (POKER_SOLVER_AUDIT_REPORT Finding 3).

### 2.2 Non-gradeable spot

A street is **non-gradeable** when any condition in §2.1 fails. This includes:
- Multiway (3+ players alive) — always `low` confidence
- Medium confidence (range fallback, borderline SPR)
- Preflop (rejected by `build_scenario`)
- Solve failure (crash, timeout, preflight rejection)
- Hero folded before acting

Non-gradeable streets are shown in the UI with appropriate confidence banners but never produce a grade quality.

### 2.3 Confidence tiers

Defined in `backend/app/scenario/builder.py` lines 652–704 (`_compute_confidence`).

| Tier | Condition | Reason code | Meaning |
|------|-----------|-------------|---------|
| `high` | HU + both ranges hit library exactly + SPR ≥ 1.0 + stack ≤ 400 bb + pot ≤ 500 bb | `hu_clean` | Solver computed true HU GTO for exact ranges. Use for grades. |
| `medium` | HU but range fallback on hero OR villain, OR borderline inputs (SPR < 1.0, deep stack, large pot) | `hu_library_fallback`, `range_gap`, `solver_input_borderline` | Solver ran on approximate ranges or near-edge inputs. Display with amber warning. Do not grade. |
| `low` | Multiway (3+ players alive) | `multiway_hu_approx` | HU approximation with tightened ranges and incorrect pot model. Show only after explicit user opt-in. Never grade. |
| `error` | Scenario rejected at build time (SPR < 0.5, empty ranges, degenerate tree) | `solver_input_unsolvable_shallow_spr` | No solver output produced. |

### 2.4 Grade quality

Defined in `frontend/src/lib/hand-analysis/grading.ts` via `gradeStreetDecision`.

| Quality | Condition |
|---------|-----------|
| `solid` | `|hero EV − best EV| ≤ SOLID_EV_GAP_BB` |
| `mixed` | `SOLID_EV_GAP_BB < |hero EV − best EV| ≤ MISTAKE_EV_GAP_BB` |
| `mistake` | `|hero EV − best EV| > MISTAKE_EV_GAP_BB` |
| `unknown` | Grade could not be computed (missing action mapping, missing EV data, non-gradeable spot) |

**Decision:** Threshold values:
- `SOLID_EV_GAP_BB = 0.12` (unchanged from current code)
- `MISTAKE_EV_GAP_BB = 0.50` (unchanged)

**Rationale:** These thresholds were set based on quick-mode exploitability noise (~0.3–0.8 bb). `solid` at 0.12 bb is conservative — actionable deviations must be clear. Raising `MISTAKE_EV_GAP_BB` would capture more borderline calls as mistakes; keeping it at 0.50 bb avoids labeling noise as errors.

**Deferred:** Separate thresholds for full-mode (200 iter) solves. Full mode produces tighter exploitability (~0.5 bb) and action EVs with less noise, so tighter thresholds (e.g. `SOLID_EV_GAP_BB = 0.08`) could be justified. Deferred until full-mode grading is tested.

---

## 3. End-to-end pipeline

```
┌──────────────────────────────────────────────────────────────────────┐
│  1. Hand History → Parse (backend)                                     │
│     Owner: backend/app/parser/coinpoker.py                              │
│     Input:  .txt file                                                   │
│     Output: Hand + HandPlayer + HandAction rows in Postgres              │
│                                                                         │
│  2. Scenario Build (backend)                                            │
│     Owner: backend/app/scenario/builder.py                              │
│     Input:  Hand ID + street ("flop"/"turn"/"river")                    │
│     Process: replay preflop → select villain → range lookup →            │
│              hero combo removal → validate envelope → compute confidence │
│     Output: { scenario, metadata, scenario_hash }                       │
│     Failure: reject (ScenarioBuildError)                                 │
│                                                                         │
│  3. Preflight (WASM)                                                   │
│     Owner: solver-wasm/src/lib.rs (preflight export)                     │
│     Input:  scenario envelope JSON                                      │
│     Checks: SPR ≥ 0.5, pot/stack bounds, range non-empty,               │
│             bet tree non-degenerate, board length 3-5                    │
│     Output: { ok: true } or { ok: false, error_class, message }          │
│     Failure: degrade to "Unsolvable spot" UX                            │
│                                                                         │
│  4. WASM Solve (WASM + Web Worker)                                      │
│     Owner: solver-wasm/src/lib.rs + frontend/src/lib/solver/worker.ts   │
│     Input:  scenario envelope JSON                                      │
│     Process: init_game → solve_step × N (quick=50 or full=200)          │
│              → export_strategy(root node)                                │
│              → optionally export_strategy(decision node via history)     │
│     Output: StrategyExport JSON (frequencies, EVs per combo/action)     │
│     Failure: structured error (error_class, message)                     │
│                                                                         │
│  5. Hero Action Inference (frontend)                                    │
│     Owner: frontend/src/lib/solver/hand-context.ts                      │
│     Input:  hand actions + solver action labels + pot_at_hero_action_bb │
│     Process: inferHeroActionOnStreet → nearestSizeAction                 │
│              → mapBetFractionToLabel                                     │
│     Output: solver action label (e.g. "bet_33", "call", "fold")         │
│     Failure: null (grade becomes "unknown")                              │
│                                                                         │
│  6. Per-Street Grade (frontend)                                         │
│     Owner: frontend/src/lib/hand-analysis/grading.ts                    │
│     Input:  StrategyExport + hero action label + hero combo             │
│     Process: gradeStreetDecision → compare hero EV vs best EV           │
│     Output: DecisionGrade { quality, bestAction, actualAction, evGap }  │
│     Failure: quality = "unknown"                                         │
│                                                                         │
│  7. Hand Score Aggregation (frontend)                                   │
│     Owner: frontend/src/lib/hand-analysis/confidence.ts                 │
│     Input:  SolvesByStreet (per-street grades + confidence tiers)       │
│     Process: filter HIGH confidence streets → weighted average           │
│     Output: { score: 0-100, counts: {...}, excludedStreets: N }         │
│     Zero-street behavior: score = 50 (see §4 Decision)                   │
│                                                                         │
│  8. UI Display (frontend)                                               │
│     Owner: HandAnalysisPane.tsx, SolverTab.tsx, CoachTab.tsx            │
│     Rendering: confidence banners, grades, range grid, hand score bar   │
└──────────────────────────────────────────────────────────────────────┘
```

**Simplified ASCII flowchart:**

```
Hand → build_scenario → preflight ──reject──→ "Unsolvable"
                             │
                           accept
                             │
                        confidence?
                       /     |      \
                    high   medium   low
                     │       │        │
                     ▼       ▼        ▼
                  Solve   Solve    Opt-in?
                  (auto)  (manual)  /  \
                     │       │    yes  no
                     ▼       ▼     │    │
                  Grade    Show   Solve  "Hidden —
                  (solid/  amber    │    use Coach"
                  mixed/   warn    ▼
                  mistake) no    Show
                     │     grade  amber
                     ▼             │
                  Score         no grade
```

---

## 4. Hand score formula

**Decision:** The v1 hand score formula is:

1. Collect all postflop streets (flop, turn, river) that have `confidence === "high"` AND a valid `grade.quality` (not `"unknown"`).
2. Assign points per street:
   - `solid` → 100 points
   - `mixed` → 60 points
   - `mistake` → 15 points
3. Compute: `score = round(total_points / num_graded_streets)`
4. If zero streets are gradeable: **score = 50** (neutral midpoint, with UI note "Insufficient data — no high-confidence streets to grade").

**Rationale:** The audit identified that `score = 50` for zero-gradeable-streets is a false signal — a multiway-only hand gets a middling score that looks meaningful when it isn't. However, changing to `null`/`"Insufficient data"` would require the hand list to handle missing scores differently (sorting, filtering, score bar display). 

**Decision:** Keep `50` as the fallback for v1, but the UI MUST display the "Insufficient data" note and the excluded-street count. If user feedback shows confusion, change to `null` in a follow-up.

**Turn/river after multiway flop:** If the flop was multiway but turn is true HU (3rd player folded on flop), the turn and river streets are independently evaluated. If `build_scenario` returns `high` confidence for those streets, they ARE gradeable and count toward the hand score. Only the flop street is excluded.

**Weights:** The point weights (100/60/15) are inherited from current code. No evidence was found to justify different weights. Deferred: A/B test different weight schemes.

**Source:** `frontend/src/lib/hand-analysis/confidence.ts` lines 78–124; duplicate logic in `HandAnalysisPane.tsx` lines 199–248 (`solveStateScore`).

---

## 5. HU vs multiway product behavior

| Situation | Solver shown? | Contributes to score? | UI copy tier | Grade display |
|-----------|---------------|----------------------|--------------|---------------|
| HU high confidence | Yes (auto on button click) | Yes (100/60/15) | "GTO Analysis — High Confidence" (green badge) | Full grade grid, solid/mixed/mistake |
| HU medium (range fallback) | Yes (auto with amber banner) | No | "Approximate GTO — Medium Confidence" (yellow badge) | Grid shown dimmed at 60% opacity, no grade label |
| HU medium (borderline SPR) | Yes (auto with amber banner) | No | "Approximate GTO — Medium Confidence" (yellow badge) | Grid shown dimmed, no grade label |
| Multiway low | Hidden by default; shown after "Show approximate analysis" button click | No | "Not GTO — Multiway Approximation" (amber badge) + "Shown by request" | Grid shown with warning, no grade |
| Scenario rejected (SPR < 0.5, empty ranges, degenerate tree) | No | No | "Solver failed" error state | Error message with reason |
| Preflop | No | No | N/A | No solver section |

**Decision:** Medium-confidence streets are shown (not hidden) with amber banner and dimmed grades so the user can inspect frequencies directionally, but the grade label itself is absent or dimmed and the grade does not count toward the hand score. This differs from the audit's recommendation to hide medium confidence entirely. 

**Rationale:** Medium confidence on borderline SPR is still directionally useful (push/fold at low SPR is well-understood). Medium confidence on range fallback is shown with an explicit warning about wrong ranges. Hiding it entirely would reduce feature surface too aggressively for v1.

---

## 6. Scenario builder rules for HU accuracy

### 6.1 Conditions for `confidence = high`

All of the following must be true:
- **True heads-up:** Exactly 2 players alive postflop (`len(alive_states) == 2`)
- **Range library hit:** Both `hero_lookup_conf == "high"` AND `villain_lookup_conf == "high"` (range library had an exact match for both action sequences)
- **SPR ≥ 1.0:** Not in borderline territory where most bets become all-in
- **Effective stack ≤ 400 bb:** Not a deep-stack anomaly
- **Pot ≤ 500 bb:** Not an abnormally large pot
- **Scenario passes `validate_scenario_envelope`:** SPR ≥ 0.5, pot/stack > 0, range has ≥ 5 hand classes after board removal, total weight ≥ 0.01, bet tree non-degenerate

### 6.2 Pot model

- **True HU:** `pot_chips_hu = hero_state.invested + villain_state.invested`. The pot model is exact — only two players contributed, so there is no dead money.
- **Multiway (HU approximation):** Same formula — hero + selected villain invested only. Folded-player dead money is excluded. `pot_error_pct` in metadata reports how much the model understates the real table pot. This is flagged in UI via `potTransparencyText` (e.g. "Pot modeled as 8.0 bb (actual table pot: 16 bb)").

**Decision:** No change to the HU pot model for v1. The model is correct for true HU. For multiway, the error is surfaced in metadata and UI. A more accurate multiway pot model (including dead money pro-rata) is deferred.

### 6.3 Known gaps

1. **Action mapping without exact pot-at-action (P2.5):** The backend now emits `pot_at_hero_action_bb` and `actions_before_hero` in metadata. The frontend uses these in `inferHeroActionOnStreet` and `buildDecisionNodeHistory`. **Status: Partial** — the fields are emitted and consumed, but correctness depends on the backend replay logic accurately computing pot size at each action. Unverified without test run.

2. **Decision-node export (P2.4):** `buildDecisionNodeHistory` constructs numeric action-index paths for `export_strategy`. The worker requests `export_strategy` at the decision node when history is non-empty. **Status: Partial** — the code path exists but depth-2+ navigation requires `get_actions_at` calls for intermediate nodes, and fallback to root node is common when node actions aren't available.

3. **Quick (50 iter) vs full (200 iter) for grading:** 
   **Decision:** Quick mode is authoritative for hand scores in v1. Full mode is available but not required for grades.
   **Rationale:** Quick mode (50 iter, 1.0 bb exploitability target) is fast and works on mobile. The EV noise (~0.3–0.8 bb) is acceptable for the grading thresholds (0.12 bb for solid, 0.50 bb for mistake). Full mode solves take 3-5× longer and don't change grade outcomes for clear decisions. Marginal spots may flip between mixed/solid with full convergence; this is acceptable for v1.
   **Deferred:** If user feedback shows grade flips are common and jarring, add full-mode grading as default with a "quick preview" fallback.

---

## 7. Implementation status vs audit backlog

### P0 items

| ID | Item | Status | Evidence | Remaining work |
|----|------|--------|----------|----------------|
| P0.1 | Disable auto-solve queue — manual per-street solve only | **Done** | `HandAnalysisPane.tsx:830-831` — per-street "Solve" button; no auto-solve effect loop | None |
| P0.2 | `panic = "unwind"` + `catch_unwind` on all WASM exports | **Done** | `Cargo.toml:54` `panic = "unwind"`; `lib.rs:159,185,331,365,518,537` all wrap in `catch_unwind` | None |
| P0.3 | Worker crash detection + auto-respawn | **Done** | `client.ts:68-82` `onerror`/`onmessageerror` handlers; `client.ts:177` `respawnWorker()`; `client.ts:93-100` `ping()` before each solve | None |
| P0.4 | Pre-WASM envelope validation hardening | **Done** | `lib.rs:552-650` `preflight_inner` — SPR ≥ 0.5, pot/stack bounds, range non-empty, board length, bet tree degeneracy; `builder.py:402-465` `validate_scenario_envelope` mirrors Rust | None |
| P0.5 | Raise `_MIN_SPR` to 0.5 in Python AND Rust | **Done** | `builder.py:69` `_MIN_SPR = Decimal("0.5")`; `lib.rs:571` `if spr < 0.5` | None |
| P0.6 | Solver failure telemetry endpoint | **Done** | Migration `010_solver_telemetry.sql`; `useSolver.ts:24-56` `_fireTelemetry` posts on every solve | None |

### P1 items

| ID | Item | Status | Evidence | Remaining work |
|----|------|--------|----------|----------------|
| P1.1 | Low-confidence opt-in UI | **Done** | `HandAnalysisPane.tsx:654-681` — hidden-by-default with "Show approximate analysis" button; `lowConfidenceOptIns` state per street | None |
| P1.2 | Worker per-solve (not singleton) | **Done** | `useSolver.ts:73` `const client = createSolverClient()` — new client per solve | None |
| P1.3 | Adaptive timeout (quick=30s, full=180s, heartbeat=15s) | **Done** | `client.ts:51-54` `TIMEOUTS` constants; `client.ts:61,123,131` heartbeat timer | None |
| P1.4 | Range weight floor (0.001 minimum) | **Done** | `builder.py:81` `_MIN_RANGE_WEIGHT_THRESHOLD = Decimal("0.001")`; `builder.py:77` `_MIN_HAND_CLASSES = 5`; `builder.py:79` `_MIN_RANGE_WEIGHT = Decimal("0.01")` | None |
| P1.5 | Envelope JSON Schema + strict validation | **Done** | `backend/schemas/scenario_envelope.json` exists; `builder.py:402-465` validates; `lib.rs:552-650` preflight mirrors | None |
| P1.6 | Honest UI labels per confidence tier | **Done** | `HandAnalysisPane.tsx:62-66` `CONFIDENCE_LABEL` — "GTO Analysis — High Confidence", "Approximate GTO — Medium Confidence", "Not GTO — Multiway Approximation" | None |
| P1.7 | Coach tab solver confidence badge | **Done** | `HandAnalysisPane.tsx:523-533` passes `solverConfidence` to CoachTab | None |

### P2 items

| ID | Item | Status | Evidence | Remaining work |
|----|------|--------|----------|----------------|
| P2.1 | Bet tree dedup by effective chips | **Done** | `builder.py:509-571` `_validate_bet_tree_degeneracy` detects collapsed trees; `builder.py:595-644` `_compute_effective_bet_sizes` computes post-dedup labels | None |
| P2.2 | HU pot transparency in UI | **Done** | `HandAnalysisPane.tsx:282-296` `potTransparencyText` — shows "Pot modeled as X bb (actual table pot: Y bb)" for multiway | None |
| P2.3 | Solve regression fixtures (20+ real-world envelopes) | **Partial** | `solver-wasm/tests/fixtures/regression/` has 5 fixtures (`degenerate_allin_tree.json`, `near_degenerate_spr_1_2.json`, `empty_range_after_removal.json`, `wide_ranges_deep_stack.json`, `multiway_metadata.json`, `fallback_range.json`); CI at `.github/workflows/regression.yml` | Need 15+ more fixtures from real CoinPoker hands; CI needs to run `cargo test regression` on PR |
| P2.4 | Decision-node export via history path | **Partial** | `lib.rs:363-416` `export_strategy` accepts `history_path_json`; `worker.ts` passes decision-node history; `hand-context.ts:272-350` `buildDecisionNodeHistory` constructs paths; `lib.rs:418-512` `get_actions_at` for intermediate node lookups | Depth-2+ navigation often falls back to root when intermediate action lists aren't pre-fetched; test coverage of multi-step history paths is thin |
| P2.5 | Action matching with pot-at-action | **Partial** | `builder.py:369-376` emits `actions_before_hero` and `pot_at_hero_action_bb`; `hand-context.ts:113-146` `nearestSizeAction` uses pot-at-action for bet-fraction mapping | Correctness of backend pot-at-action computation unverified; edge cases around raises (raise_to vs amount) need review |
| P2.6 | Auto-downgrade full→quick on timeout | **Done** | `client.ts:136-168` — full solve timeout → respawn → retry in quick mode; `result.downgradedToQuick = true` | None |
| P2.7 | Hand score only aggregates HIGH confidence streets | **Done** | `confidence.ts:78-124` `computeHandScore` filters `confidence === "high"`; `HandAnalysisPane.tsx:199-248` duplicate | None |

---

## 8. P0 implementation order for HU v1

Remaining work to ship trustworthy HU grading:

1. **Regression test coverage (P2.3):** Add 15+ real CoinPoker hand envelopes to `solver-wasm/tests/fixtures/regression/`. Run `cargo test regression` in CI. Verify all envelopes pass preflight and a 10-iteration solve without panic.
   - **Owner:** WASM / Backend
   - **Done when:** CI regression job passes on PR with ≥20 fixtures covering HU high/medium/low, multiway, degenerate SPR, empty range, deep stack, min pot.

2. **Decision-node export hardening (P2.4):** Fix depth-2+ history path fallback so intermediate node action lists are pre-fetched. Add golden tests for known action sequences.
   - **Owner:** Frontend / WASM
   - **Done when:** A hand where villain bets → hero raises → villain calls → hero acts again gets strategy exported at the correct tree depth (not root).

3. **Pot-at-action correctness (P2.5):** Backend replay logic in `_pot_at_hero_action_bb` must account for all action types (call, bet, raise, all-in) and produce correct pot size at hero's decision point. Write property tests comparing to hand-calculated pots.
   - **Owner:** Backend
   - **Done when:** A golden HU hand's `pot_at_hero_action_bb` matches manual calculation within 0.1 bb.

4. **Hand score "50 = Insufficient data" UX clarity:** The score bar shows 50/100 for hands with zero gradeable streets. Add explicit text: "Insufficient data — no high-confidence streets to grade. Hand score requires clean heads-up spots."
   - **Owner:** Frontend
   - **Done when:** Multiway-only hands display the "Insufficient data" note prominently.

5. **Golden acceptance tests:** Define and run the acceptance criteria in §9 against real hand fixtures.
   - **Owner:** Cross-stack
   - **Done when:** All acceptance tests pass or documented as "Unverified — requires test run".

---

## 9. Acceptance tests

| # | Test | Expected result | Tolerance |
|---|------|----------------|-----------|
| 1 | Golden HU flop from `backend/tests/fixtures/coinpoker/` → `build_scenario` | `confidence = "high"`, `pot_chips_hu_model == pot_chips_total_table`, `pot_error_pct == 0`, `spr ≥ 1.0` | pot ± 0.1 bb |
| 2 | Known HU range + known hero combo + action → grade | `grade.quality` within expected band (e.g. hero folds when solver says fold is best → `solid`) | EV gap ± 0.3 bb for quick mode |
| 3 | Multiway hand (3+ players alive flop) → `build_scenario` | `confidence = "low"`, `is_multiway_approximation = true`, `pot_error_pct > 0` | None |
| 4 | Multiway hand → hand score | `score = 50`, `excludedStreets ≥ 1`, "Insufficient data" note shown | None |
| 5 | HU medium confidence (range fallback) → hand score | Street excluded from score; grade grid dimmed at 60% opacity; amber "Approximate GTO" banner | None |
| 6 | Degenerate SPR envelope (SPR < 0.5) | `build_scenario` raises `ScenarioBuildError` with reason `solver_input_unsolvable_shallow_spr`; preflight rejects with structured error | None |
| 7 | Regression fixture CI (`solver-wasm/tests/fixtures/regression/*.json`) | All fixtures pass `preflight` → `init_game` → 10-iter `solve_step` → `export_strategy` without panic or OOB | None |
| 8 | Worker crash detection: solve on a valid envelope, kill worker mid-solve | `client.ts` detects crash via `onerror`, marks `dead`, rejects pending with `error_class: "worker_crashed"`, next solve spawns fresh worker | Recovery ≤ 2s |
| 9 | Low-confidence opt-in UX | Multiway solve result shown hidden with "Solver analysis hidden" banner; clicking "Show approximate analysis" reveals grid with amber "Not GTO" badge and "Shown by request" | None |
| 10 | Turn/river HU after multiway flop | Turn and river streets independently evaluated; if HU and range library hit → `confidence = "high"` → gradeable | None |

---

## 10. Open questions

| # | Question | Recommended default | Impact if wrong |
|---|----------|---------------------|-----------------|
| 1 | Should `score = 50` for zero-gradeable streets become `null`? | Keep `50` for v1 with "Insufficient data" note. Re-evaluate after 2 weeks of user feedback. | Users may misinterpret a middling score as meaningful. Hand list sorting/filtering would need to handle null. |
| 2 | Is `pot_at_hero_action_bb` correct for all action types (especially raises)? | Assume correct pending property test. If tests fail, fix replay logic in `_pot_at_hero_action_bb`. | Action mapping accuracy for bet/raise streets. |
| 3 | Should full mode (200 iter) be required for final grades? | No — quick mode is authoritative for v1. Add full-mode as optional "Verify accuracy" button. | Marginal grade flips (mixed ↔ solid) on borderline spots. |
| 4 | Are `actions_before_hero` and decision-node history paths complete for all postflop action patterns (check-raise, bet-3bet, multi-street)? | Assume partial — depth-2+ paths may fall back to root. Accept root-node grading for v1. | Grades on non-root decision nodes may compare hero's action to street-start strategy rather than decision-node strategy. |
| 5 | What percentage of real CoinPoker hands produce `high` confidence? | Unknown — requires telemetry data. If < 20%, the product's grading feature is too narrow. | If high-confidence coverage is very low, the product may need range library expansion or relaxed confidence rules before grading is broadly useful. |

---

## 11. Appendix: file index

| Concept | Primary source file(s) |
|---------|----------------------|
| Scenario build + confidence | `backend/app/scenario/builder.py` |
| Range parsing + fallback | `backend/app/scenario/ranges.py` |
| Scenario API endpoint | `backend/app/routers/hands.py` |
| Solver telemetry endpoint | `backend/app/routers/solver.py` |
| Envelope JSON schema | `backend/schemas/scenario_envelope.json` |
| Scenario response types | `backend/app/schemas.py` |
| WASM exports (init/solve/export/preflight) | `solver-wasm/src/lib.rs` |
| Bet tree construction | `solver-wasm/src/bet_tree.rs` |
| Range conversion | `solver-wasm/src/range_convert.rs` |
| Strategy export format | `solver-wasm/src/strategy_export.rs` |
| Regression fixtures | `solver-wasm/tests/fixtures/regression/` |
| Regression CI | `.github/workflows/regression.yml` |
| Worker lifecycle + timeouts | `frontend/src/lib/solver/client.ts` |
| WASM worker bridge | `frontend/src/lib/solver/worker.ts` |
| Solver hook (solve + telemetry) | `frontend/src/lib/solver/useSolver.ts` |
| Hero action inference + decision paths | `frontend/src/lib/solver/hand-context.ts` |
| Grade computation | `frontend/src/lib/hand-analysis/grading.ts` |
| Hand score + confidence labels | `frontend/src/lib/hand-analysis/confidence.ts` |
| Hand analysis UI (score bar, solve buttons, confidence gating) | `frontend/src/components/hand-analysis/HandAnalysisPane.tsx` |
| Solver tab confidence gating | `frontend/src/components/hand-review/SolverTab.tsx` |
| API types | `frontend/src/types/api.ts` |
| Migration (solver_telemetry) | `backend/migrations/010_solver_telemetry.sql` |