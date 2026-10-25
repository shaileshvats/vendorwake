"""Final assembly of an ImpactAssessment (recommendations + patch verification) and a Markdown rendering."""

from __future__ import annotations

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from vendorwake.domain.contracts import (
    ChangeExtraction,
    EvidenceEdge,
    EvidenceItem,
    EvidenceKind,
    EvidenceNode,
    ImpactAssessment,
    ImpactStatus,
    Recommendation,
    RepositoryInventory,
    VerificationStatus,
)
from vendorwake.pipeline.migration import PatchSet
from vendorwake.pipeline.verification import VerificationRun


def dependency_recommendations(
    extraction: ChangeExtraction, inventory: RepositoryInventory, assessment: ImpactAssessment
) -> list[Recommendation]:
    """Deterministic: if a manifest range admits the breaking release, recommend an upper bound."""
    if assessment.overall_status not in (ImpactStatus.confirmed, ImpactStatus.potential):
        return []
    try:
        release = Version(extraction.version)
    except InvalidVersion:
        return []
    recs = []
    for dep in inventory.dependencies:
        if dep.ecosystem != "pypi" or dep.package != extraction.package.lower() or dep.pinned_version:
            continue
        try:
            admits = dep.version_spec is None or release in SpecifierSet(dep.version_spec)
        except InvalidSpecifier:
            continue
        if admits:
            recs.append(
                Recommendation(
                    recommendation_type="dependency_pin",
                    description=(
                        f"{dep.manifest_path} allows {extraction.package} {extraction.version} "
                        f"({dep.version_spec or 'unpinned'}). Add '<{release.major}' (or an exact pin) "
                        "until the "
                        "code changes are merged and verified."
                    ),
                    file_path=dep.manifest_path,
                )
            )
    return recs


def finalize_assessment(
    assessment: ImpactAssessment,
    extraction: ChangeExtraction,
    inventory: RepositoryInventory,
    patch_set: PatchSet | None = None,
    patched_run: VerificationRun | None = None,
) -> ImpactAssessment:
    """Attach recommendations (with the patched-copy verification) and the patched test node to the graph."""
    recs = list(dependency_recommendations(extraction, inventory, assessment))
    evidence = list(assessment.evidence)
    nodes = list(assessment.graph.nodes)
    edges = list(assessment.graph.edges)
    calls = list(assessment.model_calls)
    review = assessment.requires_human_review
    if patch_set is not None:
        calls += patch_set.model_calls
        for rec in patch_set.recommendations:
            if rec.recommendation_type == "code_change" and patched_run is not None:
                rec = rec.model_copy(update={"verification": patched_run.result})
            recs.append(rec)
            if rec.recommendation_type == "review":
                review = True
    if patched_run is not None and patched_run.result.status != VerificationStatus.not_testable:
        evidence.append(
            EvidenceItem(
                kind=EvidenceKind.test_result,
                deterministic=True,
                description=(
                    f"patched copy under {extraction.package}=={extraction.version}: "
                    f"{patched_run.result.status.value}, "
                    f"{len(patched_run.failures)} failing of {patched_run.tests}"
                ),
                quote="; ".join(f"{f.test_id}: {f.message}" for f in patched_run.failures)[:1000] or None,
            )
        )
        index = len(evidence) - 1
        nodes.append(EvidenceNode(id="test:patched", type="test", label="tests on the patched copy"))
        for path in sorted((patch_set.patched_files if patch_set else {}).keys()):
            if any(n.id == f"file:{path}" for n in nodes):
                edges.append(
                    EvidenceEdge(
                        source=f"file:{path}",
                        target="test:patched",
                        label="patch verified by",
                        evidence_index=index,
                    )
                )
        if patched_run.result.status != VerificationStatus.passed:
            review = True
    return assessment.model_copy(
        update={
            "recommendations": recs,
            "evidence": evidence,
            "graph": assessment.graph.model_copy(update={"nodes": nodes, "edges": edges}),
            "model_calls": calls,
            "requires_human_review": review,
        }
    )


def render_markdown(assessment: ImpactAssessment, extraction: ChangeExtraction) -> str:
    """Human-readable report (for PR bodies, Backboard seeding, logs). Only facts from the assessment."""
    out = [
        f"## {extraction.vendor} {extraction.package} {extraction.version}: "
        f"{assessment.overall_status.value}",
        "",
        assessment.summary,
        "",
        f"Severity: **{assessment.severity.value}** | model confidence: {assessment.confidence:.2f}"
        + (" | **needs human review**" if assessment.requires_human_review else ""),
        "",
        "| Status | File | Line | Symbol | Change |",
        "|---|---|---|---|---|",
    ]
    for comp in assessment.affected:
        out.append(
            f"| {comp.status.value} | `{comp.file_path}` | {comp.line or ''} | `{comp.symbol}` "
            f"| {comp.change_title} |"
        )
    for rec in assessment.recommendations:
        verified = f" (verification: {rec.verification.status.value})" if rec.verification else ""
        out += ["", f"### {rec.recommendation_type}: {rec.file_path or ''}{verified}", "", rec.description]
        if rec.patch:
            out += ["", "```diff", rec.patch.rstrip("\n"), "```"]
    return "\n".join(out) + "\n"
