"""Temporal activities: every I/O of the workflows. Synchronous; the worker runs them in a thread pool.

Rules every activity follows:

* **Idempotent.** Investigation steps persist their output in ``investigation_steps`` keyed by
  ``(investigation_id, step)`` *before returning*, and check it first, so a retry, a restarted worker or a
  re-run never repeats a completed step (in particular never re-calls the rate-limited model). Discovery is
  keyed by content hash: unchanged pages are not re-inserted, a snapshot the model has seen is never sent
  again, and a change exists once per (vendor, content hash). Repository analysis is keyed by commit.
* **Permission-checked.** Before a tool call, the activity asks the ToolPermissionGuard whether that
  activity's agent identity holds the operation's scope. A denial is recorded in the timeline and fails the
  activity with the non-retryable ``PermissionDenied`` type.
* **Traced.** Each activity runs in a Sentry transaction; investigation activities share the trace id
  stored on the investigation.
* **Tenant-safe.** Tenant data is only touched through ``tenant_session(user_id)`` (RLS); global data only
  through ``worker_session()`` (the ``vendorwake_worker`` role).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess  # noqa: S404 (fixed git argv only, for the Entire worktree)
import tempfile
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from vendorwake.core.config import Settings
from vendorwake.core.exceptions import NotFoundError
from vendorwake.db.models import EMBEDDING_DIM, ChangeEmbedding, VendorChange
from vendorwake.db.session import tenant_session, worker_session
from vendorwake.domain.contracts import (
    ChangeExtraction,
    DiscoveryCandidate,
    ImpactAssessment,
    ImpactStatus,
    Recommendation,
    RepoRef,
    RepositoryInventory,
    VendorSignature,
)
from vendorwake.services import changes as changes_svc
from vendorwake.services import investigations as inv_svc
from vendorwake.services import recommendations as rec_svc
from vendorwake.services import repositories as repo_svc
from vendorwake.services import vendors as vendors_svc
from vendorwake.services.adapters import (
    AGENT_DISCOVERY,
    AGENT_IMPACT,
    AGENT_MIGRATION,
    AGENT_REPOSITORY,
    AGENT_VERIFICATION,
    OPERATION_SCOPES,
    AdapterUnavailableError,
    PermissionDecision,
    Ports,
)
from vendorwake.workflows import types as t
from vendorwake.workflows.tracing import activity_span

logger = logging.getLogger(__name__)

AGENT_GIT_NAME = "Vendorwake Migration Agent"
AGENT_GIT_EMAIL = "migration-agent@vendorwake.invalid"


def _non_retryable(message: str, error_type: str) -> ApplicationError:
    return ApplicationError(message, type=error_type, non_retryable=True)


def _translate(fn: Callable[[], Any]) -> Any:
    """Map domain/adapter errors onto Temporal's (non-)retryable failures."""
    try:
        return fn()
    except ApplicationError:
        raise
    except AdapterUnavailableError as exc:
        raise _non_retryable(str(exc), t.ADAPTER_UNAVAILABLE) from exc
    except NotFoundError as exc:
        raise _non_retryable(exc.message, t.NOT_FOUND) from exc
    except Exception as exc:
        name = type(exc).__name__  # integrations/llm error classes, matched by name to keep imports lazy
        if name == "NotConfiguredError":
            raise _non_retryable(str(exc), t.ADAPTER_UNAVAILABLE) from exc
        if name == "RateLimitedError":
            # Per-minute model quotas (Google AI Studio free tier) reset quickly: retry after the window
            # instead of failing the workflow. Completed steps are persisted, so nothing is re-sent.
            delay = getattr(exc, "retry_after_s", None) or 60.0
            raise ApplicationError(
                f"model rate limited; retrying in {delay:.0f}s",
                type=t.MODEL_RATE_LIMITED_RETRY,
                non_retryable=False,
                next_retry_delay=timedelta(seconds=min(max(delay, 15.0), 120.0)),
            ) from exc
        if name == "ToolPermissionDenied":
            raise _non_retryable(f"{name}", t.PERMISSION_DENIED) from exc
        raise


def is_official(url: str, signature: VendorSignature) -> bool:
    try:
        from vendorwake.catalog import is_official_url

        return bool(is_official_url(url, signature))
    except ImportError:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == d.lower() or host.endswith("." + d.lower()) for d in signature.official_domains)


def primary_package(signature: VendorSignature) -> str:
    for ecosystem in ("pypi", *signature.packages):
        names = signature.packages.get(ecosystem) or []
        if names:
            return names[0]
    return signature.vendor


def discovery_queries(signature: VendorSignature) -> list[str]:
    package = primary_package(signature)
    return [f"{signature.vendor} API changelog", f"{package} release notes breaking changes"]


def enforce_evidence_rules(assessment: ImpactAssessment) -> ImpactAssessment:
    """CONFIRMED needs deterministic evidence; model output alone is at most POTENTIAL (contracts.py)."""
    affected = []
    for comp in assessment.affected:
        if comp.status == ImpactStatus.confirmed and not any(e.deterministic for e in comp.evidence):
            comp = comp.model_copy(update={"status": ImpactStatus.potential})
        affected.append(comp)
    update: dict[str, Any] = {"affected": affected}
    deterministic = any(e.deterministic for e in assessment.evidence)
    if assessment.overall_status == ImpactStatus.confirmed and not deterministic:
        update.update(overall_status=ImpactStatus.potential, requires_human_review=True)
    return assessment.model_copy(update=update)


def verification_allowed(settings: Settings, owner: str, name: str, subpath: str) -> bool:
    """True when ``owner/name[/subpath]`` is listed in VW_VERIFICATION_ALLOWED_REPOS (exact match)."""
    target = f"{owner}/{name}".lower() + (f"/{subpath}" if subpath else "")
    for entry in (a.strip().strip("/") for a in settings.verification_allowed_repos.split(",")):
        parts = entry.split("/", 2)
        if len(parts) < 2:
            continue
        candidate = f"{parts[0]}/{parts[1]}".lower() + (f"/{parts[2]}" if len(parts) == 3 else "")
        if candidate == target:
            return True
    return False


class _Base:
    def __init__(self, ports: Ports, settings: Settings) -> None:
        self.ports = ports
        self.settings = settings

    def _check(self, agent: str, operation: str) -> PermissionDecision:
        try:
            return self.ports.permission_guard().check(agent, operation)
        except AdapterUnavailableError as exc:  # fail closed
            scope = OPERATION_SCOPES.get(operation, "")
            return PermissionDecision(agent, operation, scope, False, f"permission guard unavailable: {exc}")

    def _require(
        self,
        agent: str,
        operation: str,
        *,
        user_id: uuid.UUID | None = None,
        investigation_id: uuid.UUID | None = None,
    ) -> PermissionDecision:
        decision = self._check(agent, operation)
        detail = f"{agent} -> {operation} ({decision.required_scope}): {decision.reason}"
        if user_id is not None and investigation_id is not None:
            status = "allowed" if decision.allowed else "denied"
            with tenant_session(user_id) as s:
                inv_svc.append_timeline(s, investigation_id, "permission", status, detail)
        if not decision.allowed:
            logger.warning("agent permission denied: %s", detail)
            raise _non_retryable(f"permission denied: {detail}", t.PERMISSION_DENIED)
        return decision


# ============================================================================================ repository
class RepositoryActivities(_Base):
    @activity.defn(name=t.REPO_SET_STATUS)
    def set_status(self, inp: t.RepositoryStatusInput) -> None:
        def run() -> None:
            with tenant_session(inp.user_id) as s:
                repo_svc.set_status(s, inp.repository_id, inp.status, inp.error)

        _translate(run)

    @activity.defn(name=t.REPO_ANALYZE)
    def analyze(self, inp: t.RepositoryAnalysisInput) -> t.RepositoryAnalysisResult:
        with activity_span(t.REPO_ANALYZE, repository_id=inp.repository_id):
            return _translate(lambda: self._analyze(inp))

    def _analyze(self, inp: t.RepositoryAnalysisInput) -> t.RepositoryAnalysisResult:
        with tenant_session(inp.user_id) as s:
            repo = repo_svc.get(s, inp.repository_id)
            ref, known_sha = repo_svc.repo_ref(repo), repo.commit_sha
            known_count = len((repo.inventory or {}).get("integrations") or [])
            has_inventory = repo.inventory is not None
        self._require(AGENT_REPOSITORY, "read_repo")
        head = self.ports.repo_reader().head_commit(ref)
        if has_inventory and known_sha == head:  # same commit: nothing to re-read
            with tenant_session(inp.user_id) as s:
                repo_svc.set_status(s, inp.repository_id, "completed")
            return t.RepositoryAnalysisResult(
                repository_id=inp.repository_id,
                status="completed",
                commit_sha=head,
                integration_count=known_count,
                reused=True,
            )
        files = self.ports.repo_files(ref, head)
        inventory = self.ports.analyze(ref, files, head)
        if inventory.commit_sha is None:
            inventory = inventory.model_copy(update={"commit_sha": head})
        with tenant_session(inp.user_id) as s:
            repo_svc.save_inventory(s, inp.repository_id, inventory)
        return t.RepositoryAnalysisResult(
            repository_id=inp.repository_id,
            status="completed",
            commit_sha=inventory.commit_sha,
            integration_count=len(inventory.integrations),
        )


# ============================================================================================ discovery
class DiscoveryActivities(_Base):
    def _signature(self, vendor: str) -> VendorSignature:
        signature = self.ports.signature(vendor)
        if signature is None:
            raise _non_retryable(f"unknown vendor {vendor!r}", t.NOT_FOUND)
        return signature

    @activity.defn(name=t.DISCOVERY_SEARCH)
    def search(self, inp: t.DiscoverySearchInput) -> t.DiscoverySearchResult:
        with activity_span(t.DISCOVERY_SEARCH, vendor=inp.vendor):
            return _translate(lambda: self._search(inp))

    def _search(self, inp: t.DiscoverySearchInput) -> t.DiscoverySearchResult:
        signature = self._signature(inp.vendor)
        self._require(AGENT_DISCOVERY, "read_docs")
        with worker_session() as ws:
            vendor_id = vendors_svc.ensure_vendor(ws, inp.vendor, signature)
        queries = discovery_queries(signature)
        provider = self.ports.search(signature)
        candidates: list[DiscoveryCandidate] = []
        search_error = None if provider is not None else "search provider not configured"
        for query in queries if provider is not None else []:
            error = None
            results: list[DiscoveryCandidate] = []
            try:
                results = [
                    c.model_copy(update={"is_official": is_official(c.url, signature)})
                    for c in provider.search(query, num=10)
                ]
            except Exception as exc:  # recorded; other queries and the changelog URLs still run
                error = search_error = type(exc).__name__
            with worker_session() as ws:
                ws.execute(
                    text(
                        "INSERT INTO discovery_queries (vendor_id, query, provider, result_count, "
                        "results, workflow_id, error) "
                        "VALUES (:v, :q, 'serpapi', :n, CAST(:r AS jsonb), :w, :e)"
                    ),
                    {
                        "v": vendor_id,
                        "q": query,
                        "n": len(results),
                        "r": json.dumps([c.model_dump(mode="json") for c in results]),
                        "w": inp.workflow_id,
                        "e": error,
                    },
                )
            candidates.extend(results)
        urls: list[str] = []
        for url in [*signature.changelog_urls, *(c.url for c in candidates if c.is_official)]:
            if url not in urls:
                urls.append(url)
        return t.DiscoverySearchResult(
            vendor=inp.vendor,
            urls=urls[: inp.max_sources],
            candidates=candidates,
            queries=queries,
            search_error=search_error,
        )

    @activity.defn(name=t.DISCOVERY_FETCH)
    def fetch_snapshot(self, inp: t.FetchInput) -> t.SnapshotRef:
        with activity_span(t.DISCOVERY_FETCH, vendor=inp.vendor):
            return _translate(lambda: self._fetch(inp))

    def _fetch(self, inp: t.FetchInput) -> t.SnapshotRef:
        signature = self._signature(inp.vendor)
        self._require(AGENT_DISCOVERY, "read_docs")
        doc = self.ports.fetcher().fetch(inp.url)  # HTTP, falling back to Browserbase when configured
        source_type = "changelog" if inp.url in signature.changelog_urls else "documentation"
        with worker_session() as ws:
            vendor_id = vendors_svc.ensure_vendor(ws, inp.vendor, signature)
            source_id = vendors_svc.upsert_source(
                ws, vendor_id, inp.url, source_type, doc.retrieved_at, doc.content_sha256
            )
            latest = changes_svc.latest_snapshot_w(ws, inp.url)
            if latest is not None and latest.content_sha256 == doc.content_sha256:
                return t.SnapshotRef(
                    vendor=inp.vendor,
                    url=inp.url,
                    snapshot_id=latest.id,
                    retrieved_at=latest.retrieved_at,
                    content_sha256=doc.content_sha256,
                    fetched_via=latest.fetched_via,  # type: ignore[arg-type]
                    changed=False,
                    already_extracted=latest.extraction_status is not None,
                )
            snapshot = changes_svc.insert_snapshot_w(ws, vendor_id, source_id, doc)
            return t.SnapshotRef(
                vendor=inp.vendor,
                url=inp.url,
                snapshot_id=snapshot.id,
                retrieved_at=snapshot.retrieved_at,
                content_sha256=doc.content_sha256,
                fetched_via=doc.fetched_via,
                changed=True,
            )

    @activity.defn(name=t.DISCOVERY_EXTRACT)
    def extract(self, inp: t.ExtractInput) -> t.ExtractResult:
        with activity_span(t.DISCOVERY_EXTRACT, vendor=inp.vendor):
            return _translate(lambda: self._extract(inp))

    def _extract(self, inp: t.ExtractInput) -> t.ExtractResult:
        signature = self._signature(inp.vendor)
        ref = inp.snapshot
        with worker_session() as ws:
            vendor_id = vendors_svc.ensure_vendor(ws, inp.vendor, signature)
            snapshot = changes_svc.load_snapshot_w(ws, ref.snapshot_id, ref.retrieved_at)
            if snapshot.extraction_status is not None:
                return t.ExtractResult(
                    change_id=snapshot.change_id, created=False, reason="already extracted"
                )
            existing = changes_svc.change_by_hash_w(ws, vendor_id, snapshot.content_sha256)
            if existing is not None:
                snapshot.extraction_status, snapshot.change_id = "change", existing
                return t.ExtractResult(change_id=existing, created=False, reason="change exists for content")
            content = snapshot.content

        # The model call: no transaction is held open while it runs.
        result = self.ports.extract(
            content, vendor=signature.vendor, package=primary_package(signature), version=None
        )

        with worker_session() as ws:  # persisted before returning, so this content is never sent again
            snapshot = changes_svc.load_snapshot_w(ws, ref.snapshot_id, ref.retrieved_at)
            if not result.extraction.changes:
                snapshot.extraction_status = "no_change"
                return t.ExtractResult(change_id=None, created=False, reason="no changes in source")
            change_id, created = changes_svc.save_change_w(
                ws,
                vendor_id=vendor_id,
                source_id=snapshot.source_id,
                snapshot=snapshot,
                extraction=result.extraction,
                grounding=result.grounding,
                model_calls=list(result.model_calls),
                candidates=inp.candidates,
                is_official=is_official(snapshot.url, signature),
            )
            snapshot.extraction_status, snapshot.change_id = "change", change_id
        return t.ExtractResult(change_id=change_id, created=created, reason="extracted")

    @activity.defn(name=t.DISCOVERY_EMBED)
    def embed(self, change_id: uuid.UUID) -> bool:
        with activity_span(t.DISCOVERY_EMBED, change_id=change_id):
            return _translate(lambda: self._embed(change_id))

    def _embed(self, change_id: uuid.UUID) -> bool:
        if not self.settings.embeddings_enabled:
            # 512 MB hosts (free Render) cannot also load the ONNX embedding model next to the API,
            # worker and embedded Temporal; related-change search is then simply unavailable there.
            logger.info("embeddings disabled (VW_EMBEDDINGS_ENABLED=false); skipping change %s", change_id)
            return False
        with worker_session() as ws:
            if ws.get(ChangeEmbedding, change_id) is not None:
                return False
            change = ws.get(VendorChange, change_id)
            if change is None:
                raise _non_retryable("change not found", t.NOT_FOUND)
            content = changes_svc.embedding_text(change)
        vector = self.ports.embed(content)
        if len(vector) != EMBEDDING_DIM:
            raise _non_retryable(
                f"embedding has {len(vector)} dims, expected {EMBEDDING_DIM}", t.INVALID_INPUT
            )
        with worker_session() as ws:
            changes_svc.save_embedding_w(ws, change_id, self.settings.embedding_model, vector)
        return True


# ============================================================================================ investigation
class _Context:
    """Inputs every investigation step needs, loaded from the database."""

    def __init__(
        self,
        extraction: ChangeExtraction,
        inventory: RepositoryInventory,
        ref: RepoRef,
        commit_sha: str,
        source: dict,
        title: str,
        vendor: str,
    ) -> None:
        self.extraction, self.inventory, self.ref, self.commit_sha = extraction, inventory, ref, commit_sha
        self.source, self.title, self.vendor = source, title, vendor


class InvestigationActivities(_Base):
    @activity.defn(name=t.INV_START)
    def start(self, inp: t.InvestigationStartInput) -> t.InvestigationContext:
        def run() -> t.InvestigationContext:
            with tenant_session(inp.user_id) as s:
                inv = inv_svc.start(
                    s,
                    inp.investigation_id,
                    workflow_id=inp.workflow_id,
                    run_id=inp.run_id,
                    trace_id=inp.trace_id,
                )
                repo = repo_svc.get(s, inv.repository_id)
                return t.InvestigationContext(
                    investigation_id=inv.id,
                    repository_id=inv.repository_id,
                    change_id=inv.change_id,
                    has_inventory=repo.inventory is not None,
                    trace_id=inv.sentry_trace_id or inp.trace_id,
                )

        with activity_span(t.INV_START, trace_id=inp.trace_id, investigation_id=inp.investigation_id):
            return _translate(run)

    # ---------------------------------------------------------------------------------------- generic step
    @activity.defn(name=t.INV_STEP)
    def step(self, inp: t.StepInput) -> t.StepResult:
        handlers: dict[str, Callable[[t.StepInput], t.StepResult]] = {
            t.STEP_UPGRADE_CHECK: self._upgrade_check,
            t.STEP_ASSESSMENT: self._assessment,
            t.STEP_PATCH: self._patch,
            t.STEP_VERIFICATION: self._verification,
            t.STEP_AGENT_SESSION: self._agent_session,
            t.STEP_CONTEXT_SEED: self._context_seed,
            t.STEP_PERSIST: self._persist,
        }
        if inp.step not in handlers:
            raise _non_retryable(f"unknown step {inp.step!r}", t.INVALID_INPUT)

        with tenant_session(inp.user_id) as s:
            if inv_svc.get_step(s, inp.investigation_id, inp.step) is not None:
                inv_svc.append_timeline(
                    s,
                    inp.investigation_id,
                    inp.step,
                    "completed",
                    "reused persisted result; step not repeated",
                )
                result = t.StepResult(step=inp.step, status="completed", reused=True, detail="reused")
                if inp.step == t.STEP_PERSIST:
                    result.recommendation_ids = rec_svc.pending_ids(s, inp.investigation_id)
                    result.investigation_status = inv_svc.get(s, inp.investigation_id).status
                return result
            inv_svc.append_timeline(s, inp.investigation_id, inp.step, "running")

        span = f"investigation.{inp.step}"
        with activity_span(span, trace_id=inp.trace_id, investigation_id=inp.investigation_id):
            try:
                result = _translate(lambda: handlers[inp.step](inp))
            except ApplicationError as exc:
                self._record_failure(inp, exc.type or "ApplicationError")
                raise
            except Exception as exc:
                self._record_failure(inp, type(exc).__name__)
                raise
        with tenant_session(inp.user_id) as s:
            inv_svc.append_timeline(s, inp.investigation_id, inp.step, result.status, result.detail)
        return result

    def _record_failure(self, inp: t.StepInput, error_type: str) -> None:
        attempt = activity.info().attempt if activity.in_activity() else 1
        try:
            with tenant_session(inp.user_id) as s:
                detail = f"{error_type} (attempt {attempt})"
                inv_svc.append_timeline(s, inp.investigation_id, inp.step, "failed", detail)
        except Exception:  # never mask the original failure
            logger.exception("could not record step failure")

    # ---------------------------------------------------------------------------------------- helpers
    def _context(self, inp: t.StepInput) -> _Context:
        with tenant_session(inp.user_id) as s:
            inv = inv_svc.get(s, inp.investigation_id)
            change, vendor = changes_svc.get_change(s, inv.change_id)
            repo = repo_svc.get(s, inv.repository_id)
            if change.extraction is None:
                raise _non_retryable("change has no extraction", t.NOT_FOUND)
            if repo.inventory is None:
                raise _non_retryable("repository has not been analysed", t.NOT_FOUND)
            inventory = RepositoryInventory.model_validate(repo.inventory)
            commit = inventory.commit_sha or repo.commit_sha
            if not commit:
                raise _non_retryable("repository inventory has no commit", t.NOT_FOUND)
            return _Context(
                extraction=ChangeExtraction.model_validate(change.extraction),
                inventory=inventory,
                ref=repo_svc.repo_ref(repo),
                commit_sha=commit,
                source={"url": change.source_url or "", "content_sha256": change.content_sha256 or ""},
                title=change.title,
                vendor=vendor,
            )

    def _files(self, ctx: _Context) -> dict[str, str]:
        return self.ports.repo_files(ctx.ref, ctx.commit_sha)

    def _output(self, inp: t.StepInput, step: str) -> Any:
        with tenant_session(inp.user_id) as s:
            row = inv_svc.get_step(s, inp.investigation_id, step)
            if row is None:
                raise _non_retryable(f"step {step} has no persisted output", t.NOT_FOUND)
            return row.output

    def _save(self, inp: t.StepInput, output: Any) -> None:
        with tenant_session(inp.user_id) as s:
            inv_svc.put_step(s, inp.user_id, inp.investigation_id, inp.step, output)

    def _final(self, inp: t.StepInput) -> ImpactAssessment:
        return ImpactAssessment.model_validate(self._output(inp, t.STEP_VERIFICATION)["final"])

    def _allowed(self, ctx: _Context) -> bool:
        return verification_allowed(self.settings, ctx.ref.owner, ctx.ref.name, ctx.ref.subpath)

    # ---------------------------------------------------------------------------------------- steps
    def _upgrade_check(self, inp: t.StepInput) -> t.StepResult:
        ctx = self._context(inp)
        if not self._allowed(ctx):
            self._save(inp, None)
            return t.StepResult(
                step=inp.step, status="skipped", detail="repository not on the verification allow-list"
            )
        self._require(
            AGENT_VERIFICATION, "read_repo", user_id=inp.user_id, investigation_id=inp.investigation_id
        )
        comparison = self.ports.verify_upgrade(ctx.extraction, self._files(ctx), ctx.inventory)
        self._save(inp, comparison)
        before = comparison.get("before", {}).get("result", {}).get("status")
        after = comparison.get("after", {}).get("result", {}).get("status")
        return t.StepResult(
            step=inp.step, status="completed", detail=f"tests before: {before}, after: {after}"
        )

    def _assessment(self, inp: t.StepInput) -> t.StepResult:
        comparison = self._output(inp, t.STEP_UPGRADE_CHECK)
        ctx = self._context(inp)
        self._require(AGENT_IMPACT, "read_repo", user_id=inp.user_id, investigation_id=inp.investigation_id)
        assessment = self.ports.assess(
            ctx.extraction, ctx.source, self._files(ctx), ctx.inventory, comparison
        )
        assessment = enforce_evidence_rules(assessment)
        self._save(inp, assessment.model_dump(mode="json"))  # persisted before returning: never re-asked
        detail = f"{assessment.overall_status.value}, confidence {assessment.confidence:.2f}"
        return t.StepResult(step=inp.step, status="completed", detail=detail)

    def _patch(self, inp: t.StepInput) -> t.StepResult:
        assessment = ImpactAssessment.model_validate(self._output(inp, t.STEP_ASSESSMENT))
        if assessment.overall_status not in (ImpactStatus.confirmed, ImpactStatus.potential):
            self._save(inp, None)
            return t.StepResult(step=inp.step, status="skipped", detail=f"{assessment.overall_status.value}")
        self._require(
            AGENT_MIGRATION, "propose_patch", user_id=inp.user_id, investigation_id=inp.investigation_id
        )
        ctx = self._context(inp)
        patch_set = self.ports.propose(assessment, ctx.extraction, self._files(ctx))
        self._save(inp, patch_set)
        recs = patch_set.get("recommendations") or []
        patches = sum(1 for r in recs if r.get("patch"))
        return t.StepResult(
            step=inp.step, status="completed", detail=f"{len(recs)} recommendations, {patches} patches"
        )

    def _verification(self, inp: t.StepInput) -> t.StepResult:
        """Run the tests with the patches applied (allow-listed repos only), then finalize the report."""
        assessment = ImpactAssessment.model_validate(self._output(inp, t.STEP_ASSESSMENT))
        patch_set = self._output(inp, t.STEP_PATCH)
        ctx = self._context(inp)
        run = None
        detail = "no patches to verify"
        if patch_set and patch_set.get("patched_files"):
            if self._allowed(ctx):
                self._require(
                    AGENT_VERIFICATION,
                    "read_repo",
                    user_id=inp.user_id,
                    investigation_id=inp.investigation_id,
                )
                run = self.ports.verify_patches(ctx.extraction, self._files(ctx), ctx.inventory, patch_set)
                detail = f"patched tests: {(run or {}).get('result', {}).get('status')}"
            else:
                detail = "repository not on the verification allow-list; needs human review"
        final = self.ports.finalize(assessment, ctx.extraction, ctx.inventory, patch_set, run)
        final = enforce_evidence_rules(final)
        self._save(inp, {"run": run, "final": final.model_dump(mode="json")})
        return t.StepResult(step=inp.step, status="completed" if run else "skipped", detail=detail)

    def _agent_session(self, inp: t.StepInput) -> t.StepResult:
        """Record the Migration Agent's session with Entire in a throwaway git worktree.

        Baseline commit = original affected files; the agent commit = patched files, so the Entire checkpoint
        links the real model turns (provider-reported usage) to the diff. Nothing is pushed anywhere.
        """
        patch_set = self._output(inp, t.STEP_PATCH)
        if not self.settings.entire_enabled or not patch_set or not patch_set.get("patched_files"):
            self._save(inp, {"skipped": True})
            return t.StepResult(step=inp.step, status="skipped", detail="no migration session to record")
        ctx = self._context(inp)
        files = self._files(ctx)
        patched: dict[str, str] = patch_set["patched_files"]
        workdir = Path(tempfile.mkdtemp(prefix="vw-entire-"))
        try:
            info = self._record_session(inp, ctx, workdir, files, patched, patch_set)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        with tenant_session(inp.user_id) as s:
            inv_svc.save_agent_session(s, inp.user_id, inp.investigation_id, "entire", info)
            inv_svc.put_step(
                s, inp.user_id, inp.investigation_id, inp.step, {"session_id": info.get("session_id")}
            )
        return t.StepResult(
            step=inp.step, status="completed", detail=f"checkpoint {info.get('checkpoint_id')}"
        )

    def _record_session(
        self,
        inp: t.StepInput,
        ctx: _Context,
        workdir: Path,
        files: dict[str, str],
        patched: dict[str, str],
        patch_set: dict,
    ) -> dict[str, Any]:
        git = shutil.which("git")
        if git is None:
            raise AdapterUnavailableError("git is not installed")

        def run_git(*args: str) -> None:
            subprocess.run([git, *args], cwd=workdir, check=True, capture_output=True, timeout=60)  # noqa: S603

        def write(contents: dict[str, str]) -> list[str]:
            paths = []
            for rel, body in contents.items():
                target = (workdir / rel).resolve()
                if workdir.resolve() not in target.parents:
                    continue  # never write outside the worktree
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                paths.append(rel)
            return paths

        run_git("init", "-q")
        run_git("config", "user.name", AGENT_GIT_NAME)
        run_git("config", "user.email", AGENT_GIT_EMAIL)
        run_git("config", "commit.gpgsign", "false")
        baseline = write({p: files[p] for p in patched if p in files})
        if baseline:
            run_git("add", "--", *baseline)
        run_git(
            "commit", "-q", "--allow-empty", "-m", f"baseline {ctx.ref.owner}/{ctx.ref.name}@{ctx.commit_sha}"
        )

        recorder = self.ports.session_recorder(str(workdir))
        model = self.settings.llm_model
        prompt = f"Migrate {ctx.ref.owner}/{ctx.ref.name} for {ctx.vendor} change '{ctx.title}'"
        session_id = recorder.start(investigation_id=str(inp.investigation_id), prompt=prompt, model=model)
        recs = patch_set.get("recommendations") or []
        for i, call in enumerate(patch_set.get("model_calls") or []):
            rec = recs[i] if i < len(recs) else {}
            recorder.record_turn(
                session_id=session_id,
                content=rec.get("patch") or rec.get("description") or "",
                model=call.get("model") or model,
                usage={"input_tokens": call.get("input_tokens"), "output_tokens": call.get("output_tokens")},
            )
        paths = write(patched)
        recorder.commit(session_id=session_id, message=f"vendorwake: {ctx.title}", paths=paths)
        info = dict(recorder.end(session_id=session_id) or {})
        info.setdefault("session_id", session_id)
        info.setdefault("model", model)
        return info

    def _context_seed(self, inp: t.StepInput) -> t.StepResult:
        final = self._final(inp)
        lines = [final.summary, f"Overall: {final.overall_status.value} ({final.severity.value})"]
        lines += [
            f"- {a.file_path}:{a.line} {a.symbol}: {a.status.value} - {a.rationale}" for a in final.affected
        ]
        lines += [f"Recommendation: {r.description}" for r in final.recommendations]
        thread_id = self.ports.context_store().seed_investigation(
            user_key=str(inp.user_id), investigation_id=str(inp.investigation_id), summary="\n".join(lines)
        )
        with tenant_session(inp.user_id) as s:
            inv_svc.get(s, inp.investigation_id).backboard_thread_id = str(thread_id)[:255]
            inv_svc.put_step(s, inp.user_id, inp.investigation_id, inp.step, {"thread_id": str(thread_id)})
        return t.StepResult(step=inp.step, status="completed", detail="follow-up context seeded")

    def _persist(self, inp: t.StepInput) -> t.StepResult:
        final = self._final(inp)
        with tenant_session(inp.user_id) as s:
            pending, status = inv_svc.persist_report(
                s, inp.user_id, inp.investigation_id, final, list(final.recommendations)
            )
            ids = {"recommendation_ids": [str(p) for p in pending]}
            inv_svc.put_step(s, inp.user_id, inp.investigation_id, inp.step, ids)
        return t.StepResult(
            step=inp.step,
            status="completed",
            detail=f"report saved; {len(pending)} recommendations awaiting approval",
            recommendation_ids=pending,
            investigation_status=status,
        )

    # ---------------------------------------------------------------------------------------- decisions
    @activity.defn(name=t.INV_RECORD_DECISION)
    def record_decision(self, inp: t.DecisionInput) -> str:
        """Record a human decision. Approval triggers *no* action; we only show that the agent cannot push."""

        def run() -> str:
            approved = inp.decision == "approve"
            with tenant_session(inp.user_id) as s:
                status = "approved" if approved else "dismissed"
                detail = f"recommendation {inp.recommendation_id}"
                inv_svc.append_timeline(s, inp.investigation_id, "approval", status, detail)
            if not approved:
                return "dismissed"
            decision = self._check(AGENT_MIGRATION, "push_branch")
            with tenant_session(inp.user_id) as s:
                inv_svc.append_timeline(
                    s,
                    inp.investigation_id,
                    "push_branch",
                    "denied" if not decision.allowed else "not_performed",
                    f"{decision.reason}; no branch was pushed (out of scope; needs repo:write)",
                )
            return "approved"

        with activity_span(
            t.INV_RECORD_DECISION, trace_id=inp.trace_id, investigation_id=inp.investigation_id
        ):
            return _translate(run)

    @activity.defn(name=t.INV_FINISH)
    def finish(self, inp: t.FinishInput) -> None:
        def run() -> None:
            with tenant_session(inp.user_id) as s:
                inv_svc.finish(s, inp.investigation_id, inp.outcome)

        _translate(run)

    @activity.defn(name=t.INV_FAIL)
    def fail(self, inp: t.FailInput) -> None:
        def run() -> None:
            with tenant_session(inp.user_id) as s:
                inv_svc.fail(s, inp.investigation_id, inp.step, inp.error_type)

        _translate(run)

    # ---------------------------------------------------------------------------------------- re-verify
    @activity.defn(name=t.REC_VERIFY)
    def verify(self, inp: t.VerifyInput) -> t.VerifyResult:
        with activity_span(t.REC_VERIFY, recommendation_id=inp.recommendation_id):
            return _translate(lambda: self._verify(inp))

    def _verify(self, inp: t.VerifyInput) -> t.VerifyResult:
        with tenant_session(inp.user_id) as s:
            row = rec_svc.get(s, inp.recommendation_id)
            investigation_id, has_patch = row.investigation_id, bool(row.patch)
            patch_step = inv_svc.get_step(s, investigation_id, t.STEP_PATCH)
            patch_set = patch_step.output if patch_step is not None else None
        step = t.StepInput(investigation_id=investigation_id, user_id=inp.user_id, step=t.REC_VERIFY)
        ctx = self._context(step)
        if not has_patch or not patch_set or not patch_set.get("patched_files") or not self._allowed(ctx):
            with tenant_session(inp.user_id) as s:
                status = rec_svc.save_verification(s, inp.recommendation_id, None)
                inv_svc.append_timeline(
                    s, investigation_id, "verification", status, "not verifiable automatically"
                )
            return t.VerifyResult(recommendation_id=inp.recommendation_id, verification_status=status)
        self._require(AGENT_VERIFICATION, "read_repo", user_id=inp.user_id, investigation_id=investigation_id)
        run = self.ports.verify_patches(ctx.extraction, self._files(ctx), ctx.inventory, patch_set)
        result = Recommendation.model_validate(
            {"recommendation_type": "test", "description": "", "verification": (run or {}).get("result")}
        ).verification
        with tenant_session(inp.user_id) as s:
            status = rec_svc.save_verification(s, inp.recommendation_id, result)
            inv_svc.append_timeline(
                s, investigation_id, "verification", status, "re-run with all patches applied"
            )
        return t.VerifyResult(recommendation_id=inp.recommendation_id, verification_status=status)


def build_activities(ports: Ports, settings: Settings) -> list[Callable[..., Any]]:
    repo = RepositoryActivities(ports, settings)
    disc = DiscoveryActivities(ports, settings)
    inv = InvestigationActivities(ports, settings)
    return [
        repo.set_status,
        repo.analyze,
        disc.search,
        disc.fetch_snapshot,
        disc.extract,
        disc.embed,
        inv.start,
        inv.step,
        inv.record_decision,
        inv.finish,
        inv.fail,
        inv.verify,
    ]
