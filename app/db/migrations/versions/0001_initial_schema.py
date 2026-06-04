"""initial schema: neighbourhoods, patients, appointments

Revision ID: 0001
Revises:
Create Date: 2026-06-02

Creates the three normalised tables together with the indexes that back the
analytical endpoints (brief sections 4.1 and 4.2). Constraint/index names match
the metadata naming convention in app.db.models.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- neighbourhoods (lookup, populated during ingest) ----
    op.create_table(
        "neighbourhoods",
        sa.Column("neighbourhood_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("neighbourhood_id", name="pk_neighbourhoods"),
        sa.UniqueConstraint("name", name="uq_neighbourhoods_name"),
    )
    op.create_index("idx_neighbourhoods_name", "neighbourhoods", ["name"])

    # ---- patients (one row per patient, deduplicated) ----
    op.create_table(
        "patients",
        sa.Column("patient_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("gender", sa.CHAR(length=1), nullable=False),
        sa.Column("year_of_birth", sa.SmallInteger(), nullable=False),
        sa.Column(
            "scholarship", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "hypertension", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "diabetes", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "alcoholism", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "handicap", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Bare logical names; Alembic prefixes them via the metadata naming
        # convention to ck_patients_gender_valid / ck_patients_handicap_range.
        sa.CheckConstraint("gender IN ('M', 'F')", name="gender_valid"),
        sa.CheckConstraint("handicap BETWEEN 0 AND 4", name="handicap_range"),
        sa.PrimaryKeyConstraint("patient_id", name="pk_patients"),
    )

    # ---- appointments (fact table, grows over time) ----
    op.create_table(
        "appointments",
        sa.Column("appointment_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("patient_id", sa.BigInteger(), nullable=False),
        sa.Column("neighbourhood_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("age_at_appointment", sa.SmallInteger(), nullable=False),
        sa.Column(
            "sms_received", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("no_show", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("age_at_appointment >= 0", name="age_non_negative"),
        sa.CheckConstraint(
            "appointment_at >= scheduled_at::DATE", name="after_scheduled"
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patient_id"],
            name="fk_appointments_patient_id_patients",
        ),
        sa.ForeignKeyConstraint(
            ["neighbourhood_id"],
            ["neighbourhoods.neighbourhood_id"],
            name="fk_appointments_neighbourhood_id_neighbourhoods",
        ),
        sa.PrimaryKeyConstraint("appointment_id", name="pk_appointments"),
    )
    op.create_index("idx_appt_appointment_at", "appointments", ["appointment_at"])
    op.create_index("idx_appt_neighbourhood_id", "appointments", ["neighbourhood_id"])
    op.create_index("idx_appt_patient_id", "appointments", ["patient_id"])
    op.create_index(
        "idx_appt_no_show",
        "appointments",
        ["no_show"],
        postgresql_where=sa.text("no_show = true"),
    )
    op.create_index(
        "idx_appt_analytics",
        "appointments",
        ["neighbourhood_id", "appointment_at", "no_show"],
    )


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("patients")
    op.drop_table("neighbourhoods")
