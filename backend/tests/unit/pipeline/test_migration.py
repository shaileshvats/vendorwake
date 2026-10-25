import difflib

import pytest

from vendorwake.domain.contracts import ChangeExtraction
from vendorwake.pipeline.impact import assess_impact
from vendorwake.pipeline.migration import PatchError, propose_patches, validate_patch
from vendorwake.pipeline.run import analyze

from .conftest import stripe_16_extraction_output
from .fakes import FakeLLM
from .helpers import breaking_comparison
from .test_impact import SOURCE, keep_all

FIXES = {
    "billing/payouts.py": [("stripe.Reversal", "stripe.TransferReversal")],
    "billing/customers.py": [("retrieve(customer=customer_id)", "retrieve(customer_id)")],
    "billing/invoices.py": [
        (
            '    return invoice.request("post", f"/v1/invoices/{invoice.id}/send")',
            '    response = client.raw_request("post", f"/v1/invoices/{invoice.id}/send")\n'
            '    return client.deserialize(response, api_mode="V1")',
        )
    ],
}


def model_diff(files, path, *, sloppy=False):
    original = files[path]
    patched = original
    for old, new in FIXES[path.split("stripe-billing-demo/")[-1]]:
        patched = patched.replace(old, new)
    lines = list(
        difflib.unified_diff(
            original.splitlines(True), patched.splitlines(True), f"a/{path}", f"b/{path}", n=1
        )
    )
    diff = "".join(lines)
    if sloppy:  # models often get hunk ranges wrong; content matching must still work
        diff = "\n".join("@@ -1,9 +1,9 @@" if line.startswith("@@") else line for line in diff.split("\n"))
    return diff


def migration_responder(files, **kw):
    def respond(system, user):
        path = user.split("File: ", 1)[1].split("\n", 1)[0]
        return {
            "file_path": path,
            "diff": model_diff(files, path, **kw),
            "explanation": f"Adapt {path} to 16.0.0.",
        }

    return respond


def test_validate_patch_accepts_sloppy_headers_and_emits_canonical_diff(fixture_files):
    path = "billing/payouts.py"
    patched, diff = validate_patch(
        path, fixture_files[path], model_diff(fixture_files, path, sloppy=True), {6, 11}
    )
    assert "stripe.Reversal" not in patched and patched.count("stripe.TransferReversal") == 2
    assert diff.startswith("--- a/billing/payouts.py\n+++ b/billing/payouts.py\n@@ -")


def test_validate_patch_rejects_unrelated_lines(fixture_files):
    path = "billing/payouts.py"
    with pytest.raises(PatchError, match="not affected lines"):
        validate_patch(path, fixture_files[path], model_diff(fixture_files, path), {6})
    bad = "--- a/billing/payouts.py\n+++ b/billing/payouts.py\n@@ -6 +6 @@\n-def nothing_like_this():\n+x\n"
    with pytest.raises(PatchError, match="does not match"):
        validate_patch(path, fixture_files[path], bad, {6})
    other = "--- a/setup.py\n+++ b/setup.py\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(PatchError, match="other files"):
        validate_patch(path, fixture_files[path], other, {6})


def _assessment(files, repo):
    ex = ChangeExtraction.model_validate(stripe_16_extraction_output())
    inv = analyze(repo, files)
    return ex, assess_impact(
        ex, SOURCE, files, inv, FakeLLM(impact=keep_all), comparison=breaking_comparison()
    )


def test_propose_patches_one_per_affected_file(fixture_files, repo):
    ex, assessment = _assessment(fixture_files, repo)
    llm = FakeLLM(migration=migration_responder(fixture_files, sloppy=True))
    patch_set = propose_patches(assessment, ex, fixture_files, llm)
    assert sorted(patch_set.patched_files) == sorted(FIXES)
    assert all(r.recommendation_type == "code_change" and r.patch for r in patch_set.recommendations)
    assert llm.agents() == ["migration"] * 3
    assert fixture_files["billing/payouts.py"].count("stripe.Reversal") == 2  # caller's files untouched


def test_rejected_patch_becomes_review_after_one_retry(fixture_files, repo):
    ex, assessment = _assessment(fixture_files, repo)
    bad = {"file_path": "x", "diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-nope\n+yes\n", "explanation": "x"}
    llm = FakeLLM(migration=bad)
    patch_set = propose_patches(assessment, ex, fixture_files, llm)
    assert patch_set.patched_files == {}
    assert {r.recommendation_type for r in patch_set.recommendations} == {"review"}
    assert len(llm.calls) == 6  # 3 files x (first try + one retry)
    assert "previous diff was rejected" in llm.calls[1]["user"]
