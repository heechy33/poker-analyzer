# solver-wasm

Thin `wasm-bindgen` glue around [`b-inary/postflop-solver`][upstream]. This
crate is *not* a fork — it links to the upstream engine via a relative path
dependency and only translates the backend `ScenarioEnvelope` JSON into the
engine's `CardConfig` + `TreeConfig`, runs chunked Discounted-CFR
iterations, and serialises results into the cache document the Python
backend stores in `solver_runs.output_jsonb`.

> ⚠️ **AGPL-3.0-or-later.** Because this crate links against the AGPL
> upstream and ships the result over the network, any deployment must
> publish corresponding source. The engine commit is recorded in every
> strategy export as `"solver_version": "postflop-solver@<sha>"`.

## Public JS API

| Export                                               | Returns       | Notes |
|------------------------------------------------------|---------------|-------|
| `init_game(scenario_json: string): number`           | `u32`         | `0` on failure; details via `last_error()`. |
| `solve_step(handle: number, max_iters: number): string` | JSON          | `SolveProgress { handle, iterations_done, max_iterations, exploitability_bb, target_exploitability_bb, finished }`. |
| `get_exploitability(handle: number): number`         | `f32` (bb)    | `NaN` on unknown handle. |
| `export_strategy(handle: number, history_json: string): string` | JSON | `StrategyExport` — see below. Pass `""` for the root node. |
| `free_game(handle: number): void`                    | —             | Idempotent. |
| `last_error(): string`                               | `string`      | Last error from any function, or `""`. |

### Envelope schema (input to `init_game`)

Pass exactly what the backend returns from
`GET /hands/{id}/scenario?street=...`, with one frontend addition:
`hero_position` must be merged in from the sibling `metadata` object so we
can map `hero_range`/`villain_range` onto OOP/IP.

Alternatively, ship `oop_range` + `ip_range` directly and skip
`hero_position`. The crate prefers explicit OOP/IP keys when present.

Optional knobs (sent at the top level of the envelope, ignored by the
backend):

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

Fresh clones should use `git clone --recurse-submodules ...` (see root `README.md`).

### Native (tests, benches)

```bash
# All default features (rayon-backed parallel solving, bincode codecs):
cargo test
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
* **No bunching, no compression.** Allocates 32-bit floats unconditionally.
* **Fixed bet tree per envelope.** No on-the-fly tree editing exposed to JS.
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
│   ├── envelope.rs          # serde structs matching backend JSON
│   ├── range_convert.rs     # hand-class weights -> postflop_solver::Range
│   ├── bet_tree.rs          # backend bet_tree -> BetSizeOptions
│   └── strategy_export.rs   # PostFlopGame -> StrategyExport JSON
└── tests/
    ├── fixtures/scenario_min.json
    └── solver_integration.rs
```

[upstream]: https://github.com/b-inary/postflop-solver
[upstream-app]: https://github.com/b-inary/wasm-postflop
