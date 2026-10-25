import os

import pytest

from vendorwake.domain.contracts import ChangeExtraction, ImpactStatus, RepoRef, VerificationStatus
from vendorwake.pipeline import analyze, extract, run_investigation
from vendorwake.pipeline.report import render_markdown

from .conftest import stripe_16_extraction_output
from .fakes import FakeLLM
from .test_impact import SOURCE, keep_all
from .test_migration import migration_responder


def test_run_investigation_without_sandbox(fixture_files, repo):
    ex = ChangeExtraction.model_validate(stripe_16_extraction_output())
    inv = analyze(repo, fixture_files)
    llm = FakeLLM(impact=keep_all, migration=migration_responder(fixture_files))
    a = run_investigation(ex, SOURCE, fixture_files, inv, llm, verify=False)
    assert a.overall_status == ImpactStatus.confirmed
    assert {r.file_path for r in a.recommendations if r.recommendation_type == "code_change"} == {
        "billing/payouts.py",
        "billing/customers.py",
        "billing/invoices.py",
    }
    assert all(r.verification is None for r in a.recommendations)
    assert [c.agent for c in a.model_calls] == ["impact", "migration", "migration", "migration"]
    assert "billing/payouts.py" in render_markdown(a, ex)


def test_steps_compose_from_extraction(changelog, fixture_files, repo):
    llm = FakeLLM(
        extraction=stripe_16_extraction_output(),
        impact=keep_all,
        migration=migration_responder(fixture_files),
    )
    result = extract(changelog, vendor="stripe", package="stripe", llm=llm)
    a = run_investigation(
        result.extraction, SOURCE, fixture_files, analyze(repo, fixture_files), llm, verify=False
    )
    assert a.overall_status == ImpactStatus.confirmed
    assert ImpactAssessmentRoundTrip(a)


def ImpactAssessmentRoundTrip(a):  # noqa: N802 - tiny helper: activities pass these as JSON
    return type(a).model_validate_json(a.model_dump_json()) == a


@pytest.mark.sandbox
@pytest.mark.skipif(os.environ.get("VW_RUN_SANDBOX") != "1", reason="set VW_RUN_SANDBOX=1 (installs stripe)")
def test_real_sandbox_before_after_and_patched(fixture_files, tmp_path):
    from vendorwake.pipeline.verification import PipVenvProvider

    sub = "fixtures/stripe-billing-demo"
    files = {f"{sub}/{p}": c for p, c in fixture_files.items()}
    repo = RepoRef(owner="shaileshvats", name="vendorwake", subpath=sub)
    pip_args = os.environ.get("VW_SANDBOX_PIP_ARGS", "").split()
    provider = PipVenvProvider(os.environ.get("VW_SANDBOX_VENV_DIR") or tmp_path / "venvs", pip_args=pip_args)
    ex = ChangeExtraction.model_validate(stripe_16_extraction_output())
    llm = FakeLLM(impact=keep_all, migration=migration_responder(files))
    a = run_investigation(
        ex,
        SOURCE,
        files,
        analyze(repo, files),
        llm,
        allowed_repos=f"shaileshvats/vendorwake/{sub}",
        provider=provider,
    )
    statuses = {(c.file_path.removeprefix(sub + "/"), c.line): c.status for c in a.affected}
    assert statuses[("billing/payouts.py", 6)] == ImpactStatus.confirmed
    assert statuses[("billing/customers.py", 8)] == ImpactStatus.confirmed  # proven only by the failing test
    assert statuses[("billing/invoices.py", 10)] == ImpactStatus.confirmed
    assert statuses[("billing/catalog.py", 3)] == ImpactStatus.unaffected
    patched = [r for r in a.recommendations if r.recommendation_type == "code_change"]
    assert len(patched) == 3
    assert all(r.verification and r.verification.status == VerificationStatus.passed for r in patched)
    tests = [e for e in a.evidence if e.kind.value == "test_result"]
    assert any("before run" in e.description and "passed" in e.description for e in tests)
