"""Read-only GitHub repository access over the REST API (RepoReader).

Endpoints (https://docs.github.com/en/rest, API version 2022-11-28):
- ``GET /repos/{owner}/{repo}/commits/{ref}`` with ``Accept: application/vnd.github.sha`` returns just the
  commit SHA as plain text ("Commits" > "Get a commit", custom media types).
- ``GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1`` lists every entry (``path``, ``type``, ``size``)
  and sets ``truncated`` when the listing exceeds GitHub's limit ("Git trees" > "Get a tree").
- ``GET /repos/{owner}/{repo}/contents/{path}?ref={sha}`` with ``Accept: application/vnd.github.raw+json``
  returns the raw file bytes ("Repository contents" > "Get repository content", custom media types).

Public repositories work without a token (60 requests/hour per IP); with a token the limit is 5,000/hour.
All reads for one ``RepoRef`` are pinned to the commit resolved by the first call, so a scan sees one
consistent snapshot even if the branch moves mid-scan.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from vendorwake.domain.contracts import RepoRef
from vendorwake.integrations.errors import (
    ContentRejectedError,
    RateLimitedError,
    ResourceNotFoundError,
    UpstreamError,
)

logger = logging.getLogger(__name__)

SERVICE = "github"
API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_MAX_FILE_BYTES = 512 * 1024
DEFAULT_MAX_FILES = 5000

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Directories that are never first-party source and can be huge.
SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "site-packages"})

# Extensions that are never text. Anything else is listed and checked for text when read.
BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".psd", ".svgz",
        ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".jar", ".war", ".whl", ".egg",
        ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class", ".pyc", ".pyo", ".wasm",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".mp4", ".mov", ".avi", ".wav", ".flac", ".ogg", ".webm",
        ".sqlite", ".db", ".parquet", ".pkl", ".npy", ".npz", ".h5", ".onnx", ".pt",
    }
)  # fmt: skip


def _check_name(value: str, what: str) -> str:
    if not _NAME_RE.match(value) or value in {".", ".."}:
        raise ValueError(f"invalid GitHub {what}: {value!r}")
    return value


def _normalise_subpath(subpath: str) -> str:
    sub = subpath.strip().strip("/")
    if not sub:
        return ""
    parts = PurePosixPath(sub).parts
    if any(p in {"..", "."} for p in parts):
        raise ValueError(f"invalid subpath: {subpath!r}")
    return "/".join(parts)


def _check_file_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        raise ValueError(f"invalid repository path: {path!r}")
    parts = PurePosixPath(path).parts
    if any(p in {"..", "."} for p in parts):
        raise ValueError(f"invalid repository path: {path!r}")
    return "/".join(parts)


def is_probably_text_path(path: str) -> bool:
    """False for paths in vendored/cache directories or with a known binary extension."""
    p = PurePosixPath(path)
    if any(part in SKIPPED_DIRS for part in p.parts[:-1]):
        return False
    return p.suffix.lower() not in BINARY_EXTENSIONS


def _retry_after(response: httpx.Response) -> float | None:
    # https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
    retry_after = response.headers.get("retry-after")
    if retry_after and retry_after.strip().isdigit():
        return float(retry_after.strip())
    reset = response.headers.get("x-ratelimit-reset")
    if reset and reset.strip().isdigit():
        return max(0.0, float(reset.strip()) - time.time())
    return None


class GitHubRepoReader:
    """`RepoReader` for GitHub. Paths returned by `list_files` and accepted by `read_file` are relative to
    the repository root (not to ``RepoRef.subpath``), so they can be linked to GitHub directly."""

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = API_BASE,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_files: int = DEFAULT_MAX_FILES,
        timeout_s: float = 20.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "vendorwake",  # GitHub rejects requests without a User-Agent
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=True)
        self._owns_client = client is None
        self._headers = headers
        self._base_url = base_url.rstrip("/")
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self._commits: dict[tuple[str, str, str], str] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubRepoReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ RepoReader

    def head_commit(self, repo: RepoRef) -> str:
        """SHA of ``repo.ref`` (branch, tag or SHA), resolved once per reader and then pinned."""
        key = self._key(repo)
        with self._lock:
            cached = self._commits.get(key)
        if cached:
            return cached
        owner, name, ref = key
        response = self._get(
            f"/repos/{owner}/{name}/commits/{quote(ref, safe='')}",
            accept="application/vnd.github.sha",
            what=f"ref {ref!r} of {owner}/{name}",
        )
        sha = response.text.strip()
        if not _SHA_RE.match(sha):
            raise UpstreamError("GitHub returned an unexpected commit SHA format", service=SERVICE)
        with self._lock:
            self._commits.setdefault(key, sha)
            return self._commits[key]

    def list_files(self, repo: RepoRef) -> list[str]:
        """Text-looking blobs under ``repo.subpath`` no larger than ``max_file_bytes``, sorted, capped at
        ``max_files``. Symlinks and submodules are skipped."""
        owner, name, _ = self._key(repo)
        sha = self.head_commit(repo)
        subpath = _normalise_subpath(repo.subpath)
        response = self._get(
            f"/repos/{owner}/{name}/git/trees/{sha}",
            params={"recursive": "1"},
            what=f"tree of {owner}/{name}@{sha[:12]}",
        )
        try:
            body = response.json()
            entries = body["tree"]
        except (ValueError, KeyError, TypeError) as exc:
            raise UpstreamError("GitHub returned an unexpected tree payload", service=SERVICE) from exc
        if body.get("truncated"):
            logger.warning(
                "github tree listing truncated by GitHub",
                extra={"repo": f"{owner}/{name}", "commit": sha},
            )
        files = sorted(self._select(entries, subpath))
        if len(files) > self.max_files:
            logger.warning(
                "github file listing capped",
                extra={"repo": f"{owner}/{name}", "files_found": len(files), "max_files": self.max_files},
            )
            files = files[: self.max_files]
        return files

    def read_file(self, repo: RepoRef, path: str) -> str:
        """UTF-8 text of ``path`` (relative to the repository root) at the pinned commit.

        Raises `ContentRejectedError` for files over ``max_file_bytes`` or that are not UTF-8 text.
        """
        owner, name, _ = self._key(repo)
        clean = _check_file_path(path)
        if not is_probably_text_path(clean):
            raise ContentRejectedError(f"{clean} is not a text file", service=SERVICE)
        sha = self.head_commit(repo)
        with self._client.stream(
            "GET",
            f"{self._base_url}/repos/{owner}/{name}/contents/{quote(clean, safe='/')}",
            params={"ref": sha},
            headers={**self._headers, "Accept": "application/vnd.github.raw+json"},
        ) as response:
            if response.status_code >= 400:
                response.read()
                self._raise_for_status(response, what=f"{clean} in {owner}/{name}@{sha[:12]}")
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_file_bytes:
                raise ContentRejectedError(
                    f"{clean} is larger than {self.max_file_bytes} bytes", service=SERVICE
                )
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > self.max_file_bytes:
                    raise ContentRejectedError(
                        f"{clean} is larger than {self.max_file_bytes} bytes", service=SERVICE
                    )
        if b"\x00" in data:
            raise ContentRejectedError(f"{clean} is binary", service=SERVICE)
        try:
            return bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            raise ContentRejectedError(f"{clean} is not UTF-8 text", service=SERVICE) from None

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _key(repo: RepoRef) -> tuple[str, str, str]:
        if repo.provider != "github":
            raise ValueError(f"unsupported repository provider: {repo.provider}")
        ref = repo.ref.strip()
        if not ref or "\x00" in ref:
            raise ValueError("ref must be a non-empty string")
        return _check_name(repo.owner, "owner"), _check_name(repo.name, "repository name"), ref

    def _select(self, entries: Iterable[object], subpath: str) -> Iterable[str]:
        prefix = f"{subpath}/" if subpath else ""
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path, kind, mode, size = (
                entry.get("path"),
                entry.get("type"),
                entry.get("mode"),
                entry.get("size"),
            )
            if kind != "blob" or not isinstance(path, str) or mode == "120000":  # 120000 = symlink
                continue
            if prefix and not path.startswith(prefix):
                continue
            if isinstance(size, int) and size > self.max_file_bytes:
                continue
            if is_probably_text_path(path):
                yield path

    def _get(
        self, path: str, *, what: str, accept: str | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        headers = dict(self._headers)
        if accept:
            headers["Accept"] = accept
        try:
            response = self._client.get(f"{self._base_url}{path}", headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"GitHub request failed ({type(exc).__name__})", service=SERVICE) from exc
        self._raise_for_status(response, what=what)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, what: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise ResourceNotFoundError(f"GitHub: {what} not found", service=SERVICE, status_code=404)
        if status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
            raise RateLimitedError(
                "GitHub rate limit exceeded",
                service=SERVICE,
                status_code=status,
                retry_after_s=_retry_after(response),
            )
        if status == 403 and response.headers.get("retry-after"):  # secondary rate limit
            raise RateLimitedError(
                "GitHub secondary rate limit exceeded",
                service=SERVICE,
                status_code=status,
                retry_after_s=_retry_after(response),
            )
        if status == 401:
            raise UpstreamError("GitHub rejected the configured token", service=SERVICE, status_code=401)
        raise UpstreamError(f"GitHub returned HTTP {status} for {what}", service=SERVICE, status_code=status)
