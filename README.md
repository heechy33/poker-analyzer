# Poker Analyzer

Poker Analyzer is an offline, post-session review tool for a player's own CoinPoker NLHE cash-game hand histories. It supports private hand-history upload, statistics, replay, and general coaching.

## Current milestone

The project is in **Phase 0: legacy solver teardown and rebuild boundary**. The authoritative roadmap and completion evidence live in [MASTER_SPEC.md](./MASTER_SPEC.md), section 17.

The legacy solver-to-grade experience has been removed. In particular, the application does **not** currently expose solver tabs, action frequencies, decision grades, aggregate hand scores, solver-run routes, browser-submitted solver output, or MCP/external-agent access.

Phase 0 completed work:

- Removed the legacy solver, grading, cache-write, scenario, telemetry, range-library, and MCP contracts.
- Archived/remediated legacy database objects through an allowlisted forward migration.
- Removed regression fixtures that certified fallback ranges, multiway approximation, unfinished-node output, or numeric default scores.
- Preserved the hand list, replay, private statistics, authenticated REST API, and general-coaching path.

P0.6-P0.8 are complete. P0.9 now has local licensing, source-offer, artifact-audit, and database-purge implementation; it remains open until the pinned fork commit is published and migrations 012/013 are verified on the known database. P0.11 awaits its first hosted PostgreSQL Actions run. Phase 1 has not started: it will rebuild a canonical HUNL action ledger and eligibility boundary **without solving**.

No solver caller may be restored until the Phase 1 exit review approves the next implementation checklist.

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
├── postflop-solver/  Pinned two-player CFR engine
├── MASTER_SPEC.md    Authoritative product and implementation specification
└── AGENTS.md         Repository development guide
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

## Development rules

- Treat [MASTER_SPEC.md](./MASTER_SPEC.md) as the source of truth. Map every product or solver change to an approved Phase 0 or Phase 1 item and update its checklist evidence with the implementation.
- Keep the canonical ledger, Python/TypeScript schemas, cache keys, and versioned paired range data aligned when the approved rebuild reaches those components.
- Do not add multiway solving, six-max approximation, preflop solving, live assistance, MCP adapters, or a grading fallback.
- Never commit secrets or `.env*` files.

## License and engine obligations

The repository and retained engine/WASM boundary are licensed under GNU AGPL-3.0-or-later; see [LICENSE](./LICENSE) and [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md). Do not distribute solver artifacts unless the release source-offer gate passes. No range pack or solution set is currently approved for distribution.
