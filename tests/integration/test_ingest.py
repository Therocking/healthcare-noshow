"""Integration tests for POST /ingest/csv."""

from __future__ import annotations

import pytest

from tests.integration.conftest import SAMPLE_CSV, minimal_csv, upload

pytestmark = pytest.mark.integration


def test_ingest_happy_path(client):
    resp = upload(client, SAMPLE_CSV.read_bytes(), filename="sample_data.csv")
    assert resp.status_code == 200
    body = resp.json()

    assert body["rows_received"] == 8
    assert body["rows_valid"] == 8
    assert body["rows_invalid"] == 0
    assert body["neighbourhoods_created"] == 2  # East, West
    assert body["patients_inserted"] == 5  # five distinct patient ids
    assert body["appointments_inserted"] == 8
    assert body["appointments_skipped"] == 0


def test_ingest_is_idempotent(client):
    raw = SAMPLE_CSV.read_bytes()
    upload(client, raw)
    second = upload(client, raw).json()

    # Nothing new the second time: appointments skipped, patients re-upserted.
    assert second["appointments_inserted"] == 0
    assert second["appointments_skipped"] == 8
    assert second["neighbourhoods_created"] == 0
    assert second["patients_inserted"] == 0
    assert second["patients_updated"] == 5


def test_ingest_reports_dirty_rows(client):
    content = minimal_csv(
        "APT-1,PAT-1,30,Male,East,2024-02-10,2024-02-14,8,1,0,0,0,0,1",  # ok
        "APT-2,PAT-2,30,X,West,2024-02-10,2024-02-14,8,1,0,0,0,0,0",  # bad gender
        "APT-3,PAT-3,30,Male,West,2024-03-10,2024-02-14,8,1,0,0,0,0,0",  # appt<sched
    )
    body = upload(client, content).json()

    assert body["rows_received"] == 3
    assert body["rows_valid"] == 1
    assert body["rows_invalid"] == 2
    assert body["appointments_inserted"] == 1
    assert len(body["errors"]) == 2
    assert {e["line"] for e in body["errors"]} == {3, 4}


def test_ingest_rejects_non_csv(client):
    resp = client.post(
        "/ingest/csv", files={"file": ("data.txt", b"x", "text/plain")}
    )
    assert resp.status_code == 400


def test_ingest_rejects_empty_file(client):
    resp = client.post(
        "/ingest/csv", files={"file": ("data.csv", b"", "text/csv")}
    )
    assert resp.status_code == 400
