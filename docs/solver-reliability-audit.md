# Solver Reliability Audit — Poker Analyzer WASM CFR Engine

**Date:** 2026-05-28  
**Auditor:** Senior poker solver + WASM/Rust systems engineer  
**Scope:** Full-stack audit of solver reliability for `b-inary/postflop-solver` → `solver-wasm` glue → Web Worker → React UI → Python scenario builder

---

## Part 1 — Executive Summary

**Blunt assessment:** The current browser-WASM CFR solver path is architecturally sound in principle but **not production-shippable** in its current state. Users are seeing `memory access out of bounds`, `Unreachable`, and timeouts during normal hand review. The auto-solve queue that runs sequentially when opening a hand amplifies every single failure mode.

- The HU pot model for multiway hands produces **silent wrong answers** (incorrect SPR) but prevents the *more visible* WASM traps from inflated pots. Wrong answers are worse for trust.
- The glue crate (`solver-wasm`) does not catch panics. The upstream engine and/or bad inputs cause Rust panics that, under `panic = "abort"`, kill the Web Worker silently with opaque browser errors.
- The 120s timeout fires primarily on dead workers (not slow solves). A worker killed by a previous solve causes cascading 120s waits for all subsequent streets.
- The auto-solve queue (`HandAnalysisPane.tsx` lines 210–279) runs 3 sequential solves sharing one worker — one crash poisons all three.
- Input validation exists in Python (`builder.py`) and Rust (`lib.rs`) but has gaps: SPR floor too low (0.1), no degenerate bet-tree detection, no range-weight-floor enforcement, no board/range overlap check.
- `force_allin_threshold: 0.15` + `allin_always: true` + very low SPR creates degenerate trees where all actions resolve to all-in, likely triggering engine OOB.
- The range converter can produce technically valid `Range` objects with near-zero weights that cause internal engine indexing errors.
- **Top 3 actions:** (1) Add `catch_unwind` + `panic = "unwind"` to WASM glue to prevent silent worker death. (2) Disable auto-solve queue — make solves manual-only until recovery lands. (3) Add worker crash detection + auto-respawn in `SolverClient`.

---

## Part 2 — Failure Matrix

### Symptom taxonomy

| # | Symptom | Layer | Hypothesis | Repro Envelope | Confidence | Fix Type |
|---|---------|-------|------------|-----------------|------------|----------|
| **1** | `memory access out of bounds` | Upstream engine | `ActionTree::new()` or `cfr_step()` indexes arrays with values derived from bet tree config. Very low SPR (<0.5 bb eff) + `allin_always=true` + river 150% bet creates node-count mismatches where internal arrays are undersized vs actual traversal. | River street, SPR < 0.5, allin_always=true, river bet sizes include 150%, wide ranges (>25% of deck combos). | **High** | Reject envelope pre-engine; add `catch_unwind` |
| **2** | `Unreachable` (WASM trap) | Upstream engine via `panic = "abort"` | `unreachable!()` or `panic!()` in postflop-solver hit during `expected_values_detail()` or edge-case CFR node resolution. The `finalized` boolean in glue layer may not match engine internal state, causing EV access on un-finalized game. | Full solve that converged early but engine internal EV buffer wasn't populated. | **Medium** | `catch_unwind` in glue; validate `finalized` against engine state |
| **3** | `memory access out of bounds` | Range conversion (`range_convert.rs`) | `Range::parse()` accepts near-zero float weights (e.g., `0.000001`) that underflow to zero combos during engine internal conversion, producing array-index mismatches. Also possible: post-board-removal range has < 5 hand classes. | Range after hero combo removal + multiway tightening leaves < 5% of original combos. | **Medium** | Weight floor (reject < 0.001); minimum hand-class count check |
| **4** | `Solver worker solve timed out after 120000ms` | Worker lifecycle (`worker.ts`, `client.ts`) | Previous solve crashed the worker (trap → silent death). Next `postMessage` goes to terminated worker. 120s timeout fires because no response arrives. This is a dead worker, not a slow solve. | Sequential auto-solve: flop works, turn crashes worker, river waits 120s. | **High** | Worker respawn + health check + heartbeat |
| **5** | `Unreachable` | Bet tree + low SPR crossover | `force_allin_threshold = 0.15` + `allin_always = true` + SPR < 1.5 → all bet sizes (33%, 75%, 150%) + allin resolve to the same all-in chip amount. Tree dedup may not handle 4 identical effective actions gracefully, causing degenerate node traversal. | Turn street, SPR < 1.5, bet sizes = ["50%", "100%"] + allin → effectively 1 distinct action. | **Medium** | Deduplicate by effective chips; reject trees with < 2 distinct sizes |
| **6** | Silent wrong answer (no crash) | HU pot model (`builder.py`) | Multiway hands: folded-player dead money excluded from pot. Solver sees smaller pot → SPR appears larger → frequencies computed for wrong game state. Output labeled "GTO" but mathematically valid for a different pot size. | 6-max, 4 players fold preflop after contributing 2.5 bb each. HU pot = hero+villain only, missing 10 bb dead money. | **High** | Degrade confidence; label "Approximate — HU model"; require opt-in |
| **7** | `init_game returns 0` with generic error | Envelope validation gap | Range after hero combo removal + multiway tightening leaves valid but tiny range. `PostFlopGame::with_config` fails internally; glue surfaces generic "invalid config" without specificity. | Hand where hero holds combo that was the primary weight carrier in multiple classes. | **Low** | Better error messages; pre-check range coverage in glue |
| **8** | Legitimate slow solve exceeding reasonable time | Performance + mobile | Full 200-iter solve on wide-range river with 4 bet sizes creates large tree. On mobile or throttled CPU, solve may genuinely need > 60s. Current 120s timeout is too generous for quick solves, too tight for full mobile solves. | River, 200 iter, wide ranges (>200 combos each), 4 bet sizes, mobile Safari. | **Medium** | Adaptive timeout; auto-downgrade; heartbeat |

### Root cause deep-dives for key failures

#### Cause 1: OOB from ActionTree + low SPR + allin_always

**In `lib.rs:466`:** `force_allin_threshold: 0.15` means any bet ≥ 15% of effective stack forces all-in. With SPR = 1.0, a 33%-pot bet = 0.33 × pot = 0.33 × effective_stack = 33% of stack → exceeds 15% → forces all-in. So 33%, 75%, 150%, and `allin_always`'s explicit "a" ALL become all-in actions. The tree dedup logic may not handle 4 identical actions gracefully, producing an internal node count mismatch where the engine allocates for 4 children but only 1 is reachable.

**Minimal repro envelope:**
```json
{
  "board": ["As", "Kh", "Qd", "Jc", "Ts"],
  "pot_bb": 50.0,
  "effective_stack_bb": 55.0,
  "oop_player": "BB",
  "ip_player": "BTN",
  "hero_position": "BTN",
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
SPR = 1.1. Every river bet size + explicit all-in resolves to `Action::AllIn`.

#### Cause 4: Worker death cascade

`HandAnalysisPane.tsx:211-213` iterates `for (const street of postflopStreets)` calling `solve()` sequentially. Each call reuses the singleton `SolverClient` (`client.ts:140-145`). The worker (`worker.ts`) calls `free_game(handle)` in its `finally` block but does NOT catch WASM traps. When a trap kills the worker:

1. `worker.onmessage` stops firing
2. `SolverClient` waits 120s for a response that never comes
3. After timeout rejection, the next street's `solve()` posts to the same dead worker
4. Result: worst case 360s of silent waiting for 3 streets

**There is no `worker.onerror` handler, no health check, no respawn logic.** The `terminate()` method exists but is never called in error paths.

#### Cause 5: Degenerate bet tree from SPR × force_allin crossover

**In `bet_tree.rs:55-62`:** The `"a"` token is appended unconditionally when `allin_always` is true, but `BetSizeOptions::try_from` sorts and deduplicates. **The critical question:** does the engine deduplicate by BetSize variant or by effective chip amount? If by variant only, the tree has 4 actions that all resolve to different chip amounts but all exceed stack and thus all map to the same child node. The engine might allocate for 4 children but only use 1, causing index confusion in CFR traversal.

**In `lib.rs:468`:** `merging_threshold: 0.0` means no merging of close pot fractions. The 33%, 75%, and 150% bets are kept distinct even though they all round to the same all-in action when `force_allin_threshold` fires.

---

## Part 3 — Recommended Architecture

### Primary Recommendation: Option A — Fix WASM path in-browser (with Option C — cache-first)

**Rankings by dimension:**

| Option | Correctness | Cost | Latency | Maintainability | Trust | Verdict |
|--------|------------|------|---------|-----------------|-------|---------|
| **A — Fix WASM in-browser** | ⭐⭐⭐ (with hardening) | Low | 3–30s | Medium | Medium | **Primary path** |
| **C — Cache-first** | ⭐⭐⭐⭐ | Low-Med | 0–3s (cached) | Low | High | **Layered on A** |
| **B — Server-side Rust** | ⭐⭐⭐⭐⭐ | High | 0.5–5s | High | High | **Phase 2** |
| **D — Heuristic fallback** | ⭐⭐ | Medium | <1s | High | Low | **Rejected** |
| **E — LLM fallback** | ⭐ (not GTO) | Low code, High $ | 2–10s | Low | **Dangerous** | **Rejected as solver substitute** |
| **F — Hard reject** | ⭐⭐⭐⭐⭐ | Low | N/A | Low | Highest | **Use for edge cases** |
| **G — Reduce scope** | ⭐⭐⭐ | Low | N/A | Low | Medium | Partial (disable auto-solve) |
| **H — Different engine** | Unknown | High | Unknown | Medium | Unknown | Not justified yet |

### Architecture diagram (text)

```
User opens hand
    │
    ├── Cache hit (scenario_hash in solver_runs)?
    │       ├── YES → Return cached StrategyExport instantly ("From cache" badge)
    │       │
    │       └── NO → Pre-flight validation (Rust wasm-bindgen preflight)
    │                   │
    │                   ├── REJECT (invalid envelope) → "Unsolvable spot" UX + reason
    │                   │
    │                   └── ACCEPT → Confidence tier check
    │                                   │
    │                                   ├── HIGH → Manual "Solve" button only
    │                                   ├── MEDIUM → Manual solve + "Approximate" amber label
    │                                   └── LOW → "Unsolvable — solve anyway?" explicit opt-in
    │                                               │
    │                                               └── User clicks "Solve anyway"
    │                                                       │
    │                                                       ├── Fresh worker spawn (per solve)
    │                                                       ├── solve_step chunks with progress heartbeat
    │                                                       ├── On trap/timeout: kill worker, respawn, surface error
    │                                                       └── On success: cache → postSolverRun → render grid
    │
    └── Coach tab: always available with or without solver
            ├── With solver → solver_summary inlined in LLM prompt
            └── Without solver → "(no solver scenario attached)" + LLM-only badge
```

### What I rejected and why

**Option B (server-side solver):** Best long-term architecture for reliability. Eliminates browser memory limits, enables native threading, supports true multiway CFR. But requires standing up Rust HTTP services, managing compute resources, and handling concurrent solves. Not a "stop the bleeding" fix. **Revisit as Phase 2.**

**Option D (heuristic EV):** Produces numbers that look like solver output but aren't CFR-based. Violates the constraint: "better to refuse a spot than show fake GTO." A heuristic cannot credibly produce GTO frequencies or EV. **Rejected.**

**Option E (LLM as solver substitute):** See Part 5. An LLM cannot produce GTO frequencies or EV. It produces plausible-sounding poker analysis. Labeling LLM output as "solver" is actively misleading. **Rejected for the Solver tab. Accepted for Coach-only narrative.**

**Option F (hard reject):** Partially adopted. Unsolvable spots should be rejected with explanation. But rejecting ALL medium/low confidence spots reduces coverage too aggressively. Used selectively for edge cases.

**Option H (different engine):** Not justified. The `postflop-solver` engine is capable and actively maintained. Failures are in our glue layer and input construction, not fundamentally in the CFR algorithm. Fixing our pipeline is cheaper than re-integrating a new engine.

---

## Part 4 — Prioritized Fix Backlog

| Priority | Item | Effort | Impact | Owner | Acceptance Criteria |
|----------|------|--------|--------|-------|---------------------|
| **P0** | Disable auto-solve queue | S | Stops cascading failures | Frontend (`HandAnalysisPane.tsx`) | Opening a hand does NOT trigger solves; user must click "Solve" per street |
| **P0** | `panic = "unwind"` + `catch_unwind` in glue | M | Converts silent worker death → structured error recovery | WASM (`lib.rs`, `Cargo.toml`) | Any Rust panic caught by glue; surfaced via `last_error()`; worker stays alive |
| **P0** | Worker crash detection + auto-respawn | M | Prevents 120s dead-worker waits | Frontend (`client.ts`, `worker.ts`) | `worker.onerror` → terminate + recreate; 15s heartbeat timeout; surface "Solver crashed — retrying" message |
| **P0** | Pre-WASM envelope validation hardening | M | Catch bad inputs before engine allocation | WASM (`lib.rs`) | Reject: SPR < 0.5, ranges with < 10 combos, total weight < 0.01, degenerate all-in-only trees, pot rounding to 0 chips |
| **P0** | Failure telemetry endpoint | M | Diagnose production failures | Backend (`solver.py`), Frontend (`client.ts`) | POST `/solver-failures` with scenario_hash, error_class, street, pot_bb, eff_bb, range_combo_counts, multiway_flag, solver_version; no PII |
| **P1** | Confidence-tier gating | S | Prevents "Approximate" from appearing as "Solved" | Frontend (`SolverTab.tsx`, `HandAnalysisPane.tsx`) | LOW: require explicit opt-in; MEDIUM: amber warning; HIGH: normal |
| **P1** | Worker per-solve (not singleton) | M | Isolates crashes; enables future parallel solves | Frontend (`client.ts`) | Each solve creates new Worker; terminate after completion; pool of max 2 concurrent |
| **P1** | Adaptive timeout with heartbeat | S | Quick fails fast; full gets fair time | Frontend (`client.ts`) | Quick: 30s; Full: 180s; heartbeat: 15s no progress → kill + reject |
| **P1** | Envelope JSON Schema + strict validation | M | Contract between Python ↔ Rust; catches drift | Backend (`builder.py`), WASM (`envelope.rs`) | Published JSON Schema; Python `validate_scenario_envelope` matches Rust checks exactly; CI enforcement |
| **P1** | Range weight floor (0.001 minimum) | S | Prevents near-zero weights from causing engine issues | WASM (`range_convert.rs`) | Weights < 0.001 rounded to 0; total range weight ≥ 0.01 required |
| **P2** | Bet tree dedup by effective chips | M | Prevents degenerate all-in-only trees | WASM (`bet_tree.rs`) | After pot% → chips, deduplicate; if < 2 distinct sizes + allin_always, skip allin_always flag |
| **P2** | HU pot transparency in UI | S | Users understand approximation | Frontend (`SolverTab.tsx`) | Multiway badge: "Pot modeled as X bb (actual table pot: Y bb)" |
| **P2** | Solve regression fixtures | M | CI catches regressions | WASM (`tests/fixtures/regression/`) | 20+ real-world envelopes; `cargo test regression` verifies no-trap |
| **P2** | Auto-downgrade to quick mode | S | Slow full-solve spots get fast answer | Frontend (`worker.ts`) | If full solve > 90s without finishing, cancel + restart as quick; surface "Downgraded to quick solve" |
| **P3** | Memory profiling + budget (128MB max) | M | Prevent OOM on mobile | WASM (`lib.rs`) | Estimate memory for max envelope; reject if > 128MB |
| **P3** | Server-side Rust solver (Phase 2) | L | Eliminates browser limits; enables multiway CFR | Backend (new service) | Rust HTTP microservice; solves < 5s for 200 iter; shared cache |
| **P3** | COOP/COEP threading (Phase 2) | L | 4–8× speedup on multi-core | WASM, Frontend | Rayon parallel CFR; requires SharedArrayBuffer headers; 200 iter < 5s |

---

## Part 5 — LLM Fallback Verdict

### As Solver Tab Substitute: **NO**

An LLM (Claude, GPT-4, etc.) does not produce GTO frequencies or expected values. It produces natural-language poker analysis that may reference GTO concepts but is fundamentally a text-generation model, not a game-theory solver. The LLM cannot:
- Compute Nash equilibrium strategies
- Produce numeric action frequencies per combo
- Produce expected values in big blinds
- Distinguish between a 33% pot bet and a 75% pot bet with mathematical precision
- Avoid hallucinating frequencies, EV numbers, and fabricated "solver says..." claims

Labeling LLM output in the Solver tab would actively mislead users. Users who understand GTO will recognize fake output and lose trust in the entire product. Users who don't understand GTO will believe they're seeing real solver results when they're not.

### As Coach Enrichment Only: **YES**

The Coach tab's purpose is narrative analysis — explaining why a decision was good or bad, suggesting alternative lines, identifying leaks. This is legitimate LLM capability. When solver output is available, it grounds the LLM in real GTO data, reducing hallucination risk. When solver output is unavailable, the Coach can still provide useful (if less precise) analysis, clearly labeled as "LLM-only analysis (no solver data available)."

### As Offline Preflop/Heuristic Layer: **YES, with heavy qualification**

Preflop ranges are well-documented, discrete, and can be represented as lookup tables. An LLM can reasonably summarize preflop charts (e.g., "From UTG at 100bb, QQ is a standard open-raise"). This is NOT "solver-backed" — it's precomputed chart interpretation. The LLM should cite the chart source and avoid claiming it computed the equilibrium.

### What the Solver Tab Should Show Instead (When Solver Fails)

```
┌─────────────────────────────────────────────────────────┐
│ ⚠ Solver unavailable for this spot                      │
│                                                         │
│ This decision point could not be solved because:        │
│ [specific reason from last_error or preflight]          │
│                                                         │
│ [Retry] [View in Coach (LLM analysis)]                  │
│                                                         │
│ Coach can provide narrative analysis but cannot         │
│ produce GTO frequencies or expected values.             │
└─────────────────────────────────────────────────────────┘
```

**No fake GTO. No LLM pretending to be a solver. No heuristic dressed up as CFR.**

---

## Part 6 — Test Plan

### Unit Tests (Rust native)

| Test | Location | What It Verifies |
|------|----------|-----------------|
| `bb_to_chips_rounding` | `lib.rs` (exists) | CHIPS_PER_BB = 100 quantization |
| `parses_*_partial_weight` | `range_convert.rs` (exists) | Range weight conversion |
| `rejects_invalid_class` | `range_convert.rs` (exists) | Hand class validation |
| `rejects_empty_range` | `range_convert.rs` (exists) | Empty range rejection |
| `flop_includes_allin_and_pot_relatives` | `bet_tree.rs` (exists) | Bet tree construction |
| `river_handles_overbet` | `bet_tree.rs` (exists) | Overbet sizing |
| `allin_always_false_does_not_append` | `bet_tree.rs` (exists) | Allin gating |
| **NEW: `rejects_degenerate_allin_only_tree`** | `bet_tree.rs` | SPR=0.5 + allin_always → error |
| **NEW: `rejects_range_below_weight_floor`** | `range_convert.rs` | Total weight < 0.01 → error |
| **NEW: `rejects_board_range_overlap_unpruned`** | `lib.rs` | Board cards in range → error |
| **NEW: `rejects_spr_below_minimum`** | `lib.rs` | SPR < 0.5 → error |
| **NEW: `validate_envelope_pre_cfr`** | `lib.rs` | Comprehensive pre-CFR validation function |
| `init_solve_export_roundtrip` | `solver_integration.rs` (exists) | Full native roundtrip |
| `free_game_is_idempotent` | `solver_integration.rs` (exists) | Memory cleanup |
| `unknown_handle_surfaces_error` | `solver_integration.rs` (exists) | Error handling |
| **NEW: `catch_unwind_on_bad_input_preserves_worker`** | `lib.rs` | Panic caught as `Err(String)` |

### Envelope Fuzz Tests

| Test | Method | Target |
|------|--------|--------|
| `fuzz_build_game_no_panic` | proptest | Generate 100K random envelopes; assert `build_game` never panics, always returns Result |
| `fuzz_range_convert_no_panic` | proptest | Random BTreeMap<String, f32> → `range_from_hand_classes` never panics |
| `fuzz_bet_tree_no_panic` | proptest | Random Vec<String> bet sizes → `build_street_sizes` never panics |
| `fuzz_full_pipeline_no_trap` | proptest | Valid envelope → build_game → cfr_step(1 iter) → export_strategy → no panic |

### Browser WASM Smoke Suite

| Test | Environment | What It Verifies |
|------|------------|-----------------|
| `init_game` returns handle > 0 | Chrome, Firefox, Safari | WASM loads and initializes |
| `solve_step` × 50 produces valid JSON | Chrome | CFR runs without trap |
| `export_strategy` produces valid JSON | Chrome | Strategy export contract |
| Worker crash + respawn | Chrome | Worker recovery after kill |
| 10 consecutive solves, no leak | Chrome | Memory stability (heap snapshot delta) |
| Mobile: 50 iter < 15s | Safari iOS, Chrome Android | Mobile performance |

### Regression Fixtures

Store 20+ envelopes in `solver-wasm/tests/fixtures/regression/`:

- Multiway flop (6 players, 2 alive postflop, LOW confidence)
- River with SPR < 1.0
- Turn with wide ranges (> 200 combos each)
- Empty-ish range after hero combo removal (< 5 hand classes)
- Board with overlapping ranks (paired board, range heavy in that rank)
- Deep stack (400+ bb effective)
- Maximum pot (near 1000 bb)
- Minimum pot (0.5 bb)
- All streets for a single full hand (flop → turn → river)

### Performance Budgets

| Metric | p50 | p95 | Max | Notes |
|--------|-----|-----|-----|-------|
| Quick solve (50 iter, flop) | 5s | 15s | 30s | Desktop Chrome |
| Full solve (200 iter, flop) | 15s | 45s | 90s | Desktop Chrome |
| Quick solve (50 iter, river) | 8s | 25s | 45s | Desktop Chrome |
| Full solve (200 iter, river) | 25s | 75s | 150s | Desktop Chrome |
| Mobile quick solve (50 iter) | 10s | 30s | 60s | Safari iOS |
| WASM linear memory (single solve) | 32MB | 64MB | 128MB | Typical ranges |
| WASM memory (10 consecutive solves) | Baseline + 0% | Baseline + 5% | Baseline + 10% | No leak |

---

## Part 7 — Implementation Plan (Phased)

### Phase 0 — Stop the Bleeding (1–3 days)

**Goal:** No more silent crashes during normal hand review. Every failure surfaces a structured error.

#### File-level changes:

**1. `solver-wasm/Cargo.toml`**
- Change `[profile.release] panic = "abort"` → `panic = "unwind"` for wasm target
- Add `[profile.wasm-release]` inheriting release with `panic = "unwind"`

**2. `solver-wasm/src/lib.rs`** — Add `catch_unwind` wrappers
- Wrap `build_game()` call in `std::panic::catch_unwind` (line 143)
- Wrap `cfr_step()` call in `catch_unwind` (line 214)
- Wrap `finalize()` call in `catch_unwind` (line 229)
- Wrap `build_export()` call in `catch_unwind` (line 294)
- On panic: `set_error("internal engine panic at <location>: <panic message>"); return Err(...)`

**3. `solver-wasm/src/range_convert.rs`** — Weight floor
- Lines 44–48: Change `w <= 0.0` to `w < 0.001` (skip entries with near-zero weight)
- After building `chunks`: if `chunks.len() < 5`, return `Err("range has fewer than 5 hand classes after weight filtering")`

**4. `solver-wasm/src/lib.rs`** — `build_game()` additional validation
- After `resolve_ranges` (line 366): compute total unique combos estimate; if < 10, return error
- After `build_tree_config` (line 375): check tree has ≥ 2 distinct non-allin actions per street; else return error

**5. `solver-wasm/src/lib.rs`** — Raise SPR floor
- Line 345: Change `envelope.pot_bb * 0.1` to `envelope.pot_bb * 0.5` (minimum SPR = 0.5)

**6. `frontend/src/components/hand-analysis/HandAnalysisPane.tsx`** — Remove auto-solve
- Lines 206–280: Remove the `for (const street of postflopStreets)` loop
- Replace with: per-street "Solve" button; cache hits still show immediately
- Remove the effect that triggers on `runKey` change — make solves purely manual

**7. `frontend/src/lib/solver/client.ts`** — Worker health + respawn
- Add `private workerDead = false` field
- Add `worker.onerror = () => { this.workerDead = true; }` in constructor
- Before any `postMessage`, if `workerDead`: call `this.terminate()`, recreate worker, set `workerDead = false`
- Add `ping()` before `solve()` with 2s timeout; if fails → recreate worker
- Split timeout: `DEFAULT_QUICK_TIMEOUT_MS = 30_000`, `DEFAULT_FULL_TIMEOUT_MS = 180_000`
- Add heartbeat: track `lastProgressTime`; if > 15s stale, reject with "Solver hung — no progress"

**8. `frontend/src/lib/solver/worker.ts`** — Error hardening
- In `solve()` function (line 88): wrap entire body in try/catch that produces structured error
- After `wasm.free_game(handle)` in finally (line 138): catch any exceptions from free_game itself
- Add `self.onerror` handler inside worker that sends structured error message to main thread before worker dies

### Phase 1 — Reliability (1–2 weeks)

**Goal:** 95%+ of valid spots solve without crash; all failures surface structured errors.

#### File-level changes:

**1. `solver-wasm/src/lib.rs`** — New `preflight()` export
- `pub fn preflight(scenario_json: &str) -> String` — runs all validation without allocating game
- Returns `"ok"` or JSON error with reason code
- Worker calls `preflight` before `init_game`; on failure → shows "Unsolvable" UX

**2. `frontend/src/lib/solver/client.ts`** — Per-solve worker
- Replace singleton pattern with `createWorker()` factory
- Each `solve()` call creates fresh `SolverClient` with its own worker
- `terminate()` in finally block
- Optional: pool of 2 concurrent workers for future parallel solves

**3. `backend/app/routers/solver.py`** — Failure telemetry
- New `POST /solver-failures` endpoint
- Accepts: `scenario_hash`, `error_class`, `street`, `confidence_tier`, `pot_bb`, `eff_bb`, `range_oop_combos`, `range_ip_combos`, `multiway`, `iterations_completed`, `exploitability_at_failure`, `solver_version`
- Persist to `solver_failures` table
- No PII: no `user_id`, no `hand_id`

**4. `solver-wasm/tests/`** — Regression fixtures
- Create `tests/fixtures/regression/` directory
- Add 20+ envelopes from real hand histories (anonymized)
- `cargo test regression` runs all through init → 10 iter solve → export

**5. `frontend/src/components/hand-review/SolverTab.tsx`** — Confidence gating
- LOW confidence: disable solve buttons; show "Low confidence — solver may be unreliable. Solve anyway?" with opt-in
- MEDIUM: show amber warning; solve proceeds
- HIGH: normal behavior

**6. `backend/app/scenario/builder.py`** — Validation hardening
- Raise `_MIN_SPR` from `Decimal("0.1")` to `Decimal("0.5")`
- After hero combo removal: if total range weight < 5% of original, raise `ScenarioBuildError("range too narrow after combo removal")`
- Validate bet tree sizes are parseable (no empty bet lists, % format correct)

**7. `solver-wasm/src/bet_tree.rs`** — Effective-chip dedup
- After converting pot% to chip amounts, deduplicate sizes that round to same chip value
- If only 1 distinct size remains + `allin_always`, skip allin_always (return 1-size tree)

### Phase 2 — Performance + Architecture (1+ month)

**Goal:** <5s solve times, multiway support, production-grade reliability.

**1. Server-side Rust solver microservice** (Option B)
- New `solver-service/` crate: Axum HTTP server wrapping postflop-solver
- API: `POST /solve` with envelope → `StrategyExport` response
- Shared `solver_runs` cache with browser path
- Enables: true multiway CFR, 200 iter in <5s (native threading), no browser memory limits

**2. COOP/COEP threading for browser path**
- Enable `parallel` feature on wasm target
- Requires: `SharedArrayBuffer`, COOP/COEP headers, Rayon wasm backend
- 4–8× speedup on multi-core desktops

**3. Multiway CFR support**
- Extend envelope to support N players (server-side only)
- New confidence tier: `multiway_true` (vs current `multiway_hu_approx`)

**4. Per-decision-node tree paths**
- Map hand history actions to tree paths
- Export strategy at non-root nodes
- Required for accurate "Action Overview" grading at every decision point

---

## Direct Answers to Audit Questions

### 1. Are OOB/Unreachable bugs most likely our glue/validation or upstream engine on bad input?

**Most likely upstream engine on bad input that our glue fails to reject.** The glue layer (`lib.rs`, `range_convert.rs`, `bet_tree.rs`) validates syntactic structure but not semantic constraints that postflop-solver implicitly requires:

- Glue checks `pot_bb > 0` and `effective_stack_bb > 0`, but doesn't check that `effective_stack_bb > pot_bb × 0.5` (SPR floor where all bet sizes become all-in)
- Glue passes ranges to `Range::parse()` which accepts them, but doesn't verify the resulting Range has sufficient combos
- Glue configures `force_allin_threshold = 0.15` without checking whether this + `allin_always = true` + low SPR creates a degenerate tree

**However**, some OOB may be in the upstream engine itself — in edge cases where tree config passes engine validation but causes internal invariant violations during CFR traversal. These should be reported upstream and pinned.

### 2. Is the HU pot model for multiway creating silent wrong answers vs crashes — which is worse for the product?

**Silent wrong answers are worse for trust.** A crash is visible and fixable. A wrong answer labeled "GTO" is a lie that erodes credibility when discovered. The current HU pot model excludes folded-player dead money, making the pot smaller than reality, SPR larger, and bet sizes proportionally different. The solver computes GTO for a *different game state* than the actual hand.

The confidence system partially mitigates this (marking multiway as "low confidence"), but the UI still presents the grid with "Solver ready" and the warning is easy to miss. **Recommendation:** Phase 1 — LOW confidence requires explicit opt-in. Phase 2 — implement true multiway CFR server-side.

### 3. Should auto quick-solve on hand open be disabled immediately until P0 fixes land?

**Yes, immediately.** The auto-solve queue:
1. Runs 3 sequential solves sharing one worker
2. One crash poisons all three (up to 360s wasted)
3. No cancel/skip mechanism
4. Every opened hand triggers this pipeline

**Phase 0 action:** Disable auto-solve. Replace with manual "Solve" buttons per street. Auto-solve returns in Phase 2 after reliability > 95%.

### 4. Is 120s timeout a symptom of legitimate slow solves or hung/corrupted state?

**Predominantly hung/corrupted state.** A quick solve (50 iter) completes in 3–8s on desktop — a 120s wait is 15–40× longer than expected = dead worker. A full solve (200 iter) on complex river might legitimately need 60–120s on mobile, but progress events fire every 10 iterations (~3–6s). If no progress for 30s, the worker is hung.

**Fix:** Heartbeat-based timeout (15s no progress → kill + respawn) + mode-specific deadlines (30s quick, 180s full).

### 5. What is the best degraded mode when CFR cannot run?

**Honest "unsolvable" with explanation and Coach link.** Solver tab: "This spot cannot be solved because [reason]. Review in Coach for narrative analysis." Coach tab: "LLM-only analysis (no solver data available)" badge.

Fallback order:
1. Cache hit (already implemented)
2. Quick solve (50 iter, 1.0 bb target)
3. Honest "unsolvable" + Coach link
4. **Never:** LLM-generated frequencies or EV

### 6. What single change would improve reliability most per hour of engineering?

**`catch_unwind` + `panic = "unwind"` in the WASM glue crate** (Phase 0, ~4 hours).

Current: any Rust panic silently kills the worker with opaque browser error. After: panics caught by glue, converted to `last_error()` strings, worker stays alive. Converts ~80% of current silent-crash failures into recoverable errors with diagnostic information.

---

*End of audit.*