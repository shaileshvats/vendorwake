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
    # Worker role: writes global upstream data (changes, snapshots, embeddings); falls back to database_url
    worker_database_url: SecretStr | None = None
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

    temporal_task_queue: str = "vendorwake"
    temporal_ui_url: str = "http://localhost:8233"  # links from the UI to workflow runs

    # Open-weight model over an OpenAI-compatible endpoint. Default: Gemma 4 on Google AI Studio (free tier).
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gemma-4-31b-it"
    llm_fallback_model: str = "gemma-4-26b-a4b-it"
    # Open-weight embedding model run in-process (fastembed / ONNX); dimension must match the DB column
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embeddings_enabled: bool = True  # false on 512 MB hosts: the ONNX model does not fit next to the stack

    # Sponsor integrations
    serpapi_api_key: SecretStr | None = None
    browserbase_api_key: SecretStr | None = None
    browserbase_project_id: str = ""
    backboard_api_key: SecretStr | None = None
    # "vendorwake": Backboard stores + retrieves memories (covered by free credit) and our Gemma writes the
    # answer; "backboard": Backboard's own chat generates it (needs paid Backboard credits).
    backboard_generation: Literal["vendorwake", "backboard"] = "vendorwake"
    backboard_llm_provider: str = "openrouter"
    backboard_model: str = "google/gemma-4-31b-it"
    github_token: SecretStr | None = None
    # JSON object: {"<agent>": {"client_id": "...", "client_secret": "..."}} for Auth0 M2M agent identities
    auth0_agent_credentials: SecretStr | None = None
    sentry_org_url: str = ""  # e.g. https://<org>.sentry.io, used to link traces

    # Verification only runs repository code for these repos ("owner/name[/subpath]", comma-separated)
    verification_allowed_repos: str = "shaileshvats/vendorwake/fixtures/stripe-billing-demo"
    entire_enabled: bool = True

    @field_validator(
        "sentry_dsn",
        "temporal_api_key",
        "llm_api_key",
        "serpapi_api_key",
        "browserbase_api_key",
        "backboard_api_key",
        "github_token",
        "auth0_agent_credentials",
        "worker_database_url",
        mode="before",
    )
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
