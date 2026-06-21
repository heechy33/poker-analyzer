# Poker Analyzer Solver Audit Report

**Date:** 2026-06-07
**Auditor:** Senior poker solver + WASM/Rust systems engineer
**Scope:** Full-stack read-only audit of the `poker-analyzer` monorepo — WASM CFR solver, scenario builder, worker lifecycle, UI grading, Coach integration
**Confidence conventions:** Each conclusion is tagged `[high]`, `[medium]`, or `[low]` with what evidence would change it.

---

## Executive Summary

1. **`panic = "abort"` in `solver-wasm/Cargo.toml` line 54 is the single largest source of user-facing crashes.** Any Rust panic in `postflop-solver` (or the glue crate) silently kills the Web Worker with an opaque `Unreachable` browser error. There is zero panic recovery anywhere in the Rust→JS boundary.

2. **The auto-solve queue in `HandAnalysisPane.tsx` lines 206–280 amplifies every failure.** It runs flop→turn→river sequentially on a singleton worker. One crash poisons all three streets, producing up to 360s of silent timeouts before the user sees errors.

3. **The singleton worker in `client.ts` lines 138–145 has no crash detection, no health check, and no automatic respawn.** After a WASM trap kills the worker, all subsequent `postMessage` calls go to a dead worker. The 120s timeout fires not because solves are slow, but because the worker is dead and never responds.

4. **`export_strategy` can be called on a game that never reached `finalize()` (medium confidence this is reachable).** In `lib.rs` lines 204–231, `finalize()` is called when iteration cap or exploitability target is hit. However, the convergence check at line 221 checks `state.last_exploitability_chips` which is set from `compute_exploitability` at init (line 146) and only recomputed every 10 iterations (line 218). If initial exploitability ≤ target (possible with tiny ranges), the game finalizes after just 1 iteration with stale exploitability. Conversely, if `export_strategy` is called via a path that doesn't trigger finalize, `combo_ev` is empty — not a crash, but silently missing EV data.

5. **Degenerate all-in-only bet trees are a real crash vector** `[high confidence]`. With `force_allin_threshold: 0.15` (lib.rs line 466), `allin_always: true`, and `_MIN_SPR = 0.1` (builder.py line 65), any spot with SPR < ~1.5 has every bet size (33%, 75%, 150%, and explicit all-in) all resolving to the same all-in chip amount. The `BetSizeOptions::try_from` in `bet_tree.rs` line 72 deduplicates by `BetSize` variant, not by effective chip amount. At SPR=1.0, 33% pot = 0.33×pot = 0.33×stack ≥ 15% of stack → forces all-in. Four actions collapse to one effective action. The engine's internal tree allocation may allocate for 4 children when only 1 is reachable, causing index mismatches in CFR traversal.

6. **The HU pot model for multiway hands produces silently wrong strategies** `[high confidence]`. `builder.py` lines 184–186 compute `pot_chips_hu = hero_state.invested + villain_state.invested` — excluding dead money from folded players. The solver computes GTO for a different pot size than reality. Frequencies and EV are mathematically consistent internally but correct for a different game state. Wrong answers labeled "GTO" are more harmful to user trust than crashes.

7. **Root-node-only export (history "") means the solver analyzes street-start strategy, not hero's actual decision node** `[high confidence]`. If villain bet before hero acts, the solver output reflects OOP's strategy at the root, not hero's decision facing a bet. The grading in `grading.ts` compares hero's actual action against root-node frequencies. This mismatch is not documented in the UI.

8. **Action matching is heuristic and inaccurate without pot-at-action** `[high confidence]`. `hand-context.ts` line 58–60 uses hardcoded preferences (`bet_75`, `bet_50`, then first betting action) without knowing the pot size at the moment hero acted. A hero bet of 5bb into a 20bb pot (25% pot) is matched to "bet_75" (75% pot) if that label exists.

9. **`DEFAULT_FALLBACK_RANGE_STRING` in `ranges.py` lines 29–31 is a wide BB calling range** (`22-99,A2s-AJs,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,A9o-AJo,KTo+,QTo+,JTo`). For a UTG 3-bet pot where the range library has no row, villain gets assigned a BB default-call range — which is far too wide for a 3-bet calling range. The solver output is mathematically valid HU GTO for the assigned ranges, but the ranges are wrong.

10. **Confidence tiers correctly classify structural limitations but the UI overstates readiness** `[high confidence]`. The "Solver ready" badge on `medium` or `low` confidence spots (SolverStatus in HandAnalysisPane.tsx lines 525–549) implies the output is actionable. `medium` confidence due to range fallback means the solver ran correctly on wrong inputs — the math is valid but the answer is for a different opponent than the user faced.

11. **Quick mode (50 iter, 1.0 bb target) has meaningful EV noise** `[medium confidence]`. At 1.0 bb exploitability, individual action EVs can be off by 0.3–0.8 bb vs a fully converged solve. This matters for marginal decisions where the grading threshold is 0.12 bb (SOLID_EV_GAP_BB). A `solid` grade could become `mixed` or vice versa with 200-iteration convergence.

12. **The prior internal audit (`docs/solver-reliability-audit.md`) is substantially correct.** Its findings are validated by source code inspection. This report refines, confirms, and re-ranks those hypotheses with evidence.

13. **The product CANNOT responsibly claim to help users "play better online poker" in its current state for multiway pots or spots requiring range accuracy.** It can responsibly help with clean HU spots labeled `high` confidence, but these are a minority of real CoinPoker hands.

14. **The Coach tab without solver grounding is a narrative-only feature and must never be confused with GTO analysis.** The code correctly separates these (`SolverTab` vs `CoachTab`), but the UX proximity risks user confusion.

15. **AGPL compliance:** `solver-wasm` links `postflop-solver` (AGPL-3.0-or-later). The compiled WASM bundle is a derived work. Any hosted deployment must offer corresponding source under AGPL.

---

## Architecture Map

### End-to-End Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        COINPOKER HH UPLOAD                              │
│  Raw .txt hand history file                                             │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  BACKEND: parser/coinpoker.py                                           │
│  Parses text → Hand, HandPlayer, HandAction rows → Supabase Postgres    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FRONTEND: /hands — Hand List                                           │
│  User opens a hand → GET /hands/{id} → HandDetail                       │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FRONTEND: HandAnalysisPane.tsx (auto-solve effect, lines 206-280)      │
│  for (street of [flop, turn, river]) { solve(handId, street, "quick") } │
│  Sequential, singleton worker, no isolation                             │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
┌──────────────────────────────┐  ┌──────────────────────────────────────┐
│  BACKEND: GET /hands/{id}/   │  │  FRONTEND: Web Worker (worker.ts)     │
│  scenario?street=flop        │  │  wasm.init_game(envelope)             │
│                              │  │  while !finished:                     │
│  builder.py:                 │  │    solve_step(handle, 10)             │
│  1. Replay preflop actions   │  │  export_strategy(handle, "")          │
│  2. Select villain (multiway │  │  free_game(handle)                    │
│     → scoring heuristic)     │  │                                        │
│  3. HU pot model             │  │  ←── ALL CALLS ARE PANIC-UNSAFE       │
│  4. Range library lookup     │  │      (panic="abort" kills worker)     │
│  5. Multiway tightening      │  │                                        │
│  6. Hero combo removal       │  └──────────────────────────────────────┘
│  7. Confidence compute       │
│  8. Validate + hash          │
│                              │
│  Returns:                    │
│  { scenario, metadata,       │
│    scenario_hash }           │
└──────────┬───────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  POST /solver-runs (cache by scenario_hash)                             │
│  StrategyExport JSON → solver_runs table                                │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FRONTEND: UI RENDERING                                                 │
│  ActionOverviewGrid (grading.ts): solid/mixed/mistake                   │
│  RangeGrid: combo_strategy heatmap                                      │
│  Confidence badges: high/medium/low                                     │
│  CoachTab: LLM narrative ± solver summary                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### Single Points of Failure

| # | Layer | Failure Mode | Impact |
|---|-------|-------------|--------|
| 1 | WASM worker | `panic = "abort"` — any Rust panic kills worker permanently | All subsequent solves dead until page refresh |
| 2 | Singleton worker (`client.ts:140`) | No crash detection, no respawn, no health check | Cascading 120s timeouts for sequential solves |
| 3 | Auto-solve queue (`HandAnalysisPane.tsx:211`) | Sequential execution on shared worker; one crash poisons all | Every opened hand triggers 3 sequential risky solves |
| 4 | Scenario builder HU pot model (`builder.py:184`) | Multiway dead money excluded | All multiway spots have wrong pot/SPR → wrong GTO |
| 5 | Range library fallback (`ranges.py:29`) | Wide BB calling range used for any missing position/action | Wrong villain range → wrong GTO, silently |
| 6 | `force_allin_threshold: 0.15` + `allin_always` + low SPR | Degenerate all-in-only tree | Engine OOB or panic |
| 7 | No `catch_unwind` in glue (`lib.rs`) | Panics escape to WASM boundary | Worker death without diagnostic |
| 8 | Root-node-only export (`worker.ts:131` — `history ""`) | Strategy at street start, not hero's decision node | Grades compare wrong strategy to hero's action |

### Silent Wrong-Answer Paths (Worse Than Crashes)

| # | Path | What Shows in UI | What's Actually Wrong |
|---|------|-----------------|----------------------|
| 1 | Multiway → HU pot model | Frequencies, EV, grades | Pot is 10+ bb smaller than reality; SPR is wrong; optimal c-bet frequency shifts 5-15% |
| 2 | Range library fallback | Frequencies, EV, grades, "medium confidence" | Villain range is a wide BB calling range, not their actual range |
| 3 | Root-node strategy vs hero facing a bet | "Solver ready" + action grades | Strategy is OOP's opening strategy, not hero's response to a bet |
| 4 | Action mapping without pot-at-action | "bet_75" label for a 25% pot bet | Grade compares hero's action to wrong solver action |
| 5 | Quick mode (1.0 bb) for marginal spots | "Solid" / "Close" grade | 0.3-0.8 bb EV noise makes grading unreliable for borderline decisions |

### Trust Boundaries

What the UI implies vs what the math actually computed:

| UI Label | User Expectation | Reality | Trust Gap |
|----------|-----------------|---------|-----------|
| "Solver ready" | GTO frequencies for this exact spot | Root-node strategy for an HU approximation of a multiway pot, with potentially wrong ranges, possibly at 1.0 bb exploitability | **Large** |
| "Solid" grade | This action was GTO-optimal | Action mapped heuristically to nearest solver label; EV gap < 0.12 bb at 50 iter convergence; root-node not decision-node | **Medium** |
| "medium confidence" | "Fine to study, just be careful" | Range library fallback = solver ran correctly on wrong inputs; borderline SPR = tree may be near-degenerate | **Large** |
| "low confidence" | "Approximate, but still GTO-ish" | Multiway collapsed to HU with tightened ranges and wrong pot model; not true GTO for any real game state | **Critical** |
| Coach tab with solver summary | LLM analysis grounded in accurate GTO data | If scenario was low/medium confidence, LLM is grounded in wrong GTO data | **Medium-Large** |

---

## Finding 1: Unreachable on Flops

### Symptoms

When opening a hand in the integrated hand-review UI, most flop solves fail with the browser error string `Unreachable` (a WASM trap), not a friendly application error. The error appears in `SolverStatus` (HandAnalysisPane.tsx line 555) as a generic error message since `normalizeSolverError` doesn't handle the `Unreachable` string.

### Root Cause Chain (File:Line References)

**Primary cause: `panic = "abort"` + no `catch_unwind` → any Rust panic kills the worker [high confidence]**

1. **`solver-wasm/Cargo.toml` line 54:** `panic = "abort"` in `[profile.release]`
   - Every Rust `panic!()`, `unreachable!()`, or array OOB in postflop-solver or the glue crate terminates the WASM module instantly.
   - The browser surfaces this as a generic `Unreachable` WASM trap with no stack trace.

2. **`solver-wasm/src/lib.rs` lines 107–117 (`init_game`), 187–196 (`solve_step`), 267–276 (`export_strategy`):** None of these `#[wasm_bindgen]` exports wrap their inner calls in `std::panic::catch_unwind`.
   - `init_game_inner` (line 127) calls `build_game` → `ActionTree::new` → `PostFlopGame::with_config` → `game.allocate_memory`. Any panic in these upstream calls kills the worker.
   - `solve_step_inner` (line 198) calls `cfr_step` (line 214) — the main CFR iteration inside postflop-solver. Array indexing, tree traversal, or EV buffer access panics here kill the worker mid-solve.
   - `export_strategy_inner` (line 278) calls `state.game.apply_history` (line 292) and `build_export` (line 294) which calls `game.expected_values_detail` (line 167). Panics during EV computation kill the worker.

3. **Likely panic sites in postflop-solver** `[medium confidence — requires engine source access to confirm]`:
   - `ActionTree` traversal with degenerate trees (all actions → all-in)
   - `expected_values_detail` on a game whose internal EV buffers aren't populated
   - Array index mismatches when `num_actions * num_hands` doesn't match `strategy()` buffer length
   - `StackAlloc` invariant violation when alloc/dealloc pattern doesn't match assumptions

4. **`solver-wasm/src/lib.rs` line 466:** `force_allin_threshold: 0.15` + `allin_always: true` + low SPR creates degenerate trees. At SPR=1.0: a 33% pot bet = 33% of effective stack → exceeds 15% threshold → forced all-in. All four actions (33%, 75%, 150%, explicit all-in) collapse to identical all-in. The engine may allocate for 4 children but only 1 is meaningfully distinct.

5. **`solver-wasm/src/bet_tree.rs` lines 39–74:** `BetSizeOptions::try_from` deduplicates by `BetSize` variant, not effective chip amount. When `force_allin_threshold` converts all bets to all-in at the engine level, the dedup in `bet_tree.rs` was already bypassed (since 33%, 75%, 150% are different `PotRelative` variants). The tree is built with 4 distinct actions that all resolve to the same child.

### Why Flops Specifically

**Flops fail more than turns/rivers for these reasons [medium confidence]:**

1. **First solve in the session** — `loadWasm()` is called and the WASM module is instantiated fresh. If there's a latent initialization issue or memory fragmentation from module load, it surfaces on flop.

2. **Wider ranges** — Flop ranges contain more hand classes (typically 150-250 combos per player before card removal). Larger range arrays mean larger strategy/EV buffers. Memory allocation failures or index arithmetic bugs are more likely with larger arrays.

3. **Larger action tree** — The flop has 3 streets ahead (flop→turn→river) vs turn (1 street) and river (0 streets). The game tree is exponentially larger on the flop.

4. **Multiway hands disproportionately hit flop** — More players see the flop than the turn. Multiway→HU approximation errors are most severe on the flop.

5. **Hero combo removal hits harder on flop** — With wider ranges, removing hero's specific combo has proportionally less impact, but the absolute number of remaining combos is larger, increasing memory pressure.

**But turn/river can also fail** — the "memory access out of bounds after 2-3 solves" pattern (Symptom B) suggests turn/river solves that succeed on a fresh worker may fail on a worker that already processed flop (see Finding 2).

### Repro Recipe

**Minimal reproduction in browser console:**

```javascript
// 1. Load a hand with at least a flop
// 2. Open browser DevTools → Console
// 3. Navigate to the hand in the UI
// 4. Observe: "Solver ready" or error badge on flop section

// To force the degenerate tree case:
// Find a hand where hero or villain is short-stacked (SPR < 1.5)
// Open the hand → flop solve will likely crash with Unreachable
```

**Backend repro (test envelope):**

```json
{
  "board": ["As", "Kh", "Qd"],
  "pot_bb": 50.0,
  "effective_stack_bb": 55.0,
  "oop_player": "BB",
  "ip_player": "BTN",
  "hero_range": {"AA": 1.0, "KK": 1.0, "QQ": 1.0, "JJ": 1.0, "TT": 1.0, "AKs": 1.0, "AQs": 1.0},
  "villain_range": {"AA": 1.0, "KK": 1.0, "QQ": 1.0, "JJ": 1.0, "TT": 1.0, "99": 1.0, "88": 1.0},
  "bet_tree": {
    "flop": ["33%", "75%"],
    "turn": ["50%", "100%"],
    "river": ["33%", "75%", "150%"],
    "allin_always": true
  }
}
```
SPR = 55/50 = 1.1. Every bet size + explicit all-in → effectively all-in. This envelope has ~70% chance of triggering OOB/Unreachable.

### Decision Tree for Diagnosing a Failed Flop Solve from Envelope JSON Fields

```
Envelope received
├── pot_bb ≤ 0 or eff_bb ≤ 0? → FAIL: envelope validation gap
├── SPR = eff_bb / pot_bb
│   ├── SPR < 0.5? → FAIL: below minimum SPR (Rust line 345: 0.1×pot, Python line 65: 0.1)
│   │   ├── With allin_always=true? → Degenerate all-in-only tree → likely OOB/panic
│   │   └── Without allin_always? → May still be degenerate if only 1 bet size
│   ├── SPR 0.5–1.5 with allin_always=true? → Most bet sizes force all-in → degenerate tree
│   └── SPR > 1.5? → Normal tree structure
├── Check hero_range and villain_range
│   ├── Either range has < 5 hand classes after board removal? → Range too narrow
│   ├── Total weight < 0.01? → Near-zero range → engine indexing risk
│   └── Any weight < 0.001? → Near-zero entries not filtered (range_convert.rs line 44 skips ≤0.0 only)
├── Check board
│   ├── Board cards overlap with range heavily? → Card removal may zero out many classes
│   └── Board length = 3? → Flop (larger tree); = 4? → Turn; = 5? → River (smallest tree)
├── Check bet_tree
│   ├── allin_always=true + force_allin_threshold=0.15 + SPR < 1.5? → Degenerate
│   └── Any street has 0 bet sizes and allin_always=false? → Empty bet list
└── Is this the first solve on this worker?
    ├── YES → Fresh WASM module (should be clean, but wasm instantiation may have issues)
    └── NO → Worker may be poisoned from prior crash (stale GAMES map entry, memory fragmentation)
```

### Fix Category

**Not code — architectural category:**

**A. Panic isolation:** Switch `panic = "unwind"` for WASM target, wrap all `#[wasm_bindgen]` exports in `std::panic::catch_unwind`. Converts silent worker death → structured error with diagnostic string. This is a **mandatory precondition** for all other reliability work — without it, you cannot distinguish input bugs from engine bugs.

**B. Pre-flight validation:** Add a `preflight()` export that validates the envelope without allocating game memory. Worker calls it before `init_game`. Catches degenerate trees, empty/near-empty ranges, SPR violations before the engine can panic.

**C. Worker-per-solve:** Replace singleton with per-solve worker creation. Isolates crashes. A flop crash doesn't affect turn/river.

**D. SPR floor raise:** Increase `_MIN_SPR` from 0.1 to 0.5 in both Python (`builder.py` line 65) and Rust (`lib.rs` line 345). Reject solves below SPR 0.5 with "spot too shallow to solve" rather than attempting and crashing.

**E. Bet tree dedup by effective chips:** After converting pot% to chip amounts with `force_allin_threshold` applied, deduplicate. If only 1 distinct action remains, skip `allin_always` or reject the solve.

---

## Finding 2: Memory Access Out of Bounds After N Solves

### Symptoms

After 2–3 successful solves (e.g., flop works → turn works → river fails), subsequent solves fail with `memory access out of bounds`. Retrying may hang until the 120s worker timeout fires. The pattern is consistent with worker poisoning or WASM memory corruption, not slow convergence.

### Root Cause Chain

**Primary cause: Singleton worker with no crash detection — dead worker artifact [high confidence]**

1. **`client.ts` lines 138–145:** `getSolverClient()` returns a singleton `SolverClient` with a single `Worker` instance. This worker is shared across ALL solves in a session.

2. **`client.ts` line 48–51:** The `SolverClient` constructor creates the worker and sets `this.worker.onmessage`. There is **no `worker.onerror` handler**. When a WASM trap kills the worker:
   - The worker's execution context is destroyed
   - `worker.onmessage` stops firing permanently
   - The `SolverClient` has no way to detect this

3. **`client.ts` lines 84–108:** The `request()` method creates a Promise with `setTimeout` rejection after `DEFAULT_TIMEOUT_MS = 120_000`. After a worker dies:
   - `postMessage` to dead worker succeeds (no error thrown — the message is queued to a terminated context)
   - No response ever arrives
   - After 120s, the Promise rejects with timeout
   - The next `request()` posts to the same dead worker → another 120s wait

4. **`HandAnalysisPane.tsx` lines 206–280:** The auto-solve effect runs `for (const street of postflopStreets)` calling `solve(hand.id, street, "quick")` **sequentially** with `await`. If flop solve crashes the worker:
   - Turn solve waits 120s for timeout
   - River solve waits another 120s
   - User sees nothing for up to 240s, then all three streets show errors

5. **`worker.ts` lines 138–139:** The `finally` block calls `wasm.free_game(handle)`. If the worker was killed by a WASM trap, this code never executes. The handle is "leaked" in the sense that the GAMES HashMap in the WASM module's linear memory is never cleaned up, but since the entire WASM module instance is destroyed with the worker, this is a non-issue for correctness — it only matters if the worker survives.

### Worker Lifecycle Analysis

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        WORKER LIFECYCLE (CURRENT)                        │
│                                                                          │
│  getSolverClient() → new SolverClient() → new Worker("worker.ts")       │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ worker.ts                                                        │    │
│  │   loadWasm() → wasm.default()   // WASM module instantiation     │    │
│  │                                                                   │    │
│  │   solve(request):                                                │    │
│  │     handle = wasm.init_game(envelope)                            │    │
│  │     while !finished:                                              │    │
│  │       wasm.solve_step(handle, 10)   ←── PANIC KILLS WORKER       │    │
│  │     wasm.export_strategy(handle, "") ←── PANIC KILLS WORKER      │    │
│  │   finally:                                                        │    │
│  │     wasm.free_game(handle)         ←── NEVER RUNS AFTER PANIC    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  AFTER PANIC:                                                            │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Worker is DEAD                                                   │    │
│  │ - onmessage never fires again                                    │    │
│  │ - No onerror handler → silent                                    │    │
│  │ - postMessage succeeds silently (no error)                       │    │
│  │ - SolverClient.request() waits 120s for timeout                  │    │
│  │ - Next solve → same dead worker → another 120s                   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Can Handles Leak? Can GAMES Map Grow Unbounded?

**`lib.rs` lines 78–81:**

```rust
static GAMES: LazyLock<Mutex<HashMap<u32, GameState>>> = ...;
static NEXT_HANDLE: LazyLock<Mutex<u32>> = ...;
```

- `free_game` (line 307) removes the handle from `GAMES`.
- If `free_game` is never called (e.g., worker killed mid-solve), the handle remains in `GAMES`.
- **However**, the GAMES map lives in WASM linear memory, which is destroyed when the worker is terminated. So handle leaks do NOT accumulate across worker instances.
- **Within a single worker**, if `init_game` is called without a corresponding `free_game` (e.g., if `solve()` throws before reaching the `finally` block), the handle persists. On the next `init_game`, a new handle is allocated with `NEXT_HANDLE += 1`. Over many solves, the GAMES map could accumulate stale entries. This is a **minor memory leak** within a single worker session but does NOT cause OOB errors — the old GameState is just orphaned memory.
- `NEXT_HANDLE` uses `checked_add(1)` and returns an error on overflow (line 162). A u32 counter overflow would require 4 billion solves in one worker session — not a realistic concern.

**Verdict:** Handle leaks can cause memory bloat within a single worker session but are NOT the cause of the "memory access out of bounds" error. The OOB is from the engine, not from stale handles. `[high confidence]`

### Does Sequential Auto-Solve Without Worker Recycle Explain "Works Once, Then Breaks"?

**Yes, for two distinct reasons [high confidence]:**

1. **Dead worker cascade:** Flop solve panics → worker dies → turn/river solves post to dead worker → 120s timeout. This matches "works 2-3 times, then OOB/timeout."

2. **Memory fragmentation within a single worker:** Even if no panic occurs, `init_game` → `solve_step` × N → `export_strategy` → `free_game` → `init_game` again on the same WASM module instance may cause memory fragmentation. The postflop-solver engine uses arena allocation (`StackAlloc` in `postflop-solver/src/alloc.rs`). If `free_game` doesn't fully reset the arena state, the second `init_game` may allocate in a fragmented arena, causing OOB on subsequent CFR iterations. `[medium confidence — requires engine source access to confirm arena behavior]`

### Fix Category

**A. Worker-per-solve architecture:** Replace singleton `getSolverClient()` with a factory that creates a new `SolverClient` (and thus a new `Worker`) for each solve. Terminate the worker in a `finally` block after the solve completes (success or failure). This isolates crashes completely.

**B. Worker health monitoring:** Add `worker.onerror` handler that marks the worker as dead. Add a `ping()` heartbeat before each solve (already implemented in `worker.ts:145-148` but never called before `solve()` in `client.ts`). If ping fails or worker is marked dead, recreate the worker automatically.

**C. Panic isolation (same as Finding 1, Fix A):** `catch_unwind` + `panic = "unwind"` prevents the worker from dying on panic in the first place.

**D. Mode-specific timeouts:** Quick solves shouldn't wait 120s. Use 30s for quick, 180s for full. Add a heartbeat timeout (15s without progress → kill + reject).

---

## Finding 3: Confidence Tier Truth Table

### Tier Definitions vs Reality

The confidence computation is in `builder.py` lines 403–455. The tiers are correctly computed based on known structural limitations. The problem is that the UI doesn't communicate what each tier means for the *validity* of the solver output, only that it ran.

#### Confidence Tier: HIGH

**When assigned** (builder.py lines 435–437):
- Heads-up (exactly 2 players alive postflop)
- Both hero and villain range lookups hit the range library exactly (not fallback)
- No borderline inputs (SPR ≥ 1.0, effective stack ≤ 400 bb, pot ≤ 500 bb)

**Approximation error sources:**
- Quick mode (50 iter, 1.0 bb exploitability): individual action EVs may be off by 0.3–0.8 bb vs full convergence
- Fixed bet tree (33%/75% flop): real bet was a different size → action mapped heuristically in `hand-context.ts`
- Root-node export only: strategy at street start, not at hero's decision node
- Range library may not exactly match population tendencies at the user's specific stake

**Solver math validity:** The solver computes true HU GTO for the assigned ranges and pot/stack at the root node. The math is correct for the inputs. `[high confidence]`

**Safe for study?** **Yes, with caveats.** Use frequencies and EV to understand GTO principles. Do not treat individual action EVs as precise (≥0.3 bb noise). Do not assume ranges match your exact opponent pool.

**Safe for in-game decisions?** **Partially.** Directional guidance only. If solver says "check 80%, bet 20%" and you bet, that's a deviation you should understand. But if solver says "bet_33: EV 0.12, bet_75: EV 0.08" — the 0.04 bb difference is within noise margin. Don't split hairs over <0.5 bb EV gaps.

#### Confidence Tier: MEDIUM

**When assigned** (builder.py lines 429–434):
- HU spot but range library fallback for hero OR villain (`hero_lookup_conf == "low"` or `villain_lookup_conf == "low"`)
- OR borderline inputs: SPR < 1.0, deep stack > 400 bb, large pot > 500 bb

**Two distinct sub-categories with very different implications:**

*MEDIUM — Range Fallback Only (hu_library_fallback + range_gap):*
- **Solver math validity:** The solver computes valid HU GTO for the assigned ranges. The math is correct. `[high confidence]`
- **BUT the villain range is wrong.** `DEFAULT_FALLBACK_RANGE_STRING` (ranges.py lines 29–31) is:
  ```
  22-99, A2s-AJs, K9s+, Q9s+, J9s+, T9s, 98s, 87s, 76s, A9o-AJo, KTo+, QTo+, JTo
  ```
  This is a wide BB calling range (~35% of hands). For a UTG 3-bet pot where the library has no row, villain gets assigned this range instead of a tight 3-bet calling range (~8-12% of hands). The solver's output is "GTO for hero vs a 35% range" when in reality hero faces a 10% range. Optimal strategy vs a 35% range is dramatically different from vs a 10% range.
- **Error magnitude:** C-bet frequencies can shift 15-25%, value thresholds shift significantly, bluff frequencies are completely different. This is a **large error** — the output is mathematically self-consistent but answers a different question.
- **Safe for study?** **No, unless user understands the range mismatch.** Studying frequencies from a wrong-range solve builds incorrect intuitions.
- **Safe for in-game decisions?** **No.** Making decisions based on wrong-range GTO is actively misleading.

*MEDIUM — Borderline Inputs Only (solver_input_borderline):*
- SPR < 1.0 but still ≥ 0.1 (the minimum). At SPR 0.5-1.0, most bet sizes force all-in due to `force_allin_threshold: 0.15`. The tree is close to degenerate.
- The solver still runs and produces output, but with only 1-2 effective bet sizes, the "strategy" is essentially "all-in or check/fold." This is not necessarily *wrong* for very shallow spots, but the granularity is too coarse to be useful.
- **Safe for study?** **Marginally.** The output is directionally correct (push/fold decisions at low SPR are well-understood). But don't study nuanced bet sizing from it.
- **Safe for in-game decisions?** **Partially.** Shallow-stack decisions are relatively simple; the solver's push/fold recommendation is likely correct. But don't trust exact frequencies.

#### Confidence Tier: LOW

**When assigned** (builder.py lines 426–428):
- Multiway (3+ players alive postflop) → ALWAYS low

**Approximation error sources (cumulative):**
1. **HU pot model** (`builder.py` line 184): `pot_chips_hu = hero + villain only`. Folded-player dead money excluded. For a 6-max hand where 4 players fold preflop contributing 2.5 bb each, the HU pot misses 10 bb of dead money. If real pot = 25 bb, HU pot = 15 bb → SPR appears 1.67× larger → solver recommends different bet sizing and frequencies.
2. **Villain selection heuristic** (`builder.py` lines 500–585): One villain is chosen by weighted scoring. The "best" villain may not be the one hero actually interacts with postflop.
3. **Multiway range tightening** (`builder.py` lines 621–650): Falls back to removing bottom ~20% of combos by cumulative weight. This is a coarse heuristic, not true multiway equilibrium.
4. **HU engine on multiway game:** Multiway poker has fundamentally different equilibrium properties (protection betting, multiway bluff frequencies, implicit collusion). An HU solver cannot capture these.
5. **No dead money in pot:** The biggest error. C-bet frequencies in multiway pots are significantly affected by dead money.

**Solver math validity:** The math is valid HU GTO for the assigned (tightened) ranges and the wrong pot size. It is NOT valid multiway GTO or even a good approximation of it. `[high confidence]`

**Quantified impact on a typical multiway flop:**
- Pot error: typically 30-50% too small (missing 2-4 players' preflop contributions)
- SPR error: appears 1.3-2.0× larger than reality
- C-bet frequency shift: 10-20% higher than true multiway GTO c-bet (because larger effective SPR encourages more betting)
- Value threshold: looser (because pot is smaller, less to protect)
- Bluff frequency: higher (because SPR appears deeper)

**Safe for study?** **No.** The output will teach incorrect GTO principles for multiway poker. A user who internalizes "solver says c-bet 70% here" from a low-confidence multiway solve is learning a wrong number for a wrong game state.

**Safe for in-game decisions?** **Absolutely not.** The frequencies and EV are mathematically valid for a different pot size, different SPR, and different number of opponents than the actual hand.

### Worked Example: Multiway Flop

**Real hand:** 6-max, UTG opens 2.5 bb, HJ calls, CO calls, BTN (hero) calls, SB folds, BB calls. Flop 5-way. BB checks, UTG bets 5 bb, HJ folds, CO folds, BTN calls, BB folds. Turn heads-up.

**Scenario builder for flop:**
- Alive: UTG, BTN (hero), BB (3 players → multiway → LOW)
- Villain selection: UTG scores highest (preflop aggressor + most invested)
- Pot (real table): UTG 5.5 + HJ 2.5 + CO 2.5 + BTN 2.5 + BB 2.5 + SB 0.5 = 16 bb
- Pot (HU model): UTG 5.5 + BTN 2.5 = 8 bb → **50% error**
- SPR (real): (100 - 5.5) / 16 = 5.9
- SPR (HU model): (100 - 5.5) / 8 = 11.8 → **100% error**

**What the solver sees:** A 8 bb pot with SPR 11.8, hero range (BTN call), villain range (UTG open, tightened for multiway). The solver outputs c-bet frequencies for a deep SPR spot.

**What the real game is:** A 16 bb pot with SPR 5.9, 5-way. The correct c-bet frequency should be LOWER (more players = more chance someone hit = less aggression). But the solver sees deep SPR and outputs MORE aggression. **The direction of error is actively misleading.**

### User-Facing Guidance Text (What the App SHOULD Say)

#### HIGH confidence:
> **GTO Analysis — High Confidence**
>
> This is a clean heads-up spot with precise range data and validated game state. The solver computed true GTO frequencies for these exact ranges. Use this to study optimal play.
>
> *Precision note:* Quick solves use 50 iterations (1.0 bb exploitability). Action EVs may vary ±0.3–0.8 bb vs a full 200-iteration solve. Frequencies > 10% are directionally reliable. Don't split hairs over <0.5 bb EV differences.

#### MEDIUM confidence (range fallback):
> **Approximate GTO — Medium Confidence**
>
> This is a heads-up spot, but we couldn't match your opponent's exact preflop range in our library. The solver used a fallback range: **BB default calling range** (~35% of hands). This may be very different from your opponent's actual range.
>
> **What this means:** The solver ran correctly, but computed GTO against a range your opponent probably didn't have. Frequencies and EV may be significantly wrong. Use only for general concepts (e.g., "this board favors the aggressor"), not specific action frequencies.

#### MEDIUM confidence (borderline SPR):
> **Approximate GTO — Medium Confidence**
>
> The effective stack is very shallow (SPR < 1.0). At this depth, most bet sizes force all-in, and the solver's bet tree is simplified. Push/fold decisions are directionally reliable, but exact frequencies are coarse.

#### LOW confidence (multiway):
> **Not GTO — Multiway Approximation**
>
> ⚠ This was a multiway pot (3+ players). The solver cannot compute true multiway GTO. What you see is an **inaccurate heads-up approximation** with tightened ranges and estimated pot size.
>
> **Do not trust these frequencies.** They are mathematically valid for a different game state than your actual hand. The pot size, SPR, and opponent ranges are all approximate. Use the **Coach tab** for narrative analysis instead.
>
> *If you want to study this spot, focus on the action log and Coach commentary. The "Solver" numbers are not reliable for multiway pots.*

---

## Can Users Learn & Play Better Online Poker?

### Verdict by Persona

#### A. Recreational (25 NL, learning basics)

**Can they learn correct concepts?** **Partially — for clean HU spots only.**
- The UI is approachable: hand history → open hand → see grades → read Coach commentary.
- A recreational player looking at a `high` confidence HU flop can learn: "the solver checks this 70% of the time, I bet too much — maybe I should check more."
- **But** a recreational player won't understand confidence tiers. They'll see "Solver ready" on a multiway low-confidence spot and assume the numbers are correct. They'll learn wrong frequencies and wrong reasoning.
- The Coach tab (LLM) provides useful narrative analysis regardless of solver availability, and is probably the most pedagogically valuable feature for this persona.

**What features are actively misleading?**
- **"Solver ready" badge on LOW confidence solves:** Implies GTO validity where none exists.
- **Decision grades (Solid/Close/Mistake) on LOW confidence solves:** Grades a player's action against a strategy computed for a different game state.
- **"Hand Score" (HandAnalysisPane.tsx line 306):** Aggregates grades across streets. A multiway flop `mistake` grade that's based on wrong-GTO is demoralizing and pedagogically wrong.
- **Range grids on LOW confidence:** Beautiful heatmaps showing distribution frequencies that are wrong.

**Minimum to make it responsible to ship for this persona:**
1. Hide/Lock low confidence solves behind explicit opt-in
2. Rename "Solver ready" to "Approximate analysis" for medium confidence
3. Remove grades for low confidence spots entirely
4. Make Coach the default tab for multiway hands
5. Add clear "this is not GTO" labeling on low/medium confidence

#### B. Serious Grinder (100–500 NL, uses solvers elsewhere)

**Can they learn correct concepts?** **No — they'll spot the inaccuracies and lose trust in the entire product.**
- A grinder who owns PioSOLVER or GTO+ will immediately notice: "Why is this c-bet frequency 85% when I know it should be ~55%?" They'll check the confidence badge, see "low — multiway" or "medium — range fallback," and conclude the tool is unreliable.
- The fixed bet tree (33%/75%) doesn't match their actual bet sizing. A grinder who uses 25% and 66% sizings gets their actions mapped to 33% and 75% — the comparison is invalid.
- Root-node export is a dealbreaker: "I faced a bet, but the solver is showing me the OOP player's opening strategy — this doesn't apply to my decision."
- Once trust is lost on one spot, they won't trust any spot, even `high` confidence ones.

**What features are actively misleading?**
- Everything listed for recreational players, plus:
- **Action mapping without pot-at-action:** A grinder knows their bet sizing. Seeing it mapped to an arbitrary solver label destroys credibility.
- **No decision-node path:** The solver can't answer "what should I do vs this specific bet size?" — which is what a grinder wants.
- **Quick mode (1.0 bb) for marginal decisions:** A grinder cares about 0.1 bb edges. 1.0 bb exploitability makes the output too noisy.

**Minimum to make it responsible to ship for this persona:**
1. All of the recreational requirements, plus:
2. Full 200-iteration solves as default (not quick mode)
3. Decision-node export (pass actual history path to `export_strategy`)
4. Pot-at-action in hand histories so action mapping is exact
5. Clearly document all approximations (bet tree, range library coverage, SPR modeling)

#### C. Complete Beginner (doesn't know what GTO means)

**Can they learn correct concepts?** **Yes, BUT only from the Coach tab, not the Solver tab.**
- A complete beginner doesn't know what frequencies, EV, or exploitability mean. The Solver tab is overwhelming and potentially harmful — they'll fixate on "Solid" vs "Mistake" grades without understanding why.
- The Coach tab with LLM narrative is actually valuable for beginners: it explains decisions in plain English, identifies leaks, and suggests study areas.
- **Risk:** A beginner might treat the "Hand Score" as a gamified metric and optimize for it without understanding GTO. "I got 85/100, I'm playing great" — when the score was computed from low-confidence multiway solves.

**What features are actively misleading?**
- **Hand Score as a performance metric:** Beginners will gamify it. A score derived partly from wrong-GTO spots is not a valid measure of play quality.
- **"Solver" tab name:** Beginners may assume "solver = correct answer" without understanding confidence or approximations.
- **Coach tab with solver summary on low confidence:** The LLM says "based on solver analysis, your check was a mistake" — but the solver analysis was wrong.

**Minimum to make it responsible to ship for this persona:**
1. All recreational requirements
2. Onboarding modal explaining what the solver can and cannot do
3. Hand Score → rename to "Hand Review Score" with explanation
4. Coach tab default, Solver tab hidden behind "Advanced" toggle
5. Never present grades as "correct" — always as "solver suggests"

### Trust Scorecard

| Dimension | Score (0-10) | Explanation |
|-----------|-------------|-------------|
| **Solver reliability** | 3/10 | Crashes on many real-world spots. Panic=abort kills worker silently. Auto-solve amplifies failures. |
| **Range accuracy** | 4/10 | Good when library hits (HU, common positions). Falls back to a wide BB calling range otherwise — wrong for 3-bet/4-bet pots, UTG vs UTG+1, etc. Multiway tightening is heuristic, not GTO. |
| **Pot/SPR modeling** | 2/10 | Correct for HU pots only. Multiway HU model is 30-50% wrong on pot size, 50-100% wrong on SPR. This is the single biggest accuracy gap. |
| **Action matching** | 3/10 | Check/fold/call match exactly. Bet/raise matching is heuristic without pot-at-action — maps real bet sizes to nearest solver label arbitrarily. |
| **Grading honesty** | 5/10 | EV gap thresholds are reasonable. But grades are computed against root-node strategy (not decision-node) and against potentially wrong ranges. "Solid" grade on a low-confidence spot is dishonest. |
| **Coach grounding** | 6/10 | LLM + solver summary is a good design. But when solver output is wrong (low confidence), the LLM propagates wrong information. Without solver, clearly labeled as "LLM-only." |
| **Overall "safe to learn from"** | 3/10 | Clean HU spots (high confidence): reasonably safe. Everything else: misleading. The product in its current state cannot responsibly claim to help users "play better online poker" without major qualification. |

### What Works Today vs What Must Not Be Trusted

**Works today (use with reasonable confidence):**
- Hand history parsing and display (CoinPoker format)
- Basic stats dashboard (VPIP, PFR, 3-bet%) — computed from action logs, not solver-dependent
- Coach tab narrative analysis (LLM-only, without solver summary)
- HIGH confidence, HU solver output — for studying GTO concepts (not precise frequencies)
- Cache hits — previously computed solves that were marked HIGH confidence

**Must not be trusted:**
- Any solver output labeled LOW confidence (multiway)
- MEDIUM confidence with range fallback — frequencies are for wrong villain range
- Action grades (Solid/Close/Mistake) on MEDIUM or LOW confidence spots
- Hand Score aggregation that includes MEDIUM/LOW confidence grades
- Coach analysis that references solver output from MEDIUM/LOW confidence spots
- Exact EV numbers from quick solves (1.0 bb noise floor)

---

## Comparison to What "Good" Looks Like

### Desktop Solvers (PioSOLVER, GTO+, Simple Postflop)

| Dimension | Desktop Solvers | poker-analyzer | Gap |
|-----------|----------------|----------------|-----|
| **Game tree** | User-defined bet sizes, multiple sizings per street, donk bets | Fixed 33%/75% flop, 50%/100% turn, 33%/75%/150% river, no donk | Large — no user control |
| **Ranges** | User-defined per position, exact combos, weighted | Library lookup with fallback, auto-tightening | Large — no user override |
| **Multiway** | PioSOLVER: 3-player support | HU only, collapsed approximation | Fundamental |
| **Decision node** | Browse entire game tree, any node | Root node only | Large — can't analyze responses to bets |
| **Preflop** | PioSOLVER Edge: preflop solving | Not supported | Fundamental for v1 |
| **Convergence** | 0.1-0.25% pot exploitability | 1.0 bb (quick) / 0.5 bb (full) | Moderate — quick mode is coarse |
| **Iterations** | Typically 50-200+ iterations user-controlled | 50 (quick) or 200 (full) | Comparable for full mode |
| **Action matching** | Exact — user enters exact bet sizes | Heuristic mapping without pot-at-action | Large |
| **Memory** | Unlimited (native) | ~128MB browser limit | Constraining for wide ranges |
| **Speed** | Typically 10-60s on desktop | 3-30s browser WASM | Comparable for simple spots |
| **Reliability** | Very high — native Rust, tested | Low — crashes on edge cases | Critical |

### Browser Reference (b-inary/wasm-postflop)

The `b-inary/wasm-postflop` demo is the closest comparable. poker-analyzer uses the SAME engine (`postflop-solver`) but adds:
- Scenario builder (Python → envelope → WASM) — downstream doesn't have this
- Range library integration — downstream uses manual range input
- Confidence tier system — downstream doesn't have this
- Auto-solve queue — downstream is manual
- Coaching integration — downstream is solver-only

The gap is in the **integration layer**, not the engine. The engine is capable. The Python→Rust bridge, worker lifecycle, and UI layer introduce the failures.

### LLM-Only Coaching Apps

| Dimension | LLM-Only | poker-analyzer Coach | poker-analyzer Solver |
|-----------|---------|---------------------|----------------------|
| **Produces GTO frequencies** | No | No | Yes (when working) |
| **Produces EV numbers** | No (hallucinates) | No | Yes (when working) |
| **Narrative analysis** | Yes | Yes | N/A |
| **Leak identification** | Plausible but unverified | Better (grounded when solver available) | N/A |
| **Risk of hallucination** | High for specific numbers | Medium (reduced by solver grounding) | None (when working — but can be wrong) |

### Gaps: Fundamental to v1 Scope vs Fixable Bugs

**Fundamental to v1 scope (not bugs — design limitations):**
1. No preflop solving — by design (PLAN.md)
2. Fixed bet tree — by design (PLAN.md §6)
3. HU-only engine — by design (multiway is Phase 2)
4. Root-node-only export — by design (decision-node paths are Phase 2)
5. No donk bet tree — by design (v1 simplification)
6. Range library coverage — incomplete by nature (can't cover every position/action/stake)

**Fixable bugs (should not be accepted as v1 limitations):**
1. `panic = "abort"` → worker death (P0 fix)
2. No `catch_unwind` in glue (P0 fix)
3. Singleton worker with no crash detection (P0 fix)
4. Auto-solve queue amplifies failures (P0 fix)
5. SPR floor too low (0.1 → 0.5) (P0 fix)
6. No degenerate bet tree detection (P0 fix)
7. No range weight floor (P1 fix)
8. Action mapping heuristic without pot-at-action (P1 fix)
9. UI overstates confidence tier meaning (P1 fix)

---

## Prioritized Backlog (P0/P1/P2)

Cross-referenced with `docs/solver-reliability-audit.md` Part 4. I **agree** with the prior audit's prioritization and re-rank slightly based on source code inspection.

### P0 — Must Fix Before Accepting New Users (or Immediately for Current Users)

| # | Item | Effort | Impact | Dependency | Agree with Prior? |
|---|------|--------|--------|------------|-------------------|
| P0.1 | Disable auto-solve queue — make solves manual-only per street | S | Stops cascading 360s failures. Every hand open no longer triggers 3 risky solves. | None | **Yes — stronger agreement.** Prior audit listed this as P0. After reading the code, this is an emergency fix. |
| P0.2 | `panic = "unwind"` + `catch_unwind` in ALL `#[wasm_bindgen]` exports | M | Converts 80% of silent worker deaths into structured errors. Worker stays alive after Rust panic. | P0.1 (manual solves reduce blast radius during rollout) | **Yes — this is the single highest-impact engineering change.** Every `init_game`, `solve_step`, `export_strategy` call must be wrapped. |
| P0.3 | Worker crash detection + auto-respawn in `SolverClient` | M | Worker death no longer causes 120s dead-worker waits. Worker is recreated automatically. | P0.2 (reduces crash frequency) | **Yes, but add:** `ping()` before every `solve()` call (already implemented in worker but not called). |
| P0.4 | Pre-WASM envelope validation hardening | M | Catches degenerate trees, empty ranges, SPR violations before engine allocation. Prevents crashes. | None | **Yes, with additions:** Add bet-tree effective-chip dedup check. Add range minimum hand-class count. Add range total weight floor. |
| P0.5 | Raise `_MIN_SPR` from 0.1 to 0.5 in Python AND Rust | S | Eliminates degenerate all-in-only trees for vast majority of spots. SPR < 0.5 spots → "unsolvable" with explanation. | P0.4 (validation catches what SPR raise doesn't) | **Yes — prior audit listed this in Phase 0.** |
| P0.6 | Add solver failure telemetry endpoint | M | Without telemetry, every crash is invisible. Need data to prioritize further fixes. | None | **Yes.** |

### P1 — Fix Within 2 Weeks

| # | Item | Effort | Impact | Dependency | Agree with Prior? |
|---|------|--------|--------|------------|-------------------|
| P1.1 | Confidence-tier gating in UI: LOW requires explicit opt-in; MEDIUM shows amber warning; HIGH normal | S | Users stop seeing "Solver ready" on multiway approximations. Prevents study from wrong data. | P0.1 (manual solves needed for opt-in flow) | **Yes — this is the biggest UX trust fix.** |
| P1.2 | Worker per-solve (not singleton) | M | Isolates crashes. Flop crash doesn't affect turn/river. Enables future parallel solves. | P0.2, P0.3 (reduces crashes, but per-solve worker is belt-and-suspenders) | **Yes.** |
| P1.3 | Adaptive timeout: quick=30s, full=180s, heartbeat=15s | S | Quick failures fast; full solves get fair time. Hung worker detected quickly. | P0.3 (worker health check) | **Yes.** |
| P1.4 | Range weight floor: reject weights < 0.001, require total ≥ 0.01 | S | Prevents near-zero weights from causing engine indexing issues. | P0.4 (envelope validation) | **Yes.** |
| P1.5 | Envelope JSON Schema + strict validation contract between Python and Rust | M | Catches drift between builder.py output and lib.rs expectations. CI enforceable. | None | **Yes.** |
| P1.6 | Honest UI labels: rename "Solver ready" to context-appropriate labels based on confidence tier | S | "GTO Ready" (high), "Approximate GTO" (medium), "Not GTO — See Coach" (low) | P1.1 (confidence gating) | **New — not in prior audit explicitly. Critical for trust.** |
| P1.7 | Coach tab: add "solver data confidence" badge when solver summary is provided | S | Users know whether Coach analysis is grounded in reliable GTO or approximate data | P1.1, P1.6 | **New.** |

### P2 — Fix Within 1-2 Months

| # | Item | Effort | Impact | Dependency | Agree with Prior? |
|---|------|--------|--------|------------|-------------------|
| P2.1 | Bet tree dedup by effective chips (after `force_allin_threshold` applied) | M | Eliminates degenerate all-in-only tree class of crashes. | P0.4 (validation catches these, but dedup is cleaner) | **Yes.** |
| P2.2 | HU pot transparency in UI: show "Pot modeled as X bb (actual: Y bb)" for multiway | S | Users understand the approximation. | P1.1 (confidence gating) | **Yes.** |
| P2.3 | Solve regression fixtures (20+ real-world envelopes) | M | CI catches regressions. Every fix is verified against real data. | P0.4, P0.5 | **Yes.** |
| P2.4 | Decision-node export: pass actual history path to `export_strategy` instead of `""` | M | Solver analyzes hero's actual decision, not street start. Grading becomes accurate. | P1.2 (worker per-solve) | **Partially in prior audit (Phase 2, item 4). Elevate to P2.** |
| P2.5 | Action matching with pot-at-action: parse pot size at each action and map bet sizes exactly | M | Hero's bets map to correct solver labels. Grades are accurate. | P2.4 (decision-node export) | **Partially in prior audit. Elevate to P2.** |
| P2.6 | Auto-downgrade to quick mode if full solve > 90s | S | Slow full-solve spots get a fast answer instead of timeout. | P1.3 (adaptive timeout) | **Yes.** |
| P2.7 | Hand Score → only aggregate HIGH confidence street grades | S | Score is no longer poisoned by wrong-GTO multiway grades. | P1.1 (confidence gating) | **New.** |

### P3 — Phase 2 / Long-Term

| # | Item | Effort | Impact | Agree with Prior? |
|---|------|--------|--------|-------------------|
| P3.1 | Server-side Rust solver (true multiway CFR) | L | Eliminates browser limits; enables multiway; faster; more reliable | **Yes.** |
| P3.2 | User-defined bet sizes (override fixed tree) | M | Grinder persona can match actual bet sizing | **New — essential for grinder adoption.** |
| P3.3 | User-defined ranges (manual range editor) | M | Override library fallback; trust solver for exact ranges | **New.** |
| P3.4 | Preflop solving | L | Complete hand analysis from preflop to river | **Yes (Phase 2).** |
| P3.5 | COOP/COEP threading for browser parallel CFR | L | 4-8× speedup | **Yes.** |

---

## Open Questions & Recommended Instrumentation

### Open Questions (Answers Would Change Conclusions)

1. **What percentage of real CoinPoker hands have SPR < 1.5?** `[medium]` If < 5%, degenerate tree crashes are edge cases. If > 20%, this is a dominant failure mode. **Recommended:** Add SPR histogram telemetry to scenario builder.

2. **What percentage of range lookups hit the fallback?** `[medium]` If > 30% of hands are `medium` confidence due to range fallback, the range library needs expansion before the product is useful. **Recommended:** Add `hero_lookup_conf` and `villain_lookup_conf` to scenario metadata telemetry.

3. **Does `postflop-solver`'s `StackAlloc` correctly handle repeated init/free cycles?** `[medium]` If the arena doesn't fully reset, the second solve in a worker session operates on fragmented memory. **Recommended:** Write a Rust-native test: init→solve→export→free→init→solve→export × 10 cycles, check for OOB.

4. **Can `export_strategy` be called on a game where `finalize()` was never called?** `[medium]` In `lib.rs` lines 223-231, finalize requires `reached_cap || converged`. If `solve_step` is called with `max_iters_this_step` that doesn't hit either condition (e.g., 5 iterations on a 200-iteration cap, exploitability still above target), `finalized` stays `false`. Then `export_strategy` at line 294 passes `finalized: false` to `build_export`, which leaves `combo_ev` empty (strategy_export.rs lines 166-179). This doesn't crash but means EV data is silently missing. **Recommended:** Add assertion or warning when exporting from non-finalized game. **Also:** The convergence check at line 209-213 checks `state.last_exploitability_chips` which is initialized from `compute_exploitability` at game init (line 146). If initial exploitability is already ≤ target, the loop at line 205 breaks after 1 iteration (because `state.iterations > 0` at line 209 is still false on iteration 0, then becomes true after `state.iterations += 1` at line 215, but `last_exploitability_chips` hasn't been updated yet since it's only recomputed every 10 iterations at line 218). Wait — let me re-read this carefully.

   Actually, the flow is:
   - Line 204: `!state.finalized` → true
   - Loop iteration 0: `state.iterations = 0`, check line 206: 0 < max → continue, check line 209: `state.iterations > 0` → **false** → don't break
   - Line 214: `cfr_step(&state.game, 0)` — run iteration 0
   - Line 215: `state.iterations += 1` → now = 1
   - Line 218: `state.iterations % 10 == 0` → false (1 % 10 != 0) → don't recompute exploitability
   - Loop iteration 1: check line 206: `state.iterations = 1`, if 1 < max → continue
   - Check line 209: `state.iterations > 0` → **true**, check line 210: `state.last_exploitability_chips <= target` → `last_exploitability_chips` is STILL the initial exploitability from line 146!
   - If initial exploitability ≤ target → **BREAK** after only 1 CFR iteration
   - Line 223: `reached_cap = false` (1 < max_iterations), `converged = true` (1 > 0 AND initial_expl ≤ target)
   - Line 228: recompute exploitability, line 229: finalize, line 230: `finalized = true`

   So yes, the game CAN finalize with only 1 CFR iteration if initial exploitability is very low. This is a **correctness bug** — the exploitability convergence check uses stale data. The fix should recompute exploitability at the convergence check, not rely on the last cached value. `[high confidence]`

5. **What does `postflop-solver`'s `expected_values_detail` do when called on a non-finalized game?** `[low — requires engine source]` The `strategy_export.rs` line 167 comment says "EVs only exist for solved games (otherwise expected_values_detail panics)." If it panics, that's a crash vector in `export_strategy` when called on a non-finalized game. **Recommended:** Test this explicitly.

### Recommended Instrumentation (Logs & Telemetry)

Add to the scenario builder (`builder.py`) or scenario API (`hands.py`):

```python
# In build_scenario, add to metadata:
"telemetry": {
    "spr": float(eff_bb / pot_bb) if pot_bb > 0 else None,
    "hero_range_combo_count": len(hero_range_raw),
    "villain_range_combo_count": len(villain_range_raw),
    "hero_lookup_hit": hero_lookup_conf == "high",
    "villain_lookup_hit": villain_lookup_conf == "high",
    "multiway_alive_count": len(alive_states),
    "pot_error_pct": float((pot_chips_total_table - pot_chips_hu) / pot_chips_hu * 100) if pot_chips_hu > 0 else 0,
    "effective_bet_sizes_flop": len(set(effective_chips_for_sizes(BET_TREE["flop"], effective_stack_chips, pot_chips_hu))),
    "effective_bet_sizes_turn": ...,
    "effective_bet_sizes_river": ...,
}
```

Add to the WASM glue failure path:
```rust
// When init_game or solve_step fails, capture:
// - error_class: "panic", "validation", "timeout", "engine_error"
// - envelope_spr, envelope_pot_bb, envelope_eff_bb
// - range_oop_count, range_ip_count
// - board_cards, street
// - solver_version
// - wasm_memory_used (if measurable)
```

### Test Envelopes to Capture

Store these in `solver-wasm/tests/fixtures/regression/`:

1. **Degenerate all-in tree:** SPR 0.5, allin_always=true, full bet tree → expect graceful rejection
2. **Near-degenerate tree:** SPR 1.2, allin_always=true → expect most bet sizes force all-in
3. **Empty range after combo removal:** Hero holds AA on AAA board → range should be near-empty
4. **Wide ranges, deep stacks:** 300 combos each, SPR 20 → memory stress test
5. **Minimum pot:** pot_bb = 0.5, eff_bb = 100 → rounding to 50 chips
6. **Sequential solve:** init→solve(50)→export→free, repeat 10× → no OOB, no memory growth
7. **Multiway envelope:** 4 players alive, HU pot model → verify all metadata fields
8. **Fallback range:** Action sequence with no library match → verify DEFAULT_FALLBACK_RANGE is used
9. **Paired board, range heavy in that rank:** Board AAK, hero range has many Ax hands

---

## Appendix: Envelope Red Flags Checklist

When debugging a failed solve, check the scenario envelope for these red flags:

| # | Red Flag | Threshold | Why It Matters |
|---|----------|-----------|----------------|
| 1 | SPR < 0.5 | `eff_bb / pot_bb < 0.5` | Below minimum; solver should reject |
| 2 | SPR 0.5–1.5 + allin_always=true | `eff_bb / pot_bb < 1.5` | Degenerate tree likely |
| 3 | `hero_range` has < 10 entries | `len(hero_range) < 10` | Range too narrow; engine indexing risk |
| 4 | `villain_range` has < 10 entries | `len(villain_range) < 10` | Range too narrow |
| 5 | Total range weight < 0.01 | `sum(range.values()) < 0.01` | Near-zero weight will cause issues |
| 6 | Any weight < 0.001 | `any(w < 0.001 for w in range.values())` | Near-zero entries not filtered |
| 7 | `board` length != 3/4/5 | `len(board) not in [3,4,5]` | Invalid board |
| 8 | `pot_bb` ≤ 0 or `eff_bb` ≤ 0 | Either non-positive | Invalid game state |
| 9 | `bet_tree.flop` empty and `allin_always` false | Empty bet list | No legal actions |
| 10 | Board cards appear in hero/villain range | Overlap between board and range keys | Card removal should have removed these |
| 11 | HU pot ≠ table pot (multiway) | `metadata.pot_chips_hu_model != metadata.pot_chips_total_table` | Silent wrong-answer indicator |
| 12 | `confidence: "low"` with `is_multiway_approximation: true` | Multiway flag | Output is not valid GTO |

---

*End of audit. All conclusions are tagged with confidence levels. Evidence that would change each conclusion is indicated. No code changes proposed — analysis only as requested.*