"""CSV ingest loader: dedup + bulk upsert into the normalised schema.

The whole load runs inside a single transaction so a failure leaves the database
untouched. Deduplication is delegated to PostgreSQL via ``INSERT ... ON CONFLICT``:

* neighbourhoods — ``ON CONFLICT (name) DO NOTHING`` (idempotent lookup fill).
* patients       — ``ON CONFLICT (patient_id) DO UPDATE`` (latest attributes win).
* appointments   — ``ON CONFLICT (appointment_id) DO NOTHING`` (idempotent reload).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import islice
from typing import Iterable, Iterator, Sequence

from sqlalchemy import Connection, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import appointments, neighbourhoods, patients
from app.schemas.ingest import IngestResponse, IngestRowError
from app.services.parsing import ParsedRecord, parse_csv

logger = logging.getLogger(__name__)

# Keep multi-row INSERTs well under PostgreSQL's 65535 bind-parameter cap.
_CHUNK = 1000
# Cap on how many row errors we echo back in the HTTP response.
_MAX_ERRORS_RETURNED = 100


def _chunked(seq: Sequence[dict], size: int) -> Iterator[list[dict]]:
    it = iter(seq)
    while batch := list(islice(it, size)):
        yield batch


def _upsert_neighbourhoods(conn: Connection, names: Iterable[str]) -> tuple[dict[str, int], int]:
    """Insert any new neighbourhood names; return {name: id} and #created."""
    distinct = sorted({n for n in names})
    if not distinct:
        return {}, 0

    created = 0
    for chunk in _chunked([{"name": n} for n in distinct], _CHUNK):
        stmt = (
            pg_insert(neighbourhoods)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(neighbourhoods.c.neighbourhood_id)
        )
        created += len(conn.execute(stmt).fetchall())

    rows = conn.execute(
        select(neighbourhoods.c.name, neighbourhoods.c.neighbourhood_id).where(
            neighbourhoods.c.name.in_(distinct)
        )
    ).all()
    return {name: nid for name, nid in rows}, created


def _upsert_patients(conn: Connection, rows: list[dict]) -> tuple[int, int]:
    """Upsert patients (last row per id wins); return (#inserted, #updated)."""
    # Deduplicate within the file: the last occurrence of each patient wins.
    deduped: dict[int, dict] = {r["patient_id"]: r for r in rows}
    if not deduped:
        return 0, 0

    now = datetime.now(timezone.utc)
    inserted = 0
    total = 0
    for chunk in _chunked(list(deduped.values()), _CHUNK):
        stmt = pg_insert(patients).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["patient_id"],
            set_={
                "gender": stmt.excluded.gender,
                "year_of_birth": stmt.excluded.year_of_birth,
                "scholarship": stmt.excluded.scholarship,
                "hypertension": stmt.excluded.hypertension,
                "diabetes": stmt.excluded.diabetes,
                "alcoholism": stmt.excluded.alcoholism,
                "handicap": stmt.excluded.handicap,
                "updated_at": now,
            },
        # xmax = 0 on a freshly inserted row; non-zero when it was updated.
        ).returning(text("(xmax = 0) AS inserted"))
        for (was_inserted,) in conn.execute(stmt).all():
            total += 1
            inserted += 1 if was_inserted else 0
    return inserted, total - inserted


def _insert_appointments(conn: Connection, rows: list[dict]) -> tuple[int, int]:
    """Insert appointments; return (#inserted, #skipped_as_duplicate)."""
    if not rows:
        return 0, 0
    # Deduplicate within the file by appointment_id (first occurrence wins).
    deduped: dict[int, dict] = {}
    for r in rows:
        deduped.setdefault(r["appointment_id"], r)

    inserted = 0
    for chunk in _chunked(list(deduped.values()), _CHUNK):
        stmt = (
            pg_insert(appointments)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["appointment_id"])
            .returning(appointments.c.appointment_id)
        )
        inserted += len(conn.execute(stmt).fetchall())
    return inserted, len(deduped) - inserted


def ingest_csv(conn: Connection, raw: bytes) -> IngestResponse:
    """Parse and load a CSV upload; return a per-load diagnostic summary."""
    parsed = parse_csv(raw)
    records: list[ParsedRecord] = parsed.records

    with conn.begin():
        name_to_id, neighbourhoods_created = _upsert_neighbourhoods(
            conn, (r.neighbourhood_name for r in records)
        )

        patient_rows = [r.patient for r in records]
        patients_inserted, patients_updated = _upsert_patients(conn, patient_rows)

        appointment_rows: list[dict] = []
        for r in records:
            appt = dict(r.appointment)
            appt["neighbourhood_id"] = name_to_id[r.neighbourhood_name]
            appointment_rows.append(appt)

        appointments_inserted, appointments_skipped = _insert_appointments(
            conn, appointment_rows
        )

    logger.info(
        "csv ingest complete",
        extra={
            "rows_received": parsed.rows_received,
            "rows_valid": len(records),
            "rows_invalid": len(parsed.errors),
            "appointments_inserted": appointments_inserted,
        },
    )

    return IngestResponse(
        rows_received=parsed.rows_received,
        rows_valid=len(records),
        rows_invalid=len(parsed.errors),
        neighbourhoods_created=neighbourhoods_created,
        patients_inserted=patients_inserted,
        patients_updated=patients_updated,
        appointments_inserted=appointments_inserted,
        appointments_skipped=appointments_skipped,
        errors=[
            IngestRowError(line=line, error=msg)
            for line, msg in parsed.errors[:_MAX_ERRORS_RETURNED]
        ],
    )
