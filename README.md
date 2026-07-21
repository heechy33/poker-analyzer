# Poker Analyzer

Poker Analyzer is an offline, post-session review tool for a player's own CoinPoker NLHE cash-game hand histories. It supports private hand-history upload, statistics, replay, and general coaching.

## Current status

The private upload, statistics, hand-list, replay, and general-coaching experience is functional. Solver-backed review and decision grading are intentionally disabled while the poker-state and eligibility contracts are rebuilt around auditable chip accounting, exact decision-time information, and fail-closed validation.

Recent engineering work:

- Removed an unreliable legacy solver-to-grade path instead of presenting approximate output as authoritative.
- Preserved private hand-history ingestion, personal statistics, replay, authenticated REST APIs, and general coaching.
- Added PostgreSQL-backed backend tests, frontend unit/lint/build checks, browser acceptance coverage, and native Rust/WASM checks to CI.
- Pinned the retained two-player CFR engine and added licensing, source-offer, and artifact-distribution safeguards.

Solver output will return only after the rebuilt state, fee, range, tree, accuracy, and exact-node contracts pass their acceptance gates.

## Scope and safety boundary

This is not a live-play tool. Use it only after a session, for hands you personally played and uploaded, with the CoinPoker client closed. It must not provide live assistance, client capture, HUDs, opponent profiling, shared-pool analysis, or population reports.

The only solver cohort planned for the active roadmap is true CoinPoker two-seat HUNL: 100 bb, BTN/SB opens one exact supported size, BB calls, and the first postflop decisions. Six-max hands remain replay-only even when only two players reach the flop. Hands with three or more players at the flop remain replay-only on every later street, regardless of folds.

When the product cannot support a solver claim, the required outcome is **Not graded**—never a fallback range, nearest-size approximation, aggregate frequency, preview, unfinished node, or information from later in the hand.

## Stack

- Frontend: Next.js, TypeScript, Tailwind CSS
- Backend: FastAPI, SQLModel, Supabase
- Retained engine boundary: Rust/WASM around the pinned two-player `b-inary/postflop-solver` engine (quarantined during the rebuild)
- Authentication: Supabase

## Repository layout

```
poker-analyzer/
├── frontend/         Next.js replay and review UI
├── backend/          FastAPI parser, storage, statistics, and coaching API
├── solver-wasm/      Rust/WASM engine boundary (not a current product solver path)
└── postflop-solver/  Pinned two-player CFR engine
```

## Local development

Clone with submodules because `postflop-solver` is pinned as a submodule:

```bash
git clone --recurse-submodules https://github.com/heechy33/poker-analyzer.git
cd poker-analyzer
# If already cloned without submodules:
# git submodule update --init --recursive
```

Start the backend:

```bash
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Start the frontend in a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Run the relevant checks before contributing:

```bash
cd backend && pytest
cd frontend && npm test && npm run lint && npm run build
cd solver-wasm && cargo check --tests
```

Do not run full/ignored solver regressions locally unless explicitly requested; they are workstation/CI workloads.

## Engineering guardrails

- Keep the canonical ledger, Python/TypeScript schemas, cache keys, and versioned paired range data aligned as the rebuild reaches those components.
- Do not add multiway solving, six-max approximation, preflop solving, live assistance, MCP adapters, or a grading fallback.
- Never commit secrets or `.env*` files.

## License and engine obligations

The repository and retained engine/WASM boundary are licensed under GNU AGPL-3.0-or-later; see [LICENSE](./LICENSE) and [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md). Do not distribute solver artifacts unless the release source-offer gate passes. No range pack or solution set is currently approved for distribution.
