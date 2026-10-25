import json

import httpx
import pytest
import sentry_sdk
from pydantic import BaseModel
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.transport import Transport

from vendorwake.llm import LLMOutputError, OpenAICompatibleLLM, RateLimitedError
from vendorwake.llm.openai_compatible import strict_json_schema


class Answer(BaseModel):
    value: int
    note: str | None


def completion(content: str, *, prompt_tokens=11, completion_tokens=5, model="google/gemma-4-31b-it"):
    return {
        "id": "gen-1",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": 16},
    }


def make_llm(handler, **kwargs):
    sleeps: list[float] = []
    llm = OpenAICompatibleLLM(
        base_url=kwargs.pop("base_url", "https://openrouter.ai/api/v1"),
        model=kwargs.pop("model", "google/gemma-4-31b-it"),
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        **kwargs,
    )
    return llm, sleeps


def test_strict_schema_marks_all_properties_required():
    schema = strict_json_schema(Answer)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["value", "note"]


def test_json_schema_mode_on_openrouter_sends_require_parameters():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json=completion('{"value": 3, "note": null}'))

    llm, _ = make_llm(handler)
    result, info = llm.generate(agent="extraction", system="sys", user="usr", schema=Answer)
    body = seen[0]
    assert result.value == 3
    assert body["temperature"] == 0 and body["max_tokens"] == 2048
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    assert (info.provider, info.input_tokens, info.output_tokens, info.repaired) == (
        "openrouter",
        11,
        5,
        False,
    )


def test_free_model_uses_json_object_mode_without_require_parameters():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=completion('```json\n{"value": 1, "note": "x"}\n```'))

    llm, _ = make_llm(handler, model="google/gemma-4-31b-it:free")
    result, _ = llm.generate(agent="a", system="sys", user="usr", schema=Answer)
    body = seen[0]
    assert result.value == 1
    assert body["response_format"] == {"type": "json_object"}
    assert "provider" not in body
    assert '"value"' in body["messages"][0]["content"]  # schema described in the system prompt


def test_non_openrouter_endpoint_omits_provider_block():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=completion('{"value": 1, "note": null}'))

    llm, _ = make_llm(handler, base_url="https://api.deepinfra.com/v1/openai")
    llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert "provider" not in seen[0] and llm.provider == "deepinfra"


def test_one_repair_call_then_success_sums_reported_usage():
    replies = iter(
        [completion('{"value": "nope"}'), completion('{"value": 2, "note": null}', prompt_tokens=40)]
    )
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    llm, _ = make_llm(handler)
    result, info = llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert result.value == 2 and info.repaired is True
    assert info.input_tokens == 51 and info.output_tokens == 10
    assert "failed schema validation" in bodies[1]["messages"][-1]["content"]


def test_invalid_twice_raises_typed_error():
    llm, _ = make_llm(lambda r: httpx.Response(200, json=completion("not json")))
    with pytest.raises(LLMOutputError) as err:
        llm.generate(agent="impact", system="s", user="u", schema=Answer)
    assert err.value.agent == "impact"


def test_missing_usage_is_none_not_estimated():
    data = completion('{"value": 1, "note": null}')
    del data["usage"]
    llm, _ = make_llm(lambda r: httpx.Response(200, json=data))
    _, info = llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert info.input_tokens is None and info.output_tokens is None


def test_429_backs_off_with_retry_after_then_succeeds():
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, json={"error": {"message": "slow down"}}),
            httpx.Response(200, json=completion('{"value": 5, "note": null}')),
        ]
    )
    llm, sleeps = make_llm(lambda r: next(replies))
    result, _ = llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert result.value == 5 and sleeps == [2.0]


def test_persistent_429_raises_rate_limited():
    llm, sleeps = make_llm(lambda r: httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(RateLimitedError) as err:
        llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert err.value.retry_after_s == 1.0 and len(sleeps) == 2


def test_long_retry_after_fails_fast():
    llm, sleeps = make_llm(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(RateLimitedError):
        llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert sleeps == []


def test_rate_limited_primary_falls_back_to_second_model():
    models = []

    def handler(request):
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"].startswith("google/gemma-4-31b"):
            return httpx.Response(429, headers={"Retry-After": "999"})
        return httpx.Response(200, json=completion('{"value": 9, "note": null}', model=body["model"]))

    llm, _ = make_llm(
        handler, model="google/gemma-4-31b-it:free", fallback_model="google/gemma-4-26b-a4b-it:free"
    )
    result, info = llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert result.value == 9 and info.model == "google/gemma-4-26b-a4b-it:free"


def test_identical_requests_are_served_from_cache():
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(200, json=completion('{"value": 4, "note": null}'))

    llm, _ = make_llm(handler)
    llm.generate(agent="a", system="s", user="u", schema=Answer)
    result, info = llm.generate(agent="a", system="s", user="u", schema=Answer)
    assert result.value == 4 and len(count) == 1
    assert (info.input_tokens, info.output_tokens, info.latency_s) == (0, 0, 0.0)


def test_sentry_chat_span_has_gen_ai_attributes_and_no_prompt_text():
    payloads: list[str] = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                payloads.append(item.payload.get_bytes().decode("utf-8", "replace"))

    sentry_sdk.init(dsn="https://public@example.invalid/1", transport=Capture, traces_sample_rate=1.0)
    try:
        llm, _ = make_llm(lambda r: httpx.Response(200, json=completion('{"value": 1, "note": null}')))
        with sentry_sdk.start_transaction(op="test", name="t"):
            llm.generate(agent="extraction", system="SECRET-SYSTEM-PROMPT", user="SECRET-USER", schema=Answer)
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)  # leave no active client for other tests
    wire = "\n".join(payloads)
    assert OP.GEN_AI_CHAT in wire
    for key in (
        SPANDATA.GEN_AI_REQUEST_MODEL,
        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
        SPANDATA.GEN_AI_PROVIDER_NAME,
    ):
        assert key in wire
    assert "SECRET-SYSTEM-PROMPT" not in wire and "SECRET-USER" not in wire
