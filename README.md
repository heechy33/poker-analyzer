# CoinPoker Hand History Analyzer

A full-stack portfolio project for analyzing CoinPoker `.txt` hand history files. Upload hand histories, view a stats dashboard (VPIP, PFR, 3-bet%, BB/100), and review individual hands with browser-based GTO solving (WASM) and Claude-powered coaching.

## Stack

- **Frontend:** Next.js 14, TypeScript, Tailwind CSS, shadcn/ui
- **Backend:** FastAPI, SQLModel, Supabase
- **Solver:** Rust → WebAssembly (`solver-wasm/`, built from `postflop-solver/`; see [solver-wasm/README.md](./solver-wasm/README.md))
- **Hosting:** Vercel (frontend), AWS Lambda container (backend)
- **Auth:** Supabase magic link
- **LLM:** Anthropic Claude

## Monorepo layout

```
poker-analyzer/
├── frontend/         Next.js app
├── backend/          FastAPI API
├── solver-wasm/      WASM build output (future)
├── postflop-solver/  Rust CFR solver crate
├── docker-compose.yml
└── .env.example
```

---

## MCP (Model Context Protocol)

The backend is simultaneously a REST API **and** an MCP server, enabling any
MCP-aware LLM agent (Claude Desktop, Cursor, custom agents) to query the
user's poker data with structured tools.

### Server URL

| Environment | URL |
|---|---|
| Local dev | `http://localhost:8000/mcp` |
| Production | `https://api.poker-analyzer.app/mcp` |

### Authentication

MCP requests use the same Supabase JWT as the REST API.  Pass it as a bearer
token in every request header:

```
Authorization: Bearer <your-supabase-jwt>
```

To obtain a JWT: log in via the frontend, open browser dev-tools → Application
→ Local Storage → `supabase.auth.token` → `access_token`.

### Claude Desktop / Cursor configuration

Add the following block to your `claude_desktop_config.json`
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "poker-analyzer": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_JWT"
      }
    }
  }
}
```

Replace `YOUR_JWT` with the access token from your Supabase session.  For the
production deployment, swap the URL for the production endpoint.

### Exposed tools (exactly 6)

| Tool | Backing endpoint | Purpose |
|---|---|---|
| `list_recent_hands` | `GET /hands` | Paginated, filterable hand summaries |
| `get_hand` | `GET /hands/{hand_id}` | Full action log for one hand |
| `find_biggest_losers` | `GET /hands/losers` | Top-N losing hands, pre-sorted |
| `get_stats` | `GET /stats/summary` | VPIP / PFR / 3-bet / BB/100 aggregate |
| `analyze_hand` | `POST /hands/{hand_id}/analyze` | Claude commentary + leak tags (use `stream=false`) |
| `find_leaks` | `GET /stats/leaks` | Aggregated leak tag frequency table |

The following endpoints are **intentionally excluded** from the MCP manifest
to avoid noise: `/health`, `/uploads/*`, `/hands/{id}/scenario`, `/solver/*`,
`/hands/{id}/analyses`, `/stats/by-position`.

### Example agent session

> "Go through my poker hands from last week and tell me my single biggest exploitable pattern."

1. Agent calls `find_leaks(timeframe="7d")` → gets ranked leak tags.
2. Agent calls `find_biggest_losers(since="2026-05-17")` → gets the 10 worst hands.
3. Agent calls `analyze_hand(hand_id="...", stream=false)` on each → reads leak tags.
4. Agent synthesizes a coherent weakness report.

### Future: OAuth 2.1 + PKCE

v1 uses static bearer tokens.  A future release will add OAuth 2.1 + PKCE
(the full MCP auth spec) for a first-class public-facing deployment without
requiring users to paste JWTs into config files.

---

## Running locally

```bash
# Clone and install (postflop-solver is a pinned git submodule)
git clone --recurse-submodules https://github.com/heechy33/poker-analyzer.git
cd poker-analyzer
# If you already cloned without submodules:
#   git submodule update --init --recursive
cp .env.example .env   # fill in Supabase + Anthropic keys

On Windows (or any IPv4-only network), set `DATABASE_URL` to the **Session pooler**
connection string from the Supabase dashboard (Connect → Session pooler, port 5432),
with the async driver prefix `postgresql+asyncpg://`. The direct `db.*.supabase.co`
host is IPv6-only and often times out locally.

# Backend
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Backend runs on `http://localhost:8000`, MCP server at `http://localhost:8000/mcp`.

### Supabase Storage (hand history uploads)

1. **API keys** — In `backend/.env`, set `SUPABASE_SERVICE_ROLE_KEY` to the **service_role** secret from Project Settings → API. Do not paste the `anon` / publishable key there; uploads will fail with an RLS error.
2. **Bucket** — In Storage, create a **private** bucket named `hand-histories` (matches `SUPABASE_STORAGE_BUCKET`).
3. **Policies** — Run `backend/migrations/007_storage_policies.sql` in the Supabase SQL Editor.
4. Restart the backend after changing `.env`.

For local dev without Storage, open `/upload` and enable **Send raw text to API** (development only).

### Building the WASM solver

```bash
# One-time toolchain setup
rustup target add wasm32-unknown-unknown
cargo install wasm-pack

# From the repo root
cd frontend
npm run build:wasm
```

Artifacts land in `frontend/public/wasm/`. See
[`solver-wasm/README.md`](./solver-wasm/README.md) for the JS API, the
strategy-export JSON shape, and known limitations.

### Hand review UX and solver limitations

The `/hands` page uses an integrated split review: the left pane lists hand
cards grouped by date, while the right pane shows players, street-by-street
action, embedded postflop Action Overview grids, an advanced range map, and
results. Opening a postflop hand starts quick solver runs in the background
for each available street; cached solver output is reused immediately.

The solver is postflop-only and built around a heads-up scenario envelope. For
multiway hands the app selects a primary villain and marks ranges as low
confidence; it does not run true multiway CFR. Preflop hands show the action
timeline without a solver overview. If `frontend/public/wasm/` is missing, the
UI will show a "Solver not built" error and prompt you to run
`cd frontend && npm run build:wasm`.
