# CoinPoker Hand History Analyzer

A full-stack portfolio project for analyzing CoinPoker `.txt` hand history files. Upload hand histories, view a stats dashboard (VPIP, PFR, 3-bet%, BB/100), and review individual hands with browser-based GTO solving (WASM) and Claude-powered coaching.

## Stack

- **Frontend:** Next.js 14, TypeScript, Tailwind CSS, shadcn/ui
- **Backend:** FastAPI, SQLModel, Supabase
- **Solver:** Rust → WebAssembly (`solver-wasm/`, built from `postflop-solver/`)
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
# Clone and install
git clone https://github.com/heechy33/poker-analyzer.git
cd poker-analyzer
cp .env.example .env   # fill in Supabase + Anthropic keys

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
