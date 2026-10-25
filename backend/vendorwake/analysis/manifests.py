"""Dependency manifest and lockfile parsers. Pure functions over file contents; nothing is executed."""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from vendorwake.domain.contracts import DependencyFinding

_EXACT_SEMVER = re.compile(r"^=?v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)$")
_EXACT_PEP440 = re.compile(r"^(?:===?)\s*([0-9][0-9A-Za-z.+!\-]*)$")
_REQ_FILE = re.compile(r"(^|/)requirements[^/]*\.(txt|in)$")


def normalize_package(ecosystem: str, name: str) -> str:
    return canonicalize_name(name) if ecosystem == "pypi" else name.strip().lower()


def _pep440_pin(spec: str) -> str | None:
    """'==1.2.3' -> '1.2.3'. Wildcards, ranges and multiple clauses are not pins."""
    spec = spec.strip()
    if "," in spec or "*" in spec:
        return None
    match = _EXACT_PEP440.match(spec)
    return match.group(1) if match else None


def _line_of(content: str, name: str) -> int | None:
    pattern = re.compile(rf"""(^|[\s"'\[{{,]){re.escape(name)}([\s"'=<>!~\[;:,@]|$)""", re.IGNORECASE)
    for number, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            return number
    return None


def _from_pep508(text: str, manifest_path: str, line: int | None) -> DependencyFinding | None:
    try:
        req = Requirement(text)
    except InvalidRequirement:
        return None
    spec = str(req.specifier) or None
    return DependencyFinding(
        ecosystem="pypi",
        package=normalize_package("pypi", req.name),
        version_spec=spec,
        pinned_version=_pep440_pin(spec) if spec else None,
        manifest_path=manifest_path,
        line=line,
    )


def parse_requirements(path: str, content: str) -> list[DependencyFinding]:
    findings: list[DependencyFinding] = []
    for number, raw in enumerate(content.splitlines(), start=1):
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http://", "https://", "file:")):
            continue  # comments, options (-r/-e/--hash/...), and URL requirements
        finding = _from_pep508(line, path, number)
        if finding:
            findings.append(finding)
    return findings


def _poetry_spec(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return value["version"]
    return None


def _poetry_pin(spec: str | None) -> str | None:
    if not spec:
        return None
    if re.fullmatch(r"\d+(\.\d+)*([a-z]+\d*)?", spec.strip()):
        return spec.strip()  # a bare version is an exact constraint in Poetry
    return _pep440_pin(spec)


def parse_pyproject(path: str, content: str) -> list[DependencyFinding]:
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []
    findings: list[DependencyFinding] = []
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    pep508: list[str] = list(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies", {}) or {}).values():
        pep508.extend(group or [])
    for group in (data.get("dependency-groups", {}) or {}).values():
        pep508.extend(item for item in (group or []) if isinstance(item, str))
    for text in pep508:
        if not isinstance(text, str):
            continue
        name_match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._\-]*)", text)
        finding = _from_pep508(text, path, _line_of(content, name_match.group(1)) if name_match else None)
        if finding:
            findings.append(finding)

    poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
    tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    tables += [
        g.get("dependencies", {}) for g in (poetry.get("group", {}) or {}).values() if isinstance(g, dict)
    ]
    for table in tables:
        for name, value in (table or {}).items():
            if name.lower() == "python":
                continue
            spec = _poetry_spec(value)
            findings.append(
                DependencyFinding(
                    ecosystem="pypi",
                    package=normalize_package("pypi", name),
                    version_spec=spec,
                    pinned_version=_poetry_pin(spec),
                    manifest_path=path,
                    line=_line_of(content, name),
                )
            )
    return findings


def parse_package_json(path: str, content: str) -> list[DependencyFinding]:
    try:
        data = json.loads(content)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    findings: list[DependencyFinding] = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            spec_str = spec if isinstance(spec, str) else None
            match = _EXACT_SEMVER.match(spec_str.strip()) if spec_str else None
            findings.append(
                DependencyFinding(
                    ecosystem="npm",
                    package=normalize_package("npm", name),
                    version_spec=spec_str,
                    pinned_version=match.group(1) if match else None,
                    manifest_path=path,
                    line=_line_of(content, name),
                )
            )
    return findings


def parse_package_lock(path: str, content: str) -> list[DependencyFinding]:
    try:
        data = json.loads(content)
    except ValueError:
        return []
    findings: list[DependencyFinding] = []
    packages = data.get("packages") if isinstance(data, dict) else None
    if isinstance(packages, dict):  # lockfileVersion 2/3
        for key, meta in packages.items():
            if not key.startswith("node_modules/") or "/node_modules/" in key or not isinstance(meta, dict):
                continue  # only top-level installs
            name = key.removeprefix("node_modules/")
            if isinstance(meta.get("version"), str):
                findings.append(_locked("npm", name, meta["version"], path))
    elif isinstance(data, dict) and isinstance(data.get("dependencies"), dict):  # lockfileVersion 1
        for name, meta in data["dependencies"].items():
            if isinstance(meta, dict) and isinstance(meta.get("version"), str):
                findings.append(_locked("npm", name, meta["version"], path))
    return findings


def parse_toml_lock(path: str, content: str) -> list[DependencyFinding]:
    """poetry.lock and uv.lock both list ``[[package]]`` tables with name and version."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []
    findings = []
    for pkg in data.get("package", []) or []:
        if isinstance(pkg, dict) and isinstance(pkg.get("name"), str) and isinstance(pkg.get("version"), str):
            findings.append(_locked("pypi", pkg["name"], pkg["version"], path))
    return findings


def _locked(ecosystem: str, name: str, version: str, path: str) -> DependencyFinding:
    return DependencyFinding(
        ecosystem=ecosystem,
        package=normalize_package(ecosystem, name),
        version_spec=None,
        pinned_version=version,
        manifest_path=path,
        line=None,
    )


def is_lockfile(path: str) -> bool:
    return posixpath.basename(path) in {"package-lock.json", "poetry.lock", "uv.lock"}


def parse_manifests(files: dict[str, str]) -> list[DependencyFinding]:
    """All dependency declarations (manifests first, then lockfiles) found in ``files``."""
    findings: list[DependencyFinding] = []
    for path in sorted(files):
        name = posixpath.basename(path)
        if "node_modules/" in path:
            continue
        content = files[path]
        if _REQ_FILE.search(path):
            findings += parse_requirements(path, content)
        elif name == "pyproject.toml":
            findings += parse_pyproject(path, content)
        elif name == "package.json":
            findings += parse_package_json(path, content)
        elif name == "package-lock.json":
            findings += parse_package_lock(path, content)
        elif name in ("poetry.lock", "uv.lock"):
            findings += parse_toml_lock(path, content)
    return findings
