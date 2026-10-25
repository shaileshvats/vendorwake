"""Free-tier pacing: per-minute token budget and Google's retryDelay on 429."""

import json

import httpx
from pydantic import BaseModel

from vendorwake.llm import openai_compatible as oc
from vendorwake.llm.openai_compatible import OpenAICompatibleLLM, _google_retry_delay, _TokenWindow

AI_STUDIO = "https://generativelanguage.googleapis.com/v1beta/openai"


class Answer(BaseModel):
    answer: str


def test_window_waits_until_the_minute_frees_up() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    window = _TokenWindow(10_000, clock=lambda: now[0])
    assert window.acquire(6_000, sleep) == 0
    now[0] = 20.0
    waited = window.acquire(6_000, sleep)  # 12K > 10K: must wait for the first call to age out
    assert waited == slept[0] == 40.0
    assert window.acquire(10_000_000, sleep) >= 0  # oversized requests are clamped, never deadlock


def test_google_retry_delay_is_parsed_from_the_error_body() -> None:
    body = [{"error": {"code": 429, "details": [{"@type": "RetryInfo", "retryDelay": "37s"}]}}]
    assert _google_retry_delay(httpx.Response(429, json=body)) == 37.0
    assert _google_retry_delay(httpx.Response(429, text="not json")) is None


def test_ai_studio_429_waits_out_the_window_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(oc, "_WINDOWS", {})
    replies = iter(
        [
            httpx.Response(429, json=[{"error": {"code": 429, "details": [{"retryDelay": "42s"}]}}]),
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]}),
        ]
    )
    slept: list[float] = []
    llm = OpenAICompatibleLLM(
        base_url=AI_STUDIO,
        model="gemma-4-31b-it",
        api_key="k",
        cache=None,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: next(replies))),
        sleep=slept.append,
    )
    result, _info = llm.generate(agent="extraction", system="s", user="u", schema=Answer)
    assert result.answer == "ok"
    assert 42.0 in slept  # honoured Google's retryDelay instead of failing fast
    assert llm._tpm == 14_000 and llm._max_retry_wait_s == 65.0
