"""Pipeline entry points. Each step is a pure-ish function with serialisable (Pydantic) inputs and outputs, so
Temporal activities can call them one by one and retry them idempotently; ``run_investigation`` chains them.

Model calls per investigation (free-tier budget): extraction = 1 per release chunk (usually 1), impact = 1
(skipped when nothing matched), migration = 1-2 per affected file. Identical prompts hit the in-process cache.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from vendorwake.analysis.inventory import build_inventory, detected_version, scan_repository, scope_files
from vendorwake.domain.contracts import (
    ChangeExtraction,
    ImpactAssessment,
    ImpactStatus,
    RepoRef,
    RepositoryInventory,
    StructuredLLM,
)
from vendorwake.pipeline.extraction import DEFAULT_MAX_CHARS, ExtractionResult, extract_release
from vendorwake.pipeline.impact import assess_impact, signature_for
from vendorwake.pipeline.migration import PatchSet, propose_patches
from vendorwake.pipeline.report import finalize_assessment
from vendorwake.pipeline.sources import SourceRef
from vendorwake.pipeline.verification import (
    EnvironmentProvider,
    VerificationRun,
    VersionComparison,
    verify_patched,
    verify_versions,
)


def extract(
    text: str,
    *,
    vendor: str,
    package: str,
    llm: StructuredLLM,
    version: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ExtractionResult:
    """Step 1: one release of vendor release notes -> grounded ChangeExtraction (+ grounding, calls)."""
    return extract_release(
        text, vendor=vendor, package=package, llm=llm, version=version, max_chars=max_chars
    )


def analyze(
    repo: RepoRef,
    files: dict[str, str],
    *,
    commit_sha: str | None = None,
    analyzed_at: datetime | None = None,
) -> RepositoryInventory:
    """Step 2: repository files -> RepositoryInventory (no model calls)."""
    return build_inventory(repo, files, commit_sha=commit_sha, analyzed_at=analyzed_at)


def installed_version(
    extraction: ChangeExtraction, files: dict[str, str], inventory: RepositoryInventory
) -> str | None:
    sig = signature_for(extraction)
    scan = scan_repository(scope_files(files, inventory.repo.subpath), [sig])[sig.vendor]
    deps = [d for d in scan.dependencies if d.ecosystem == "pypi" and d.package == extraction.package.lower()]
    return detected_version(deps)


def verify_upgrade(
    extraction: ChangeExtraction,
    files: dict[str, str],
    inventory: RepositoryInventory,
    *,
    allowed_repos: str | Iterable[str] | None = None,
    provider: EnvironmentProvider | None = None,
) -> VersionComparison:
    """Step 3: run the repo's tests on the pinned version and on the release (allowlisted repos only)."""
    return verify_versions(
        inventory.repo,
        files,
        package=extraction.package,
        before_version=installed_version(extraction, files, inventory),
        after_version=extraction.version,
        provider=provider,
        allowed_repos=allowed_repos,
    )


def assess(
    extraction: ChangeExtraction,
    source: SourceRef | object,
    files: dict[str, str],
    inventory: RepositoryInventory,
    llm: StructuredLLM | None,
    *,
    comparison: VersionComparison | None = None,
) -> ImpactAssessment:
    """Step 4: deterministic matching + one impact-model call -> ImpactAssessment (no recommendations yet)."""
    return assess_impact(extraction, source, files, inventory, llm, comparison=comparison)


def propose(
    assessment: ImpactAssessment, extraction: ChangeExtraction, files: dict[str, str], llm: StructuredLLM
) -> PatchSet:
    """Step 5: validated unified diffs for affected files (never applied to the repository)."""
    return propose_patches(assessment, extraction, files, llm)


def verify_patches(
    extraction: ChangeExtraction,
    files: dict[str, str],
    inventory: RepositoryInventory,
    patch_set: PatchSet,
    *,
    allowed_repos: str | Iterable[str] | None = None,
    provider: EnvironmentProvider | None = None,
) -> VerificationRun | None:
    """Step 6: run the tests on a temp copy with all patches applied, under the new version."""
    if not patch_set.patched_files:
        return None
    return verify_patched(
        inventory.repo,
        files,
        patch_set.patched_files,
        package=extraction.package,
        before_version=installed_version(extraction, files, inventory),
        after_version=extraction.version,
        provider=provider,
        allowed_repos=allowed_repos,
    )


def finalize(
    assessment: ImpactAssessment,
    extraction: ChangeExtraction,
    inventory: RepositoryInventory,
    patch_set: PatchSet | None = None,
    patched_run: VerificationRun | None = None,
) -> ImpactAssessment:
    """Step 7: attach recommendations and patch verification."""
    return finalize_assessment(assessment, extraction, inventory, patch_set, patched_run)


def run_investigation(
    change_extraction: ChangeExtraction,
    source_doc_meta: SourceRef | object,
    files: dict[str, str],
    inventory: RepositoryInventory,
    llm: StructuredLLM,
    verify: bool = True,
    *,
    allowed_repos: str | Iterable[str] | None = None,
    provider: EnvironmentProvider | None = None,
) -> ImpactAssessment:
    """All steps after extraction, in order. ``verify=False`` skips every sandbox run."""
    comparison = (
        verify_upgrade(change_extraction, files, inventory, allowed_repos=allowed_repos, provider=provider)
        if verify
        else None
    )
    assessment = assess(change_extraction, source_doc_meta, files, inventory, llm, comparison=comparison)
    patch_set = None
    patched_run = None
    if assessment.overall_status in (ImpactStatus.confirmed, ImpactStatus.potential):
        patch_set = propose(assessment, change_extraction, files, llm)
        if verify:
            patched_run = verify_patches(
                change_extraction, files, inventory, patch_set, allowed_repos=allowed_repos, provider=provider
            )
    return finalize(assessment, change_extraction, inventory, patch_set, patched_run)
