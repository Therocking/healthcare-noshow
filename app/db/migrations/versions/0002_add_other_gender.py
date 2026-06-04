"""allow 'O' (Other) as a patient gender

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04

Widens the patients.gender CHECK from ('M','F') to ('M','F','O') so the loader
can record patients whose gender is reported as Other.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Bare name; the metadata naming convention resolves it to
    # ck_patients_gender_valid (matching the create side).
    op.drop_constraint("gender_valid", "patients", type_="check")
    op.create_check_constraint(
        "gender_valid", "patients", "gender IN ('M', 'F', 'O')"
    )


def downgrade() -> None:
    # NOTE: will fail if any rows already use 'O'.
    # Bare name; the metadata naming convention resolves it to
    # ck_patients_gender_valid (matching the create side).
    op.drop_constraint("gender_valid", "patients", type_="check")
    op.create_check_constraint("gender_valid", "patients", "gender IN ('M', 'F')")
