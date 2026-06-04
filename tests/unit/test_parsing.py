"""Unit tests for the database-free CSV parsing/normalisation layer."""

from __future__ import annotations

from app.services.parsing import parse_csv

HEADER = (
    "appointment_id,patient_id,age,gender,neighborhood,scheduled_day,"
    "appointment_day,appointment_hour,sms_received,hypertension,diabetes,"
    "alcoholism,disability,no_show"
)


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


def test_happy_path_maps_all_fields():
    raw = _csv(
        "APT-100000,PAT-25795,42,Female,East,2024-02-10,2024-02-14,8,1,0,0,0,0,1"
    )
    result = parse_csv(raw)

    assert result.rows_received == 1
    assert result.errors == []
    rec = result.records[0]

    assert rec.neighbourhood_name == "East"
    # IDs are stripped of their alpha prefix.
    assert rec.appointment["appointment_id"] == 100000
    assert rec.patient["patient_id"] == 25795
    # Female -> F.
    assert rec.patient["gender"] == "F"
    # year_of_birth derived from appointment year minus age.
    assert rec.patient["year_of_birth"] == 2024 - 42
    # appointment_hour layered onto the bare appointment date.
    assert rec.appointment["appointment_at"].hour == 8
    assert rec.appointment["no_show"] is True
    assert rec.appointment["sms_received"] is True


def test_other_gender_maps_to_o():
    raw = _csv(
        "APT-100008,PAT-700,29,Other,East,2024-02-10,2024-02-14,8,0,0,0,0,0,0"
    )
    result = parse_csv(raw)
    assert result.errors == []
    assert result.records[0].patient["gender"] == "O"


def test_invalid_gender_is_rejected():
    raw = _csv(
        "APT-100009,PAT-701,29,Zz,East,2024-02-10,2024-02-14,8,0,0,0,0,0,0"
    )
    result = parse_csv(raw)
    assert len(result.errors) == 1
    assert "gender" in result.errors[0][1]


def test_accepts_original_kaggle_header_dialect():
    header = (
        "PatientId,AppointmentID,Gender,ScheduledDay,AppointmentDay,Age,"
        "Neighbourhood,Scholarship,Hipertension,Diabetes,Alcoholism,Handcap,"
        "SMS_received,No-show"
    )
    row = "29872499,5642903,F,2024-04-29,2024-04-29,62,CENTRO,0,1,0,0,0,0,Yes"
    raw = (header + "\n" + row + "\n").encode("utf-8")

    result = parse_csv(raw)

    assert result.errors == []
    rec = result.records[0]
    assert rec.patient["patient_id"] == 29872499
    assert rec.appointment["appointment_id"] == 5642903
    assert rec.patient["hypertension"] is True
    assert rec.appointment["no_show"] is True  # 'Yes' -> True


def test_collects_row_errors_without_aborting():
    raw = _csv(
        "APT-1,PAT-1,30,X,East,2024-02-10,2024-02-14,8,1,0,0,0,0,0",  # bad gender
        "APT-2,PAT-2,30,Male,West,2024-02-20,2024-02-10,8,1,0,0,0,0,0",  # appt < sched
        "APT-3,PAT-3,30,Male,East,2024-02-10,2024-02-14,8,1,0,0,0,0,1",  # valid
    )
    result = parse_csv(raw)

    assert result.rows_received == 3
    assert len(result.records) == 1
    assert len(result.errors) == 2
    # Errors carry the file line number (header is line 1).
    assert result.errors[0][0] == 2
    assert "gender" in result.errors[0][1]
    assert "before scheduled" in result.errors[1][1]


def test_missing_required_column_is_reported():
    raw = b"appointment_id,patient_id\nAPT-1,PAT-1\n"
    result = parse_csv(raw)
    assert result.records == []
    assert "missing required columns" in result.errors[0][1]
