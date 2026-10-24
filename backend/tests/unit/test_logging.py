from __future__ import annotations

import io
import json
import logging

from vendorwake.core.logging import REDACTED, JsonFormatter, RedactionFilter, redact_text

JWT = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhdXRoMHwxMjMifQ.c2lnbmF0dXJlLXNpZ25hdHVyZQ"


def _render(logger_name: str, msg: str, *args, **extra) -> dict:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info(msg, *args, extra=extra or None)
    finally:
        logger.removeHandler(handler)
    return json.loads(stream.getvalue())


def test_bearer_token_is_masked_in_message_and_args() -> None:
    record = _render("vw.test.bearer", "calling upstream with %s", f"Authorization: Bearer {JWT}")
    assert JWT not in json.dumps(record)
    assert record["message"] == f"calling upstream with Authorization: Bearer {REDACTED}"
    assert record["level"] == "INFO" and record["logger"] == "vw.test.bearer"


def test_bare_bearer_and_jwt_are_masked() -> None:
    assert redact_text("got bearer abc.def-ghi_jkl==") == f"got bearer {REDACTED}"
    assert redact_text(f"token in body {JWT} end") == f"token in body {REDACTED} end"


def test_secret_pairs_are_masked() -> None:
    text = redact_text('password=hunter2 client_secret: "s3cr3t" api_key=abc123 access_token=xyz&page=2')
    for secret in ("hunter2", "s3cr3t", "abc123", "xyz"):
        assert secret not in text
    assert "page=2" in text


def test_extra_fields_are_masked_by_key_and_value() -> None:
    record = _render(
        "vw.test.extra",
        "request done",
        api_key="k-123",
        headers={"Authorization": f"Bearer {JWT}", "Accept": "application/json"},
        input_tokens=42,
    )
    assert record["api_key"] == REDACTED
    assert record["headers"] == {"Authorization": REDACTED, "Accept": "application/json"}
    assert record["input_tokens"] == 42  # usage counters are not secrets


def test_exception_text_is_masked() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("vw.test.exc")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        raise ValueError(f"bad header Bearer {JWT}")
    except ValueError:
        logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    record = json.loads(stream.getvalue())
    assert JWT not in record["exception"]
    assert "ValueError" in record["exception"]
