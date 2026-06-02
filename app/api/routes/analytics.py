"""Analytics endpoints (brief sections 4.1 and 4.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.api.deps import get_connection
from app.core.config import Settings, get_settings
from app.schemas.analytics import (
    AboveAverageNeighbourhoodsResponse,
    NoShowsByQuarterResponse,
)
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _year(
    year: int | None = Query(
        default=None,
        ge=2000,
        le=2100,
        description="Analysis year; defaults to ANALYSIS_DEFAULT_YEAR.",
    ),
    settings: Settings = Depends(get_settings),
) -> int:
    return year if year is not None else settings.analysis_default_year


@router.get(
    "/no-shows-by-quarter",
    response_model=NoShowsByQuarterResponse,
    response_model_by_alias=True,
    summary="No-shows per neighbourhood and gender, by quarter",
)
def no_shows_by_quarter(
    year: int = Depends(_year),
    conn: Connection = Depends(get_connection),
) -> NoShowsByQuarterResponse:
    return analytics.no_shows_by_quarter(conn, year)


@router.get(
    "/above-average-neighbourhoods",
    response_model=AboveAverageNeighbourhoodsResponse,
    summary="Neighbourhoods with above-average no-shows",
)
def above_average_neighbourhoods(
    year: int = Depends(_year),
    conn: Connection = Depends(get_connection),
) -> AboveAverageNeighbourhoodsResponse:
    return analytics.above_average_neighbourhoods(conn, year)
