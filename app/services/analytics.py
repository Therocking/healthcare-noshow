"""Analytical queries for the no-show endpoints (brief sections 4.1 and 4.2).

Both queries are authored as parameterised SQL (``:year`` bind parameter) — never
string-formatted — per SonarSource RSPEC-2077. They lean on the composite index
``idx_appt_analytics(neighbourhood_id, appointment_at, no_show)`` and the partial
index ``idx_appt_no_show``.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from app.schemas.analytics import (
    AboveAverageNeighbourhoodRow,
    AboveAverageNeighbourhoodsResponse,
    NoShowsByQuarterResponse,
    NoShowsByQuarterRow,
)

# 4.1 — no-shows per neighbourhood + gender, pivoted by quarter of appointment_at.
_NO_SHOWS_BY_QUARTER = text(
    """
    SELECT
        n.name AS neighbourhood,
        p.gender AS gender,
        COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM a.appointment_at) = 1) AS q1,
        COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM a.appointment_at) = 2) AS q2,
        COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM a.appointment_at) = 3) AS q3,
        COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM a.appointment_at) = 4) AS q4
    FROM appointments a
    JOIN patients p ON p.patient_id = a.patient_id
    JOIN neighbourhoods n ON n.neighbourhood_id = a.neighbourhood_id
    WHERE a.no_show = TRUE
      AND EXTRACT(YEAR FROM a.appointment_at) = :year
    GROUP BY n.name, p.gender
    ORDER BY n.name ASC, p.gender ASC
    """
)

# 4.2 — neighbourhoods with above-average no-show counts for the year.
_ABOVE_AVERAGE = text(
    """
    WITH no_shows_per_neighbourhood AS (
        SELECT
            n.neighbourhood_id AS id,
            n.name AS neighbourhood,
            COUNT(*) AS no_shows
        FROM appointments a
        JOIN neighbourhoods n ON n.neighbourhood_id = a.neighbourhood_id
        WHERE a.no_show = TRUE
          AND EXTRACT(YEAR FROM a.appointment_at) = :year
        GROUP BY n.neighbourhood_id, n.name
    ),
    global_avg AS (
        SELECT AVG(no_shows) AS mean_no_shows FROM no_shows_per_neighbourhood
    )
    SELECT ns.id, ns.neighbourhood, ns.no_shows, ga.mean_no_shows
    FROM no_shows_per_neighbourhood ns
    CROSS JOIN global_avg ga
    WHERE ns.no_shows > ga.mean_no_shows
    ORDER BY ns.no_shows DESC
    """
)


def no_shows_by_quarter(conn: Connection, year: int) -> NoShowsByQuarterResponse:
    rows = conn.execute(_NO_SHOWS_BY_QUARTER, {"year": year}).mappings().all()
    return NoShowsByQuarterResponse(
        year=year,
        rows=[
            NoShowsByQuarterRow(
                neighbourhood=r["neighbourhood"],
                gender=r["gender"],
                q1=r["q1"],
                q2=r["q2"],
                q3=r["q3"],
                q4=r["q4"],
            )
            for r in rows
        ],
    )


def above_average_neighbourhoods(
    conn: Connection, year: int
) -> AboveAverageNeighbourhoodsResponse:
    rows = conn.execute(_ABOVE_AVERAGE, {"year": year}).mappings().all()
    mean = float(rows[0]["mean_no_shows"]) if rows else 0.0
    return AboveAverageNeighbourhoodsResponse(
        year=year,
        mean_no_shows=round(mean, 4),
        rows=[
            AboveAverageNeighbourhoodRow(
                id=r["id"], neighbourhood=r["neighbourhood"], no_shows=r["no_shows"]
            )
            for r in rows
        ],
    )
