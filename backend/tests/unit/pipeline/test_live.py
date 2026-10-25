"""Real model calls. Skipped unless VW_LLM_API_KEY is set; free-tier budget: 2 requests."""

import os

import pytest

from vendorwake.core.config import Settings
from vendorwake.domain.contracts import ChangeType
from vendorwake.llm import OpenAICompatibleLLM
from vendorwake.pipeline import extract

from .test_extraction import INJECTION

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("VW_LLM_API_KEY"), reason="needs VW_LLM_API_KEY"),
]


@pytest.fixture(scope="module")
def llm():
    return OpenAICompatibleLLM.from_settings(Settings(_env_file=None))


def test_live_extraction_is_valid_and_grounded(llm, changelog):
    result = extract(changelog, vendor="stripe", package="stripe", llm=llm)
    ex = result.extraction
    print(result.model_calls, result.flags, result.grounding)
    assert ex.version == "16.0.0" and result.chunks == 1
    symbols = {s for c in ex.changes for s in c.affected_symbols}
    assert any("Reversal" in s for s in symbols)
    assert any(
        c.change_type in (ChangeType.removal, ChangeType.breaking, ChangeType.rename) for c in ex.changes
    )
    assert all(c.input_tokens is not None for c in result.model_calls)


def test_live_injection_does_not_suppress_changes(llm, changelog):
    injected = changelog.replace("* [#1904]", INJECTION.strip() + "\n* [#1904]", 1)
    result = extract(injected, vendor="stripe", package="stripe", llm=llm)
    print(result.flags)
    assert result.extraction.requires_human_review is True
    assert len(result.extraction.changes) >= 1
