"""Repository inventory: which vendors a repository integrates with, where, and at which version.

Pure functions over ``dict[path, content]``. Detection combines manifests, imports, API hosts and env-var
references, so a vendor is found even when it is called over raw HTTP or vendored without a manifest entry.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from datetime import UTC, datetime

from vendorwake.analysis.js_scan import JS_EXTENSIONS, scan_js
from vendorwake.analysis.manifests import is_lockfile, normalize_package, parse_manifests
from vendorwake.analysis.python_scan import CodeSite, scan_python
from vendorwake.analysis.text_scan import scan_text
from vendorwake.catalog.signatures import SIGNATURES
from vendorwake.domain.contracts import (
    DependencyFinding,
    IntegrationFinding,
    ReferenceSite,
    RepoRef,
    RepositoryInventory,
    VendorSignature,
)

MAX_REFERENCES_PER_VENDOR = 500
_SKIP_DIRS = (
    "node_modules/",
    ".git/",
    ".venv/",
    "venv/",
    "site-packages/",
    "dist/",
    "build/",
    "__pycache__/",
)
_TEXT_EXTENSIONS = (
    *JS_EXTENSIONS,
    ".py",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".json",
    ".env",
    ".example",
    ".sample",
    ".sh",
    ".md",
    ".txt",
    ".go",
    ".rb",
    ".java",
    ".kt",
    ".php",
    ".cs",
    ".rs",
)


@dataclass
class VendorScan:
    signature: VendorSignature
    dependencies: list[DependencyFinding] = field(default_factory=list)
    code_sites: list[CodeSite] = field(default_factory=list)  # Python + JS usage sites
    config_sites: list[CodeSite] = field(default_factory=list)  # env vars and API hosts
    parse_errors: dict[str, str] = field(default_factory=dict)

    @property
    def used(self) -> bool:
        return bool(self.dependencies or self.code_sites or self.config_sites)

    def files_using(self) -> set[str]:
        return {s.file_path for s in self.code_sites}


def scope_files(files: dict[str, str], subpath: str = "") -> dict[str, str]:
    """Files under ``subpath`` (keys unchanged). If no key carries the prefix, keys are already relative."""
    prefix = subpath.strip("/")
    if not prefix:
        return dict(files)
    scoped = {p: c for p, c in files.items() if p == prefix or p.startswith(prefix + "/")}
    return scoped or dict(files)


def _skipped(path: str) -> bool:
    return any(part in f"/{path}" for part in (f"/{d}" for d in _SKIP_DIRS))


def _is_text(path: str) -> bool:
    name = posixpath.basename(path)
    return (
        path.endswith(_TEXT_EXTENSIONS)
        or name in ("Dockerfile", "Procfile", ".env")
        or name.startswith(".env")
    )


def scan_repository(
    files: dict[str, str], signatures: list[VendorSignature] | None = None
) -> dict[str, VendorScan]:
    """Per-vendor raw findings, with full code-site detail (used by the impact matcher)."""
    signatures = signatures if signatures is not None else list(SIGNATURES.values())
    dependencies = parse_manifests(files)
    scans = {sig.vendor: VendorScan(signature=sig) for sig in signatures}
    for sig in signatures:
        wanted = {
            (eco, normalize_package(eco, name)) for eco, names in sig.packages.items() for name in names
        }
        scans[sig.vendor].dependencies = [d for d in dependencies if (d.ecosystem, d.package) in wanted]

    for path in sorted(files):
        if _skipped(path):
            continue
        content = files[path]
        if path.endswith(".py"):
            prefixes = {p for sig in signatures for p in sig.import_prefixes}
            py = scan_python(path, content, prefixes)
            for sig in signatures:
                own = [s for s in py.sites if s.vendor_root in sig.import_prefixes]
                scans[sig.vendor].code_sites += own
                if py.parse_error and own:
                    scans[sig.vendor].parse_errors[path] = py.parse_error
        elif path.endswith(JS_EXTENSIONS):
            for sig in signatures:
                npm = sig.packages.get("npm", [])
                if npm:
                    scans[sig.vendor].code_sites += scan_js(path, content, npm, sig.vendor)
        if _is_text(path) and not is_lockfile(path):
            for sig in signatures:
                scans[sig.vendor].config_sites += scan_text(
                    path, content, env_vars=sig.env_vars, api_hosts=sig.api_hosts, vendor_root=sig.vendor
                )
    return scans


def detected_version(dependencies: list[DependencyFinding]) -> str | None:
    """Lockfile version first, then an exact manifest pin. Several distinct versions -> None (ambiguous)."""
    locked = {d.pinned_version for d in dependencies if d.pinned_version and is_lockfile(d.manifest_path)}
    if len(locked) == 1:
        return locked.pop()
    pinned = {d.pinned_version for d in dependencies if d.pinned_version and not is_lockfile(d.manifest_path)}
    if len(pinned) == 1 and not locked:
        return pinned.pop()
    return None


def _integration(scan: VendorScan) -> IntegrationFinding | None:
    if not scan.used:
        return None
    imports = [s for s in scan.code_sites if s.kind == "import"]
    hosts = [s for s in scan.config_sites if s.symbol.startswith("host:")]
    if scan.dependencies and imports:
        kind, confidence = "sdk", 0.95
    elif imports:
        kind, confidence = "sdk", 0.85  # vendored or undeclared dependency
    elif hosts:
        kind, confidence = "http_api", 0.75
    elif scan.dependencies:
        kind, confidence = "sdk", 0.6  # declared but no usage found
    else:
        kind, confidence = "config", 0.4  # only env-var names
    usage = [s for s in scan.code_sites if s.kind in ("import", "attribute", "call")]
    sites = sorted(usage + scan.config_sites, key=lambda s: (s.file_path, s.line))[:MAX_REFERENCES_PER_VENDOR]
    return IntegrationFinding(
        vendor=scan.signature.vendor,
        integration_type=kind,
        packages=scan.dependencies,
        detected_version=detected_version(scan.dependencies),
        references=[
            ReferenceSite(file_path=s.file_path, line=s.line, symbol=s.symbol, snippet=s.snippet)
            for s in sites
        ],
        confidence=confidence,
    )


def build_inventory(
    repo: RepoRef,
    files: dict[str, str],
    *,
    signatures: list[VendorSignature] | None = None,
    commit_sha: str | None = None,
    analyzed_at: datetime | None = None,
) -> RepositoryInventory:
    """Analyse ``files`` (path -> content, already read by the repo reader) into a ``RepositoryInventory``."""
    scoped = scope_files(files, repo.subpath)
    scans = scan_repository(scoped, signatures)
    integrations = [i for i in (_integration(s) for s in scans.values()) if i is not None]
    return RepositoryInventory(
        repo=repo,
        commit_sha=commit_sha,
        analyzed_at=analyzed_at or datetime.now(UTC),
        files_scanned=len(scoped),
        dependencies=parse_manifests(scoped),
        integrations=integrations,
    )
