"""Response schemas for the CSV ingest endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestRowError(BaseModel):
    """A rejected source row, surfaced for data-quality diagnostics."""

    line: int = Field(..., description="1-based line number in the CSV (header = 1)")
    error: str


class IngestResponse(BaseModel):
    rows_received: int
    rows_valid: int
    rows_invalid: int
    neighbourhoods_created: int
    patients_inserted: int
    patients_updated: int
    appointments_inserted: int
    appointments_skipped: int = Field(
        ..., description="Valid rows whose appointment_id already existed (idempotent)"
    )
    # Capped sample of row errors so the response stays bounded for large files.
    errors: list[IngestRowError] = Field(default_factory=list)
