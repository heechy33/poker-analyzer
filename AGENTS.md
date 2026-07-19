# Poker Analyzer agent guide

## Hard rules

- Offline, post-session study of the user's own CoinPoker NLHE cash hands only. Never add live assistance, client capture/detection, HUDs, opponent profiling, shared-pool analysis, or population reports.
- Keep MCP and other external-agent adapters out of the active roadmap; authenticated product access is through the web app and REST API.
- The active solver roadmap uses the pinned `b-inary/postflop-solver` engine. The first cohort is true CoinPoker two-seat HUNL; six-max and actual multiway hands are replay-only until separately approved.
- Initial cohort: two-seat HUNL, 100 bb, BTN/SB opens one exact supported size, BB calls, flop decisions first. Preflop solving and unconditioned turn/river solving are unsupported.
- Prefer `Not graded`. Never grade fallback ranges, aggregate frequencies, nearest sizes, preview output, wrong/unfinished nodes, missing exact-combo EV, or future showdown information.
- Preserve unrelated worktree changes. Never commit secrets or `.env*` files.

## Read the spec selectively

`MASTER_SPEC.md` is the source of truth, but routine tasks should not load all of it.

- For any product/solver change, read sections 4–6 and section 17's tracking rules plus the active item(s).
- Then read only the routed sections: state/scenarios `8(C3,C6,C7), 9.2, 14`; ranges `8(C5,C14), 10`; solver/tree/cache `8(C1,C2,C7–C11), 11, 14–15`; grading/UI/Coach `8(C4,C9,C13), 12–13`; release/compliance `3, 18–19`.
- Read the full spec only for architecture, cross-cutting correctness, roadmap/release decisions, or edits to the spec itself.

Do not create a parallel plan. If code and the spec disagree, stop grading on that path and update the spec or implementation deliberately.

## Phase tracking is part of implementation

- Before coding, map the change to a Phase 0/1 ID in section 17. If none exists, amend the spec and get approval before implementation. Do not implement provisional later phases.
- After each batch of one or two related features, update the checklist in the same change.
- Mark `[x]` only when implementation and required tests are complete; append concise test/file evidence. Leave partial work unchecked with a short `Progress:` note.
- Run the smallest relevant tests while iterating, then all affected fast suites. Model/schema work requires boundary and regression tests; unsupported paths must be proven unable to display a grade.

## Repository and checks

- `backend/`: FastAPI, parser, ledger/scenarios, storage — `cd backend; pip install -e ".[dev]"; pytest`
- `frontend/`: Next.js replay/solver/review UI — `cd frontend; npm install; npm test; npm run lint; npm run build`
- `solver-wasm/`: Rust/WASM contract — `cd solver-wasm; cargo check --tests`; CI subset: `cargo test --lib -- --test-threads=1`
- `postflop-solver/`: pinned two-player CFR engine; never describe it as multiway-capable.
- WASM build: `cd frontend; npm run build:wasm`

Do not run ignored/full solver regressions locally unless explicitly requested; they are workstation/CI workloads. Keep the canonical ledger, Python/Rust/TypeScript schemas, cache keys, and versioned paired range data aligned.
