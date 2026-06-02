"""FastAPI application entrypoint.

Wires together configuration, structured logging, the request-id middleware and
the API routers. Run locally with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import analytics, health, ingest
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("application starting", extra={"environment": settings.environment})
    yield
    logger.info("application shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Healthcare No-Show Analytics API",
        version="0.1.0",
        description=(
            "Ingests the denormalised appointments CSV into a normalised "
            "PostgreSQL schema and exposes no-show analytics."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        # Honour an inbound correlation id, otherwise mint one.
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled error",
                extra={"method": request.method, "path": request.url.path},
            )
            response = JSONResponse(
                status_code=500, content={"detail": "Internal Server Error"}
            )
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request handled",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": elapsed_ms,
            },
        )
        return response

    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(analytics.router)
    return app


app = create_app()
