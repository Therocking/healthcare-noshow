"""Transactional batch insert of appointments.

The brief requires inserting 1..1000 appointment rows "in a single request,
targeting the appointments table". We validate the whole batch up front
(referential integrity + duplicate detection) and surface row-level diagnostics.
The insert is atomic: if any row is invalid, nothing is written.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import appointments, neighbourhoods, patients
from app.schemas.appointment import AppointmentIn, BatchInsertResponse, RowError

logger = logging.getLogger(__name__)


def _validate(conn: Connection, rows: list[AppointmentIn]) -> list[RowError]:
    """Return row-level errors for missing FKs and duplicate appointment ids."""
    patient_ids = {r.patient_id for r in rows}
    neighbourhood_ids = {r.neighbourhood_id for r in rows}
    appointment_ids = [r.appointment_id for r in rows]

    existing_patients = set(
        conn.execute(
            select(patients.c.patient_id).where(patients.c.patient_id.in_(patient_ids))
        ).scalars()
    )
    existing_neighbourhoods = set(
        conn.execute(
            select(neighbourhoods.c.neighbourhood_id).where(
                neighbourhoods.c.neighbourhood_id.in_(neighbourhood_ids)
            )
        ).scalars()
    )
    existing_appointments = set(
        conn.execute(
            select(appointments.c.appointment_id).where(
                appointments.c.appointment_id.in_(appointment_ids)
            )
        ).scalars()
    )

    seen: set[int] = set()
    errors: list[RowError] = []
    for index, row in enumerate(rows):
        reasons: list[str] = []
        if row.patient_id not in existing_patients:
            reasons.append(f"patient_id {row.patient_id} does not exist")
        if row.neighbourhood_id not in existing_neighbourhoods:
            reasons.append(f"neighbourhood_id {row.neighbourhood_id} does not exist")
        if row.appointment_id in existing_appointments:
            reasons.append(f"appointment_id {row.appointment_id} already exists")
        if row.appointment_id in seen:
            reasons.append(f"appointment_id {row.appointment_id} duplicated in batch")
        seen.add(row.appointment_id)

        if reasons:
            errors.append(
                RowError(
                    index=index,
                    appointment_id=row.appointment_id,
                    error="; ".join(reasons),
                )
            )
    return errors


def insert_batch(conn: Connection, rows: list[AppointmentIn]) -> BatchInsertResponse:
    """Validate and insert a batch atomically.

    On any validation error nothing is inserted and the errors are returned with
    ``inserted == 0`` (the route maps that to HTTP 422).
    """
    with conn.begin():
        errors = _validate(conn, rows)
        if errors:
            logger.warning(
                "batch insert rejected",
                extra={"received": len(rows), "errors": len(errors)},
            )
            return BatchInsertResponse(inserted=0, received=len(rows), errors=errors)

        payload = [row.model_dump() for row in rows]
        result = conn.execute(
            pg_insert(appointments)
            .values(payload)
            .returning(appointments.c.appointment_id)
        )
        inserted = len(result.fetchall())

    logger.info("batch insert committed", extra={"inserted": inserted})
    return BatchInsertResponse(inserted=inserted, received=len(rows), errors=[])
