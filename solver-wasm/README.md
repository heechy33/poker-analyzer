# solver-wasm

Thin `wasm-bindgen` glue around [`b-inary/postflop-solver`][upstream]. This
crate is *not* a fork — it links to the upstream engine via a relative path
dependency and translates a legacy `ScenarioEnvelope` JSON into the engine's
`CardConfig` + `TreeConfig`, runs chunked Discounted-CFR iterations, and
serialises strategy output. The legacy backend scenario/cache routes were
deleted during Phase 0; this crate is quarantined infrastructure and has no
product caller until the canonical HUNL contract is rebuilt.

> ⚠️ **AGPL-3.0-or-later.** Because this crate links against the AGPL
> upstream and ships the result over the network, any deployment must
> publish corresponding source. The engine commit is recorded in every
> strategy export as `"solver_version": "postflop-solver@<sha>"`.

## Public JS API

| Export                                               | Returns       | Notes |
|------------------------------------------------------|---------------|-------|
| `preflight(scenario_json: string): string`           | JSON          | Structured `{ok}` envelope; validates without allocation. |
| `init_game(scenario_json: string): string`           | JSON          | Structured `{ok, handle}` or `{ok: false, error_class, message}` envelope. |
| `solve_step(handle: number, max_iters: number): string` | JSON          | `SolveProgress { handle, iterations_done, max_iterations, exploitability_bb, target_exploitability_bb, finished }`. |
| `get_exploitability(handle: number): number`         | `f32` (bb)    | `NaN` on unknown handle. |
| `export_strategy(handle: number, history_json: string): string` | JSON | `StrategyExport` — see below. Pass `""` for the root node. |
| `get_actions_at(handle: number, history_json: string): string` | JSON | Action labels and player at a node, used to build deeper history paths. |
| `free_game(handle: number): void`                    | —             | Idempotent. |
| `last_error(): string`                               | `string`      | Last error from any function, or `""`. |

### Envelope schema (input to `init_game`)

The current structs document the deleted legacy envelope only. There is no
backend endpoint that emits this shape. Tests and isolated development callers
must provide `hero_position` when they use hero/villain-keyed ranges so the
crate can map them onto OOP/IP.

Alternatively, ship `oop_range` + `ip_range` directly and skip
`hero_position`. The crate prefers explicit OOP/IP keys when present.

Optional legacy-envelope knobs:

* `max_iterations: number` (default `200`)
* `target_exploitability_bb: number` (default `0.5`)

### Strategy export shape

```json
{
  "solver_version": "postflop-solver@3a64f855cf20",
  "iterations": 200,
  "exploitability_bb": 0.42,
  "finalized": true,
  "current_player": 0,
  "actions": ["check", "bet_33", "bet_75", "allin"],
  "combo_strategy": {
    "AsKs": { "check": 0.70, "bet_33": 0.30, "bet_75": 0.00, "allin": 0.00 }
  },
  "combo_ev": {
    "AsKs": { "check": 1.21, "bet_33": 1.50, "bet_75": 1.10, "allin": -3.20 }
  },
  "aggregate_frequencies": {
    "check": 0.55, "bet_33": 0.30, "bet_75": 0.10, "allin": 0.05
  }
}
```

Key invariants (see `src/strategy_export.rs` for the full mapping doc):

* `actions[i]` lines up with `combo_strategy[*][actions[i]]` and
  `combo_ev[*][actions[i]]` — same string keys throughout.
* Probabilities per combo sum to `≈1.0` (within float ε).
* Aggregate frequencies are the `normalized_weights`-weighted mean over
  combos and also sum to `≈1.0`.
* EV is in **big blinds**, biased by the upstream solver so 0.0 means
  "neutral vs. start-of-street equity".
* `current_player` is `0` for OOP, `1` for IP.

## Build

### Prerequisites

`postflop-solver` is a **git submodule** (pinned fork at `3a64f855cf20`). From the
repo root:

```bash
git submodule update --init --recursive
```

Fresh clones should use `git clone --recurse-submodules ...`.

### Native (tests, benches)

**Do not run the full regression suite locally on a laptop** — wide-range
fixtures peg CPU/RAM for 30–60+ minutes. GitHub Actions is the regression
runner (see `.github/workflows/regression.yml`).

```bash
# Fast compile check (safe on any machine):
cargo check --tests

# CI-equivalent subset (preflight all 22 + 3 light smokes in a separate test):
cargo test --test solver_integration regression_ free_game reinit unknown_handle -- --test-threads=1

# Full 22-fixture smoke (slow — run on a workstation or GHA, not a dev laptop):
cargo test --test solver_integration regression_all_fixtures_dynamically -- --ignored --nocapture --test-threads=1

# 60-iter convergence roundtrip (also slow):
cargo test --test solver_integration init_solve_export_roundtrip -- --nocapture --test-threads=1
```

### WebAssembly (v1: single-threaded)

```bash
# Install toolchain prerequisites (one-time):
rustup target add wasm32-unknown-unknown
cargo install wasm-pack            # last verified: wasm-pack 0.13
# Rust pin (matches the postflop-solver community-fix commit): 1.94+.

# From the repo root:
cd frontend
npm run build:wasm
# Equivalent to:
#   wasm-pack build ../solver-wasm --target web --release --no-default-features \
#     && node scripts/copy-wasm.mjs
```

Artifacts land in `frontend/public/wasm/`:

* `solver_wasm_bg.wasm`
* `solver_wasm.js`
* `solver_wasm.d.ts`
* `solver_wasm_bg.wasm.d.ts`

### WebAssembly + threads (future, T11+)

Enabling rayon on wasm needs:

* nightly Rust + `-Z build-std`
* `wasm32-unknown-unknown` with atomics + bulk-memory target features
* host serving COOP `same-origin` + COEP `require-corp` for SharedArrayBuffer

When wiring this up, build with `--features parallel` and follow the
[wasm-postflop reference][upstream-app] for the worker-pool plumbing. The
crate is structured so the public API stays unchanged.

## Known limitations (v1)

* **Single-threaded wasm.** A 200-iter solve on a typical flop spot takes
  ~10–30s in-browser on a modern laptop. Acceptable for "review one hand"
  flow; will be revisited in T11 with worker threads.
* **No bunching.** Browser builds use the engine's 16-bit compressed storage
  (native tests remain 32-bit) and reject trees above a 1 GiB pre-allocation budget.
* **Quarantined browser-bounded preview tree.** The legacy compatibility flag
  keeps the initial-street sizes but makes future streets check-down runouts.
  This materially changes current strategy and EV, so it is not a verified or
  gradeable product path. Other envelopes keep their configured tree. No
  on-the-fly tree editing is exposed to JS.
* **One active game per worker.** Spawn additional workers to solve
  concurrently; the global state inside the wasm module is `Mutex`-guarded
  but not designed for parallel solves on a shared module.

## Files

```
solver-wasm/
├── Cargo.toml
├── build.rs                 # injects SOLVER_WASM_ENGINE_SHA into strategy export
├── README.md                # this file
├── src/
│   ├── lib.rs               # public wasm-bindgen surface + global state
│   ├── envelope.rs          # quarantined legacy envelope serde structs
│   ├── range_convert.rs     # hand-class weights -> postflop_solver::Range
│   ├── bet_tree.rs          # legacy bet_tree -> BetSizeOptions
│   └── strategy_export.rs   # PostFlopGame -> StrategyExport JSON
└── tests/
    ├── fixtures/scenario_min.json
    └── solver_integration.rs
```

[upstream]: https://github.com/b-inary/postflop-solver
[upstream-app]: https://github.com/b-inary/wasm-postflop
