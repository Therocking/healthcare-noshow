"""SQLAlchemy *Core* table definitions.

This module is the single source of truth for the relational schema. It is used
by:

* the application services (to build typed, parameterised queries),
* the test suite (``metadata.create_all`` against a disposable database),
* Alembic (``target_metadata``) for migration autogeneration.

The design is intentionally normalised into three tables:

* ``neighbourhoods`` — lookup with a surrogate key, populated during ingest.
* ``patients`` — one row per patient (deduplicated), demographics + conditions.
* ``appointments`` — the fact table that grows over time.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    text,
)

# Predictable constraint/index naming makes migrations and debugging easier.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


neighbourhoods = Table(
    "neighbourhoods",
    metadata,
    Column("neighbourhood_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False, unique=True),
)
Index("idx_neighbourhoods_name", neighbourhoods.c.name)


patients = Table(
    "patients",
    metadata,
    Column("patient_id", BigInteger, primary_key=True, autoincrement=False),
    Column("gender", CHAR(1), nullable=False),
    Column("year_of_birth", SmallInteger, nullable=False),
    Column("scholarship", Boolean, nullable=False, server_default=text("false")),
    Column("hypertension", Boolean, nullable=False, server_default=text("false")),
    Column("diabetes", Boolean, nullable=False, server_default=text("false")),
    Column("alcoholism", Boolean, nullable=False, server_default=text("false")),
    Column("handicap", SmallInteger, nullable=False, server_default=text("0")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    CheckConstraint("gender IN ('M', 'F')", name="gender_valid"),
    CheckConstraint("handicap BETWEEN 0 AND 4", name="handicap_range"),
)


appointments = Table(
    "appointments",
    metadata,
    Column("appointment_id", BigInteger, primary_key=True, autoincrement=False),
    Column(
        "patient_id",
        BigInteger,
        ForeignKey("patients.patient_id"),
        nullable=False,
    ),
    Column(
        "neighbourhood_id",
        Integer,
        ForeignKey("neighbourhoods.neighbourhood_id"),
        nullable=False,
    ),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("appointment_at", DateTime(timezone=True), nullable=False),
    Column("age_at_appointment", SmallInteger, nullable=False),
    Column("sms_received", Boolean, nullable=False, server_default=text("false")),
    Column("no_show", Boolean, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    CheckConstraint("age_at_appointment >= 0", name="age_non_negative"),
    # You cannot schedule an appointment for a day before it was booked.
    CheckConstraint(
        "appointment_at >= scheduled_at::DATE",
        name="after_scheduled",
    ),
)

# Indexes supporting the analytical queries (brief sections 4.1 and 4.2).
Index("idx_appt_appointment_at", appointments.c.appointment_at)
Index("idx_appt_neighbourhood_id", appointments.c.neighbourhood_id)
Index(
    "idx_appt_no_show",
    appointments.c.no_show,
    postgresql_where=text("no_show = true"),
)
Index("idx_appt_patient_id", appointments.c.patient_id)
Index(
    "idx_appt_analytics",
    appointments.c.neighbourhood_id,
    appointments.c.appointment_at,
    appointments.c.no_show,
)
