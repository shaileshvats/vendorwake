"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from vendorwake.core.config import Settings, get_settings


def get_app_settings(request: Request) -> Settings:
    """The Settings the app was created with (see `create_app`), falling back to the process settings."""
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
