"""Entire session recording (Entire CLI + tools/entire-agent-vendorwake, driven via subprocess)."""

from __future__ import annotations

import shutil
from pathlib import Path

from vendorwake.core.config import Settings
from vendorwake.integrations.entire.recorder import (
    ADAPTER_BINARY,
    DEFAULT_ADAPTER_DIR,
    EntireError,
    EntireSessionRecorder,
    ensure_entire_enabled,
)
from vendorwake.integrations.errors import NotConfiguredError


def from_settings(settings: Settings, worktree: str | Path, **kwargs: object) -> EntireSessionRecorder:
    """Recorder for ``worktree``. Raises NotConfiguredError when ``VW_ENTIRE_ENABLED`` is false or the
    Entire CLI is not installed. Call `ensure_entire_enabled` on the worktree first."""
    if not settings.entire_enabled:
        raise NotConfiguredError("Entire recording is disabled (VW_ENTIRE_ENABLED=false)", service="entire")
    entire_bin = str(kwargs.pop("entire_bin", "entire"))
    if shutil.which(entire_bin) is None:
        raise NotConfiguredError(f"Entire CLI ({entire_bin}) is not on PATH", service="entire")
    return EntireSessionRecorder(worktree, entire_bin=entire_bin, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "ADAPTER_BINARY",
    "DEFAULT_ADAPTER_DIR",
    "EntireError",
    "EntireSessionRecorder",
    "ensure_entire_enabled",
    "from_settings",
]
