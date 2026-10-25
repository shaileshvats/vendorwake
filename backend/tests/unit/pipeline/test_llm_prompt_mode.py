"""Prompt mode (Gemma on Google AI Studio): no response_format, schema in the prompt, thoughts stripped."""

import json

import httpx
from pydantic import BaseModel

from vendorwake.llm.openai_compatible import OpenAICompatibleLLM

AI_STUDIO = "https://generativelanguage.googleapis.com/v1beta/openai"


class Rename(BaseModel):
    renamed_from: str
    renamed_to: str


def _llm(handler) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        base_url=AI_STUDIO,
        model="gemma-4-31b-it",
        api_key="test-key",
        cache=None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_ai_studio_uses_prompt_mode_and_strips_thoughts() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        content = (
            "<thought>The user wants a rename... reason reason</thought>\n"
            '{"renamed_from": "stripe.Reversal", "renamed_to": "stripe.TransferReversal"}'
        )
        return httpx.Response(
            200,
            json={
                "model": "gemma-4-31b-it",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 44, "completion_tokens": 140},
            },
        )

    llm = _llm(handler)
    result, info = llm.generate(
        agent="extraction", system="Extract.", user="<source>…</source>", schema=Rename
    )

    assert llm.provider == "google_ai_studio"
    assert llm.mode_for("gemma-4-31b-it") == "prompt"
    assert result == Rename(renamed_from="stripe.Reversal", renamed_to="stripe.TransferReversal")
    assert "response_format" not in seen[0]
    assert "JSON Schema" in seen[0]["messages"][0]["content"]
    assert seen[0]["max_tokens"] > 2048  # thinking headroom
    assert (info.provider, info.input_tokens, info.output_tokens, info.repaired) == (
        "google_ai_studio",
        44,
        140,
        False,
    )


def test_unterminated_thought_is_dropped_and_triggers_repair() -> None:
    replies = iter(
        [
            "<thought>ran out of tokens while thinking",
            '{"renamed_from": "a", "renamed_to": "b"}',
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    result, info = _llm(handler).generate(agent="impact", system="s", user="u", schema=Rename)
    assert result.renamed_to == "b"
    assert info.repaired is True
    assert info.input_tokens is None  # provider reported no usage; never estimated
