import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
from mangum import Mangum

from app.config import get_settings
from app.database import init_db
from app.routers import hands, solver, stats, uploads

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting up")
    try:
        await init_db()
    except Exception:
        logger.exception("database initialization failed; API will run in degraded mode")
    yield
    logger.info("shutting down")


settings = get_settings()

app = FastAPI(title="CoinPoker Analyzer", lifespan=lifespan)

if settings.ENVIRONMENT == "development":
    cors_origins = ["*"]
else:
    cors_origins = [
        "https://poker-analyzer.vercel.app",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.ENVIRONMENT}


app.include_router(hands.router)
app.include_router(stats.router)
app.include_router(uploads.router)
app.include_router(solver.router)

# ---------------------------------------------------------------------------
# MCP server — curated to exactly 6 tools.
#
# Only the operations listed in ``include_operations`` are exposed to LLM
# agents.  This intentionally excludes noisy or unsafe endpoints such as
# /health, /uploads/presign, /hands/{id}/scenario, /solver/*, and
# /hands/{id}/analyses.
#
# Auth: MCP requests must include the same Supabase JWT used by the REST API:
#   Authorization: Bearer <supabase_jwt>
#
# Tool → operation_id mapping
# ---------------------------
# list_recent_hands  GET /hands
# get_hand           GET /hands/{hand_id}
# find_biggest_losers GET /hands/losers
# get_stats          GET /stats/summary
# analyze_hand       POST /hands/{hand_id}/analyze
# find_leaks         GET /stats/leaks
# ---------------------------------------------------------------------------

_MCP_TOOLS = [
    "list_recent_hands",
    "get_hand",
    "find_biggest_losers",
    "get_stats",
    "analyze_hand",
    "find_leaks",
]

mcp = FastApiMCP(
    app,
    name="CoinPoker Hand Analyzer",
    description=(
        "Query and analyze a user's CoinPoker hand history. "
        "All tools require a valid Supabase JWT in the Authorization header. "
        "Start with find_leaks or find_biggest_losers for a high-level view, "
        "then drill into specific hands with get_hand and analyze_hand."
    ),
    include_operations=_MCP_TOOLS,
)
mcp.mount_http()

handler = Mangum(app, lifespan="off")
