"""Pydantic v2 schemas for the batch appointment insert endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppointmentIn(BaseModel):
    """A single appointment row submitted to ``POST /appointments/batch``.

    ``patient_id`` and ``neighbourhood_id`` must reference rows that already
    exist (patients/neighbourhoods are created by the CSV ingest path). The
    service validates referential integrity and reports missing references as
    row-level diagnostics.
    """

    model_config = ConfigDict(extra="forbid")

    appointment_id: int = Field(..., ge=1)
    patient_id: int = Field(..., ge=1)
    neighbourhood_id: int = Field(..., ge=1)
    scheduled_at: datetime
    appointment_at: datetime
    age_at_appointment: int = Field(..., ge=0, le=120)
    sms_received: bool = False
    no_show: bool

    @model_validator(mode="after")
    def _appointment_not_before_scheduled_day(self) -> "AppointmentIn":
        # Mirrors the DB CHECK (appointment_at >= scheduled_at::DATE): the visit
        # cannot land on a calendar day earlier than the day it was booked.
        if self.appointment_at.date() < self.scheduled_at.date():
            raise ValueError(
                "appointment_at must not be on a calendar day before scheduled_at"
            )
        return self


class BatchInsertRequest(BaseModel):
    """Envelope carrying 1..N appointments inserted in a single transaction."""

    model_config = ConfigDict(extra="forbid")

    # Upper bound is also enforced against settings.max_batch_size in the route
    # so the limit stays configurable; 1000 is the brief's hard cap.
    appointments: list[AppointmentIn] = Field(..., min_length=1, max_length=1000)


class RowError(BaseModel):
    """A single rejected row, surfaced to the caller for diagnostics."""

    index: int = Field(..., description="0-based position in the submitted batch")
    appointment_id: int | None = None
    error: str


class BatchInsertResponse(BaseModel):
    inserted: int
    received: int
    errors: list[RowError] = Field(default_factory=list)
