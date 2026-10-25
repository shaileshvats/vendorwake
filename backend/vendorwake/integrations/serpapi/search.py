"""Web discovery through SerpApi's Google Search API (SearchProvider).

Plain HTTPS with httpx rather than the ``serpapi`` package (which pulls in ``requests``). The request
shape matches both the official docs (https://serpapi.com/search-api: ``GET https://serpapi.com/search``
with ``engine``, ``q``, ``api_key``, ``start``, ``output``) and the ``serpapi==1.1.2`` client source
(``HTTPClient.request`` -> ``GET {BASE_DOMAIN}/search`` with ``api_key`` injected as a query parameter).

Errors (https://serpapi.com/api-status-and-error-codes): 401 bad key, 429 throughput/plan limit, 5xx
server error; a search with no results is HTTP 200 with an ``error`` message and
``search_information.organic_results_state == "Fully empty"``, which we return as an empty list.

The docs no longer list a ``num`` parameter (Google dropped it), so we never send it: we read
result pages of ~10 via ``start`` until ``num`` unique results are collected (at most `MAX_PAGES` pages,
each page is a billed search).

Search results are untrusted discovery hints, never evidence: ``is_official`` only says the URL's host
is in the vendor's ``official_domains``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from vendorwake.domain.contracts import DiscoveryCandidate
from vendorwake.integrations.errors import RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)

SERVICE = "serpapi"
SEARCH_URL = "https://serpapi.com/search"
PAGE_SIZE = 10
MAX_PAGES = 3

_TRACKING_PARAMS = frozenset({"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src", "srsltid"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def canonical_url(url: str) -> str:
    """A stable form of ``url`` for display and dedupe: lowercase scheme/host, no default port, no
    fragment, no tracking parameters (``utm_*``, ``gclid``, ...), no trailing slash except the root."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    netloc = host
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def _dedupe_key(url: str) -> str:
    # http/https and a leading "www." do not make a different page for discovery purposes.
    canon = canonical_url(url)
    _, rest = canon.split("://", 1) if "://" in canon else ("", canon)
    return rest.removeprefix("www.")


def is_official_url(url: str, official_domains: Sequence[str]) -> bool:
    """True when ``url``'s host equals or is a subdomain of an entry in ``official_domains``.

    An entry may carry a path prefix (``"github.com/stripe"``), which then must match whole path
    segments (``github.com/stripe/stripe-python`` matches; ``github.com/stripe-evil`` does not).
    Only http(s) URLs can be official.
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"}:
        return False
    host = _host(url)
    if not host:
        return False
    path_segments = [s for s in parts.path.split("/") if s]
    for entry in official_domains:
        entry = entry.strip().lower()
        if not entry:
            continue
        entry = entry.split("://", 1)[-1]
        domain, _, prefix = entry.partition("/")
        domain = domain.rstrip(".").removeprefix("*.")
        if not domain or not (host == domain or host.endswith("." + domain)):
            continue
        prefix_segments = [s for s in prefix.split("/") if s]
        if [s.lower() for s in path_segments[: len(prefix_segments)]] == prefix_segments:
            return True
    return False


class SerpApiSearch:
    """`SearchProvider` backed by SerpApi. ``official_domains`` marks ``is_official`` on candidates;
    use `with_official_domains` to get a per-vendor view that shares the HTTP client."""

    def __init__(
        self,
        api_key: str,
        *,
        official_domains: Sequence[str] = (),
        engine: str = "google",
        extra_params: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
        timeout_s: float = 30.0,
        max_pages: int = MAX_PAGES,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self.official_domains = tuple(official_domains)
        self.engine = engine
        self.extra_params = dict(extra_params or {})
        self._client = client or httpx.Client(timeout=timeout_s)
        self._owns_client = client is None
        self.max_pages = max(1, max_pages)

    def __repr__(self) -> str:  # never show the key
        return f"SerpApiSearch(engine={self.engine!r}, official_domains={self.official_domains!r})"

    def with_official_domains(self, official_domains: Sequence[str]) -> SerpApiSearch:
        clone = object.__new__(SerpApiSearch)
        clone.__dict__.update(self.__dict__)
        clone.official_domains = tuple(official_domains)
        clone._owns_client = False
        return clone

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, query: str, *, num: int = 10) -> list[DiscoveryCandidate]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        num = max(1, num)
        seen: set[str] = set()
        candidates: list[DiscoveryCandidate] = []
        pages = min(self.max_pages, -(-num // PAGE_SIZE))
        for page in range(pages):
            results = self._fetch_page(query, start=page * PAGE_SIZE)
            if not results:
                break
            for result in results:
                link = result.get("link")
                if not isinstance(link, str) or urlsplit(link).scheme.lower() not in {"http", "https"}:
                    continue
                key = _dedupe_key(link)
                if key in seen:
                    continue
                seen.add(key)
                url = canonical_url(link)
                candidates.append(
                    DiscoveryCandidate(
                        url=url,
                        title=str(result.get("title") or "")[:500],
                        snippet=str(result.get("snippet") or "")[:2000],
                        query=query,
                        rank=len(candidates) + 1,
                        is_official=is_official_url(url, self.official_domains),
                    )
                )
                if len(candidates) >= num:
                    break
            if len(candidates) >= num or len(results) < PAGE_SIZE:
                break
        logger.info(
            "serpapi search",
            extra={
                "query": query,
                "engine": self.engine,
                "results": len(candidates),
                "official": sum(c.is_official for c in candidates),
            },
        )
        return candidates

    def _fetch_page(self, query: str, *, start: int) -> list[dict]:
        params: dict[str, str] = {
            **self.extra_params,
            "engine": self.engine,
            "q": query,
            "output": "json",
            "api_key": self._api_key,
        }
        if start:
            params["start"] = str(start)
        try:
            response = self._client.get(SEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            # The exception text would include the request URL, which carries the api_key.
            raise UpstreamError(f"SerpApi request failed ({type(exc).__name__})", service=SERVICE) from exc

        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None

        if response.status_code == 429:
            raise RateLimitedError("SerpApi rate or plan limit reached", service=SERVICE, status_code=429)
        if response.status_code == 401:
            raise UpstreamError("SerpApi rejected the API key", service=SERVICE, status_code=401)
        if response.status_code >= 400:
            raise UpstreamError(
                f"SerpApi returned HTTP {response.status_code}",
                service=SERVICE,
                status_code=response.status_code,
            )
        if not isinstance(body, dict):
            raise UpstreamError("SerpApi returned a non-JSON response", service=SERVICE)

        organic = body.get("organic_results")
        if isinstance(organic, list):
            return [r for r in organic if isinstance(r, dict)]
        state = (body.get("search_information") or {}).get("organic_results_state")
        if state == "Fully empty" or (isinstance(error, str) and "hasn't returned any results" in error):
            return []
        if error:
            raise UpstreamError(f"SerpApi search failed: {str(error)[:200]}", service=SERVICE)
        return []
