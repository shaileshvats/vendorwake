"""Structured JSON logging with secret redaction.

`configure_logging()` installs one JSON handler on the root logger and routes uvicorn's loggers
through it, so every log line (application, uvicorn access/error) is a single JSON object with
credentials masked by `RedactionFilter`.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

# Keys whose values are always masked when they appear in structured data (dicts, `extra=`).
# Matches e.g. "password", "client_secret", "x-api-key", "access_token", "set-cookie"; not "input_tokens".
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_\-.])(?:authorization|cookie|password|passwd|secret|api[_-]?key|apikey|token|credentials?)$",
    re.IGNORECASE,
)

_AUTH_HEADER_RE = re.compile(
    r"""(?P<key>\b(?:proxy-)?authorization["']?\s*[:=]\s*["']?)"""
    r"""(?:(?P<scheme>bearer|basic|token|digest)\s+)?[^\s"',;}]+""",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\b(?P<scheme>bearer)\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_SECRET_PAIR_RE = re.compile(
    r"""(?P<key>["']?\b[\w-]*(?:password|passwd|secret|api[_-]?key|token)["']?\s*[:=]\s*)"""
    r"""(?P<quote>["']?)[^\s"',&;}]+""",
    re.IGNORECASE,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def _mask_auth_header(match: re.Match[str]) -> str:
    scheme = match.group("scheme")
    return f"{match.group('key')}{scheme + ' ' if scheme else ''}{REDACTED}"


# Attributes every LogRecord has; anything else on a record came from `extra=`.
# uvicorn's "color_message" (an ANSI-coloured duplicate of the message) is dropped as well.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime", "taskName", "color_message"}
)


def is_sensitive_key(key: object) -> bool:
    """True for mapping keys / header names whose values must never be logged or reported."""
    return isinstance(key, str) and _SENSITIVE_KEY_RE.search(key) is not None


def redact_text(text: str) -> str:
    """Mask credentials in free text: Authorization headers, bearer tokens, secret-looking pairs, JWTs."""
    text = _AUTH_HEADER_RE.sub(_mask_auth_header, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group('scheme')} {REDACTED}", text)
    text = _SECRET_PAIR_RE.sub(lambda m: f"{m.group('key')}{m.group('quote')}{REDACTED}", text)
    return _JWT_RE.sub(REDACTED, text)


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Recursively mask credentials in strings, mappings and sequences."""
    if _depth > 8:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if is_sensitive_key(k) else redact_value(v, _depth=_depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact_value(v, _depth=_depth + 1) for v in value)
    return value


class RedactionFilter(logging.Filter):
    """Masks credentials in the rendered message, exception text and `extra=` fields of a record.

    Attach it to handlers (not loggers) so it also applies to records propagated from child loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed %-args: keep the raw template rather than dropping the record
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        for key in list(record.__dict__):
            if key in _STANDARD_RECORD_ATTRS:
                continue
            if is_sensitive_key(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = redact_value(record.__dict__[key])
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, plus `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


class _VendorwakeHandler(logging.StreamHandler):
    """Marker subclass so repeated configure_logging() calls replace rather than duplicate it."""


def configure_logging(level: str | int = "INFO") -> None:
    """Install the JSON handler on the root logger (idempotent) and route uvicorn logs through it."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, _VendorwakeHandler):
            root.removeHandler(existing)

    handler = _VendorwakeHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)

    resolved = logging.getLevelName(level.upper()) if isinstance(level, str) else level
    root.setLevel(resolved if isinstance(resolved, int) else logging.INFO)

    # uvicorn installs its own plain-text handlers before importing the app; hand its records to root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
