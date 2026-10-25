"""Builders for deterministic verification outcomes used by impact tests."""

from vendorwake.domain.contracts import VerificationResult, VerificationStatus
from vendorwake.pipeline.verification import TestFailure, VerificationRun, VersionComparison


def run(label: str, status: VerificationStatus, failures=(), tests=4, version="16.0.0") -> VerificationRun:
    return VerificationRun(
        label=label,
        result=VerificationResult(
            status=status,
            command="python -m pytest",
            exit_code=0 if status == VerificationStatus.passed else 1,
            stdout_tail="",
            stderr_tail="",
            duration_s=0.1,
            environment="test",
            before_version="15.6.1",
            after_version=version if label != "before" else None,
        ),
        failures=list(failures),
        tests=tests,
    )


def breaking_comparison(prefix: str = "") -> VersionComparison:
    def loc(path, line):
        return (f"{prefix}{path}", line)

    failures = [
        TestFailure(
            test_id="tests.test_payouts", message="AttributeError", locations=[loc("billing/payouts.py", 6)]
        ),
        TestFailure(
            test_id="tests.test_customers::test_billing_email",
            message="TypeError: positional-only arguments passed as keyword arguments: 'customer'",
            locations=[loc("tests/test_customers.py", 8), loc("billing/customers.py", 8)],
        ),
        TestFailure(
            test_id="tests.test_invoices::test_send_invoice",
            message="AttributeError: request",
            locations=[loc("billing/invoices.py", 10)],
        ),
    ]
    return VersionComparison(
        before=run("before", VerificationStatus.passed),
        after=run("after", VerificationStatus.failed, failures),
    )
