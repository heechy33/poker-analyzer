import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.config import get_settings
from app.database import init_db
from app.routers import hands, stats, uploads

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

handler = Mangum(app, lifespan="off")
