"""Liveness / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from app.api.deps import get_connection

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db", summary="Readiness probe (checks DB connectivity)")
def health_db(conn: Connection = Depends(get_connection)) -> dict[str, str]:
    conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
