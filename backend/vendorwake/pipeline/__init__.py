"""Upstream-to-impact pipeline: extraction -> grounding -> analysis -> impact -> migration -> verification."""

from vendorwake.pipeline.run import (
    analyze,
    assess,
    extract,
    finalize,
    propose,
    run_investigation,
    verify_patches,
    verify_upgrade,
)

__all__ = [
    "analyze",
    "assess",
    "extract",
    "finalize",
    "propose",
    "run_investigation",
    "verify_patches",
    "verify_upgrade",
]
