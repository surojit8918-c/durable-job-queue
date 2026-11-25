"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Import handlers to register their job types
import app.handlers.partner_api  # noqa: F401
from app.api.routes.jobs import router as jobs_router
from app.api.routes.stats import router as stats_router
from app.core.config import settings
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    logger.info(
        "Starting Durable Job Queue API Server",
        extra={"app_env": settings.APP_ENV, "event": "api_started"},
    )
    yield
    logger.info("Stopping Durable Job Queue API Server", extra={"event": "api_stopped"})


app = FastAPI(
    title="Durable Job Queue API",
    description="A reliable, database-backed background job queue system using PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Include route modules
app.include_router(jobs_router)
app.include_router(stats_router)


@app.get("/health", tags=["Health"], summary="API Health Check")
def health_check() -> dict[str, str]:
    """Health check endpoint for container probes."""
    return {"status": "healthy"}
