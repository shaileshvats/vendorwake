from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from vendorwake.integrations import serpapi
from vendorwake.integrations.errors import NotConfiguredError, RateLimitedError, UpstreamError
from vendorwake.integrations.serpapi import SerpApiSearch, canonical_url, is_official_url

from ..conftest import make_settings
from .conftest import live_env, live_settings, mock_client

KEY = "serp-test-key-123"
STRIPE = ["stripe.com", "github.com/stripe"]


def organic(*links: str) -> dict:
    return {
        "search_metadata": {"status": "Success"},
        "organic_results": [
            {"position": i + 1, "title": f"T{i}", "link": link, "snippet": f"S{i}"}
            for i, link in enumerate(links)
        ],
    }


def test_request_shape_and_candidates() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=organic(
                "https://docs.stripe.com/changelog?utm_source=x#top",
                "https://DOCS.stripe.com/changelog/",  # duplicate after canonicalisation
                "http://www.example-blog.com/stripe-16",
                "https://github.com/stripe/stripe-python/releases",
                "https://github.com/stripe-evil/stripe-python",
                "javascript:alert(1)",
            ),
        )

    search = SerpApiSearch(KEY, official_domains=STRIPE, client=mock_client(handler))
    results = search.search("stripe-python 16.0.0 breaking changes", num=10)

    params = seen[0].url.params
    assert seen[0].url.host == "serpapi.com" and seen[0].url.path == "/search"
    assert params["engine"] == "google" and params["output"] == "json" and params["api_key"] == KEY
    assert params["q"] == "stripe-python 16.0.0 breaking changes"
    assert "num" not in params and "start" not in params

    assert [(c.url, c.rank, c.is_official) for c in results] == [
        ("https://docs.stripe.com/changelog", 1, True),
        ("http://www.example-blog.com/stripe-16", 2, False),
        ("https://github.com/stripe/stripe-python/releases", 3, True),
        ("https://github.com/stripe-evil/stripe-python", 4, False),
    ]
    assert all(c.query == "stripe-python 16.0.0 breaking changes" for c in results)


def test_paginates_with_start_until_num_reached() -> None:
    starts: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = request.url.params.get("start")
        starts.append(start)
        base = int(start or 0)
        return httpx.Response(200, json=organic(*[f"https://e{base + i}.com/" for i in range(10)]))

    results = SerpApiSearch(KEY, client=mock_client(handler)).search("q", num=15)
    assert starts == [None, "10"]
    assert len(results) == 15 and results[-1].rank == 15


def test_no_results_is_empty_list() -> None:
    body = {
        "search_metadata": {"status": "Success"},
        "search_information": {"organic_results_state": "Fully empty"},
        "error": "Google hasn't returned any results for this query.",
    }
    search = SerpApiSearch(KEY, client=mock_client(lambda r: httpx.Response(200, json=body)))
    assert search.search("zzzz") == []


@pytest.mark.parametrize(
    ("status", "error_type"), [(401, UpstreamError), (429, RateLimitedError), (503, UpstreamError)]
)
def test_errors_are_typed_and_never_leak_the_key(status: int, error_type: type[Exception]) -> None:
    body = {
        "error": f"Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key {KEY}"
    }
    search = SerpApiSearch(KEY, client=mock_client(lambda r: httpx.Response(status, json=body)))
    with pytest.raises(error_type) as info:
        search.search("q")
    assert KEY not in str(info.value)
    assert KEY not in repr(search)


def test_transport_error_message_does_not_include_url_with_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    with pytest.raises(UpstreamError) as info:
        SerpApiSearch(KEY, client=mock_client(handler)).search("q")
    assert KEY not in str(info.value)


def test_with_official_domains_shares_client() -> None:
    base = SerpApiSearch(
        KEY, client=mock_client(lambda r: httpx.Response(200, json=organic("https://x.io/a")))
    )
    vendor = base.with_official_domains(["x.io"])
    assert vendor.search("q")[0].is_official is True
    assert base.search("q")[0].is_official is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://stripe.com/blog", True),
        ("https://docs.stripe.com/upgrades", True),
        ("https://stripe.com.evil.io/", False),
        ("https://notstripe.com/", False),
        ("https://github.com/stripe/stripe-python", True),
        ("https://github.com/Stripe/stripe-python", True),
        ("https://github.com/stripe-evil/x", False),
        ("https://github.com/", False),
        ("ftp://stripe.com/x", False),
    ],
)
def test_is_official_url(url: str, expected: bool) -> None:
    assert is_official_url(url, STRIPE) is expected


def test_canonical_url() -> None:
    assert (
        canonical_url("HTTPS://Docs.Stripe.com:443/a/b/?utm_medium=x&v=2#frag")
        == "https://docs.stripe.com/a/b?v=2"
    )
    assert canonical_url("http://example.com") == "http://example.com/"
    assert canonical_url("http://example.com:8080/x") == "http://example.com:8080/x"


def test_from_settings_requires_key() -> None:
    with pytest.raises(NotConfiguredError, match="VW_SERPAPI_API_KEY"):
        serpapi.from_settings(make_settings(serpapi_api_key=None))
    built = serpapi.from_settings(
        make_settings(serpapi_api_key=SecretStr(KEY)), official_domains=["stripe.com"]
    )
    assert built.official_domains == ("stripe.com",)


@pytest.mark.live
@live_env("VW_SERPAPI_API_KEY")
def test_live_search() -> None:
    search = serpapi.from_settings(live_settings(), official_domains=["stripe.com", "github.com/stripe"])
    results = search.search("stripe-python changelog", num=5)
    assert 0 < len(results) <= 5
    assert any(c.is_official for c in results)
