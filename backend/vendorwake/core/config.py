"""Application settings, read from environment variables (prefix VW_) or a .env file.

Secrets are typed as SecretStr so they never appear in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VW_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"  # comma-separated

    # Database. The app role is subject to row-level security; the migration role owns the schema.
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://vendorwake_app:change-me-app@localhost:5432/vendorwake"
    )
    migration_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://vendorwake_owner:change-me-owner@localhost:5432/vendorwake"
    )

    # Auth0: the API accepts RS256 access tokens issued for this audience.
    auth0_domain: str = ""
    auth0_audience: str = ""

    # Sentry: empty DSN disables it.
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = 1.0

    # Temporal
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: SecretStr | None = None

    @field_validator("sentry_dsn", "temporal_api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `VW_SENTRY_DSN=` in an env file means "not configured", not an empty secret.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth0_issuer(self) -> str:
        return f"https://{self.auth0_domain}/"

    @property
    def auth0_jwks_url(self) -> str:
        return f"https://{self.auth0_domain}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
