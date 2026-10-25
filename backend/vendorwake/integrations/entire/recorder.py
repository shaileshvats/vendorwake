"""Record Migration Agent sessions with Entire (SessionRecorder).

Verified with Entire CLI 0.12.0 (docs/m0-findings.md section 1, spikes/entire/run_session.sh):
- Entire discovers ``entire-agent-vendorwake`` (tools/entire-agent-vendorwake) on PATH after
  ``entire enable --agent vendorwake --telemetry=false``.
- We fire hooks ourselves: ``entire hooks vendorwake <session-start|turn-start|turn-end|session-end>``
  with a JSON payload on stdin (``session_id``, ``session_ref`` = transcript path, ``timestamp``,
  ``model``, ``metadata``; ``user_prompt`` on turn-start).
- A commit made after turn-end gets an ``Entire-Checkpoint: <26-char ULID>`` trailer, and the checkpoint
  is stored as git ref ``refs/entire/checkpoints/<shard>/<id>`` holding ``transcript.jsonl``,
  ``full.jsonl``, ``prompt.txt`` and ``metadata.json``.

Session lifecycle::

    sid = rec.start(investigation_id=..., prompt=..., model=...)   # session-start + turn-start
    rec.record_turn(session_id=sid, content=..., model=..., usage={...})  # real provider usage only
    rec.record_tool(session_id=sid, name="apply_patch", input={"path": ...}, ok=True)
    sha = rec.commit(session_id=sid, message="...")                # turn-end, then git commit
    info = rec.end(session_id=sid)                                 # session-end, read checkpoint

The transcript (JSONL) lives outside the worktree in ``<git-common-dir>/vendorwake-sessions/``, next to
a small ``<sid>.state.json`` so start/record/end may run in different processes (Temporal activities).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess  # noqa: S404 - fixed argv lists, never a shell
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vendorwake.core.logging import redact_text
from vendorwake.integrations.errors import IntegrationError

logger = logging.getLogger(__name__)

SERVICE = "entire"
AGENT_NAME = "vendorwake"
ADAPTER_BINARY = "entire-agent-vendorwake"
TRAILER_RE = re.compile(r"^Entire-Checkpoint:\s*(\S+)\s*$", re.MULTILINE)
CHECKPOINT_REF_PREFIX = "refs/entire/checkpoints/"
SESSIONS_DIRNAME = "vendorwake-sessions"

# tools/entire-agent-vendorwake in a source checkout (absent in images that install it on PATH).
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ADAPTER_DIR = _REPO_ROOT / "tools" / "entire-agent-vendorwake"

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9-]+")


class EntireError(IntegrationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, service=SERVICE)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _default_extra_path() -> list[str]:
    return [str(DEFAULT_ADAPTER_DIR)] if (DEFAULT_ADAPTER_DIR / ADAPTER_BINARY).exists() else []


def _env(extra_path: Sequence[str], base: Mapping[str, str] | None = None, **extra: str) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    if extra_path:
        env["PATH"] = os.pathsep.join([*extra_path, env.get("PATH", "")])
    env.update(extra)
    return env


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    stdin: str | None = None,
    what: str,
) -> str:
    try:
        proc = subprocess.run(  # noqa: S603 - argv list built from constants and validated values
            list(argv),
            cwd=cwd,
            env=dict(env),
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EntireError(f"{argv[0]} not found on PATH ({what})") from exc
    except subprocess.TimeoutExpired as exc:
        raise EntireError(f"{what} timed out after {timeout_s:.0f}s") from exc
    if proc.returncode != 0:
        tail = redact_text((proc.stderr or proc.stdout or "").strip()[-400:])
        raise EntireError(f"{what} exited with {proc.returncode}: {tail}")
    return proc.stdout


def ensure_entire_enabled(
    repo_dir: str | Path,
    *,
    entire_bin: str = "entire",
    extra_path: Sequence[str] | None = None,
    timeout_s: float = 120.0,
) -> None:
    """Run ``entire enable --agent vendorwake --telemetry=false`` in ``repo_dir`` and check that the
    adapter reports its hooks installed. Safe to call repeatedly.

    Entire 0.12.0 refuses to enable without an identity when there is no TTY: it needs git
    ``user.name``/``user.email`` configured (or ``ENTIRE_TOKEN``), so this checks for them first."""
    repo = Path(repo_dir).resolve()
    path_dirs = list(extra_path) if extra_path is not None else _default_extra_path()
    env = _env(path_dirs, ENTIRE_REPO_ROOT=str(repo))
    for binary in (entire_bin, ADAPTER_BINARY):
        if shutil.which(binary, path=env["PATH"]) is None:
            raise EntireError(f"{binary} is not on PATH; install the Entire CLI and tools/{ADAPTER_BINARY}")
    if not env.get("ENTIRE_TOKEN"):
        for key in ("user.name", "user.email"):
            probe = subprocess.run(  # noqa: S603
                ["git", "config", "--get", key],  # noqa: S607 - git from PATH, as everywhere else
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if not probe.stdout.strip():
                raise EntireError(f"git {key} is not configured in {repo}; Entire needs a git identity")
    _run(
        [entire_bin, "enable", "--agent", AGENT_NAME, "--telemetry=false"],
        cwd=repo,
        env=env,
        timeout_s=timeout_s,
        what="entire enable",
    )
    out = _run([ADAPTER_BINARY, "are-hooks-installed"], cwd=repo, env=env, timeout_s=30, what="hook check")
    try:
        installed = bool(json.loads(out).get("installed"))
    except (ValueError, AttributeError):
        installed = False
    if not installed:
        raise EntireError("entire enable finished but the vendorwake hooks are not installed")


@dataclass
class _State:
    session_id: str
    investigation_id: str
    transcript: str
    model: str
    turn_open: bool = False
    ended: bool = False
    commit_sha: str | None = None


class EntireSessionRecorder:
    """`SessionRecorder` for one git worktree (the Migration Agent's sandbox checkout)."""

    def __init__(
        self,
        worktree: str | Path,
        *,
        entire_bin: str = "entire",
        git_bin: str = "git",
        extra_path: Sequence[str] | None = None,
        timeout_s: float = 120.0,
        excerpt_chars: int = 4000,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.worktree = Path(worktree).resolve()
        if not self.worktree.is_dir():
            raise EntireError(f"worktree {self.worktree} does not exist")
        self.entire_bin = entire_bin
        self.git_bin = git_bin
        self.timeout_s = timeout_s
        self.excerpt_chars = excerpt_chars
        self.metadata = dict(metadata or {})
        path_dirs = list(extra_path) if extra_path is not None else _default_extra_path()
        self._env = _env(path_dirs, ENTIRE_REPO_ROOT=str(self.worktree))
        self._session_dir: Path | None = None

    # ------------------------------------------------------------------ SessionRecorder

    def start(self, *, investigation_id: str, prompt: str, model: str) -> str:
        safe = _SAFE_ID_RE.sub("-", investigation_id).strip("-")[:64] or "inv"
        session_id = f"vw-{safe}-{uuid.uuid4().hex[:12]}"
        transcript = self.session_dir / f"{session_id}.jsonl"
        state = _State(session_id, investigation_id, str(transcript), model)
        self._append(state, {"type": "user", "content": prompt, "ts": _now()})
        self._save(state)
        self._hook("session-start", state)
        self._hook("turn-start", state, user_prompt=prompt)
        state.turn_open = True
        self._save(state)
        logger.info(
            "entire session started", extra={"session_id": session_id, "investigation_id": investigation_id}
        )
        return session_id

    def record_turn(self, *, session_id: str, content: str, model: str, usage: dict | None) -> None:
        """Append one model response. ``usage`` must be the provider-reported token counts (or None);
        Entire sums ``input_tokens``/``output_tokens`` from these records."""
        state = self._load(session_id)
        record: dict[str, Any] = {"type": "assistant", "content": content, "model": model, "ts": _now()}
        if usage:
            record["usage"] = {
                k: int(v)
                for k, v in usage.items()
                if k in {"input_tokens", "output_tokens"} and v is not None
            }
        self._append(state, record)

    def end(self, *, session_id: str) -> dict:
        """Close the turn (if open) and the session, then read the checkpoint created by `commit`.

        Returns ``{"session_id", "checkpoint_id", "commit_sha", "transcript_excerpt", "checkpoint_ref",
        "prompt", "metadata", "transcript_path"}``; checkpoint fields are None when nothing was committed.
        """
        state = self._load(session_id)
        if state.turn_open:
            self._hook("turn-end", state)
            state.turn_open = False
            self._save(state)
        if not state.ended:
            self._hook("session-end", state)
            state.ended = True
            self._save(state)

        checkpoint_id = self._checkpoint_id(state.commit_sha) if state.commit_sha else None
        ref = self._checkpoint_ref(checkpoint_id) if checkpoint_id else None
        session_files = self._session_files(ref, session_id) if ref else {}
        prompt = self._show(ref, session_files["prompt"]) if "prompt" in session_files else None
        metadata = self._json(ref, session_files["metadata"]) if "metadata" in session_files else None
        # "transcript" is our native JSONL as Entire stored it (full.jsonl); fall back to the local file.
        transcript_text = (
            self._show(ref, session_files["transcript"])
            if "transcript" in session_files
            else Path(state.transcript).read_text(encoding="utf-8")
        )
        if checkpoint_id and not ref:
            logger.warning("entire checkpoint ref not found", extra={"checkpoint_id": checkpoint_id})
        result = {
            "session_id": session_id,
            "checkpoint_id": checkpoint_id,
            "commit_sha": state.commit_sha,
            "transcript_excerpt": self._excerpt(transcript_text),
            "checkpoint_ref": ref,
            "prompt": prompt,
            "metadata": metadata,
            "transcript_path": state.transcript,
        }
        logger.info(
            "entire session ended",
            extra={"session_id": session_id, "checkpoint_id": checkpoint_id, "commit_sha": state.commit_sha},
        )
        return result

    # ------------------------------------------------------------------ extras

    def record_tool(self, *, session_id: str, name: str, input: Mapping[str, Any], ok: bool) -> None:  # noqa: A002
        """Record a tool call. ``write_file``/``apply_patch`` with ``input["path"]`` mark modified files."""
        self._append(self._load(session_id), {"type": "tool", "name": name, "input": dict(input), "ok": ok})

    def record_summary(self, *, session_id: str, content: str) -> None:
        self._append(self._load(session_id), {"type": "summary", "content": content})

    def commit(
        self,
        *,
        session_id: str,
        message: str,
        paths: Sequence[str] | None = None,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> str:
        """End the open turn (Entire snapshots it), then commit the patch so the commit gets the
        ``Entire-Checkpoint`` trailer. Returns the commit SHA."""
        state = self._load(session_id)
        if state.ended:
            raise EntireError("session already ended")
        if state.turn_open:
            self._hook("turn-end", state)
            state.turn_open = False
            self._save(state)
        add = [self.git_bin, "add", "--", *paths] if paths else [self.git_bin, "add", "-A"]
        self._git(add, what="git add")
        staged = subprocess.run(  # noqa: S603
            [self.git_bin, "diff", "--cached", "--quiet"], cwd=self.worktree, env=self._env, check=False
        )
        if staged.returncode == 0:
            raise EntireError("nothing to commit")
        identity: dict[str, str] = {}
        if author_name:
            identity |= {"GIT_AUTHOR_NAME": author_name, "GIT_COMMITTER_NAME": author_name}
        if author_email:
            identity |= {"GIT_AUTHOR_EMAIL": author_email, "GIT_COMMITTER_EMAIL": author_email}
        _run(
            [self.git_bin, "commit", "-q", "-m", message],
            cwd=self.worktree,
            env={**self._env, **identity},
            timeout_s=self.timeout_s,
            what="git commit",
        )
        state.commit_sha = self._git([self.git_bin, "rev-parse", "HEAD"], what="git rev-parse").strip()
        self._save(state)
        return state.commit_sha

    @property
    def session_dir(self) -> Path:
        if self._session_dir is None:
            common = self._git(
                [self.git_bin, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                what="git rev-parse",
            ).strip()
            self._session_dir = Path(common) / SESSIONS_DIRNAME
            self._session_dir.mkdir(parents=True, exist_ok=True)
        return self._session_dir

    # ------------------------------------------------------------------ internals

    def _git(self, argv: Sequence[str], *, what: str) -> str:
        return _run(argv, cwd=self.worktree, env=self._env, timeout_s=self.timeout_s, what=what)

    def _hook(self, hook: str, state: _State, **extra: Any) -> None:
        payload = {
            "hook_type": hook,
            "session_id": state.session_id,
            "session_ref": state.transcript,
            "timestamp": _now(),
            "model": state.model,
            "metadata": {**self.metadata, "investigation_id": state.investigation_id, "agent": "migration"},
            **extra,
        }
        _run(
            [self.entire_bin, "hooks", AGENT_NAME, hook],
            cwd=self.worktree,
            env=self._env,
            timeout_s=self.timeout_s,
            stdin=json.dumps(payload),
            what=f"entire hooks {hook}",
        )

    def _state_path(self, session_id: str) -> Path:
        if not re.fullmatch(r"vw-[A-Za-z0-9-]+", session_id):
            raise EntireError("invalid session id")
        return self.session_dir / f"{session_id}.state.json"

    def _save(self, state: _State) -> None:
        self._state_path(state.session_id).write_text(json.dumps(asdict(state)), encoding="utf-8")

    def _load(self, session_id: str) -> _State:
        path = self._state_path(session_id)
        try:
            return _State(**json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise EntireError(f"unknown session {session_id}") from None

    @staticmethod
    def _append(state: _State, record: Mapping[str, Any]) -> None:
        with open(state.transcript, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _checkpoint_id(self, commit_sha: str) -> str | None:
        message = self._git([self.git_bin, "log", "-1", "--format=%B", commit_sha], what="git log")
        found = TRAILER_RE.findall(message)
        return found[-1] if found else None

    def _checkpoint_ref(self, checkpoint_id: str) -> str | None:
        refs = self._git(
            [self.git_bin, "for-each-ref", "--format=%(refname)", CHECKPOINT_REF_PREFIX],
            what="git for-each-ref",
        ).split()
        for ref in refs:
            if ref.endswith("/" + checkpoint_id):
                return ref
        return None

    def _session_files(self, ref: str, session_id: str) -> dict[str, str]:
        """Paths of this session's files inside the checkpoint ref.

        Entire 0.12.0 layout (observed): root ``metadata.json`` lists ``sessions`` as
        ``{"metadata": "/0/metadata.json", "transcript": "/0/full.jsonl", "compact_transcript":
        "/0/transcript.jsonl", "prompt": "/0/prompt.txt", ...}``; ``full.jsonl`` is our native
        transcript, ``transcript.jsonl`` Entire's compacted form.
        """
        listing = set(
            self._git([self.git_bin, "ls-tree", "-r", "--name-only", ref], what="git ls-tree").split()
        )
        root = self._json(ref, "metadata.json") if "metadata.json" in listing else None
        sessions = root.get("sessions") if isinstance(root, dict) else None
        candidates: list[dict[str, str]] = []
        for entry in sessions if isinstance(sessions, list) else []:
            if not isinstance(entry, dict):
                continue
            files = {
                k: v.lstrip("/") for k, v in entry.items() if isinstance(v, str) and v.lstrip("/") in listing
            }
            candidates.append(files)
            meta = self._json(ref, files["metadata"]) if "metadata" in files else None
            if isinstance(meta, dict) and meta.get("session_id") == session_id:
                return files
        if len(candidates) == 1:
            return candidates[0]
        # Unknown layout: pick the first file with each expected basename.
        by_name: dict[str, str] = {}
        for path in sorted(listing):
            by_name.setdefault(path.rsplit("/", 1)[-1], path)
        fallback = {"prompt": "prompt.txt", "metadata": "metadata.json", "transcript": "full.jsonl"}
        return {key: by_name[name] for key, name in fallback.items() if name in by_name}

    def _show(self, ref: str | None, path: str) -> str:
        return self._git([self.git_bin, "show", f"{ref}:{path}"], what="git show")

    def _json(self, ref: str | None, path: str) -> Any:
        try:
            return json.loads(self._show(ref, path))
        except (ValueError, EntireError):
            return None

    def _excerpt(self, transcript_text: str) -> str:
        lines: list[str] = []
        for raw in transcript_text.splitlines():
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            kind = record.get("type")
            if kind == "tool":
                line = f"tool {record.get('name')} ({'ok' if record.get('ok') else 'failed'})"
            elif kind in {"user", "assistant", "summary"}:
                line = f"{kind}: {str(record.get('content', '')).strip()}"
            else:
                continue
            lines.append(line[:600])
        text = "\n".join(lines)
        if len(text) > self.excerpt_chars:
            text = text[: self.excerpt_chars - 1].rstrip() + "…"
        return text
