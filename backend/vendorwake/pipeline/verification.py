"""Sandboxed test verification: run a repository's tests under the old and new vendor version.

What the sandbox is (and is not):
- Only repositories in ``settings.verification_allowed_repos`` are ever executed; anything else is
  ``not_testable`` without touching the code.
- Each run uses a fresh temp copy of the files and a venv built from the repo's requirements with the vendor
  package pinned, installed with ``--only-binary=:all:`` (no setup.py / build backends run).
- The child runs with a minimal environment (nothing inherited: no API keys, tokens or DSNs), its own HOME and
  TMPDIR, a process-group timeout, and ``setrlimit`` caps on CPU, address space, file size and open files.
- Network is NOT isolated (no containers on Render). Proxy variables point at a closed local port, which stops
  well-behaved HTTP clients only; the ``environment`` string of every result says so.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET  # noqa: S405 - parsing our own pytest junit output
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from vendorwake.analysis.manifests import normalize_package, parse_pyproject, parse_requirements
from vendorwake.domain.contracts import RepoRef, VerificationResult, VerificationStatus

TEST_TIMEOUT_S = 300
INSTALL_TIMEOUT_S = 600
TAIL_CHARS = 4000
NETWORK_NOTE = "network=not isolated (proxy env -> 127.0.0.1:9, best effort only)"
_DEAD_PROXY = "http://127.0.0.1:9"
_LOCATION = re.compile(r'(?:^|\s|")([\w./\-]+\.py)(?::|", line )(\d+)', re.MULTILINE)

# (resource name, soft=hard limit). Applied in the child before exec; unsupported limits are skipped.
TEST_LIMITS = {
    "RLIMIT_CPU": 240,
    "RLIMIT_AS": 2 * 1024**3,
    "RLIMIT_FSIZE": 64 * 1024**2,
    "RLIMIT_NOFILE": 512,
}
INSTALL_LIMITS = {
    "RLIMIT_CPU": 600,
    "RLIMIT_AS": 4 * 1024**3,
    "RLIMIT_FSIZE": 512 * 1024**2,
    "RLIMIT_NOFILE": 1024,
}


class TestFailure(BaseModel):
    __test__ = False  # not a pytest test class

    test_id: str
    message: str
    locations: list[tuple[str, int]]  # project files/lines in the traceback (repo-relative paths)


class VerificationRun(BaseModel):
    label: str  # "before" | "after" | "patched"
    result: VerificationResult
    failures: list[TestFailure] = []
    tests: int = 0


class VersionComparison(BaseModel):
    before: VerificationRun
    after: VerificationRun

    @property
    def breaks(self) -> bool:
        """True when the suite passes on the old version and fails on the new one."""
        return (
            self.before.result.status == VerificationStatus.passed
            and self.after.result.status == VerificationStatus.failed
        )


class EnvironmentProvider(Protocol):
    def python_for(self, requirements: list[str]) -> tuple[str, str]:
        """Return (python executable, description) of an environment with ``requirements`` installed."""
        ...


# ---------------------------------------------------------------------------- sandbox helpers


def _limits(limits: dict[str, int]):
    def apply() -> None:  # runs in the child between fork and exec
        import resource

        for name, value in limits.items():
            res = getattr(resource, name, None)
            if res is None:
                continue
            try:
                resource.setrlimit(res, (value, value))
            except (ValueError, OSError):
                pass  # e.g. RLIMIT_AS is not settable on macOS

    return apply


def _limits_note(limits: dict[str, int]) -> str:
    return "rlimits " + ",".join(f"{k.removeprefix('RLIMIT_').lower()}={v}" for k, v in limits.items())


def minimal_env(*, home: str, path_prefix: str | None = None, offline: bool = True) -> dict[str, str]:
    """A from-scratch environment. Nothing from ``os.environ`` is inherited."""
    path = "/usr/local/bin:/usr/bin:/bin"
    env = {
        "PATH": f"{path_prefix}:{path}" if path_prefix else path,
        "HOME": home,
        "TMPDIR": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }
    if offline:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[name] = _DEAD_PROXY
        env["NO_PROXY"] = env["no_proxy"] = ""
    return env


def _run(cmd: list[str], *, cwd: str, env: dict[str, str], timeout_s: float, limits: dict[str, int]):
    """(exit code or None on timeout, stdout, stderr, seconds). Kills the whole process group on timeout."""
    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=_limits(limits),  # noqa: PLW1509 - required to set rlimits on the child only
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        code = None
        err = (err or "") + f"\n[vendorwake] killed after {timeout_s:.0f}s timeout"
    return code, out or "", err or "", round(time.monotonic() - started, 2)


def _tail(text: str) -> str:
    return text[-TAIL_CHARS:]


def write_project(files: dict[str, str], dest: Path) -> None:
    """Write files under ``dest``, refusing absolute paths and anything that escapes it."""
    root = dest.resolve()
    for rel, content in files.items():
        if rel.startswith("/") or "\\" in rel:
            raise ValueError(f"refusing unsafe path {rel!r}")
        target = (root / rel).resolve()
        if root not in target.parents:
            raise ValueError(f"refusing path outside the sandbox: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


# ---------------------------------------------------------------------------- environments


class PipVenvProvider:
    """Builds (and caches) one venv per requirement set with ``pip --only-binary=:all:``."""

    def __init__(
        self,
        base_dir: str | Path | None = None,
        *,
        pip_args: Iterable[str] = (),
        python: str = sys.executable,
        timeout_s: float = INSTALL_TIMEOUT_S,
    ) -> None:
        self.base_dir = Path(base_dir or Path(tempfile.gettempdir()) / "vendorwake-venvs")
        self.pip_args = list(pip_args)
        self.python = python
        self.timeout_s = timeout_s

    def python_for(self, requirements: list[str]) -> tuple[str, str]:
        key = hashlib.sha256("\n".join(sorted(requirements) + self.pip_args).encode()).hexdigest()[:16]
        venv = self.base_dir / key
        python = venv / "bin" / "python"
        desc = f"venv {key} (python {self._base_version()}, pip --only-binary=:all:)"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with open(self.base_dir / f"{key}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # one builder per requirement set across workers
            if (venv / ".ready").exists():
                return str(python), f"cached {desc}"
            shutil.rmtree(venv, ignore_errors=True)
            home = self.base_dir / ".home"
            home.mkdir(exist_ok=True)
            env = minimal_env(home=str(home), offline=False)
            env["PIP_CACHE_DIR"] = str(self.base_dir / ".pip-cache")
            try:
                code, _, err, _ = _run(
                    [self.python, "-m", "venv", str(venv)],
                    cwd=str(self.base_dir),
                    env=env,
                    timeout_s=120,
                    limits=INSTALL_LIMITS,
                )
                if code != 0:
                    raise RuntimeError(f"venv creation failed: {_tail(err)[-500:]}")
                cmd = [str(python), "-m", "pip", "install", "-q", "--only-binary=:all:", *self.pip_args]
                code, _, err, _ = _run(
                    [*cmd, *requirements],
                    cwd=str(self.base_dir),
                    env=env,
                    timeout_s=self.timeout_s,
                    limits=INSTALL_LIMITS,
                )
                if code != 0:
                    raise RuntimeError(f"pip install failed (exit {code}): {_tail(err)[-800:]}")
            except Exception:
                shutil.rmtree(venv, ignore_errors=True)
                raise
            (venv / ".ready").write_text("\n".join(requirements))
            return str(python), f"fresh {desc}"

    def _base_version(self) -> str:
        if self.python == sys.executable:
            return ".".join(map(str, sys.version_info[:3]))
        return Path(self.python).name


# ---------------------------------------------------------------------------- requirements


def is_allowed(repo: RepoRef, allowed: str | Iterable[str]) -> bool:
    entries = [e.strip() for e in allowed.split(",")] if isinstance(allowed, str) else list(allowed)
    sub = repo.subpath.strip("/")
    for entry in entries:
        parts = [p for p in entry.strip("/").split("/") if p]
        if len(parts) < 2:
            continue
        owner, name, entry_sub = parts[0], parts[1], "/".join(parts[2:])
        if owner.lower() == repo.owner.lower() and name.lower() == repo.name.lower():
            if not entry_sub or entry_sub == sub:
                return True
    return False


def project_files(files: dict[str, str], subpath: str) -> dict[str, str]:
    """Files relative to the project root (``subpath`` prefix stripped when present)."""
    prefix = subpath.strip("/")
    if not prefix or not any(p.startswith(prefix + "/") for p in files):
        return dict(files)
    return {p[len(prefix) + 1 :]: c for p, c in files.items() if p.startswith(prefix + "/")}


def verification_requirements(files: dict[str, str], *, package: str, version: str) -> list[str]:
    """Root-level requirements (requirements*.txt, pyproject deps) with ``package`` pinned to ``version``."""
    wanted = normalize_package("pypi", package)
    deps = []
    for path, content in sorted(files.items()):
        if "/" in path:
            continue
        if re.fullmatch(r"requirements[^/]*\.txt", path):
            deps += parse_requirements(path, content)
        elif path == "pyproject.toml":
            deps += parse_pyproject(path, content)
    reqs = {f"{wanted}=={version}"}
    for dep in deps:
        if dep.package != wanted:
            reqs.add(f"{dep.package}{dep.version_spec or ''}")
    if not any(r.split("=")[0].split("<")[0].split(">")[0].strip() == "pytest" for r in reqs):
        reqs.add("pytest")
    return sorted(reqs)


# ---------------------------------------------------------------------------- running tests


def _parse_junit(path: Path, project: set[str], prefix: str) -> tuple[int, list[TestFailure]]:
    if not path.exists():
        return 0, []
    try:
        tree = ET.parse(path)  # noqa: S314 - file written by pytest in our temp dir
    except ET.ParseError:
        return 0, []
    total = 0
    failures: list[TestFailure] = []
    for case in tree.iter("testcase"):
        total += 1
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        text = f"{problem.get('message', '')}\n{problem.text or ''}"
        locations: list[tuple[str, int]] = []
        for match in _LOCATION.finditer(text):
            rel = posixpath.normpath(match.group(1)).lstrip("./")
            if rel in project:
                loc = (f"{prefix}/{rel}" if prefix else rel, int(match.group(2)))
                if loc not in locations:
                    locations.append(loc)
        test_id = "::".join(x for x in (case.get("classname", ""), case.get("name", "")) if x)
        lines = (problem.get("message") or "").strip().splitlines()
        message = lines[0] if lines else ""
        if message in ("", "collection failure") or message.startswith("failed on setup"):
            # Collection/setup errors carry a generic message; the real one is the first "E   ..." line.
            detail = next(
                (ln[1:].strip() for ln in (problem.text or "").splitlines() if ln.startswith("E ")), ""
            )
            message = f"{message}: {detail}" if message and detail else (detail or message)
        failures.append(TestFailure(test_id=test_id, message=message[:300], locations=locations))
    return total, failures


def _status(code: int | None, tests: int) -> VerificationStatus:
    if code == 0:
        return VerificationStatus.passed
    if code == 1:
        return VerificationStatus.failed
    if code == 5 or (code == 0 and tests == 0):
        return VerificationStatus.not_testable
    return VerificationStatus.requires_human_review  # timeout, usage/internal error, interrupted


def not_testable(reason: str, *, before: str | None = None, after: str | None = None) -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.not_testable,
        command="",
        exit_code=None,
        stdout_tail=reason,
        stderr_tail="",
        duration_s=0.0,
        environment="not run",
        before_version=before,
        after_version=after,
    )


def run_tests(
    files: dict[str, str],
    *,
    label: str,
    package: str,
    version: str,
    provider: EnvironmentProvider,
    subpath: str = "",
    before_version: str | None = None,
    after_version: str | None = None,
    timeout_s: float = TEST_TIMEOUT_S,
) -> VerificationRun:
    """Run pytest for the project in ``files`` with ``package==version`` installed. Never raises for test
    failures; environment problems come back as ``requires_human_review``."""
    sub = subpath.strip("/")
    prefix = sub if sub and any(p.startswith(sub + "/") for p in files) else ""
    project = project_files(files, prefix)
    requirements = verification_requirements(project, package=package, version=version)
    try:
        python, env_desc = provider.python_for(requirements)
    except Exception as exc:  # install problems are reported, not raised
        result = not_testable(
            f"environment setup failed: {exc}"[:TAIL_CHARS], before=before_version, after=after_version
        )
        result.status = VerificationStatus.requires_human_review
        return VerificationRun(label=label, result=result)

    with tempfile.TemporaryDirectory(prefix="vw-verify-") as tmp:
        work = Path(tmp) / "src"
        home = Path(tmp) / "home"
        home.mkdir()
        write_project(project, work)
        junit = Path(tmp) / "junit.xml"
        cmd = [
            python,
            "-m",
            "pytest",
            "-q",
            "-rfE",
            "-p",
            "no:cacheprovider",
            "--continue-on-collection-errors",
            f"--junitxml={junit}",
        ]
        env = minimal_env(home=str(home), path_prefix=str(Path(python).parent))
        code, out, err, seconds = _run(cmd, cwd=str(work), env=env, timeout_s=timeout_s, limits=TEST_LIMITS)
        tests, failures = _parse_junit(junit, set(project), prefix)

    environment = (
        f"subprocess, {env_desc}, {package}=={version}, {NETWORK_NOTE}, "
        f"minimal env (no inherited secrets), timeout={timeout_s:.0f}s, {_limits_note(TEST_LIMITS)}"
    )
    display = "python -m pytest -q -rfE -p no:cacheprovider --continue-on-collection-errors"
    result = VerificationResult(
        status=_status(code, tests),
        command=f"{display}  # {package}=={version}",
        exit_code=code,
        stdout_tail=_tail(out),
        stderr_tail=_tail(err),
        duration_s=seconds,
        environment=environment,
        before_version=before_version,
        after_version=after_version,
    )
    return VerificationRun(label=label, result=result, failures=failures, tests=tests)


def _allowed_repos(allowed_repos: str | Iterable[str] | None) -> str | Iterable[str]:
    if allowed_repos is not None:
        return allowed_repos
    from vendorwake.core.config import get_settings

    return get_settings().verification_allowed_repos


def verify_versions(
    repo: RepoRef,
    files: dict[str, str],
    *,
    package: str,
    before_version: str | None,
    after_version: str,
    provider: EnvironmentProvider | None = None,
    allowed_repos: str | Iterable[str] | None = None,
    ecosystem: str = "pypi",
) -> VersionComparison:
    """Run the suite on the currently pinned version and on the new release."""
    reason = _skip_reason(repo, allowed_repos, ecosystem, before_version)
    if reason:
        skipped = not_testable(reason, before=before_version, after=after_version)
        return VersionComparison(
            before=VerificationRun(label="before", result=skipped),
            after=VerificationRun(label="after", result=skipped),
        )
    provider = provider or PipVenvProvider()
    assert before_version is not None
    before = run_tests(
        files,
        label="before",
        package=package,
        version=before_version,
        provider=provider,
        subpath=repo.subpath,
        before_version=before_version,
    )
    after = run_tests(
        files,
        label="after",
        package=package,
        version=after_version,
        provider=provider,
        subpath=repo.subpath,
        before_version=before_version,
        after_version=after_version,
    )
    return VersionComparison(before=before, after=after)


def verify_patched(
    repo: RepoRef,
    files: dict[str, str],
    patched: dict[str, str],
    *,
    package: str,
    before_version: str | None,
    after_version: str,
    provider: EnvironmentProvider | None = None,
    allowed_repos: str | Iterable[str] | None = None,
    ecosystem: str = "pypi",
) -> VerificationRun:
    """Run the suite on a temp copy with ``patched`` (path -> new content) applied, under the new version.
    The caller's ``files`` (and the user's repository) are never modified."""
    reason = _skip_reason(repo, allowed_repos, ecosystem, before_version or "n/a")
    if reason:
        return VerificationRun(
            label="patched", result=not_testable(reason, before=before_version, after=after_version)
        )
    unknown = set(patched) - set(files)
    if unknown:
        raise ValueError(f"patched files not in the repository: {sorted(unknown)}")
    return run_tests(
        {**files, **patched},
        label="patched",
        package=package,
        version=after_version,
        provider=provider or PipVenvProvider(),
        subpath=repo.subpath,
        before_version=before_version,
        after_version=after_version,
    )


def _skip_reason(
    repo: RepoRef, allowed_repos: str | Iterable[str] | None, ecosystem: str, before_version: str | None
) -> str | None:
    if not is_allowed(repo, _allowed_repos(allowed_repos)):
        target = "/".join(x for x in (repo.owner, repo.name, repo.subpath.strip("/")) if x)
        return f"not run: {target} is not in the verification allowlist (VW_VERIFICATION_ALLOWED_REPOS)"
    if ecosystem != "pypi":
        return f"not run: verification supports Python (pypi) projects only, not {ecosystem}"
    if not before_version:
        return "not run: the currently installed version could not be determined (no exact pin or lockfile)"
    return None
