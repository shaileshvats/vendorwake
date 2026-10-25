"""A minimal synchronous Chrome DevTools Protocol client over one WebSocket.

Browserbase's session ``connectUrl`` is a browser-level CDP WebSocket endpoint: its official Puppeteer
guide passes it straight to ``puppeteer.connect({browserWSEndpoint: session.connectUrl})``
(https://docs.browserbase.com/introduction/puppeteer). Speaking CDP directly lets us skip Playwright
(a ~40 MB wheel plus a Node driver) and needs only ``websockets``, which uvicorn[standard] already
installs. Methods used (https://chromedevtools.github.io/devtools-protocol/): ``Target.createTarget``,
``Target.attachToTarget`` (``flatten: true`` so page commands carry a ``sessionId``), ``Page.enable``,
``Page.navigate``, ``Runtime.evaluate``, ``Page.captureScreenshot``.
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable
from typing import Any, Protocol


class CdpError(Exception):
    """A CDP command failed or timed out. Messages never include the connect URL (it is signed)."""


class CdpTransport(Protocol):
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...
    def close(self) -> None: ...


ConnectFn = Callable[[str], CdpTransport]


def websocket_connect(url: str, *, open_timeout_s: float = 20.0) -> CdpTransport:
    from websockets.sync.client import connect  # websockets>=13 (sync client, recv(timeout=...))

    # Screenshots arrive as base64 inside one message; allow up to 64 MiB.
    return connect(url, open_timeout=open_timeout_s, max_size=64 * 1024 * 1024, compression=None)


class CdpSession:
    def __init__(self, transport: CdpTransport, *, default_timeout_s: float = 30.0) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self.default_timeout_s = default_timeout_s

    def close(self) -> None:
        try:
            self._transport.close()
        except Exception:  # noqa: S110 - closing a dead socket must not mask the real error
            pass

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        message_id = next(self._ids)
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        try:
            self._transport.send(json.dumps(message))
        except Exception as exc:
            raise CdpError(f"CDP send failed for {method} ({type(exc).__name__})") from exc
        deadline = time.monotonic() + (timeout_s or self.default_timeout_s)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpError(f"CDP {method} timed out")
            try:
                raw = self._transport.recv(timeout=remaining)
            except TimeoutError:
                raise CdpError(f"CDP {method} timed out") from None
            except Exception as exc:
                raise CdpError(f"CDP connection lost during {method} ({type(exc).__name__})") from exc
            try:
                reply = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(reply, dict) or reply.get("id") != message_id:
                continue  # an event or a reply to someone else; we poll instead of tracking events
            if "error" in reply:
                error = reply["error"] if isinstance(reply["error"], dict) else {}
                raise CdpError(f"CDP {method} failed: {str(error.get('message', 'unknown error'))[:200]}")
            result = reply.get("result")
            return result if isinstance(result, dict) else {}

    def evaluate(self, expression: str, *, session_id: str, timeout_s: float | None = None) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
            session_id=session_id,
            timeout_s=timeout_s,
        )
        if "exceptionDetails" in result:
            raise CdpError("page script evaluation failed")
        return (result.get("result") or {}).get("value")
