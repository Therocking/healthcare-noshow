"""Application configuration via pydantic-settings.

All settings are read from environment variables (or a local ``.env`` file).
The effective SQLAlchemy URL is derived from the ``POSTGRES_*`` parts unless an
explicit ``DATABASE_URL`` is provided, which always wins.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "healthcare-noshow"
    environment: str = "local"
    log_level: str = "INFO"

    # ---- PostgreSQL ----
    postgres_user: str = "noshow"
    postgres_password: str = "noshow"
    postgres_db: str = "noshow"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Explicit override; takes precedence over the POSTGRES_* parts when set.
    database_url: str | None = None

    # ---- Domain ----
    analysis_default_year: int = 2024
    max_batch_size: int = Field(default=1000, ge=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
