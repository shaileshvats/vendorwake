"""StructuredLLM over any OpenAI-compatible ``/chat/completions`` endpoint (OpenRouter in production).

Two structured-output modes:

- ``json_schema``: strict ``response_format.json_schema``. On OpenRouter we also send
  ``provider.require_parameters=true`` so requests are only routed to upstreams that honour it.
- ``json_object``: JSON-object mode with the schema described in the system prompt. Used for OpenRouter
  ``:free`` models, which (per OpenRouter's model metadata) support ``response_format`` but not strict
  schemas. ``require_parameters`` is not sent here: it could exclude every free upstream.

Either way the output is validated with Pydantic, and an invalid output gets exactly one repair call that
sends the validation error back. Token counts are whatever the provider reports (never estimated).

Free accounts are rate-limited, so identical requests are served from an in-process cache keyed by
(endpoint, model, mode, prompt, schema) and HTTP 429 is retried with the server's Retry-After before a
typed ``RateLimitedError`` is raised.

Sentry: one manual ``gen_ai.chat`` span per HTTP request, with model, provider and usage attributes taken
from ``sentry_sdk.consts``. Prompt and response text are never attached to spans or logs.
"""

from __future__ import annotations

import copy
import email.utils
import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import sentry_sdk
from pydantic import BaseModel, SecretStr, ValidationError
from sentry_sdk.consts import OP, SPANDATA

from vendorwake.domain.contracts import ModelCallInfo
from vendorwake.llm.errors import LLMOutputError, LLMRequestError, RateLimitedError

logger = logging.getLogger(__name__)

StructuredMode = Literal["json_schema", "json_object", "prompt"]

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ERROR_CHARS = 2000


def strict_json_schema(schema_cls: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON Schema adjusted for strict structured outputs.

    Every object gets ``additionalProperties: false`` and lists all its properties as required; defaults are
    dropped (strict mode forbids optional keys; nullable fields stay nullable via ``anyOf``).
    """
    schema = copy.deepcopy(schema_cls.model_json_schema())

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


def provider_name_for(base_url: str) -> str:
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if host.endswith("openrouter.ai"):
        return "openrouter"
    if "deepinfra" in host:
        return "deepinfra"
    if host == "generativelanguage.googleapis.com":
        return "google_ai_studio"
    if parts.port == 11434:
        return "ollama"
    return host or "openai_compatible"


_THINKING_HEADROOM_TOKENS = 2048
_THOUGHT_BLOCK = re.compile(r"<thought>.*?(</thought>|$)", re.DOTALL)


def _strip_fences(content: str) -> str:
    # Gemma 4 on Google AI Studio emits its reasoning as a leading <thought>...</thought> block (it does not
    # accept a thinking budget there), so drop that before parsing. Thoughts are never stored or logged.
    text = _THOUGHT_BLOCK.sub("", content).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _provider_error(response: httpx.Response) -> str:
    """Short provider error message (OpenAI-style ``{"error": {"message"}}``), truncated."""
    try:
        body = response.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except ValueError:
        message = None
    return (message or response.reason_phrase or "")[:300]


class _ResponseCache:
    """Small thread-safe LRU of validated outputs, shared by all adapters in this process."""

    def __init__(self, max_entries: int = 256) -> None:
        self._data: OrderedDict[str, tuple[str, ModelCallInfo]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries

    def get(self, key: str) -> tuple[str, ModelCallInfo] | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is not None:
                self._data.move_to_end(key)
            return hit

    def put(self, key: str, value: tuple[str, ModelCallInfo]) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


RESPONSE_CACHE = _ResponseCache()


class OpenAICompatibleLLM:
    """``StructuredLLM`` implementation; see the module docstring for behaviour."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: SecretStr | str | None = None,
        structured_mode: StructuredMode | None = None,
        fallback_model: str | None = None,
        timeout_s: float = 120.0,
        max_retries: int = 2,
        max_retry_wait_s: float = 30.0,
        cache: _ResponseCache | None = RESPONSE_CACHE,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.provider = provider_name_for(self.base_url)
        self._explicit_mode = structured_mode
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self._max_retries = max_retries
        self._max_retry_wait_s = max_retry_wait_s
        self._cache = cache
        self._client = client or httpx.Client(timeout=timeout_s)
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> OpenAICompatibleLLM:
        """Build from ``vendorwake.core.config.Settings`` (llm_base_url, llm_api_key, llm_model)."""
        values: dict[str, Any] = {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
        }
        for name in ("llm_structured_mode", "llm_fallback_model"):  # optional settings, if ever added
            if getattr(settings, name, None):
                values[name.removeprefix("llm_")] = getattr(settings, name)
        values.update(overrides)
        return cls(**values)

    def mode_for(self, model: str) -> StructuredMode:
        if self._explicit_mode:
            return self._explicit_mode
        if self.provider == "google_ai_studio":
            # Gemma on AI Studio: no documented structured output, and json_object mode intermittently 500s.
            return "prompt"
        return "json_object" if model.endswith(":free") else "json_schema"

    # ------------------------------------------------------------------ public API

    def generate[T: BaseModel](
        self, *, agent: str, system: str, user: str, schema: type[T], max_tokens: int = 2048
    ) -> tuple[T, ModelCallInfo]:
        try:
            return self._generate(self.model, agent, system, user, schema, max_tokens)
        except LLMRequestError as exc:
            retryable = isinstance(exc, RateLimitedError) or exc.status_code in (
                None,
                404,
                *_RETRYABLE_STATUS,
            )
            if not self.fallback_model or not retryable:
                raise
            logger.warning("model %s failed for agent %s (%s); trying fallback", self.model, agent, exc)
            return self._generate(self.fallback_model, agent, system, user, schema, max_tokens)

    # ------------------------------------------------------------------ internals

    def _generate[T: BaseModel](
        self, model: str, agent: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> tuple[T, ModelCallInfo]:
        mode = self.mode_for(model)
        json_schema = strict_json_schema(schema)
        if mode in ("json_object", "prompt"):
            system = (
                f"{system}\n\nRespond with a single JSON object (no prose, no code fences) that conforms to "
                f"this JSON Schema:\n{json.dumps(json_schema, separators=(',', ':'))}"
            )
        cache_key = hashlib.sha256(
            json.dumps(
                [self.base_url, model, mode, system, user, schema.__name__, json_schema, max_tokens],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if self._cache is not None and (hit := self._cache.get(cache_key)):
            content, info = hit
            # Served from memory: no provider call happened, so no tokens were used and no latency incurred.
            cached_info = info.model_copy(
                update={"agent": agent, "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0}
            )
            return schema.model_validate_json(content), cached_info

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        started = time.monotonic()
        usages: list[dict[str, Any] | None] = []
        served_model = model
        repaired = False
        last_error = ""
        for attempt in range(2):  # first try + exactly one repair
            data = self._chat(model, mode, agent, messages, json_schema, schema.__name__, max_tokens)
            usages.append(data.get("usage"))
            served_model = data.get("model") or model
            content = self._content(data)
            try:
                result = schema.model_validate_json(_strip_fences(content))
            except ValidationError as exc:
                last_error = str(exc)[:_MAX_ERROR_CHARS]
                if attempt == 0:
                    repaired = True
                    messages += [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": "Your previous output failed schema validation:\n"
                            f"{last_error}\nReturn the corrected JSON object only.",
                        },
                    ]
                continue
            info = ModelCallInfo(
                agent=agent,
                provider=self.provider,
                model=served_model,
                input_tokens=self._sum_usage(usages, "prompt_tokens"),
                output_tokens=self._sum_usage(usages, "completion_tokens"),
                latency_s=round(time.monotonic() - started, 3),
                repaired=repaired,
            )
            if self._cache is not None:
                self._cache.put(cache_key, (result.model_dump_json(), info))
            return result, info
        raise LLMOutputError(
            f"{agent}: model output failed {schema.__name__} validation after one repair",
            agent=agent,
            model=served_model,
            validation_error=last_error,
        )

    @staticmethod
    def _sum_usage(usages: list[dict[str, Any] | None], key: str) -> int | None:
        values = [u.get(key) if isinstance(u, dict) else None for u in usages]
        if any(not isinstance(v, int) for v in values):
            return None  # provider did not report it for every call; never estimate
        return sum(values)  # type: ignore[arg-type]

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            content = None
        return content if isinstance(content, str) else ""

    def _chat(
        self,
        model: str,
        mode: StructuredMode,
        agent: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
            }
            if self.provider == "openrouter":
                body["provider"] = {"require_parameters": True}
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        else:
            # mode == "prompt": the schema is in the system prompt; output is validated (and repaired once).
            # The model may think before answering, so leave room for the thought block.
            body["max_tokens"] = max_tokens + _THINKING_HEADROOM_TOKENS
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        with sentry_sdk.start_span(op=OP.GEN_AI_CHAT, name=f"chat {model}") as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
            span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent)
            span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)
            span.set_data(SPANDATA.GEN_AI_SYSTEM, self.provider)
            span.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, self.provider)
            span.set_data(SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)
            span.set_data(SPANDATA.GEN_AI_REQUEST_TEMPERATURE, 0)
            span.set_data("vendorwake.structured_mode", mode)
            try:
                data = self._post_with_retries(model, agent, body, headers)
            except Exception as exc:
                span.set_status("internal_error")
                span.set_data("error.type", type(exc).__name__)
                raise
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if isinstance(usage.get("prompt_tokens"), int):
                span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage["prompt_tokens"])
            if isinstance(usage.get("completion_tokens"), int):
                span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["completion_tokens"])
            if isinstance(usage.get("total_tokens"), int):
                span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["total_tokens"])
            if data.get("model"):
                span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, data["model"])
            if data.get("id"):
                span.set_data(SPANDATA.GEN_AI_RESPONSE_ID, str(data["id"]))
            finish = [c.get("finish_reason") for c in data.get("choices", []) if isinstance(c, dict)]
            if any(finish):
                span.set_data(SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, [f for f in finish if f])
            return data

    def _post_with_retries(
        self, model: str, agent: str, body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                response = self._client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if last:
                    raise LLMRequestError(
                        f"{agent}: request to {self.provider} failed: {type(exc).__name__}",
                        agent=agent,
                        model=model,
                    ) from exc
                self._sleep(min(2.0**attempt, self._max_retry_wait_s))
                continue

            if response.status_code == 429:
                retry_after = _retry_after_seconds(response)
                if last or (retry_after is not None and retry_after > self._max_retry_wait_s):
                    raise RateLimitedError(
                        f"{agent}: rate limited by {self.provider}: {_provider_error(response)}",
                        agent=agent,
                        model=model,
                        retry_after_s=retry_after,
                    )
                self._sleep(retry_after if retry_after is not None else min(2.0 ** (attempt + 1), 30.0))
                continue
            if response.status_code in _RETRYABLE_STATUS and not last:
                self._sleep(min(2.0**attempt, self._max_retry_wait_s))
                continue
            if response.status_code >= 400:
                raise LLMRequestError(
                    f"{agent}: {self.provider} returned HTTP {response.status_code}: "
                    f"{_provider_error(response)}",
                    agent=agent,
                    model=model,
                    status_code=response.status_code,
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMRequestError(
                    f"{agent}: {self.provider} returned a non-JSON body", agent=agent, model=model
                ) from exc
            if not isinstance(data, dict):
                raise LLMRequestError(f"{agent}: unexpected response shape", agent=agent, model=model)
            if isinstance(data.get("error"), dict) and not data.get("choices"):
                # OpenRouter can report upstream failures in a 200 body.
                message = str(data["error"].get("message", ""))[:300]
                code = data["error"].get("code")
                if code == 429:
                    raise RateLimitedError(
                        f"{agent}: rate limited upstream: {message}",
                        agent=agent,
                        model=model,
                        retry_after_s=None,
                    )
                raise LLMRequestError(
                    f"{agent}: provider error: {message}",
                    agent=agent,
                    model=model,
                    status_code=code if isinstance(code, int) else None,
                )
            return data
        raise AssertionError("unreachable")  # pragma: no cover
