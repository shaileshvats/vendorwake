from __future__ import annotations

from fastapi.testclient import TestClient

from vendorwake import __version__
from vendorwake.api.app import create_app

from .conftest import make_settings


def test_openapi_metadata(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    assert spec["info"] == {"title": "Vendorwake API", "version": __version__}
    assert "/api/health" in spec["paths"] and "/api/me" in spec["paths"]
    assert client.get("/api/docs").status_code == 200


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_unhandled_exception_does_not_leak() -> None:
    app = create_app(make_settings())

    @app.get("/api/_test/boom")
    def boom() -> None:
        raise RuntimeError("database password is hunter2")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/_test/boom")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}
    assert "hunter2" not in response.text


def test_validation_error_envelope_omits_input() -> None:
    app = create_app(make_settings())

    @app.get("/api/_test/items/{item_id}")
    def item(item_id: int) -> dict:
        return {"id": item_id}

    with TestClient(app) as client:
        response = client.get("/api/_test/items/secret-value")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["path", "item_id"]
    assert "secret-value" not in response.text


def test_cors_allows_configured_origin_with_credentials() -> None:
    app = create_app(make_settings(cors_origins="http://localhost:3000, *"))
    with TestClient(app) as client:
        allowed = client.options(
            "/api/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert allowed.headers["access-control-allow-credentials"] == "true"

        other = client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers  # '*' was ignored
