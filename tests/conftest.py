"""Shared pytest fixtures.

Integration tests run against a real PostgreSQL instance (the analytical queries
use PostgreSQL-specific features: ``FILTER``, ``EXTRACT``, partial indexes,
``ON CONFLICT``). Point them at a database with ``TEST_DATABASE_URL``; if none is
reachable, the integration tests are skipped rather than failed.

    docker compose up -d db
    set TEST_DATABASE_URL=postgresql+psycopg2://noshow:noshow@localhost:5432/noshow
    pytest
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from app.db.models import metadata

DEFAULT_TEST_URL = "postgresql+psycopg2://noshow:noshow@localhost:5432/noshow"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_URL)
    eng = create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable at {url}: {exc}")

    # Fresh schema for the test session.
    metadata.drop_all(eng)
    metadata.create_all(eng)
    yield eng
    metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(request: pytest.FixtureRequest) -> None:
    """Truncate all tables before each integration test for isolation."""
    if "engine" not in request.fixturenames:
        return
    eng: Engine = request.getfixturevalue("engine")
    with eng.begin() as conn:
        conn.execute(
            text("TRUNCATE appointments, patients, neighbourhoods RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def client(engine: Engine):
    """A FastAPI TestClient wired to the test engine."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_connection
    from app.main import app

    def _override() -> Iterator[Connection]:
        conn = engine.connect()
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_connection] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
