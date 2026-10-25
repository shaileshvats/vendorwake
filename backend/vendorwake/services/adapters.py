"""The single seam between workflows/API and the pipeline + sponsor adapters.

Workflows, activities and routes depend only on :class:`Ports`. :class:`LazyPorts` is the production
implementation: it imports ``vendorwake.{catalog,pipeline,llm}`` and ``vendorwake.integrations.*`` lazily
on first use (so this package and its tests work without them) and wires them as follows:

=========================  ===================================================================
catalog                    ``catalog.get_signature`` / ``catalog.SIGNATURES``
repo reader                ``integrations.github.from_settings(settings)``
search                     ``integrations.serpapi.from_settings(settings, official_domains=...)``
fetch (HTTP -> browser)    ``integrations.http_fetch.build_page_fetcher(settings)``
context store              ``integrations.backboard.from_settings(settings)``
session recorder           ``integrations.entire.ensure_entire_enabled`` + ``from_settings(settings, dir)``
permission guard           ``integrations.auth0_agents.guard_from_settings(settings)``
model                      ``llm.OpenAICompatibleLLM.from_settings(settings)``
pipeline steps             ``pipeline.analyze / extract / verify_upgrade / assess / propose /
                           verify_patches / finalize``
embeddings                 ``llm.embeddings.from_settings(settings).embed([text])`` (not built yet)
=========================  ===================================================================

Pipeline-internal step outputs (``VersionComparison``, ``PatchSet``, ``VerificationRun``) cross this seam as
JSON-ready dicts, so activities can persist them without importing pipeline types. Nothing here logs or
returns credentials.
"""

from __future__ import annotations

import importlib
import logging
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from vendorwake.core.config import Settings
from vendorwake.domain.contracts import (
    ContextStore,
    ImpactAssessment,
    ModelCallInfo,
    PageFetcher,
    RepoReader,
    RepoRef,
    RepositoryInventory,
    SearchProvider,
    SessionRecorder,
    VendorSignature,
)

logger = logging.getLogger(__name__)

#: Operation -> scope required by the agent's Auth0 M2M token (docs/api-contract.md).
OPERATION_SCOPES: dict[str, str] = {
    "read_docs": "docs:read",
    "read_repo": "repo:read",
    "propose_patch": "patch:propose",
    "push_branch": "repo:write",
}

#: Logical agent identities used by the workflows (keys of VW_AUTH0_AGENT_CREDENTIALS).
AGENT_DISCOVERY = "discovery"
AGENT_REPOSITORY = "repository"
AGENT_IMPACT = "impact"
AGENT_MIGRATION = "migration"
AGENT_VERIFICATION = "verification"
AGENTS = (AGENT_DISCOVERY, AGENT_REPOSITORY, AGENT_IMPACT, AGENT_MIGRATION, AGENT_VERIFICATION)

#: Files read per repository for analysis/impact (worker-local cache, keyed by commit).
MAX_REPO_FILES = 2000
_FILES_CACHE_SIZE = 8


class AdapterUnavailableError(RuntimeError):
    """A pipeline/integration module is missing, misnamed or not configured. Not worth retrying."""


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    agent: str
    operation: str
    required_scope: str
    allowed: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PermissionGuard(Protocol):
    def check(self, agent: str, operation: str) -> PermissionDecision: ...
    def list_agents(self) -> list[dict[str, Any]]: ...


class ExtractionOutput(Protocol):
    """``pipeline.extraction.ExtractionResult``: extraction, grounding, model_calls (+ flags)."""

    extraction: Any
    grounding: Any
    model_calls: list[ModelCallInfo]


class Ports(Protocol):
    """Everything the workflows and API need from the pipeline and sponsor adapters."""

    # catalog
    def signature(self, vendor: str) -> VendorSignature | None: ...
    def signatures(self) -> list[VendorSignature]: ...

    # integrations
    def repo_reader(self) -> RepoReader: ...
    def search(self, signature: VendorSignature) -> SearchProvider | None: ...  # None: not configured
    def fetcher(self) -> PageFetcher: ...
    def context_store(self) -> ContextStore: ...
    def session_recorder(self, worktree: str) -> SessionRecorder: ...
    def permission_guard(self) -> PermissionGuard: ...
    def embed(self, text: str) -> list[float]: ...

    # pipeline
    def repo_files(self, repo: RepoRef, commit_sha: str) -> dict[str, str]: ...
    def analyze(
        self, repo: RepoRef, files: dict[str, str], commit_sha: str | None
    ) -> RepositoryInventory: ...
    def extract(self, text: str, *, vendor: str, package: str, version: str | None) -> ExtractionOutput: ...
    def verify_upgrade(
        self, extraction: Any, files: dict[str, str], inventory: RepositoryInventory
    ) -> dict: ...
    def assess(
        self,
        extraction: Any,
        source: dict,
        files: dict[str, str],
        inventory: RepositoryInventory,
        comparison: dict | None,
    ) -> ImpactAssessment: ...
    def propose(self, assessment: ImpactAssessment, extraction: Any, files: dict[str, str]) -> dict: ...
    def verify_patches(
        self, extraction: Any, files: dict[str, str], inventory: RepositoryInventory, patch_set: dict
    ) -> dict | None: ...
    def finalize(
        self,
        assessment: ImpactAssessment,
        extraction: Any,
        inventory: RepositoryInventory,
        patch_set: dict | None,
        patched_run: dict | None,
    ) -> ImpactAssessment: ...


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def normalize_decision(agent: str, operation: str, raw: Any) -> PermissionDecision:
    """Accept the guard's decision object (or a dict) and return our PermissionDecision."""
    if isinstance(raw, PermissionDecision):
        return raw
    allowed = bool(_get(raw, "allowed", False))
    scope = _get(raw, "required_scope") or OPERATION_SCOPES.get(operation, "")
    reason = _get(raw, "reason") or ("granted" if allowed else "denied")
    return PermissionDecision(agent, operation, str(scope), allowed, str(reason))


def _is_not_configured(exc: BaseException) -> bool:
    return type(exc).__name__ == "NotConfiguredError"


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise AdapterUnavailableError(f"{name} is not available") from exc


def _attr(module_name: str, attr: str) -> Any:
    module = _module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise AdapterUnavailableError(f"{module_name}.{attr} is not defined") from exc


class LazyPorts:
    """Production Ports. Thread-safe; built adapters are cached per instance (one per process)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._files: OrderedDict[tuple, dict[str, str]] = OrderedDict()

    def _built(self, key: str, factory: Any) -> Any:
        with self._lock:
            if key not in self._cache:
                try:
                    self._cache[key] = factory()
                except Exception as exc:
                    if _is_not_configured(exc):
                        raise AdapterUnavailableError(str(exc)) from exc
                    raise
            return self._cache[key]

    def _llm(self) -> Any:
        return self._built(
            "llm", lambda: _attr("vendorwake.llm", "OpenAICompatibleLLM").from_settings(self.settings)
        )

    def _pipeline(self, name: str) -> Any:
        return _attr("vendorwake.pipeline", name)

    # -- catalog --------------------------------------------------------------------------------
    def signature(self, vendor: str) -> VendorSignature | None:
        get_signature = _attr("vendorwake.catalog", "get_signature")
        try:
            return get_signature(vendor)
        except (KeyError, LookupError, ValueError):
            return None

    def signatures(self) -> list[VendorSignature]:
        return list(_attr("vendorwake.catalog", "SIGNATURES").values())

    # -- integrations ---------------------------------------------------------------------------
    def repo_reader(self) -> RepoReader:
        return self._built(
            "github", lambda: _attr("vendorwake.integrations.github", "from_settings")(self.settings)
        )

    def search(self, signature: VendorSignature) -> SearchProvider | None:
        if self.settings.serpapi_api_key is None:
            return None
        factory = _attr("vendorwake.integrations.serpapi", "from_settings")
        return self._built(
            f"serpapi:{signature.vendor}",
            lambda: factory(self.settings, official_domains=list(signature.official_domains)),
        )

    def fetcher(self) -> PageFetcher:
        return self._built(
            "fetcher",
            lambda: _attr("vendorwake.integrations.http_fetch", "build_page_fetcher")(self.settings),
        )

    def context_store(self) -> ContextStore:
        if self.settings.backboard_api_key is None:
            raise AdapterUnavailableError("Backboard is not configured (VW_BACKBOARD_API_KEY)")
        return self._built(
            "backboard", lambda: _attr("vendorwake.integrations.backboard", "from_settings")(self.settings)
        )

    def session_recorder(self, worktree: str) -> SessionRecorder:
        entire = _module("vendorwake.integrations.entire")
        try:
            recorder = entire.from_settings(self.settings, worktree)  # checks VW_ENTIRE_ENABLED and the CLI
            entire.ensure_entire_enabled(worktree)
        except Exception as exc:
            if _is_not_configured(exc):
                raise AdapterUnavailableError(str(exc)) from exc
            raise
        return recorder

    def permission_guard(self) -> PermissionGuard:
        guard = self._built(
            "guard",
            lambda: _attr("vendorwake.integrations.auth0_agents", "guard_from_settings")(self.settings),
        )
        return _NormalizingGuard(guard, self.settings)

    def embed(self, text: str) -> list[float]:
        embedder = self._built(
            "embedder", lambda: _attr("vendorwake.llm.embeddings", "from_settings")(self.settings)
        )
        return [float(x) for x in list(embedder.embed([text]))[0]]

    # -- pipeline -------------------------------------------------------------------------------
    def repo_files(self, repo: RepoRef, commit_sha: str) -> dict[str, str]:
        """Every text file under ``repo.subpath`` (the reader filters binaries/vendored dirs and size).
        Cached per commit, so one investigation reads the repository once per worker process."""
        key = (repo.owner.lower(), repo.name.lower(), repo.subpath, commit_sha)
        with self._lock:
            if key in self._files:
                self._files.move_to_end(key)
                return self._files[key]
        reader = self.repo_reader()
        pinned = repo.model_copy(update={"ref": commit_sha})
        files = {path: reader.read_file(pinned, path) for path in reader.list_files(pinned)[:MAX_REPO_FILES]}
        with self._lock:
            self._files[key] = files
            while len(self._files) > _FILES_CACHE_SIZE:
                self._files.popitem(last=False)
        return files

    def analyze(self, repo: RepoRef, files: dict[str, str], commit_sha: str | None) -> RepositoryInventory:
        return self._pipeline("analyze")(repo, files, commit_sha=commit_sha)

    def extract(self, text: str, *, vendor: str, package: str, version: str | None) -> ExtractionOutput:
        return self._pipeline("extract")(
            text, vendor=vendor, package=package, llm=self._llm(), version=version
        )

    def verify_upgrade(self, extraction: Any, files: dict[str, str], inventory: RepositoryInventory) -> dict:
        comparison = self._pipeline("verify_upgrade")(
            extraction, files, inventory, allowed_repos=self.settings.verification_allowed_repos
        )
        return comparison.model_dump(mode="json")

    def assess(
        self,
        extraction: Any,
        source: dict,
        files: dict[str, str],
        inventory: RepositoryInventory,
        comparison: dict | None,
    ) -> ImpactAssessment:
        cmp = None
        if comparison is not None:
            cmp = _attr("vendorwake.pipeline.verification", "VersionComparison").model_validate(comparison)
        return self._pipeline("assess")(extraction, source, files, inventory, self._llm(), comparison=cmp)

    def propose(self, assessment: ImpactAssessment, extraction: Any, files: dict[str, str]) -> dict:
        return self._pipeline("propose")(assessment, extraction, files, self._llm()).model_dump(mode="json")

    def _patch_set(self, patch_set: dict | None) -> Any:
        if patch_set is None:
            return None
        return _attr("vendorwake.pipeline.migration", "PatchSet").model_validate(patch_set)

    def verify_patches(
        self, extraction: Any, files: dict[str, str], inventory: RepositoryInventory, patch_set: dict
    ) -> dict | None:
        run = self._pipeline("verify_patches")(
            extraction,
            files,
            inventory,
            self._patch_set(patch_set),
            allowed_repos=self.settings.verification_allowed_repos,
        )
        return run.model_dump(mode="json") if run is not None else None

    def finalize(
        self,
        assessment: ImpactAssessment,
        extraction: Any,
        inventory: RepositoryInventory,
        patch_set: dict | None,
        patched_run: dict | None,
    ) -> ImpactAssessment:
        run = None
        if patched_run is not None:
            run = _attr("vendorwake.pipeline.verification", "VerificationRun").model_validate(patched_run)
        return self._pipeline("finalize")(assessment, extraction, inventory, self._patch_set(patch_set), run)


class _NormalizingGuard:
    """Adapts ``ToolPermissionGuard`` (keyword-only ``check``) to :class:`PermissionGuard`."""

    def __init__(self, inner: Any, settings: Settings) -> None:
        self._inner = inner
        self._settings = settings

    def check(self, agent: str, operation: str) -> PermissionDecision:
        return normalize_decision(agent, operation, self._inner.check(agent=agent, operation=operation))

    def list_agents(self) -> list[dict[str, Any]]:
        """Configured agents with their client id and the scopes their real tokens hold. Scopes are
        established by performing the guard check for every operation (cached tokens)."""
        parse = _attr("vendorwake.integrations.auth0_agents", "parse_agent_credentials")
        credentials = parse(self._settings.auth0_agent_credentials)
        agents = []
        for agent in sorted(credentials):
            granted = sorted(
                d.required_scope
                for d in (self.check(agent, op) for op in OPERATION_SCOPES)
                if d.allowed and d.required_scope
            )
            # Only these three fields leave this method: never a client secret or token.
            agents.append({"agent": agent, "client_id": credentials[agent].client_id, "scopes": granted})
        return agents
