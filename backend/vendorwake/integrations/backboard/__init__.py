"""Backboard follow-up context store (``backboard-sdk``, imported lazily)."""

from __future__ import annotations

from vendorwake.core.config import Settings
from vendorwake.integrations.backboard.store import BackboardContextStore, assistant_name_for
from vendorwake.integrations.errors import NotConfiguredError


def from_settings(settings: Settings, **kwargs: object) -> BackboardContextStore:
    if not settings.backboard_api_key:
        raise NotConfiguredError("Backboard is not configured: set VW_BACKBOARD_API_KEY", service="backboard")
    if not settings.backboard_llm_provider.strip() or not settings.backboard_model.strip():
        raise NotConfiguredError(
            "Backboard needs VW_BACKBOARD_LLM_PROVIDER and VW_BACKBOARD_MODEL (never use its defaults)",
            service="backboard",
        )
    if settings.backboard_generation == "vendorwake" and "answerer" not in kwargs:
        # Free plan: Backboard does memory + retrieval; our open-weight model writes the answer.
        from vendorwake.llm import OpenAICompatibleLLM

        kwargs["answerer"] = OpenAICompatibleLLM.from_settings(settings)
    return BackboardContextStore(
        api_key=settings.backboard_api_key.get_secret_value(),
        llm_provider=settings.backboard_llm_provider.strip(),
        model_name=settings.backboard_model.strip(),
        **kwargs,  # type: ignore[arg-type]
    )


__all__ = ["BackboardContextStore", "assistant_name_for", "from_settings"]
