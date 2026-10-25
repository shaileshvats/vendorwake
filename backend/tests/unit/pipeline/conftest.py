"""Shared fixtures for pipeline tests: the demo fixture repo, the 16.0.0 changelog, scripted model output."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vendorwake.domain.contracts import RepoRef
from vendorwake.llm.openai_compatible import RESPONSE_CACHE

DATA = Path(__file__).parent / "data"
FIXTURE_REL = "fixtures/stripe-billing-demo"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls the real model endpoint; needs VW_LLM_API_KEY")
    config.addinivalue_line("markers", "sandbox: installs real packages into venvs; needs VW_RUN_SANDBOX=1")


def _fixture_dir() -> Path | None:
    candidates = [Path(p) for p in (os.environ.get("VW_REPO_ROOT"), "/repo") if p]
    candidates += list(Path(__file__).resolve().parents)
    for root in candidates:
        if (root / FIXTURE_REL / "requirements.txt").exists():
            return root / FIXTURE_REL
    return None


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    RESPONSE_CACHE.clear()
    yield
    RESPONSE_CACHE.clear()


@pytest.fixture(scope="session")
def changelog() -> str:
    return (DATA / "stripe-python-16.0.0.md").read_text()


@pytest.fixture(scope="session")
def fixture_files() -> dict[str, str]:
    root = _fixture_dir()
    if root is None:
        pytest.skip("fixtures/stripe-billing-demo not found (set VW_REPO_ROOT)")
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")
    }


@pytest.fixture
def repo() -> RepoRef:
    return RepoRef(owner="acme", name="billing")


QUOTE_REMOVE = "The deprecated `StripeObject.request()` method has been removed."
QUOTE_RENAME = "Rename the `stripe.Reversal` resource class to `stripe.TransferReversal`."
QUOTE_POSITIONAL = "Path parameters must now be passed positionally to service methods."


def stripe_16_extraction_output(**overrides) -> dict:
    """What a well-behaved model returns for the 16.0.0 section (used as scripted FakeLLM output)."""
    data = {
        "vendor": "stripe",
        "package": "stripe",
        "version": "16.0.0",
        "release_date": "2026-09-30",
        "pinned_api_version": "2026-09-30.endive",
        "changes": [
            {
                "title": "Remove deprecated StripeObject.request() method",
                "change_type": "removal",
                "severity": "high",
                "affected_symbols": ["StripeObject.request()"],
                "replacement": "StripeClient.raw_request()",
                "evidence_quote": QUOTE_REMOVE,
            },
            {
                "title": "Rename stripe.Reversal to stripe.TransferReversal",
                "change_type": "rename",
                "severity": "high",
                "affected_symbols": ["`stripe.Reversal`", "stripe.TransferReversal"],
                "replacement": "stripe.TransferReversal",
                "evidence_quote": QUOTE_RENAME,
            },
            {
                "title": "Make path parameters positional-only in all service methods",
                "change_type": "breaking",
                "severity": "high",
                "affected_symbols": [],
                "replacement": None,
                "evidence_quote": QUOTE_POSITIONAL,
            },
        ],
        "requires_human_review": False,
    }
    data.update(overrides)
    return data
