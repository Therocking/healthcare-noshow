"""CSV parsing and row-level normalisation.

This module is deliberately free of any database dependency so that the mapping
rules can be unit-tested in isolation. It converts the denormalised source CSV
into typed records for the three target tables, collecting per-row errors rather
than aborting on the first dirty row (data-governance: dirty rows are reported,
not silently dropped).

The loader is tolerant of two header dialects:

* the brief's original Kaggle columns (``PatientId``, ``AppointmentID``,
  ``Hipertension``, ``Handcap``, ``No-show`` = 'Yes'/'No', ...), and
* the richer synthetic export actually provided (``patient_id`` = ``PAT-25795``,
  ``appointment_id`` = ``APT-100000``, ``gender`` = 'Female'/'Male',
  ``neighborhood``, ``disability``, ``no_show`` = 0/1, plus many extra columns
  that are ignored).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Map each logical field to the header names we accept (compared case-insensitively
# after stripping spaces/underscores/hyphens).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "appointment_id": ("appointmentid",),
    "patient_id": ("patientid",),
    "gender": ("gender",),
    "age": ("age",),
    "neighbourhood": ("neighbourhood", "neighborhood"),
    "scheduled_day": ("scheduledday", "scheduledat"),
    "appointment_day": ("appointmentday", "appointmentat"),
    "appointment_hour": ("appointmenthour",),
    "scholarship": ("scholarship",),
    "hypertension": ("hypertension", "hipertension"),
    "diabetes": ("diabetes",),
    "alcoholism": ("alcoholism",),
    "handicap": ("handicap", "handcap", "disability"),
    "sms_received": ("smsreceived",),
    "no_show": ("noshow",),
}

_TRUE_TOKENS = {"1", "true", "t", "yes", "y"}
_FALSE_TOKENS = {"0", "false", "f", "no", "n"}


@dataclass
class ParsedRecord:
    """A successfully parsed source row, split into its target-table parts."""

    neighbourhood_name: str
    patient: dict
    appointment: dict


@dataclass
class ParseResult:
    records: list[ParsedRecord] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (line_no, message)
    rows_received: int = 0


def _norm_key(key: str) -> str:
    return re.sub(r"[\s_\-]+", "", key).strip().lower()


def _build_header_map(fieldnames: list[str]) -> dict[str, str]:
    """Resolve logical field -> actual CSV header present in the file."""
    normalized = {_norm_key(name): name for name in fieldnames if name}
    resolved: dict[str, str] = {}
    for logical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                resolved[logical] = normalized[alias]
                break
    return resolved


def _parse_id(raw: str | None, label: str) -> int:
    """Extract the numeric id from values like 'APT-100000' or '100000'."""
    if raw is None:
        raise ValueError(f"{label} is missing")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"{label} has no numeric component: {raw!r}")
    return int(digits)


def _parse_gender(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise ValueError("gender is missing")
    first = raw.strip()[0].upper()
    if first not in ("M", "F"):
        raise ValueError(f"gender must be M/F (or Male/Female): {raw!r}")
    return first


def _parse_bool(raw: str | None, label: str, *, default: bool | None = None) -> bool:
    if raw is None or not raw.strip():
        if default is not None:
            return default
        raise ValueError(f"{label} is missing")
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ValueError(f"{label} is not a recognised boolean: {raw!r}")


def _parse_int(raw: str | None, label: str) -> int:
    if raw is None or not raw.strip():
        raise ValueError(f"{label} is missing")
    try:
        # tolerate values like '3.0'
        return int(float(raw.strip()))
    except ValueError as exc:
        raise ValueError(f"{label} is not an integer: {raw!r}") from exc


def _parse_datetime(raw: str | None, label: str) -> datetime:
    if raw is None or not raw.strip():
        raise ValueError(f"{label} is missing")
    value = raw.strip().replace("Z", "+00:00")
    try:
        dtv = datetime.fromisoformat(value)
    except ValueError:
        # date-only fallback
        try:
            dtv = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{label} is not a valid date/datetime: {raw!r}") from exc
    if dtv.tzinfo is None:
        dtv = dtv.replace(tzinfo=UTC)
    return dtv


def _parse_row(row: dict[str, str], hmap: dict[str, str]) -> ParsedRecord:
    def get(field_name: str) -> str | None:
        header = hmap.get(field_name)
        return row.get(header) if header else None

    appointment_id = _parse_id(get("appointment_id"), "appointment_id")
    patient_id = _parse_id(get("patient_id"), "patient_id")
    gender = _parse_gender(get("gender"))
    age = _parse_int(get("age"), "age")
    if age < 0 or age > 120:
        raise ValueError(f"age out of range [0, 120]: {age}")

    neighbourhood = (get("neighbourhood") or "").strip()
    if not neighbourhood:
        raise ValueError("neighbourhood is missing")

    scheduled_at = _parse_datetime(get("scheduled_day"), "scheduled_day")
    appointment_at = _parse_datetime(get("appointment_day"), "appointment_day")

    # If the appointment time came in as a bare date, layer on appointment_hour.
    hour_raw = get("appointment_hour")
    if hour_raw and hour_raw.strip() and appointment_at.hour == 0:
        hour = _parse_int(hour_raw, "appointment_hour")
        if 0 <= hour <= 23:
            appointment_at = appointment_at.replace(hour=hour)

    if appointment_at.date() < scheduled_at.date():
        raise ValueError("appointment_day is before scheduled_day")

    handicap = _parse_int(get("handicap"), "handicap") if get("handicap") else 0
    if handicap < 0 or handicap > 4:
        raise ValueError(f"handicap out of range [0, 4]: {handicap}")

    patient = {
        "patient_id": patient_id,
        "gender": gender,
        "year_of_birth": appointment_at.year - age,
        "scholarship": _parse_bool(get("scholarship"), "scholarship", default=False),
        "hypertension": _parse_bool(get("hypertension"), "hypertension", default=False),
        "diabetes": _parse_bool(get("diabetes"), "diabetes", default=False),
        "alcoholism": _parse_bool(get("alcoholism"), "alcoholism", default=False),
        "handicap": handicap,
    }

    appointment = {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "scheduled_at": scheduled_at,
        "appointment_at": appointment_at,
        "age_at_appointment": age,
        "sms_received": _parse_bool(get("sms_received"), "sms_received", default=False),
        "no_show": _parse_bool(get("no_show"), "no_show"),
    }

    return ParsedRecord(
        neighbourhood_name=neighbourhood, patient=patient, appointment=appointment
    )


def parse_csv(raw: bytes) -> ParseResult:
    """Parse raw CSV bytes into typed records, accumulating per-row errors."""
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    result = ParseResult()
    if not reader.fieldnames:
        result.errors.append((1, "CSV has no header row"))
        return result

    hmap = _build_header_map(list(reader.fieldnames))
    required = ("appointment_id", "patient_id", "gender", "age", "neighbourhood",
                "scheduled_day", "appointment_day", "no_show")
    missing = [f for f in required if f not in hmap]
    if missing:
        result.errors.append((1, f"CSV is missing required columns: {', '.join(missing)}"))
        return result

    # DictReader consumes the header as line 1; data rows start at line 2.
    for line_no, row in enumerate(reader, start=2):
        result.rows_received += 1
        try:
            result.records.append(_parse_row(row, hmap))
        except ValueError as exc:
            result.errors.append((line_no, str(exc)))

    return result
