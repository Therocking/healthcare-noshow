"""Ingestion endpoints: bulk CSV load and transactional batch insert."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import Connection

from app.api.deps import get_connection
from app.core.config import Settings, get_settings
from app.schemas.appointment import BatchInsertRequest, BatchInsertResponse
from app.schemas.ingest import IngestResponse
from app.services.batch import insert_batch
from app.services.ingest import ingest_csv

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingestion"])


@router.post(
    "/ingest/csv",
    response_model=IngestResponse,
    summary="Bulk-load the historical appointments CSV",
)
def upload_csv(
    file: UploadFile = File(..., description="UTF-8 CSV with a header row"),
    conn: Connection = Depends(get_connection),
) -> IngestResponse:
    filename = file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .csv files are accepted",
        )

    raw = file.file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )

    logger.info("csv upload received", extra={"filename": filename, "bytes": len(raw)})
    return ingest_csv(conn, raw)


@router.post(
    "/appointments/batch",
    response_model=BatchInsertResponse,
    summary="Insert 1..1000 appointments atomically",
)
def batch_insert(
    payload: BatchInsertRequest,
    response: Response,
    conn: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> BatchInsertResponse:
    if len(payload.appointments) > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Batch exceeds max size of {settings.max_batch_size}",
        )

    result = insert_batch(conn, payload.appointments)
    if result.errors:
        # Atomic rejection: nothing was written; report row-level diagnostics.
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return result
