"""Integration tests for POST /appointments/batch."""

from __future__ import annotations

import pytest

from tests.integration.conftest import SAMPLE_CSV, upload

pytestmark = pytest.mark.integration


def _seed(client):
    """Load the sample so patients and neighbourhoods (East=1) exist."""
    upload(client, SAMPLE_CSV.read_bytes())


def _appt(appointment_id: int, **overrides) -> dict:
    base = {
        "appointment_id": appointment_id,
        "patient_id": 25795,
        "neighbourhood_id": 1,
        "scheduled_at": "2024-07-01T09:00:00Z",
        "appointment_at": "2024-07-05T09:00:00Z",
        "age_at_appointment": 42,
        "sms_received": True,
        "no_show": False,
    }
    base.update(overrides)
    return base


def test_batch_happy_path(client):
    _seed(client)
    resp = client.post(
        "/appointments/batch",
        json={"appointments": [_appt(900001), _appt(900002, no_show=True)]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"inserted": 2, "received": 2, "errors": []}


def test_batch_is_atomic_on_bad_reference(client):
    _seed(client)
    resp = client.post(
        "/appointments/batch",
        json={
            "appointments": [
                _appt(900003),  # valid
                _appt(900004, patient_id=999999),  # missing patient
            ]
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["inserted"] == 0  # nothing written: atomic
    assert len(body["errors"]) == 1
    assert body["errors"][0]["index"] == 1
    assert "patient_id 999999 does not exist" in body["errors"][0]["error"]


def test_batch_detects_in_batch_duplicate(client):
    _seed(client)
    resp = client.post(
        "/appointments/batch",
        json={"appointments": [_appt(900005), _appt(900005)]},
    )
    assert resp.status_code == 422
    assert "duplicated in batch" in resp.json()["errors"][0]["error"]


def test_batch_rejects_appointment_before_scheduled(client):
    _seed(client)
    resp = client.post(
        "/appointments/batch",
        json={
            "appointments": [
                _appt(
                    900006,
                    scheduled_at="2024-07-10T09:00:00Z",
                    appointment_at="2024-07-05T09:00:00Z",
                )
            ]
        },
    )
    # Pydantic model validation rejects before reaching the DB.
    assert resp.status_code == 422


def test_batch_rejects_empty_list(client):
    _seed(client)
    resp = client.post("/appointments/batch", json={"appointments": []})
    assert resp.status_code == 422
