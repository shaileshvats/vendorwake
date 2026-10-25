import sys

import pytest

from vendorwake.domain.contracts import RepoRef, VerificationStatus
from vendorwake.pipeline.verification import (
    is_allowed,
    run_tests,
    verification_requirements,
    verify_versions,
    write_project,
)


class CurrentPython:
    """Uses the interpreter running the tests (already has pytest) instead of building a venv."""

    def __init__(self):
        self.requested = []

    def python_for(self, requirements):
        self.requested.append(requirements)
        return sys.executable, "current interpreter"


PROBE = """
import os, resource


def test_env_is_minimal():
    assert "VW_LLM_API_KEY" not in os.environ
    assert not any("SECRET" in k or "TOKEN" in k for k in os.environ)
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:9"


def test_limits_applied():
    assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] == 512
    assert resource.getrlimit(resource.RLIMIT_CPU)[0] == 240
"""


def test_allowlist_matching():
    allowed = "shaileshvats/vendorwake/fixtures/stripe-billing-demo, acme/billing"
    assert is_allowed(
        RepoRef(owner="ShaileshVats", name="vendorwake", subpath="/fixtures/stripe-billing-demo/"), allowed
    )
    assert not is_allowed(RepoRef(owner="shaileshvats", name="vendorwake"), allowed)
    assert is_allowed(RepoRef(owner="acme", name="billing", subpath="any"), allowed)
    assert not is_allowed(RepoRef(owner="evil", name="billing"), allowed)


def test_not_allowlisted_repo_is_never_run(fixture_files):
    provider = CurrentPython()
    cmp = verify_versions(
        RepoRef(owner="someone", name="else"),
        fixture_files,
        package="stripe",
        before_version="15.6.1",
        after_version="16.0.0",
        provider=provider,
        allowed_repos="acme/billing",
    )
    assert cmp.after.result.status == VerificationStatus.not_testable
    assert "allowlist" in cmp.after.result.stdout_tail and provider.requested == []


def test_requirements_pin_the_package(fixture_files):
    reqs = verification_requirements(fixture_files, package="stripe", version="16.0.0")
    assert reqs == ["pytest>=8", "stripe==16.0.0"]


def test_sandbox_env_has_no_inherited_secrets_and_has_limits(monkeypatch):
    monkeypatch.setenv("VW_LLM_API_KEY", "sk-live-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_leak")
    files = {"test_probe.py": PROBE}
    result = run_tests(files, label="after", package="probe", version="1.0", provider=CurrentPython())
    assert result.result.status == VerificationStatus.passed, result.result.stdout_tail
    assert result.tests == 2 and "network=not isolated" in result.result.environment


def test_failures_are_parsed_with_project_locations():
    files = {
        "lib/mod.py": "def f():\n    raise TypeError('boom')\n",
        "test_mod.py": "from lib.mod import f\n\n\ndef test_f():\n    f()\n",
        "lib/__init__.py": "",
    }
    result = run_tests(files, label="after", package="x", version="1", provider=CurrentPython())
    assert result.result.status == VerificationStatus.failed
    failure = result.failures[0]
    assert "TypeError: boom" in failure.message
    assert ("lib/mod.py", 2) in failure.locations


def test_timeout_kills_and_needs_review():
    files = {"test_slow.py": "import time\n\n\ndef test_slow():\n    time.sleep(30)\n"}
    result = run_tests(files, label="after", package="x", version="1", provider=CurrentPython(), timeout_s=2)
    assert result.result.status == VerificationStatus.requires_human_review
    assert result.result.exit_code is None and "timeout" in result.result.stderr_tail


def test_write_project_refuses_traversal(tmp_path):
    with pytest.raises(ValueError):
        write_project({"../escape.py": "x"}, tmp_path / "w")
    with pytest.raises(ValueError):
        write_project({"/etc/passwd": "x"}, tmp_path / "w")
