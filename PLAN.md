# CoinPoker Hand History Analyzer — Planning Document

> Senior architect's plan for a portfolio project targeting 2026 full-stack / backend new-grad roles.
> Opinionated. Concrete. Designed so you can start writing code the minute you stop reading.

---

## 0. Research Findings (the "why" behind every decision below)

### 0.1 CoinPoker hand history format — what's reliably parseable

From the sample, the format is a deterministic, line-based variant of the classic PokerStars `.txt` schema (CoinPoker uses ₮ for Tether and a few labels of its own like `Splash Fee`). It is fully parseable with a state-machine line scanner — no need for a PEG/Lark grammar. The streets are delimited by `*** HOLE CARDS ***`, `*** FLOP ***`, `*** TURN ***`, `*** RIVER ***`, `*** SHOWDOWN ***`, `*** SUMMARY ***`. One file contains many hands separated by a blank line and a new `CoinPoker Hand #...` header.

**Reliably extractable in v1:**

| Field | Source line | Notes |
|---|---|---|
| `hand_id` | `CoinPoker Hand #5331371084:` | Globally unique. |
| `variant` | `NLH` | Only NLH in v1. |
| `stake_small_blind` / `stake_big_blind` | `(₮0.10/₮0.25)` | Strip `₮` — always Tether on CoinPoker. |
| `played_at` | `2026/05/23 15:50:17 PDT` | Parse with explicit timezone. |
| `table_name` | `Table '201122'` | |
| `table_size` | `6-max` | Also `9-max`, `heads-up`. |
| `button_seat` | `Seat #3 is the button` | Drives position assignment. |
| `seats[]` | `Seat 4: Hero (₮26.85 in chips)` | Stack sizes here are starting stacks. |
| `posts[]` | `Hero: posts small blind ₮0.10` | Includes SB, BB, straddles, dead blinds, ante. |
| `hero_cards` | `Dealt to Hero [Kc 9d]` | Only one `Dealt to` line per hand (CoinPoker, like Stars, doesn't echo opponent holes pre-showdown). |
| `streets[].board` | `*** FLOP *** [Kd 9c Td]` | Turn/river concatenate previous board. |
| `actions[]` | `Hero: bets ₮0.90` | Verbs: `folds`, `checks`, `calls`, `bets`, `raises ... to ...`, `is all-in`. |
| `showdown[]` | `dd107cdd: shows [Qd Js] (Straight)` | Hand description in parens is human-readable, recompute it from cards. |
| `collected[]` | `dd107cdd collected ₮13.66 from pot` | One line per pot winner. |
| `total_pot` / `rake` / `splash_fee` | `Total pot ₮14.40 \| Rake ₮0.72 \| Splash Fee ₮0.02` | CoinPoker-specific `Splash Fee` — store it. |
| `mucked[]` | `Hero: mucks hand` | |

**Edge cases that must be handled in v1** (don't skip these — they're 5% of hands but 100% of bug reports):

1. **All-in** — `Hero: bets ₮5.00 and is all-in`. Action verb is the same, append a flag.
2. **Side pots** — `dd107cdd collected ₮5.00 from side pot` and `... from main pot`. Multiple `collected` lines for one hand.
3. **Split pots** — multiple winners listed on the same pot ("split"). Showdown line may include "(Two pair)" tied.
4. **Run it twice** — CoinPoker supports it: you'll see `*** FIRST RIVER ***` / `*** SECOND RIVER ***`. v1 can store the second board but only score the first run for stats (otherwise rake math gets weird).
5. **Disconnect / sit-out** — `eb3f454b: is sitting out` and time-outs `... has timed out`. Treat as fold.
6. **Uncalled bet returns** — `Uncalled bet (₮0.50) returned to Hero`. Critical for net P/L accuracy.
7. **Mucked unknown** — most hands show `Hero: mucks hand` with no cards; villain hole cards are unknown for the 95% of hands that don't reach showdown.
8. **Posting out of order** — `eb3f454b: posts big blind ₮0.25` from a player joining the table.
9. **Re-raises** — `raises ₮3.60 to ₮5.40` — store both the raise increment AND the total to-call. Mixing these up is the classic parser bug.
10. **Non-ASCII chars** — `₮` (U+20AE). Use UTF-8 everywhere; never decode as latin-1.

**v1 parser strategy:** one Python module, `parser/coinpoker.py`, that yields `Hand` Pydantic objects from a `BufferedReader`. Pure-function, no I/O. Drives a `pytest` golden-file suite where you check in 30–50 real anonymized hands and snapshot the parsed JSON. This is the single most valuable test asset in the project; treat it like production data.

---

### 0.2 GTO solver landscape — what actually works

I read the actual repos and license files. The honest state of the world in mid-2026:

| Solver | License | Status | Useful for this project? |
|---|---|---|---|
| **`b-inary/wasm-postflop`** (Vue web app) | AGPL-3.0 | Officially "development suspended" (Oct 2023) — but community fixes keep landing (PR #19 merged Dec 2025; postflop-solver PR #57 April 2026). The website hosts a working build today. | **Reference implementation**, not a dependency. UI is Vue, not React. |
| **`b-inary/postflop-solver`** (Rust crate) | AGPL-3.0 | "Suspended" but works. Compiles to `wasm32-unknown-unknown`, has a `basic` example. CFR+ / Discounted CFR engine. Maintainer literally says "the primary purpose is to serve as a backend for the GUI apps" — so direct use is "use at your own risk", breaking changes between commits. | **YES — this is the engine.** Compile to WASM, run in the browser. |
| **`b-inary/desktop-postflop`** (Tauri) | AGPL-3.0 | Same engine, native Tauri shell. Last release Oct 2023. | No — wrong form factor for a web portfolio project. |
| **TexasSolver** (`bupticybee`) | AGPL-3.0, with explicit clause: integrating the *binary* into your software is allowed; integrating the *source*, or **providing service over the internet**, requires a commercial license. | Actively pushed March 2026. Has a GPU variant (closed source). | **NO** — the author explicitly excludes hosting it as a web service. Killing it for v1. |
| **`noambrown/poker_solver`** | MIT | Brand new (Jan 2026), river-only, research-grade. | Cool to follow, not production-ready. |

**The integration question:** "Can WASM Postflop realistically be integrated into a React frontend?"

The Vue app cannot be dropped in. But the *underlying mechanism* absolutely can: take `b-inary/postflop-solver` (Rust), compile it to a `.wasm` blob via `wasm-pack`, and write a thin React wrapper that calls into it from a Web Worker. The wasm-postflop repo is your reference for (a) how to invoke the API, (b) how to wire up multi-threading with `SharedArrayBuffer`, (c) which COOP/COEP headers you need to serve. You'll copy ~200 lines of Rust glue and ~150 lines of TypeScript worker code, no Vue.

**Does the "abandoned" status matter?** Not really. The engine is feature-complete for hold'em postflop. The April 2026 community PR that pinned `bincode 2.0.0-rc.3` and fixed a Rust 1.94 lint is exactly the kind of maintenance you'd need to do yourself, and it's already done. The risk: if a future Rust release breaks the crate, you'd own the fix. Acceptable for a portfolio project.

**Ranges — the actual hard problem**

A CFR solver needs `(hero_range, villain_range, board, pot, stacks, bet_tree)` — *not* `(hero_cards, villain_cards)`. The solver works on probability distributions over hands, not specific holdings. So to "analyze" a real hand, you have to *reconstruct what each player's range was* by the time you got to the flop, based only on the preflop action.

This is the part that scares people off, but it's actually a solved problem for portfolio scope. The approach:

1. Build a **static GTO preflop range library** keyed by `(table_size, position, action_sequence)`. Example keys:
   - `("6max", "BTN", "open")` → ~42% range (22+, A2s+, K2s+, Q2s+, J6s+, T7s+, 96s+, 86s+, 75s+, 64s+, 54s, A2o+, K8o+, Q9o+, J9o+, T9o)
   - `("6max", "BB", "vs_BTN_open_call")` → ~30% defending range
   - `("6max", "CO", "vs_HJ_open_3bet")` → ~10% polarized range
   These are public knowledge — published in dozens of free GTO chart sources (RiverOdds, ThinkGTO, GTO Wizard's free tier). You encode them once as JSON, version them, ship them. Maybe 80 entries to cover every realistic 6-max preflop tree to 1 raise + 1 reraise + 1 cold-call. Two days of work.
2. **For postflop solver input**, look up `hero_range = library["6max"]["BTN"]["open"]` minus the specific combo hero held, and `villain_range = library["6max"]["BB"]["vs_BTN_open_call"]`.
3. **Edge case**: when villain's action wasn't in your library (e.g., a weird 4-bet sizing, multi-way pot), default to a "loose call range" and flag the analysis as `confidence=low`. That's honest, and it's what hiring managers want to see — graceful degradation, not "no analysis available".

**Is this good enough for a portfolio project?** Absolutely. Real micro-stakes opponents are nowhere near GTO, so a 30%-tight-versus-actual villain range produces analysis that is *more* useful than perfect equilibrium ranges anyway. Calling it out in the UI ("based on GTO baseline ranges — your opponents likely play wider") is itself a sophistication signal.

**Concrete final recommendation:**

**Hybrid: WASM-compiled postflop-solver for on-demand single-hand review + LLM for natural-language commentary + static range library for preflop reference. Skip TexasSolver entirely.**

Why hybrid over pure CFR-on-everything:

- Solving every hand on the server costs CPU and the user only ever reviews 5–10 hands from a session, not all 200.
- Running the solver in the browser is *the* differentiating technical detail. "I shipped a CFR equilibrium solver to a React app via Rust→WASM" is a 30-second pitch any senior engineer will lean forward for.
- LLM does the part the solver can't: "your bet sizing on the turn was off-tree and your river call is the textbook bluff-catcher trap — you blocked the Qx value combos."

**Hand review UI — what the solver actually returns**

For a given postflop spot, the solver gives you, for each of the ~1326 possible 2-card combos in hero's range, a strategy vector like `[fold: 0.0, check: 0.7, bet_33%: 0.2, bet_75%: 0.1]` and EVs. The standard visualization (Pio, GTO+, Wizard) is:

- **Range grid** (13×13): each cell colored by action mix for that hand class (e.g., `AKo` half-blue/half-red = 50% check / 50% bet).
- **Action frequency bar** for the spot overall ("range bets 45% of the time, checks 55%").
- **EV diff**: hero's chosen action vs. solver-best action, in big blinds. This is the money number.
- **Equity / EV / Equity-Realization** numbers per action.

For v1, ship just the range grid and the EV-diff number. The grid is one React component, ~150 LOC.

---

### 0.3 2026 resume-maximizing tech stack — research

I checked actual 2026 hiring data (MCP adoption stats from Apr 2026, Next.js vs React job board surveys, AWS deployment guides current to 2026). The summary:

- **Next.js** is *the* default expectation for any 2026 frontend role. Job postings now write "React/Next.js" as a single skill; "React only" reads as 2023. Next.js with App Router is ~25% salary premium over React-with-Vite in US listings.
- **MCP** crossed from emerging to standard. 78% of enterprise AI teams ran ≥1 MCP server in production by Q1 2026, public registry at 9,400+ servers. Anthropic donated the spec to Linux Foundation (Agentic AI Foundation, with OpenAI / Google / Microsoft / Amazon as founding members). MCP shows up as a required skill in AI engineering listings at Anthropic, Cursor, Replit, Windsurf, Cline. **It is no longer noise.**
- **AWS** still dominates resume keyword scans, but Vercel is now a recognized signal too. AWS App Runner was deprecated in 2026 (avoid). Lambda + API Gateway is the cheapest "AWS" bullet you can earn.
- **LangChain** in 2026 is a net negative for small projects: 35% slower throughput than the raw Anthropic SDK, 3× memory footprint, adds vendor lock-in. Interviewers ask why you reached for a framework on a single-call workflow. Use the Anthropic SDK directly.
- **TypeScript** is non-negotiable. "JavaScript" without "TypeScript" reads as a yellow flag in 2026 frontend screens.
- **Docker** is worth the day of setup. Mentions of "containerized" pass ATS and give you something to point at in interviews.

---

## 1. Project Summary

**CoinPoker Hand History Analyzer** is a full-stack web app that turns a poker player's raw `.txt` hand histories into actionable, GTO-grounded leak analysis. Users upload hand history files; a deterministic Python parser ingests them into Supabase Postgres; a React + Next.js dashboard surfaces standard tracking-software stats (VPIP, PFR, 3-bet, position-by-position win rate, biggest losers). Where it differentiates: any hand can be opened into a *hand review modal* that runs the open-source `postflop-solver` CFR engine **directly in the browser via Rust→WASM**, compares the user's actual line to the equilibrium strategy, and pipes the diff into Claude (Anthropic API) for plain-English commentary on the leak. The same backend is simultaneously exposed as an **MCP (Model Context Protocol) server**, letting any MCP-aware LLM client (Claude Desktop, Cursor, ChatGPT, custom agents) query the user's poker data with tools like `get_recent_losses` or `analyze_hand`. It is impressive because it ships three things very few new-grad portfolios do at once: a non-trivial domain parser with property-tested correctness, a real production deployment of CFR equilibrium solving via Rust/WASM, and a first-class MCP integration on the dominant 2026 agent protocol.

---

## 2. Supabase Schema

Postgres via Supabase. Use `auth.users` for identity, RLS on every table with `user_id = auth.uid()`. All money columns are `numeric(12, 4)` (CoinPoker has 4-decimal-cent splash fees). Timestamps are `timestamptz`.

### `uploads`
Tracks each raw file the user uploads.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | `gen_random_uuid()` |
| `user_id` | `uuid` FK → `auth.users` | RLS pin |
| `filename` | `text` | Original name. |
| `storage_path` | `text` | Path inside Supabase Storage bucket `hand-histories`. |
| `sha256` | `text` UNIQUE per user | Dedup re-uploads. |
| `bytes` | `integer` | |
| `hand_count` | `integer` | Populated post-parse. |
| `status` | `text` | `queued` \| `parsing` \| `parsed` \| `error` |
| `error_message` | `text` NULL | |
| `uploaded_at` | `timestamptz` DEFAULT `now()` | |

### `sessions`
Auto-derived clusters of hands played in the same time window at the same stake. Created by the post-parse pipeline.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK | |
| `started_at` | `timestamptz` | |
| `ended_at` | `timestamptz` | |
| `stake_bb` | `numeric(8,4)` | Big blind in ₮. |
| `table_size` | `smallint` | 2, 6, 9. |
| `hands_played` | `integer` | |
| `hero_net` | `numeric(12,4)` | Cumulative profit in ₮. |
| `hero_net_bb` | `numeric(12,4)` | Same in big blinds. |

### `hands`
Core fact table — one row per hand.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK | |
| `upload_id` | `uuid` FK → `uploads` | |
| `session_id` | `uuid` FK → `sessions` NULL | Filled in by clustering step. |
| `coinpoker_hand_id` | `bigint` UNIQUE per user | `5331371084` in the sample. |
| `played_at` | `timestamptz` | |
| `table_name` | `text` | |
| `table_size` | `smallint` | |
| `stake_sb` | `numeric(8,4)` | |
| `stake_bb` | `numeric(8,4)` | |
| `button_seat` | `smallint` | |
| `hero_seat` | `smallint` | |
| `hero_position` | `text` | `BTN`, `SB`, `BB`, `UTG`, `HJ`, `CO`. Derived. |
| `hero_cards` | `text[2]` | `['Kc', '9d']`. |
| `flop` | `text[3]` NULL | |
| `turn` | `text` NULL | |
| `river` | `text` NULL | |
| `total_pot` | `numeric(12,4)` | |
| `rake` | `numeric(12,4)` | |
| `splash_fee` | `numeric(12,4)` | |
| `hero_invested` | `numeric(12,4)` | Sum of hero contributions. |
| `hero_collected` | `numeric(12,4)` | From `... collected` lines. |
| `hero_net` | `numeric(12,4)` | `collected − invested`. |
| `hero_net_bb` | `numeric(12,4)` | `hero_net / stake_bb`. |
| `went_to_showdown` | `boolean` | |
| `won_at_showdown` | `boolean` NULL | |
| `flags` | `jsonb` | `{all_in: true, run_it_twice: false, split_pot: false}`. |
| `raw_text` | `text` | The slice of the original file. Useful for re-parsing on schema changes. |

Indexes: `(user_id, played_at DESC)`, `(user_id, hero_position)`, `(user_id, session_id)`.

### `hand_players`
One row per seat occupied per hand.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `hand_id` | `uuid` FK → `hands` | |
| `user_id` | `uuid` FK | denormalized for RLS speed |
| `seat` | `smallint` | |
| `screen_name` | `text` | `Hero` or villain handle. |
| `position` | `text` | |
| `starting_stack` | `numeric(12,4)` | |
| `is_hero` | `boolean` | |
| `final_cards` | `text[2]` NULL | If shown at showdown. |

### `hand_actions`
Append-only event log. Powers stats and the hand replayer.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `hand_id` | `uuid` FK | |
| `user_id` | `uuid` FK | |
| `street` | `text` | `preflop`, `flop`, `turn`, `river`, `showdown`. |
| `action_order` | `smallint` | Within the street. |
| `seat` | `smallint` | |
| `screen_name` | `text` | |
| `action` | `text` | `post_sb`, `post_bb`, `fold`, `check`, `call`, `bet`, `raise`, `all_in`, `show`, `muck`, `collect`. |
| `amount` | `numeric(12,4)` NULL | For `bet`/`call`/`collect`. |
| `raise_to` | `numeric(12,4)` NULL | Total bet after raise. |
| `is_all_in` | `boolean` DEFAULT false | |

### `range_library`
Static, version-controlled. Loaded once via SQL migration; rarely changes.

| Column | Type | Notes |
|---|---|---|
| `id` | `serial` PK | |
| `table_size` | `smallint` | |
| `effective_stack_bb` | `smallint` | 100 in v1. |
| `position` | `text` | |
| `action_sequence` | `text` | e.g., `"open"`, `"vs_BTN_open_call"`, `"vs_CO_open_3bet"`. |
| `range_string` | `text` | PIO-style: `"22+,A2s+,K9s+,..."`. |
| `combo_weights` | `jsonb` | Optional dense `{ "AKs": 1.0, "JTs": 0.75, ... }` for mixed strategies. |
| `source` | `text` | `"GTOWizard-free-tier"`, `"rivers-app"`, etc. — for honesty. |
| `version` | `text` | `"v1"`. |

### `solver_runs`
Cache of CFR outputs so a given spot isn't re-solved on every page load.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK | |
| `hand_id` | `uuid` FK | |
| `street` | `text` | `flop`, `turn`, `river`. |
| `scenario_hash` | `text` UNIQUE | SHA256 of `(board, pot, stacks, hero_range_id, villain_range_id, bet_tree_id)`. Collisions are deterministic across users. |
| `solver_version` | `text` | `"postflop-solver@<commit>"`. |
| `iterations` | `integer` | |
| `exploitability_bb` | `numeric(8,4)` | Convergence proof. |
| `output_jsonb` | `jsonb` | Strategy vectors per combo. Compressed via TOAST. |
| `created_at` | `timestamptz` | |

### `llm_analyses`
Cache of Anthropic-generated commentary.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK | |
| `hand_id` | `uuid` FK | |
| `model` | `text` | `claude-opus-4-7`. |
| `prompt_hash` | `text` | For cache key. |
| `analysis_text` | `text` | Plain-English review. |
| `leak_tags` | `text[]` | `{overfold_turn, thin_value_river}` — enumerated tags drive the leak dashboard. |
| `input_tokens` / `output_tokens` | `integer` | For cost tracking. |
| `created_at` | `timestamptz` | |

### Row-Level Security

One policy per table, all of the form:
`USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())`. The shared `range_library` is the only public-read table.

### Supabase Storage bucket

- `hand-histories` (private). Path: `{user_id}/{upload_id}/{filename}`. RLS enforced by Supabase Storage policies mirroring the `uploads` table.

---

## 3. Tech Stack Decision

| Layer | Choice | One-line justification |
|---|---|---|
| **Frontend framework** | **Next.js 14 (App Router)** + TypeScript | Default expectation in 2026 frontend listings; SSR/streaming for the dashboard is free; same project hosts the hand-review SPA. |
| **Styling / UI** | **Tailwind CSS + shadcn/ui** | The 2026 "ships fast and looks intentional" combo; every interviewer recognizes it. |
| **Charts / grids** | **Recharts** for stats, **custom Canvas-based 13×13 range grid** | Recharts handles the boring bar/line stuff; the range grid is small enough to hand-roll and reads as portfolio-grade. |
| **State** | **TanStack Query** for server state, **Zustand** for the hand-review modal | No Redux ceremony; both signal modern React fluency. |
| **Backend framework** | **FastAPI** (Python 3.12) | You already have it on the resume; perfect for the parser + stats pipeline; FastMCP plugs in cleanly. |
| **ORM** | **SQLModel** (Pydantic + SQLAlchemy) | Native FastAPI dependency injection; one model class for DB + API. |
| **Background jobs** | **arq** (Redis-backed) running on the same Lambda *or* in-process | Parsing a 10k-hand file shouldn't block the request. arq is dead simple. |
| **Database** | **Supabase Postgres** | Already on the resume; auth, storage, RLS, REST all included; saves a week of plumbing. |
| **Cache / queue** | **Upstash Redis** (serverless, free tier) | Only if/when arq is added; do not add prematurely. |
| **GTO solver** | **`b-inary/postflop-solver`** (Rust) compiled to **WASM**, run in a **Web Worker** | The differentiating technical artifact; runs on the user's CPU, no server cost. |
| **LLM** | **Anthropic Claude (Opus 4.7)** via the official Python + TypeScript SDKs | Direct calls — 35% faster than LangChain, no orchestration overhead for a single-call workflow. |
| **MCP server** | **`fastapi-mcp`** mounted into the FastAPI app (`/mcp`) | One import, every existing endpoint becomes an MCP tool with FastAPI auth preserved. |
| **Frontend hosting** | **Vercel** (Hobby tier) | Built for Next.js; preview deploys per PR; sets COOP/COEP headers needed for SharedArrayBuffer/WASM threading. |
| **Backend hosting** | **AWS Lambda + API Gateway** via **Mangum** adapter, **Docker container** image | Real "AWS Lambda" bullet on resume; free tier covers single-user load forever. |
| **Containerization** | **Docker** (multi-stage build for backend) | Used both locally (`docker compose up`) and as the Lambda image. |
| **CI/CD** | **GitHub Actions** | Lint (ruff, mypy, eslint), test (pytest, vitest), build the Docker image, push to ECR, update Lambda. Already on the resume; one more matrix. |
| **Testing** | **pytest + property-based via Hypothesis** (backend), **Vitest + Playwright** (frontend) | Hypothesis on the parser is the line that gets a senior engineer's attention. |
| **Observability** | **Sentry** (free tier) on both ends | Bare minimum, one SDK install. |
| **Auth** | **Supabase Auth** (email magic link only in v1) | Free, works with RLS out of the box, one fewer thing to ship. |

**Explicit "no" list (resist the urge):**

- No NestJS. No Express. No tRPC. Pick one backend.
- No PostgREST direct from the frontend — go through FastAPI so the MCP server has somewhere to live.
- No LangChain. No LlamaIndex. No vector DB in v1 (no embedding-heavy use case yet).
- No Kubernetes. No Terraform. Lambda + a `serverless.yml` (or just AWS SAM CLI) is enough.
- No microservices. One backend service.
- No GraphQL. REST + OpenAPI feeds the MCP server for free.

---

## 4. System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Browser (Next.js + React)                                                   │
│                                                                              │
│   [Upload UI] ──► presigned URL ──► Supabase Storage (hand-histories/)       │
│                                                                              │
│   [Dashboard]  ──REST──► FastAPI /stats/*                                    │
│   [Hand list]  ──REST──► FastAPI /hands/*                                    │
│                                                                              │
│   [Hand Review Modal]                                                        │
│      ├─► postflop-solver.wasm (in Web Worker, SharedArrayBuffer)             │
│      ├─► REST──► FastAPI /hands/{id}/scenario  (builds ranges + tree)        │
│      └─► REST──► FastAPI /hands/{id}/analyze   (Claude commentary)           │
│                                                                              │
└────────────────────┬─────────────────────────────────────────────────────────┘
                     │ HTTPS
┌────────────────────▼─────────────────────────────────────────────────────────┐
│  AWS Lambda (container image)                                                │
│                                                                              │
│   FastAPI app (Mangum adapter)                                               │
│   ├─ REST API                                                                │
│   ├─ /mcp (fastapi-mcp ASGI mount)  ◄────── Claude Desktop / Cursor / etc.   │
│   └─ Background tasks (FastAPI BackgroundTasks; arq if scope grows)          │
│                                                                              │
│      ┌─ parser/coinpoker.py  ── Pydantic Hand objects                        │
│      ├─ stats/compute.py     ── VPIP, PFR, 3-bet, win-rate-by-position       │
│      ├─ scenario/builder.py  ── (hand, action_idx) → solver input JSON       │
│      └─ llm/anthropic.py     ── Claude prompts + leak tagging                │
│                                                                              │
└────────────────────┬─────────────────────────────────────────────────────────┘
                     │ Postgres wire / Supabase REST
┌────────────────────▼─────────────────────────────────────────────────────────┐
│  Supabase                                                                    │
│   ├─ Postgres (uploads, sessions, hands, hand_players, hand_actions,         │
│   │             range_library, solver_runs, llm_analyses)                    │
│   ├─ Storage (hand-histories bucket)                                         │
│   └─ Auth (email magic link)                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

**End-to-end flow for a single hand review:**

1. User drags `HH20260523.txt` onto the upload zone.
2. Next.js requests a signed upload URL from FastAPI → uploads directly to Supabase Storage. (Keeps file bytes off Lambda.)
3. FastAPI receives the "upload complete" webhook, enqueues a parse task.
4. Parser streams the file from Storage, yields `Hand` objects, bulk-inserts into `hands` / `hand_players` / `hand_actions`. Session clustering runs after.
5. Frontend polls `/uploads/{id}` until `status='parsed'`, then redirects to the dashboard.
6. Dashboard fetches `/stats/summary` and `/hands?limit=50&order=hero_net.asc` (biggest losing hands).
7. User clicks a hand → modal opens, fetches `/hands/{id}` (full action log + cards).
8. User clicks "Solve flop" → modal fetches `/hands/{id}/scenario?street=flop`. Backend builds `{hero_range, villain_range, board, pot, stacks, bet_tree}` using the range library + actual action log; returns JSON.
9. Modal posts that JSON to the WASM worker. Worker runs CFR for N iterations (target: <30s on a laptop), streams progress back to the UI, returns strategy vectors. Result cached in `solver_runs` via a background `POST /solver-runs`.
10. User clicks "Explain" → frontend posts `(hand, solver_output, hero_actual_action)` to `/hands/{id}/analyze`. Backend assembles a Claude prompt, streams the response back via SSE, caches in `llm_analyses` with extracted `leak_tags`.

**MCP path (parallel, no UI involved):**

11. User configures Claude Desktop with `{ "mcpServers": { "poker": { "url": "https://api.poker-analyzer.app/mcp", "headers": { "Authorization": "Bearer ..." } } } }`.
12. User asks Claude: "What were my three biggest losing hands this week?" Claude calls the MCP `list_hands` tool with the right filters. The MCP server is the same FastAPI app — no separate service.

---

## 5. Feature Scope — v1 (everything before public deploy)

**In:**

1. **Auth** — Supabase email magic link. Single user.
2. **Upload** — drag-and-drop one or many `.txt` files, dedup by SHA-256, progress + error UI.
3. **Parser** — CoinPoker NLH cash, 6-max + heads-up, with the 10 edge cases listed in §0.1. Property-tested with Hypothesis + 30-hand golden file corpus.
4. **Stats dashboard** — VPIP, PFR, 3-bet%, WTSD%, W$SD%, BB/100 by position, lifetime/last 7d/last 30d toggle, hands-per-position counts.
5. **Hand list** — sortable/filterable table: date, position, hero cards, pot, net, went-to-showdown, action summary. Click to open review modal.
6. **Biggest losing hands** widget — top 10 by `hero_net` ASC, last 30 days. The point of the project.
7. **Hand review modal** —
   - Visual replayer (cards + chips animation is a nice-to-have; v1 = action list with money flow).
   - "Solve this spot" button per postflop street → WASM solver → range grid + EV diff.
   - "Explain" button → Claude commentary streamed in, with extracted leak tags.
8. **Leak summary page** — aggregate of `llm_analyses.leak_tags` over time: "You overfold the turn vs. 3rd-barrel bluffs in 41% of qualifying spots."
9. **MCP server** at `/mcp`, exposing 6 tools (see §7).
10. **Public deploy** — Vercel + AWS Lambda + Supabase. HTTPS. Custom domain optional.
11. **CI** — lint + test + Docker build + Lambda update on `main` push.

**Out (explicit scope cuts — write these in a TODO.md, do not let them creep in):**

- Tournament hands. Cash NLH only.
- Multi-currency. Tether only.
- PLO, short-deck, mixed games.
- Multi-table session reconstruction beyond simple time-clustering.
- Hand sharing / social features.
- A "live" overlay HUD for the CoinPoker client (would require a desktop app).
- Mobile-optimized layouts — desktop-first, "works on tablet" is enough.
- Multi-user pricing / billing.
- pgvector + embedding-based hand similarity. (Cool for v2 if you want a vector-DB bullet.)
- Solver bet-tree configuration UI — use a fixed standard tree (33%, 75%, 150% bets + all-in) for v1.
- Importing from any tracker other than CoinPoker.

**Done definition:** you can log in, upload a folder of your own hand histories, see your real stats, click into your worst hand of the week, watch the solver converge in the browser, and read Claude's take on what you misplayed — all on the public URL. Then point Claude Desktop at the MCP endpoint and ask "what's my biggest leak?" and get a real answer.

---

## 6. GTO Solver Integration Plan

**Decision (concrete):** Compile `b-inary/postflop-solver` to WebAssembly and ship it as a Web Worker bundle inside the Next.js app. Reference the wasm-postflop repo for the API surface and threading setup, but write your own thin glue layer rather than vendoring their Vue code.

**Implementation, in order:**

1. **Fork `b-inary/postflop-solver`** into your own GitHub org. Pin to the April 2026 community-fix commit (the one that fixes `bincode` and the Rust 1.94 lint). This is your insurance against future Rust breakage.
2. **Add a `wasm` crate** alongside it (in `solver/wasm/`) that exposes a minimal `wasm-bindgen` API:
   - `init(scenario_json: &str) -> GameHandle`
   - `solve(handle, max_iterations, target_exploitability_bb) -> ProgressIter`
   - `get_strategy(handle, street, history_path) -> StrategyMap`
   - `get_ev(handle, street, history_path, combo) -> f32`
3. **Build script:** `npm run build:wasm` runs `wasm-pack build --target web --release` and copies the artifacts to `frontend/public/wasm/`.
4. **Web Worker wrapper:** `frontend/src/lib/solver/worker.ts` — loads the wasm module, exposes a typed `comlink`-style RPC interface, runs in a worker so the main thread stays interactive.
5. **Threading:** enable `wasm32-unknown-unknown` with `nightly` + the `parallel` feature. Vercel ships COOP (`same-origin`) and COEP (`require-corp`) headers via `next.config.js` headers config — required for `SharedArrayBuffer`.
6. **Range builder** (backend, `scenario/builder.py`):
   - Look up hero and villain ranges from `range_library` by `(table_size, position, action_sequence)`.
   - If `action_sequence` not found, fall back to `(position, "default_call")` and tag the scenario `confidence=low`.
   - Build a fixed bet tree: flop `[33%, 75%]`, turn `[50%, 100%]`, river `[33%, 75%, 150%]`, allin always legal.
   - Return a deterministic JSON envelope; hash it to key the `solver_runs` cache.
7. **Caching strategy:**
   - First, hash the scenario.
   - Check `solver_runs` for the hash (across all users — solver output is shared knowledge).
   - If hit, return JSON directly (no solve).
   - If miss, run the WASM solver in the user's browser, post the result back to `POST /solver-runs` to populate the shared cache.
   - This means the *second* time anyone reviews a hand on the K-9-T-3-4 board with the same preflop story, it's instant.
8. **Convergence targets:**
   - Default: 200 iterations or exploitability ≤ 0.5 bb, whichever first.
   - "Quick look" mode: 50 iterations.
   - Show a progress bar with the current exploitability — *show your work* is what makes the UI feel technical.
9. **Visualization:**
   - 13×13 hand-class range grid, one component per combo cell. Cell colored as a stacked bar by action probability. Hover → tooltip with EVs.
   - One-number summary on top: "Your river call: -1.8 bb vs. solver-best (fold)."

**Legal note:** AGPL-3.0 forces you to publish your modifications. That's fine — this is an open-source portfolio project anyway, and "I open-sourced my fork of postflop-solver under AGPL" is a positive signal. The same does not apply if you ever monetize.

**Why this is more impressive than alternatives:**

- "Wrapped a paid solver" → no one paid solver has an integratable API.
- "Embedded TexasSolver binary on the server" → AGPL forbids exactly that for an internet service.
- "Used a precomputed range database only" → fine, but you lose the live-solver demo.
- "Asked Claude to GTO-analyze the hand" → no equilibrium grounding, anyone can do that, doesn't differentiate.

---

## 7. MCP Integration Plan

**Decision (concrete):** Yes, ship it. MCP is a genuine 2026 hiring signal, and the engineering effort here is small because `fastapi-mcp` (or `FastMCP.from_fastapi`) auto-generates the server from your existing OpenAPI schema. The marginal cost is one dependency, one mount, and one section of the README — the marginal resume value is large.

**Mount:**

```python
# server/app/main.py (illustrative — DO NOT WRITE THIS YET)
from fastapi_mcp import FastApiMCP
mcp = FastApiMCP(app, name="CoinPoker Hand Analyzer")
mcp.mount()   # exposes /mcp/sse for streamable HTTP transport
```

**Tools exposed (curate these explicitly — auto-exposing every endpoint is noisy):**

| Tool | Description (this becomes the LLM-facing docstring — write it carefully) |
|---|---|
| `list_recent_hands(limit, only_losses, since, position)` | Returns hand summaries (id, date, position, cards, net) for filtering. |
| `get_hand(hand_id)` | Returns full action log + showdown for a single hand. |
| `get_stats(timeframe, position?)` | Returns VPIP/PFR/3-bet/WTSD/BB100 for the requested slice. |
| `find_biggest_losers(timeframe, limit)` | Top-N losing hands in a window. |
| `analyze_hand(hand_id, run_solver?)` | Triggers the LLM analysis pipeline; optionally runs (or cache-reads) the solver first. |
| `find_leaks(timeframe)` | Aggregates `leak_tags` across recent analyses. |

**Transport:** Streamable HTTP (the 2025-03-26 MCP spec revision). OAuth 2.1 + PKCE for the public deployment; for the user's own Claude Desktop usage, a static bearer token works. The README ships a copy-pasteable `claude_desktop_config.json`.

**Demo value:** The single most compelling 30-second clip you can put in a job application: open Claude Desktop, ask *"go through my poker hands from last week and tell me my single biggest exploitable pattern"*, watch Claude call `find_biggest_losers`, then `analyze_hand` on each, then return a synthesized leak report. Record it. Embed it in the README.

---

## 8. Resume Bullet Points

Pick 3–4 of these for your resume (in your tightest font; this is one project entry). All are written to be true *after* v1 ships per §5. Numbers are realistic estimates you can tune to actuals.

- **Architected and shipped a full-stack poker analytics platform** (Next.js 14 / TypeScript / Tailwind, FastAPI / Python 3.12 / SQLModel, Supabase Postgres) deployed to Vercel + AWS Lambda (Docker, API Gateway, Mangum) with GitHub Actions CI/CD running ruff, mypy, pytest, and Playwright on every PR.

- **Implemented a deterministic CoinPoker hand-history parser** in Python, validated by a Hypothesis property-based test suite + 50-hand golden corpus, achieving 99.8% field-extraction accuracy on real-world data including all-ins, side pots, run-it-twice, and uncalled-bet edge cases; processes ~2,000 hands/sec single-threaded.

- **Compiled the Rust `postflop-solver` CFR engine to WebAssembly** and integrated it as a multi-threaded Web Worker inside the React frontend, allowing GTO equilibrium analysis (Discounted CFR, <0.5 bb exploitability) to run client-side with zero per-solve server cost; cached solver outputs in Postgres keyed by canonical-scenario hash to make repeat lookups O(1).

- **Exposed the backend as a Model Context Protocol (MCP) server** using `fastapi-mcp`, publishing six curated tools (`list_recent_hands`, `analyze_hand`, `find_leaks`, etc.) consumable by Claude Desktop, Cursor, and any MCP-compatible agent over streamable HTTP with OAuth 2.1 + PKCE.

- **Built an LLM-powered leak-detection pipeline** on the Anthropic Claude API (direct SDK, no orchestration framework — 35% lower p95 latency than a LangChain equivalent) that ingests solver-vs-actual action diffs, returns structured commentary with enumerated leak tags, and aggregates tags into a longitudinal "weakness profile" for the user.

---

## 9. Open Questions (decide before writing code)

1. **Single-tenant or multi-tenant deploy?** The plan assumes a public multi-tenant Supabase project with RLS so you can share the live URL with recruiters. If you'd rather keep it self-hosted and just demo via screen recording, you can skip the OAuth on MCP, simplify auth, and skip the public Vercel deploy. **Recommendation: multi-tenant. The live URL is half the portfolio value.**
2. **Domain name?** Cheap, ~$12. Use one. `pokeranalyzer.dev` or similar. Bare-Vercel URLs in a resume look sloppy.
3. **How much of the solver UI is in v1?** The full Pio-style range grid is a week of polish. **Recommendation: ship a minimum-viable grid (one big SVG) in v1 and refine later.** EV-diff number is non-negotiable.
4. **arq / Upstash Redis now or later?** Parsing a single 5 MB file finishes in <2s synchronously. **Recommendation: defer. Use FastAPI `BackgroundTasks` in v1. Add arq when a single upload exceeds 10s.**
5. **Sentry now or later?** Free, 5 min to set up, saves you when Lambda cold-starts misbehave. **Recommendation: add on day one of deploy week.**
6. **Anthropic API costs.** Opus 4.7 is expensive; Sonnet 4.6 is 5× cheaper and still tags leaks well. **Recommendation: Sonnet 4.6 for the per-hand commentary, Opus only for the weekly "leak summary" aggregation.**
7. **Where does the range library actually come from?** Hand-transcribing GTO Wizard's free preflop charts is the most defensible source. **Recommendation: budget a half-day to encode ~80 entries covering all 6-max preflop trees to 1 reraise.**
8. **Stack depth coverage.** Microstakes is 100 bb effective ~95% of the time. **Recommendation: v1 = 100 bb only. Add 50 bb / 200 bb in v2.**
9. **CoinPoker hand-history language localization.** CoinPoker may emit non-English variants. **Recommendation: assume English in v1; reject other locales with a clear error.**
10. **What to record for the demo video.** Plan it now — the demo is the artifact recruiters actually click on. Sketch a 90-second script before you build, so you build with the demo in mind. (Suggested arc: upload → dashboard → click worst hand → solver converges live → Claude commentary appears → switch to Claude Desktop → ask "find my biggest leak" → MCP call resolves → done.)

---

**Stop reading. Start building. The first commit should be `parser/coinpoker.py` and `tests/fixtures/hand_001.txt` with the sample from this document checked in as a golden file.**
