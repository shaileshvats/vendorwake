# ruff: noqa: S106 - test credentials
from __future__ import annotations

import os

import httpx
import pytest

from vendorwake.domain.contracts import RepoRef
from vendorwake.integrations import github
from vendorwake.integrations.errors import ContentRejectedError, RateLimitedError, ResourceNotFoundError
from vendorwake.integrations.github import GitHubRepoReader

from ..conftest import make_settings
from .conftest import live_settings, mock_client

SHA = "a" * 40
REPO = RepoRef(owner="acme", name="shop", ref="main")

TREE = {
    "sha": "t",
    "truncated": False,
    "tree": [
        {"path": "README.md", "type": "blob", "mode": "100644", "size": 120},
        {"path": "app", "type": "tree", "mode": "040000"},
        {"path": "app/billing.py", "type": "blob", "mode": "100644", "size": 900},
        {"path": "app/logo.png", "type": "blob", "mode": "100644", "size": 900},
        {"path": "app/huge.sql", "type": "blob", "mode": "100644", "size": 900_000},
        {"path": "app/link.py", "type": "blob", "mode": "120000", "size": 10},
        {"path": "node_modules/x/index.js", "type": "blob", "mode": "100644", "size": 10},
        {"path": "fixtures/demo/requirements.txt", "type": "blob", "mode": "100644", "size": 20},
        {"path": "fixtures/demo/pay.py", "type": "blob", "mode": "100644", "size": 20},
        {"path": "vendored", "type": "commit", "mode": "160000"},
    ],
}


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/repos/acme/shop/commits/main":
            assert request.headers["accept"] == "application/vnd.github.sha"
            return httpx.Response(200, text=SHA)
        if path == f"/repos/acme/shop/git/trees/{SHA}":
            assert request.url.params["recursive"] == "1"
            return httpx.Response(200, json=TREE)
        if path.startswith("/repos/acme/shop/contents/"):
            assert request.headers["accept"] == "application/vnd.github.raw+json"
            assert request.url.params["ref"] == SHA
            name = path.removeprefix("/repos/acme/shop/contents/")
            files = {
                "app/billing.py": b"import stripe\n",
                "app/blob.txt": b"abc\x00def",
                "app/latin1.txt": "caf\xe9".encode("latin-1"),
                "app/big.txt": b"x" * 2000,
            }
            if name in files:
                return httpx.Response(200, content=files[name])
            return httpx.Response(404, json={"message": "Not Found"})
        if path == "/repos/acme/missing/commits/main":
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected request {request.url}")


def reader(handler, **kwargs) -> GitHubRepoReader:
    return GitHubRepoReader(client=mock_client(handler), **kwargs)


def test_head_commit_is_resolved_once_and_pinned() -> None:
    rec = Recorder()
    r = reader(rec)
    assert r.head_commit(REPO) == SHA
    r.list_files(REPO)
    r.read_file(REPO, "app/billing.py")
    assert [q.url.path for q in rec.requests].count("/repos/acme/shop/commits/main") == 1


def test_list_files_filters_binary_large_symlink_and_vendored() -> None:
    files = reader(Recorder(), max_file_bytes=512 * 1024).list_files(REPO)
    assert files == [
        "README.md",
        "app/billing.py",
        "fixtures/demo/pay.py",
        "fixtures/demo/requirements.txt",
    ]


def test_list_files_respects_subpath_and_cap() -> None:
    sub = RepoRef(owner="acme", name="shop", ref="main", subpath="/fixtures/demo/")
    assert reader(Recorder()).list_files(sub) == ["fixtures/demo/pay.py", "fixtures/demo/requirements.txt"]
    assert reader(Recorder(), max_files=2).list_files(REPO) == ["README.md", "app/billing.py"]


def test_read_file_returns_text_and_rejects_binary_large_and_non_utf8() -> None:
    r = reader(Recorder(), max_file_bytes=1000)
    assert r.read_file(REPO, "app/billing.py") == "import stripe\n"
    with pytest.raises(ContentRejectedError, match="binary"):
        r.read_file(REPO, "app/blob.txt")
    with pytest.raises(ContentRejectedError, match="UTF-8"):
        r.read_file(REPO, "app/latin1.txt")
    with pytest.raises(ContentRejectedError, match="larger"):
        r.read_file(REPO, "app/big.txt")
    with pytest.raises(ContentRejectedError, match="not a text file"):
        r.read_file(REPO, "app/logo.png")
    with pytest.raises(ResourceNotFoundError):
        r.read_file(REPO, "app/nope.py")


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs", "a/../../b", "a\\b", ""])
def test_read_file_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        reader(Recorder()).read_file(REPO, bad)


def test_invalid_owner_is_rejected_before_any_request() -> None:
    with pytest.raises(ValueError):
        reader(Recorder()).head_commit(RepoRef(owner="acme/../x", name="shop"))


def test_missing_repo_is_typed_not_found() -> None:
    with pytest.raises(ResourceNotFoundError, match="not found"):
        reader(Recorder()).head_commit(RepoRef(owner="acme", name="missing"))


def test_rate_limit_is_typed_with_retry_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "retry-after": "42"},
            json={"message": "API rate limit exceeded"},
        )

    with pytest.raises(RateLimitedError) as info:
        reader(handler).head_commit(REPO)
    assert info.value.retry_after_s == 42
    assert info.value.status_code == 403


def test_token_is_sent_only_when_configured() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        assert request.headers["user-agent"] == "vendorwake"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        return httpx.Response(200, text=SHA)

    reader(handler).head_commit(REPO)
    reader(handler, token="ghp_test").head_commit(REPO)
    assert seen == [None, "Bearer ghp_test"]


def test_from_settings_works_without_token() -> None:
    assert isinstance(github.from_settings(make_settings(github_token=None)), GitHubRepoReader)


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("VW_LIVE_GITHUB") or os.environ.get("VW_GITHUB_TOKEN")),
    reason="live test needs VW_LIVE_GITHUB=1 or VW_GITHUB_TOKEN",
)
def test_live_public_repo_read() -> None:
    with github.from_settings(live_settings()) as r:
        repo = RepoRef(owner="stripe", name="stripe-python", ref="master")
        sha = r.head_commit(repo)
        assert len(sha) == 40
        files = r.list_files(repo)
        assert "README.md" in files
        assert "stripe" in r.read_file(repo, "README.md").lower()
