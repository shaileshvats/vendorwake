"""Test doubles. FakeLLM lives in tests only; production code never ships canned model output."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from vendorwake.domain.contracts import ModelCallInfo

Responder = dict[str, Any] | Callable[[str, str], dict[str, Any]]


class FakeLLM:
    """Returns scripted outputs per agent and records every call (agent, system, user, schema)."""

    def __init__(self, **by_agent: Responder | list[Responder]) -> None:
        self.by_agent = {k: (v if isinstance(v, list) else [v]) for k, v in by_agent.items()}
        self.calls: list[dict[str, Any]] = []

    def generate[T: BaseModel](
        self, *, agent: str, system: str, user: str, schema: type[T], max_tokens: int = 2048
    ) -> tuple[T, ModelCallInfo]:
        self.calls.append({"agent": agent, "system": system, "user": user, "schema": schema})
        queue = self.by_agent[agent]
        responder = queue.pop(0) if len(queue) > 1 else queue[0]
        data = responder(system, user) if callable(responder) else responder
        info = ModelCallInfo(agent=agent, provider="fake", model="fake-model", latency_s=0.0)
        return schema.model_validate(data), info

    def agents(self) -> list[str]:
        return [c["agent"] for c in self.calls]
