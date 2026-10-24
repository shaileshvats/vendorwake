from vendorwake.core.config import Settings


def test_blank_optional_secrets_are_unset(monkeypatch):
    monkeypatch.setenv("VW_SENTRY_DSN", "")
    monkeypatch.setenv("VW_TEMPORAL_API_KEY", "   ")
    settings = Settings(_env_file=None)
    assert settings.sentry_dsn is None
    assert settings.temporal_api_key is None


def test_sentry_dsn_is_kept_secret(monkeypatch):
    monkeypatch.setenv("VW_SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")
    settings = Settings(_env_file=None)
    assert settings.sentry_dsn is not None
    assert "key@" not in repr(settings)
    assert settings.sentry_dsn.get_secret_value().startswith("https://")


def test_cors_origins_are_comma_separated(monkeypatch):
    monkeypatch.setenv("VW_CORS_ORIGINS", "http://localhost:3000, https://app.example.test")
    settings = Settings(_env_file=None)
    assert settings.cors_origin_list == ["http://localhost:3000", "https://app.example.test"]
