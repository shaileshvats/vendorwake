"""ASGI application factory. Run with: ``uvicorn vendorwake.api.app:app``."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vendorwake import __version__
from vendorwake.api.routes import (
    api_router,
    changes,
    investigations,
    recommendations,
    repositories,
    security,
    vendors,
)
from vendorwake.core.config import Settings, get_settings
from vendorwake.core.exceptions import register_exception_handlers
from vendorwake.core.logging import configure_logging
from vendorwake.core.observability import init_sentry

logger = logging.getLogger(__name__)

CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# sentry-trace/baggage let the frontend's Sentry SDK continue traces into the API.
CORS_HEADERS = ["Authorization", "Content-Type", "Accept", "sentry-trace", "baggage"]


def _cors_origins(settings: Settings) -> list[str]:
    origins = settings.cors_origin_list
    if "*" in origins:
        # A wildcard is not valid together with credentials; refuse it rather than reflect any origin.
        logger.warning("ignoring '*' in VW_CORS_ORIGINS: credentialed CORS needs explicit origins")
        origins = [o for o in origins if o != "*"]
    return origins


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)

    app = FastAPI(
        title="Vendorwake API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings),
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")
    for module in (repositories, vendors, changes, investigations, recommendations, security):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()
