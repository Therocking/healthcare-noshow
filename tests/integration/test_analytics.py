"""Integration tests for the analytics endpoints (brief 4.1 and 4.2)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import SAMPLE_CSV, upload

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(client):
    upload(client, SAMPLE_CSV.read_bytes())
    return client


def test_no_shows_by_quarter(seeded):
    resp = seeded.get("/analytics/no-shows-by-quarter", params={"year": 2024})
    assert resp.status_code == 200
    body = resp.json()
    assert body["year"] == 2024

    rows = body["rows"]
    # Ordered by neighbourhood asc, then gender asc.
    assert [(r["neighbourhood"], r["gender"]) for r in rows] == [
        ("East", "F"),
        ("East", "M"),
        ("West", "F"),
        ("West", "M"),
    ]
    # Keys are emitted as Q1..Q4 to match the brief's example output.
    east_f = rows[0]
    assert (east_f["Q1"], east_f["Q2"], east_f["Q3"], east_f["Q4"]) == (1, 1, 0, 0)
    east_m = rows[1]
    assert (east_m["Q1"], east_m["Q2"], east_m["Q3"], east_m["Q4"]) == (0, 0, 1, 1)
    west_f = rows[2]
    assert (west_f["Q1"], west_f["Q2"], west_f["Q3"], west_f["Q4"]) == (1, 0, 0, 0)


def test_above_average_neighbourhoods(seeded):
    resp = seeded.get(
        "/analytics/above-average-neighbourhoods", params={"year": 2024}
    )
    assert resp.status_code == 200
    body = resp.json()

    # East has 4 no-shows, West has 2 -> mean 3.0; only East is above average.
    assert body["mean_no_shows"] == 3.0
    assert body["rows"] == [{"id": 1, "neighbourhood": "East", "no_shows": 4}]


def test_year_filter_excludes_other_years(seeded):
    resp = seeded.get("/analytics/no-shows-by-quarter", params={"year": 2023})
    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def test_year_defaults_to_configured_value(seeded):
    # No ?year= -> falls back to ANALYSIS_DEFAULT_YEAR (2024).
    resp = seeded.get("/analytics/above-average-neighbourhoods")
    assert resp.status_code == 200
    assert resp.json()["year"] == 2024


def test_health_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/db").json()["database"] == "reachable"
