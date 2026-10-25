"""Sentry spans around activities.

Every activity runs inside a Sentry transaction. Investigation activities continue one trace whose id the
workflow generated (and the investigation row stores), so the whole investigation - including the gen_ai
spans the model adapter creates inside these transactions - shows up as one trace. No-op without a DSN.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sentry_sdk

ACTIVITY_OP = "vendorwake.activity"


@contextmanager
def activity_span(name: str, *, trace_id: str | None = None, **attributes: Any) -> Iterator[Any]:
    if not sentry_sdk.is_initialized():
        yield None
        return
    if trace_id:
        # sentry-trace header format: <trace_id>-<parent_span_id>-<sampled>
        headers = {"sentry-trace": f"{trace_id}-{secrets.token_hex(8)}-1"}
        transaction = sentry_sdk.continue_trace(headers, op=ACTIVITY_OP, name=name)
    else:
        transaction = sentry_sdk.continue_trace({}, op=ACTIVITY_OP, name=name)
    with sentry_sdk.start_transaction(transaction) as tx:
        for key, value in attributes.items():
            if value is not None:
                tx.set_tag(key, str(value))
        try:
            yield tx
        except BaseException:
            tx.set_status("internal_error")
            raise
