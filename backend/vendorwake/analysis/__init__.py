"""Static repository analysis: manifests, imports, call sites, env vars and API hosts. Nothing is executed."""

from vendorwake.analysis.inventory import (
    VendorScan,
    build_inventory,
    detected_version,
    scan_repository,
    scope_files,
)
from vendorwake.analysis.manifests import parse_manifests
from vendorwake.analysis.python_scan import CodeSite, scan_python

__all__ = [
    "CodeSite",
    "VendorScan",
    "build_inventory",
    "detected_version",
    "parse_manifests",
    "scan_python",
    "scan_repository",
    "scope_files",
]
