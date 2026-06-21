# ClubWPT Gold-Style Hand Review Feasibility

## Feature Matrix

| Feature | Verdict | Notes |
|---|---:|---|
| Split list + detail layout | ✅ | Frontend-only. `/hands` now uses a card list and integrated analysis pane on large screens. |
| Hand cards with hole + board visuals | ✅ | Hole cards are available in `HandSummary`; board cards require `HandDetail`, so unopened list cards show hole cards first. |
| Hand score bar + ✓/?/✗ counts | ⚠️ | Implemented from cached/opened solver grades. Full list-wide grading would require background solves or summary fields. |
| Street timeline with action pills | ✅ | Uses `HandDetail.actions` and groups by Preflop, Flop, Turn, River. |
| Action Overview grid per hero decision | ⚠️ | Implemented for postflop streets using existing root street solver output. It is attached to the last hero action on that street. |
| Auto-run solver when opening hand | ⚠️ | Implemented as a sequential quick-solve queue for available postflop streets. Cache hits return immediately. |
| Preflop ClubWPT-style analysis | ❌ | `build_scenario` rejects preflop; a preflop solver/range engine is a separate project. |
| Multiway postflop | ⚠️ approximated | Scenario builder picks the best strategic villain via weighted scoring and uses an HU pot model (hero + villain contributions only). Multiway confidence is marked `low` with tiered reason codes and explicit UI labeling that the strategy is approximate, not true multiway GTO. Ranges are tightened for multiway via DB rows (`_multiway` / `_mw` suffixes) or a code-side cumulative-weight fallback. |
| Exact bet size labels | ⚠️ | Solver labels are fixed bet-tree buckets. Hero action mapping is heuristic because `pot_at_action` is not exposed. |
| Context strings (`vs 3-bet`, `vs Limp`) | ⚠️ | Added simple postflop labels such as `vs Bet` / `vs Check`. Richer preflop context remains Tier B. |
| Results table | ✅ | Shows all players, known/derived net, and final stack estimate. Non-hero net is best-effort from parsed actions. |

## Verified Constraints

- WASM artifacts are absent locally: `frontend/public/wasm/` does not exist.
- `npm run build:wasm` currently fails because `wasm-pack` is not installed on this machine.
- The worker expects `/wasm/solver_wasm.js` and already emits an error mentioning `npm run build:wasm`; the integrated UI now surfaces a clearer "Solver not built" message.
- `backend/app/scenario/builder.py` only accepts `flop`, `turn`, and `river`.
- Scenario construction is heads-up oriented: multiway hands are approximated by selecting one villain and lowering confidence.
- `frontend/src/lib/solver/hand-context.ts` maps hero bets to nearest fixed solver labels without pot-at-action, so exact ClubWPT sizing parity is not available.
- The solver exports only the root node for a street. True per-decision-node overviews require passing exact postflop history paths or building multiple scenarios.

## Scope Decision

### Tier A: Implemented Now

- ClubWPT-inspired split list/detail UX for `/hands`.
- Street-by-street integrated hand narrative.
- Embedded postflop Action Overview grids from existing solver output.
- Auto quick-solve queue with cache badge, progress, retry, low-confidence, and missing-WASM errors.
- Advanced range map retained behind a collapsible section.
- Results section with player rows and net/final-stack estimates.

### Tier B: Stretch / Low-Risk Follow-Up

- Richer context labels (`vs Limp`, `vs 3-bet`) from preflop action history.
- Better hero action matching after adding `pot_at_action` to `HandActionOut`.
- Persisted hand-level grade counts in the list API to avoid opening/solving each hand first.

### Tier C: Out Of Scope

- Full preflop GTO.
- True multiway postflop CFR.
- Per-decision-node scenario tree for every hero action.

## Solver Diagnosis

Current local blocker:

```bash
cd frontend
npm run build:wasm
```

Fails with:

```text
'wasm-pack' is not recognized as an internal or external command
```

Install the Rust WASM toolchain, then rebuild:

```bash
rustup target add wasm32-unknown-unknown
cargo install wasm-pack
cd frontend
npm run build:wasm
```

Expected artifacts:

- `frontend/public/wasm/solver_wasm.js`
- `frontend/public/wasm/solver_wasm_bg.wasm`

Once those exist and the backend is running, opening a postflop hand should start quick solves automatically. Cache hits show `From cache`; scenario errors show as inline amber callouts.
