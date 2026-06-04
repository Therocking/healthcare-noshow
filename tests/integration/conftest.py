"""Helpers shared by integration tests."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = PROJECT_ROOT / "sample_data.csv"

MINIMAL_HEADER = (
    "appointment_id,patient_id,age,gender,neighborhood,scheduled_day,"
    "appointment_day,appointment_hour,sms_received,hypertension,diabetes,"
    "alcoholism,disability,no_show"
)


def minimal_csv(*rows: str) -> bytes:
    """Build a small CSV (header + rows) as bytes for upload."""
    return ("\n".join([MINIMAL_HEADER, *rows]) + "\n").encode("utf-8")


def upload(client, content: bytes, filename: str = "data.csv"):
    return client.post(
        "/ingest/csv",
        files={"file": (filename, content, "text/csv")},
    )
