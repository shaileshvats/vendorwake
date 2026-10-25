from vendorwake.domain.contracts import ChangeExtraction, ImpactStatus, RepoRef
from vendorwake.pipeline.impact import assess_impact
from vendorwake.pipeline.run import analyze as inventory_for

from .conftest import stripe_16_extraction_output
from .fakes import FakeLLM
from .helpers import breaking_comparison

SOURCE = {
    "url": "https://raw.githubusercontent.com/stripe/stripe-python/master/CHANGELOG.md",
    "content_sha256": "ab" * 32,
}


def extraction() -> ChangeExtraction:
    return ChangeExtraction.model_validate(stripe_16_extraction_output())


def narrative(components, **kw):
    return {
        "summary": kw.get("summary", "Three breaking changes hit billing."),
        "severity": "high",
        "confidence": 0.8,
        "components": components,
        "requires_human_review": False,
    }


def keep_all(system, user):
    import json
    import re

    payload = json.loads(re.search(r"<untrusted_findings>\n(.*)\n</untrusted_findings>", user, re.S).group(1))
    return narrative(
        [{"index": c["index"], "status": c["status"], "rationale": "ok"} for c in payload["components"]]
    )


def by_location(assessment):
    return {(a.file_path, a.line): a.status for a in assessment.affected}


def test_static_only_statuses(fixture_files, repo):
    inv = inventory_for(repo, fixture_files)
    llm = FakeLLM(impact=keep_all)
    a = assess_impact(extraction(), SOURCE, fixture_files, inv, llm)
    status = by_location(a)
    assert status[("billing/payouts.py", 6)] == ImpactStatus.confirmed  # exact renamed symbol
    assert status[("billing/payouts.py", 11)] == ImpactStatus.confirmed
    assert status[("billing/invoices.py", 10)] == ImpactStatus.potential  # receiver type only inferred
    assert status[("billing/customers.py", 8)] == ImpactStatus.potential  # keyword pattern, unproven
    assert status[("billing/catalog.py", 3)] == ImpactStatus.unaffected
    assert a.overall_status == ImpactStatus.confirmed
    assert llm.agents() == ["impact"]
    assert not any(a.symbol == "stripe.TransferReversal" for a in a.affected)  # replacement is not a hit


def test_failing_tests_confirm_matching_sites(fixture_files, repo):
    inv = inventory_for(repo, fixture_files)
    a = assess_impact(
        extraction(), SOURCE, fixture_files, inv, FakeLLM(impact=keep_all), comparison=breaking_comparison()
    )
    status = by_location(a)
    assert status[("billing/invoices.py", 10)] == ImpactStatus.confirmed
    assert status[("billing/customers.py", 8)] == ImpactStatus.confirmed
    comp = next(c for c in a.affected if c.file_path == "billing/customers.py")
    kinds = {e.kind.value for e in comp.evidence}
    assert {"vendor_source", "code_reference", "dependency", "version_check", "test_result"} <= kinds
    assert all(e.deterministic for e in comp.evidence if e.kind.value != "model_inference")


def test_model_cannot_upgrade_but_can_downgrade(fixture_files, repo):
    inv = inventory_for(repo, fixture_files)

    def judge(system, user):
        out = keep_all(system, user)
        first_confirmed = next(c for c in out["components"] if c["status"] == "confirmed")
        for c in out["components"]:
            c["status"] = "confirmed"  # tries to upgrade everything
        first_confirmed["status"] = "potential"  # and downgrades one confirmed finding
        return out

    a = assess_impact(extraction(), SOURCE, fixture_files, inv, FakeLLM(impact=judge))
    status = by_location(a)
    assert status[("billing/invoices.py", 10)] == ImpactStatus.potential
    assert status[("billing/customers.py", 8)] == ImpactStatus.potential
    assert "tried to upgrade" in a.summary
    assert status[("billing/payouts.py", 6)] == ImpactStatus.potential  # downgrade applied
    assert a.requires_human_review is True  # a confirmed finding was downgraded by the model


def test_graph_edges_point_at_evidence(fixture_files, repo):
    inv = inventory_for(repo, fixture_files)
    a = assess_impact(
        extraction(), SOURCE, fixture_files, inv, FakeLLM(impact=keep_all), comparison=breaking_comparison()
    )
    ids = {n.id for n in a.graph.nodes}
    types = {n.type for n in a.graph.nodes}
    assert {"vendor_source", "change", "symbol", "integration", "file", "test", "risk"} <= types
    for edge in a.graph.edges:
        assert edge.source in ids and edge.target in ids
        assert edge.evidence_index is not None and 0 <= edge.evidence_index < len(a.evidence)
    src = next(e for e in a.evidence if e.kind.value == "vendor_source")
    assert src.source_url == SOURCE["url"] and src.content_sha256 == SOURCE["content_sha256"]


def test_vendor_not_used_is_unaffected_without_model_call():
    files = {"app.py": "import requests\n", "requirements.txt": "requests==2.32.3\n"}
    repo = RepoRef(owner="o", name="n")
    llm = FakeLLM()
    a = assess_impact(extraction(), SOURCE, files, inventory_for(repo, files), llm)
    assert a.overall_status == ImpactStatus.unaffected and a.affected == [] and llm.calls == []


def test_llm_failure_falls_back_to_deterministic_summary(fixture_files, repo):
    from vendorwake.llm.errors import RateLimitedError

    class Failing:
        def generate(self, **kw):
            raise RateLimitedError("rate limited", agent="impact", model="m", retry_after_s=None)

    a = assess_impact(extraction(), SOURCE, fixture_files, inventory_for(repo, fixture_files), Failing())
    assert a.overall_status == ImpactStatus.confirmed and a.requires_human_review and a.model_calls == []
