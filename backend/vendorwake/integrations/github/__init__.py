"""GitHub repository reader (REST API over httpx; no SDK)."""

from __future__ import annotations

from vendorwake.core.config import Settings
from vendorwake.integrations.github.reader import GitHubRepoReader, is_probably_text_path


def from_settings(settings: Settings, **kwargs: object) -> GitHubRepoReader:
    """A reader using ``VW_GITHUB_TOKEN`` when set. Without a token only public repos are readable
    (and GitHub's anonymous limit of 60 requests/hour applies), so this never raises NotConfiguredError."""
    token = settings.github_token.get_secret_value() if settings.github_token else None
    return GitHubRepoReader(token=token, **kwargs)  # type: ignore[arg-type]


__all__ = ["GitHubRepoReader", "from_settings", "is_probably_text_path"]
