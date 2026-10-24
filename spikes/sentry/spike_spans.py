"""M0 spike: which gen_ai span ops/attributes does the installed sentry-sdk define, and what does a
manually instrumented agent run look like on the wire?

No DSN or network needed: a capturing transport records the envelope the SDK would send.
Every op/attribute name used below is read from sentry_sdk.consts, never typed by hand, so the
script fails loudly if the SDK does not define it.
"""

from __future__ import annotations

import json
import pathlib

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.transport import Transport

captured: list[dict] = []


class CapturingTransport(Transport):
    def capture_envelope(self, envelope):  # type: ignore[override]
        for item in envelope.items:
            payload = item.payload.json
            if payload:
                captured.append(payload)


def gen_ai_names() -> dict:
    return {
        "OP": {k: getattr(OP, k) for k in dir(OP) if k.startswith("GEN_AI")},
        "SPANDATA": {k: getattr(SPANDATA, k) for k in dir(SPANDATA) if k.startswith("GEN_AI")},
    }


def main() -> None:
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=CapturingTransport,
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

    model = "gemma4:e4b-it-qat"
    with sentry_sdk.start_transaction(op="vendorwake.investigation", name="investigation demo"):
        with sentry_sdk.start_span(op=OP.GEN_AI_INVOKE_AGENT, name="invoke_agent extraction_agent") as agent:
            agent.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            agent.set_data(SPANDATA.GEN_AI_AGENT_NAME, "extraction_agent")
            agent.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            with sentry_sdk.start_span(op=OP.GEN_AI_CHAT, name=f"chat {model}") as chat:
                chat.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
                chat.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)
                chat.set_data(SPANDATA.GEN_AI_SYSTEM, "ollama")
                # Values a real run takes from the provider's usage block.
                chat.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, 1234)
                chat.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, 321)

            with sentry_sdk.start_span(op=OP.GEN_AI_EXECUTE_TOOL, name="execute_tool fetch_source") as tool:
                tool.set_data(SPANDATA.GEN_AI_TOOL_NAME, "fetch_source")
                try:
                    raise TimeoutError("browserbase session timed out")
                except TimeoutError as exc:
                    tool.set_status(SPANSTATUS.DEADLINE_EXCEEDED)
                    tool.set_data("error.type", type(exc).__name__)
                    sentry_sdk.capture_exception(exc)

    sentry_sdk.flush()

    out = {"sdk_version": sentry_sdk.VERSION, "names": gen_ai_names(), "captured": captured}
    path = pathlib.Path(__file__).with_name("results.json")
    path.write_text(json.dumps(out, indent=2, default=str))

    print("sentry-sdk", sentry_sdk.VERSION)
    print("gen_ai OPs:", json.dumps(out["names"]["OP"], indent=1))
    print("gen_ai SPANDATA count:", len(out["names"]["SPANDATA"]))
    for ev in captured:
        if ev.get("type") == "transaction":
            print("transaction:", ev["transaction"], "spans:")
            for s in ev.get("spans", []):
                print(f"  op={s['op']!r} desc={s.get('description')!r} status={s.get('status')} data_keys={sorted(s.get('data', {}))}")
        elif ev.get("exception"):
            print("error event:", ev["exception"]["values"][0]["type"], "-", ev["exception"]["values"][0]["value"])
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
