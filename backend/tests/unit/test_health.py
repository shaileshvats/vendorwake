from __future__ import annotations

import asyncio
import builtins
import socket
import sys
import types

import pytest
from fastapi.testclient import TestClient

from vendorwake import __version__
from vendorwake.api.app import create_app
from vendorwake.api.routes import health as health_module

from .conftest import make_settings


@pytest.fixture
def no_db_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `vendorwake.db.session` unimportable, as before the database layer exists."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "vendorwake.db.session" or name.startswith("vendorwake.db"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "vendorwake.db.session", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)


def _fake_db_session(monkeypatch: pytest.MonkeyPatch, result: bool | Exception) -> list[str | None]:
    calls: list[str | None] = []

    def ping(url: str | None = None) -> bool:
        calls.append(url)
        if isinstance(result, Exception):
            raise result
        return result

    module = types.ModuleType("vendorwake.db.session")
    module.ping = ping  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vendorwake.db.session", module)
    return calls


def _get_health(**overrides) -> dict:
    with TestClient(create_app(make_settings(**overrides))) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    return response.json()


def test_health_db_module_missing_is_skipped_and_temporal_down_is_error(no_db_module: None) -> None:
    body = _get_health()
    assert body == {
        "status": "degraded",
        "version": __version__,
        "checks": {"database": "skipped", "temporal": "error"},
    }


def test_health_db_unreachable_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_db_session(monkeypatch, False)
    body = _get_health(database_url="postgresql+psycopg://u:p@db.invalid:5432/x")
    assert body["status"] == "degraded"
    assert body["checks"] == {"database": "error", "temporal": "error"}
    assert calls == ["postgresql+psycopg://u:p@db.invalid:5432/x"]  # uses the app's own settings


def test_health_db_ping_raising_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_db_session(monkeypatch, RuntimeError("boom"))
    assert _get_health()["checks"]["database"] == "error"


def test_health_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_db_session(monkeypatch, True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        body = _get_health(temporal_address=f"127.0.0.1:{port}")
    assert body == {"status": "ok", "version": __version__, "checks": {"database": "ok", "temporal": "ok"}}


def test_health_temporal_blank_address_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_db_session(monkeypatch, True)
    body = _get_health(temporal_address="")
    assert body == {
        "status": "ok",
        "version": __version__,
        "checks": {"database": "ok", "temporal": "skipped"},
    }


@pytest.mark.parametrize("address", ["no-port", "host:", ":7233", "host:abc"])
def test_temporal_malformed_address_is_error(address: str) -> None:
    assert asyncio.run(health_module.check_temporal(make_settings(temporal_address=address))) == "error"


def test_health_is_unauthenticated_even_when_auth_not_configured(no_db_module: None) -> None:
    assert _get_health(auth0_domain="", auth0_audience="")["checks"]["database"] == "skipped"
