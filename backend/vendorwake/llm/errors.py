"""Typed errors raised by model adapters. Messages never contain prompt text or credentials."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for model-call failures."""

    def __init__(self, message: str, *, agent: str, model: str) -> None:
        super().__init__(message)
        self.agent = agent
        self.model = model


class LLMRequestError(LLMError):
    """The endpoint could not be reached or returned a non-success HTTP status."""

    def __init__(self, message: str, *, agent: str, model: str, status_code: int | None = None) -> None:
        super().__init__(message, agent=agent, model=model)
        self.status_code = status_code


class RateLimitedError(LLMRequestError):
    """HTTP 429 persisted after backing off. ``retry_after_s`` is the server's hint, when it sent one."""

    def __init__(self, message: str, *, agent: str, model: str, retry_after_s: float | None) -> None:
        super().__init__(message, agent=agent, model=model, status_code=429)
        self.retry_after_s = retry_after_s


class LLMOutputError(LLMError):
    """The model's output failed schema validation, even after one repair attempt."""

    def __init__(self, message: str, *, agent: str, model: str, validation_error: str) -> None:
        super().__init__(message, agent=agent, model=model)
        self.validation_error = validation_error
