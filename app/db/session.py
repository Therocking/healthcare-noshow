"""Database engine management.

A single SQLAlchemy ``Engine`` is created lazily and reused for the process
lifetime. We use SQLAlchemy *Core* (not the ORM) throughout the codebase, so
callers work with :class:`~sqlalchemy.engine.Connection` objects directly.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from app.core.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.sqlalchemy_url,
            pool_pre_ping=True,  # transparently recycle stale connections
            future=True,
        )
    return _engine


def dispose_engine() -> None:
    """Dispose of the engine and its connection pool (used on shutdown/tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
