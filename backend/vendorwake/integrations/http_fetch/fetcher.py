"""Plain-HTTP page fetcher (PageFetcher) plus the JS-shell heuristic used by the fallback fetcher.

Page URLs come from search results and vendor catalogs, i.e. untrusted input, so:
- only http(s) on default-looking hosts; hosts resolving to private, loopback, link-local or reserved
  addresses are refused (basic SSRF guard; redirects are followed manually and every hop is checked);
- the body is streamed and cut off at ``max_bytes``; total time is bounded by ``timeout_s``;
- only text-like content types are accepted; the text is extracted, never executed or rendered.
The extracted text can still contain prompt-injection attempts and must be treated as data downstream.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx

from vendorwake.domain.contracts import SourceDocument
from vendorwake.integrations.errors import ContentRejectedError, RateLimitedError, UpstreamError
from vendorwake.integrations.http_fetch.html_text import html_to_text, normalise_text

logger = logging.getLogger(__name__)

SERVICE = "http"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_TEXT_CHARS = 1_000_000
MAX_REDIRECTS = 5
USER_AGENT = "VendorwakeBot/0.1 (+https://github.com/shaileshvats/vendorwake; changelog monitor)"

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
TEXT_TYPES = frozenset(
    {
        "text/plain", "text/markdown", "text/x-markdown", "text/x-rst", "text/xml", "application/xml",
        "application/rss+xml", "application/atom+xml", "application/json", "text/csv",
    }
)  # fmt: skip

# Markers of client-rendered app shells whose static HTML carries little or no content.
_SHELL_MARKERS = (
    'id="root"></div>',
    'id="app"></div>',
    'id="__next"></div>',
    'id="__nuxt"></div>',
    "you need to enable javascript",
    "please enable javascript",
    "requires javascript",
)

Resolver = Callable[[str], Sequence[str]]


def system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def _is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def check_public_url(url: str, resolver: Resolver = system_resolver) -> None:
    """Raise `ContentRejectedError` unless ``url`` is http(s) and its host resolves only to public IPs."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ContentRejectedError(f"unsupported URL scheme: {parts.scheme!r}", service=SERVICE)
    if parts.username or parts.password:
        raise ContentRejectedError("URLs with credentials are not fetched", service=SERVICE)
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise ContentRejectedError("URL has no host", service=SERVICE)
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
        raise ContentRejectedError(f"refusing to fetch internal host {host}", service=SERVICE)
    try:
        addresses = [host] if _looks_like_ip(host) else list(resolver(host))
    except OSError as exc:
        raise UpstreamError(f"could not resolve {host}", service=SERVICE) from exc
    if not addresses or not all(_is_public_address(a) for a in addresses):
        raise ContentRejectedError(f"refusing to fetch non-public address for {host}", service=SERVICE)


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A `SourceDocument` plus the raw signals the fallback heuristic needs."""

    document: SourceDocument
    content_type: str
    raw_bytes: int
    script_count: int
    shell_marker: bool


def looks_like_js_shell(page: FetchedPage, *, min_text_chars: int = 400) -> bool:
    """True when static HTML probably needs a browser: very little text, or an app-shell marker with
    little text, or many scripts relative to the text yield."""
    if page.content_type not in HTML_TYPES:
        return False
    chars = len(page.document.text)
    if chars < min_text_chars:
        return True
    if page.shell_marker and chars < 4 * min_text_chars:
        return True
    return page.script_count >= 20 and chars < 2 * min_text_chars


class HttpPageFetcher:
    """`PageFetcher` over plain HTTP(S) with size, time and address limits."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = 20.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
        user_agent: str = USER_AGENT,
        resolver: Resolver = system_resolver,
        allow_private_networks: bool = False,
    ) -> None:
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0))
        self._owns_client = client is None
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_text_chars = max_text_chars
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,application/xml;q=0.8,*/*;q=0.5",
        }
        self._resolver = resolver
        self.allow_private_networks = allow_private_networks

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, url: str) -> SourceDocument:
        return self.fetch_page(url).document

    def fetch_page(self, url: str) -> FetchedPage:
        deadline = time.monotonic() + self.timeout_s
        current = url.strip()
        for _ in range(MAX_REDIRECTS + 1):
            if not self.allow_private_networks:
                check_public_url(current, self._resolver)
            try:
                with self._client.stream(
                    "GET", current, headers=self._headers, follow_redirects=False
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise UpstreamError("redirect without Location", service=SERVICE)
                        current = urljoin(current, location)
                        continue
                    self._raise_for_status(response, current)
                    content_type = self._content_type(response)
                    body = self._read_limited(response, deadline)
                    encoding = response.charset_encoding or "utf-8"
                    final_url = str(response.url)
            except httpx.TimeoutException as exc:
                raise UpstreamError(f"timed out fetching {_host(current)}", service=SERVICE) from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    f"failed fetching {_host(current)} ({type(exc).__name__})", service=SERVICE
                ) from exc
            return self._build(final_url, content_type, body, encoding)
        raise UpstreamError(f"too many redirects for {_host(url)}", service=SERVICE)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _raise_for_status(response: httpx.Response, url: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 429:
            raise RateLimitedError(f"{_host(url)} rate-limited the fetch", service=SERVICE, status_code=429)
        raise UpstreamError(f"{_host(url)} returned HTTP {status}", service=SERVICE, status_code=status)

    @staticmethod
    def _content_type(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not content_type:
            content_type = "text/html"  # servers that omit it are almost always serving HTML
        if content_type not in HTML_TYPES and content_type not in TEXT_TYPES:
            raise ContentRejectedError(f"unsupported content type {content_type!r}", service=SERVICE)
        return content_type

    def _read_limited(self, response: httpx.Response, deadline: float) -> bytes:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise ContentRejectedError(f"page larger than {self.max_bytes} bytes", service=SERVICE)
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > self.max_bytes:
                raise ContentRejectedError(f"page larger than {self.max_bytes} bytes", service=SERVICE)
            if time.monotonic() > deadline:
                raise UpstreamError("page download exceeded the time limit", service=SERVICE)
        return bytes(data)

    def _build(self, url: str, content_type: str, body: bytes, encoding: str) -> FetchedPage:
        try:
            raw = body.decode(encoding, errors="replace")
        except LookupError:
            raw = body.decode("utf-8", errors="replace")
        if content_type in HTML_TYPES:
            title, text, scripts = html_to_text(raw)
            lowered = raw.lower()
            marker = any(m in lowered for m in _SHELL_MARKERS)
        else:
            title, text, scripts, marker = None, normalise_text(raw), 0, False
        text = text[: self.max_text_chars]
        document = SourceDocument(
            url=url,
            retrieved_at=datetime.now(UTC),
            fetched_via="http",
            content_sha256=sha256_text(text),
            text=text,
            title=title,
        )
        return FetchedPage(
            document=document,
            content_type=content_type,
            raw_bytes=len(body),
            script_count=scripts,
            shell_marker=marker,
        )


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or "?"
    except ValueError:
        return "?"
