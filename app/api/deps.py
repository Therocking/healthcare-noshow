"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Connection

from app.db.session import get_engine


def get_connection() -> Iterator[Connection]:
    """Yield a Core connection for the lifetime of a request.

    Transactions are owned by the service layer (``with conn.begin(): ...``) so
    that write operations are explicit and atomic; read-only handlers simply
    execute against the connection.
    """
    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
    finally:
        conn.close()
