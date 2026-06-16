"""
app/main.py
────────────
FastAPI application factory.

Responsibilities:
  - Create and configure the FastAPI app instance
  - Register CORS middleware
  - Mount all API routers under /api/v1
  - Wire lifespan events (startup / shutdown) for connection pools
  - Expose /health endpoint for ECS health checks
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app.api.v1 import (
    alerts,
    auth,
    cycles,
    devices,
    ota,
    status,
    telemetry,
    users,
    waste,
)
from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application-level resources across the full process lifetime.

    Startup:
      - Validate DB connectivity (fail fast rather than on first request)
      - Warm Redis connection pool

    Shutdown:
      - Gracefully close all open connections

    NOTE: MQTT ingestion is intentionally NOT started here. It runs as a
    separate process (app/workers/mqtt_listener.py) so a single subscriber
    owns the broker connection regardless of how many API replicas run.
    """
    from app.db.session import engine

    logger.info("Starting Rawbin API — validating database connectivity…")
    async with engine.connect() as conn:
        await conn.exec_driver_sql("SELECT 1")
    logger.info("Database connection OK.")

    # Warm Redis pool
    redis: aioredis.Redis = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    await redis.ping()
    logger.info("Redis connection OK.")

    yield  # ── application runs here ──────────────────────────────────────

    logger.info("Shutting down — closing connections…")
    await engine.dispose()
    await redis.aclose()
    logger.info("Shutdown complete.")


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Rawbin Companion API",
        description=(
            "Backend for the Rawbin BLE-enabled smart home composter. "
            "The mobile app acts as a BLE-to-HTTPS gateway."
        ),
        version="0.1.0",
        # Disable built-in doc routes — we serve custom ones below
        # so we can pin CDN URLs that work reliably in every environment.
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    API_PREFIX = "/api/v1"

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(devices.router, prefix=API_PREFIX)
    app.include_router(telemetry.router, prefix=API_PREFIX)
    app.include_router(status.router, prefix=API_PREFIX)
    app.include_router(users.router, prefix=API_PREFIX)
    app.include_router(alerts.router, prefix=API_PREFIX)
    app.include_router(ota.router, prefix=API_PREFIX)
    app.include_router(cycles.router, prefix=API_PREFIX)
    app.include_router(waste.router, prefix=API_PREFIX)

    # ── Health check (ECS / ALB target group health probe) ───────────────────
    @app.get("/health", tags=["Infra"], include_in_schema=False)
    async def health_check() -> dict:
        return {"status": "ok", "version": app.version}

    # ── Custom doc routes (pinned CDN URLs — no blank-page surprises) ─────────
    if settings.DEBUG:
        @app.get("/docs", include_in_schema=False)
        async def swagger_ui() -> HTMLResponse:
            return get_swagger_ui_html(
                openapi_url="/openapi.json",
                title="Rawbin Companion API — Swagger UI",
                swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
                swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
                swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
            )

        @app.get("/redoc", include_in_schema=False)
        async def redoc_ui() -> HTMLResponse:
            return get_redoc_html(
                openapi_url="/openapi.json",
                title="Rawbin Companion API — ReDoc",
                redoc_js_url="https://unpkg.com/redoc@latest/bundles/redoc.standalone.js",
                redoc_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
                with_google_fonts=False,
            )

    return app


app = create_app()
