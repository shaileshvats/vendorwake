from datetime import timedelta

import pytest
from temporalio.exceptions import ApplicationError

from vendorwake.llm.errors import RateLimitedError
from vendorwake.workflows import types as t
from vendorwake.workflows.activities import _translate


def _raise(exc: Exception):
    def fn():
        raise exc

    return fn


def test_model_rate_limit_is_retryable_after_the_quota_window() -> None:
    err = RateLimitedError("slow down", agent="extraction", model="gemma-4-31b-it", retry_after_s=37.0)
    with pytest.raises(ApplicationError) as info:
        _translate(_raise(err))
    assert info.value.type == t.MODEL_RATE_LIMITED_RETRY
    assert info.value.non_retryable is False
    assert info.value.next_retry_delay == timedelta(seconds=37)
    assert t.MODEL_RATE_LIMITED_RETRY not in t.NON_RETRYABLE_ERRORS


def test_rate_limit_without_hint_waits_a_full_minute() -> None:
    err = RateLimitedError("slow down", agent="impact", model="m", retry_after_s=None)
    with pytest.raises(ApplicationError) as info:
        _translate(_raise(err))
    assert info.value.next_retry_delay == timedelta(seconds=60)
