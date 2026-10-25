from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from vendorwake.integrations import backboard
from vendorwake.integrations.backboard import BackboardContextStore, assistant_name_for
from vendorwake.integrations.errors import NotConfiguredError, RateLimitedError, UpstreamError

from ..conftest import make_settings
from .conftest import live_env, live_settings

PROVIDER, MODEL = "openrouter", "google/gemma-4-26b-a4b-it"


class FakeRateLimit(Exception):
    status_code = 429


class FakeClient:
    """Mimics the subset of backboard.BackboardClient (1.5.19) we use."""

    instances: list[FakeClient] = []

    def __init__(self, *, assistants=(), answer: dict | None = None, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.assistants = list(assistants)
        self.answer = answer
        self.fail = fail
        self.closed = False
        FakeClient.instances.append(self)

    async def list_assistants(self, skip: int = 0, limit: int = 100, name: str | None = None):
        self.calls.append(("list_assistants", {"limit": limit, "name": name}))
        return [a for a in self.assistants if a.name == name]

    async def create_assistant(self, name: str, description=None, system_prompt=None, **kw):
        self.calls.append(("create_assistant", {"name": name, "system_prompt": system_prompt}))
        return SimpleNamespace(assistant_id=uuid.UUID(int=7), name=name, created_at=datetime.now(UTC))

    async def create_thread(self, assistant_id):
        self.calls.append(("create_thread", {"assistant_id": assistant_id}))
        return SimpleNamespace(thread_id=uuid.UUID(int=99))

    async def add_message(self, thread_id, content=None, **kwargs):
        self.calls.append(("add_message", {"thread_id": thread_id, "content": content, **kwargs}))
        if self.fail:
            raise self.fail
        if kwargs.get("send_to_llm") == "false":
            return SimpleNamespace(messages=[{"role": "user", "content": content}])
        return SimpleNamespace(messages=[{"role": "user", "content": content}, self.answer])

    async def get_thread(self, thread_id):
        self.calls.append(("get_thread", {"thread_id": thread_id}))
        seed = "Investigation inv-1 summary (from the Vendorwake database):\n\nrefunds.py:4 is affected"
        return SimpleNamespace(thread_id=thread_id, messages=[{"role": "user", "content": seed}])

    async def search_memories(self, assistant_id, query, limit=5):
        self.calls.append(("search_memories", {"assistant_id": assistant_id, "query": query, "limit": limit}))
        return {"memories": [{"id": "m1", "content": "Investigation inv-1: refunds.py:4", "score": 0.8}]}

    async def add_memory(self, assistant_id, content, metadata=None):
        self.calls.append(
            ("add_memory", {"assistant_id": assistant_id, "content": content, "metadata": metadata})
        )
        return {"success": True, "memory_id": "m1"}

    async def aclose(self) -> None:
        self.closed = True


def store(client: FakeClient) -> BackboardContextStore:
    return BackboardContextStore(
        api_key="bb", llm_provider=PROVIDER, model_name=MODEL, client_factory=lambda: client
    )


def test_assistant_name_is_stable_and_hides_user_key() -> None:
    name = assistant_name_for("auth0|user-123")
    assert name == assistant_name_for("auth0|user-123") and name.startswith("vendorwake-user-")
    assert "user-123" not in name and name != assistant_name_for("auth0|user-124")


def test_seed_creates_assistant_thread_and_stores_summary_without_generation() -> None:
    client = FakeClient()
    thread_id = store(client).seed_investigation(
        user_key="auth0|u1", investigation_id="inv-1", summary="stripe 16.0.0 breaks refunds.py:4"
    )
    assert thread_id == str(uuid.UUID(int=99))
    names = [c[0] for c in client.calls]
    assert names == ["list_assistants", "create_assistant", "create_thread", "add_message", "add_memory"]
    message = dict(client.calls)["add_message"]
    assert message["send_to_llm"] == "false" and message["memory"] == "off"
    assert "llm_provider" not in message  # nothing is generated, so no model is involved
    assert "stripe 16.0.0 breaks refunds.py:4" in message["content"]
    assert dict(client.calls)["add_memory"]["metadata"]["investigation_id"] == "inv-1"
    assert client.closed


def test_seed_reuses_oldest_existing_assistant() -> None:
    name = assistant_name_for("auth0|u1")
    old = SimpleNamespace(
        assistant_id=uuid.UUID(int=1), name=name, created_at=datetime.now(UTC) - timedelta(days=1)
    )
    new = SimpleNamespace(assistant_id=uuid.UUID(int=2), name=name, created_at=datetime.now(UTC))
    client = FakeClient(assistants=[new, old])
    store(client).seed_investigation(user_key="auth0|u1", investigation_id="inv-2", summary="s")
    assert "create_assistant" not in [c[0] for c in client.calls]
    assert dict(client.calls)["create_thread"]["assistant_id"] == str(uuid.UUID(int=1))


def test_ask_uses_configured_model_readonly_memory_and_returns_memories() -> None:
    memories = [{"id": "m1", "memory": "Investigation inv-1: stripe 16 breaks refunds", "score": 0.91}]
    answer = {"role": "assistant", "content": "refunds.py line 4", "retrieved_memories": memories,
              "model_provider": PROVIDER, "model_name": MODEL}  # fmt: skip
    client = FakeClient(answer=answer)
    text, used = store(client).ask(user_key="u-1", thread_id="t-1", question="Which file breaks?")
    assert text == "refunds.py line 4" and used == memories
    call = dict(client.calls)["add_message"]
    assert call["llm_provider"] == PROVIDER and call["model_name"] == MODEL
    assert call["memory"] == "Readonly" and call["stream"] is False


def test_ask_warns_when_backboard_serves_another_model(caplog: pytest.LogCaptureFixture) -> None:
    answer = {"role": "assistant", "content": "x", "model_provider": "openai", "model_name": "gpt-4o"}
    store(FakeClient(answer=answer)).ask(user_key="u-1", thread_id="t", question="q")
    assert "different model" in caplog.text


def test_ask_errors_are_typed() -> None:
    with pytest.raises(RateLimitedError):
        store(FakeClient(fail=FakeRateLimit("slow down"))).ask(user_key="u-1", thread_id="t", question="q")
    with pytest.raises(UpstreamError, match="no answer"):
        store(FakeClient(answer={"role": "assistant", "content": None, "status": "FAILED"})).ask(
            user_key="u-1", thread_id="t", question="q"
        )


def test_sync_facade_works_inside_a_running_event_loop() -> None:
    answer = {"role": "assistant", "content": "ok"}

    async def caller() -> str:
        return store(FakeClient(answer=answer)).ask(user_key="u-1", thread_id="t", question="q")[0]

    assert asyncio.run(caller()) == "ok"


def test_calls_match_installed_sdk_signatures() -> None:
    sdk = pytest.importorskip("backboard")
    client_cls = sdk.BackboardClient
    inspect.signature(client_cls.__init__).bind(None, api_key="k", timeout=60)
    inspect.signature(client_cls.list_assistants).bind(None, name="n", limit=200)
    inspect.signature(client_cls.create_assistant).bind(None, name="n", description="d", system_prompt="s")
    inspect.signature(client_cls.create_thread).bind(None, "a")
    inspect.signature(client_cls.add_message).bind(
        None, thread_id="t", content="c", send_to_llm="false", memory="off", metadata={}
    )
    inspect.signature(client_cls.add_message).bind(
        None, thread_id="t", content="c", llm_provider="p", model_name="m", memory="Readonly", stream=False
    )
    inspect.signature(client_cls.add_memory).bind(None, assistant_id="a", content="c", metadata={})
    assert inspect.iscoroutinefunction(client_cls.add_message)


def test_from_settings() -> None:
    with pytest.raises(NotConfiguredError, match="VW_BACKBOARD_API_KEY"):
        backboard.from_settings(make_settings(backboard_api_key=None))
    with pytest.raises(NotConfiguredError, match="MODEL"):
        backboard.from_settings(make_settings(backboard_api_key=SecretStr("k"), backboard_model=" "))
    settings = make_settings(backboard_api_key=SecretStr("k"))
    built = backboard.from_settings(settings)
    assert built.llm_provider == settings.backboard_llm_provider == "openrouter"
    assert built.model_name == settings.backboard_model
    assert built.model_name.startswith("google/gemma-")  # always an open-weight Gemma, never the SDK default


@pytest.mark.live
@live_env("VW_BACKBOARD_API_KEY")
def test_live_seed_and_ask() -> None:
    s = backboard.from_settings(live_settings())
    thread = s.seed_investigation(
        user_key="vendorwake-live-test",
        investigation_id=f"live-{uuid.uuid4().hex[:8]}",
        summary="stripe-python 16.0.0 removed stripe.Reversal; payments/refunds.py line 4 is affected.",
    )
    answer, memories = s.ask(
        user_key="vendorwake-live-test",
        thread_id=thread,
        question="Which file is affected, and by which change?",
    )
    assert "refunds" in answer.lower()
    assert isinstance(memories, list)


def test_free_plan_answers_with_our_model_from_backboard_retrieval() -> None:
    class RecordingLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def generate(self, *, agent, system, user, schema, max_tokens=2048):
            self.prompts.append(user)
            return schema(answer="payments/refunds.py line 4"), None

    client, llm = FakeClient(), RecordingLLM()
    s = BackboardContextStore(
        api_key="bb", llm_provider=PROVIDER, model_name=MODEL, client_factory=lambda: client, answerer=llm
    )
    answer, memories = s.ask(user_key="auth0|u1", thread_id="t-1", question="Which file breaks?")

    assert answer == "payments/refunds.py line 4"
    assert memories == [{"id": "m1", "memory": "Investigation inv-1: refunds.py:4", "score": 0.8}]
    names = [c[0] for c in client.calls]
    assert "add_message" not in names  # no Backboard chat (not covered by the free credit)
    assert names[-2:] == ["get_thread", "search_memories"]
    assert "refunds.py:4 is affected" in llm.prompts[0] and "<context>" in llm.prompts[0]
