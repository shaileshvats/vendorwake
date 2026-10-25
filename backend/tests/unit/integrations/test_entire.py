# ruff: noqa: S603, S607 - tests drive git with fixed argv lists
"""Entire recorder tests.

The fake-CLI tests need only git. ``test_real_entire_cli_session`` needs the Entire CLI on PATH (it runs
offline in the integrations test image, see tools/entire-agent-vendorwake/Dockerfile.test) and is
skipped elsewhere.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from vendorwake.integrations import entire
from vendorwake.integrations.entire import DEFAULT_ADAPTER_DIR, EntireError, EntireSessionRecorder
from vendorwake.integrations.errors import NotConfiguredError

from ..conftest import make_settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

GIT_ID = {
    "GIT_AUTHOR_NAME": "VW Test",
    "GIT_AUTHOR_EMAIL": "test@vendorwake.invalid",
    "GIT_COMMITTER_NAME": "VW Test",
    "GIT_COMMITTER_EMAIL": "test@vendorwake.invalid",
}


def git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ID},
    ).stdout


@pytest.fixture
def worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in GIT_ID.items():
        monkeypatch.setenv(key, value)
    main = tmp_path / "repo"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.name", "VW Test")  # Entire requires a git identity
    git(main, "config", "user.email", "test@vendorwake.invalid")
    (main / "payments").mkdir()
    (main / "payments" / "refunds.py").write_text(
        'import stripe\n\ndef r(t):\n    return "stripe.Reversal"\n'
    )
    git(main, "add", "-A")
    git(main, "commit", "-qm", "fixture")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", "-b", "vendorwake/migrate", str(wt))
    return wt


@pytest.fixture
def fake_entire(tmp_path: Path) -> tuple[Path, Path]:
    """An `entire` stand-in that logs each hook invocation (argv + stdin JSON)."""
    log = tmp_path / "hooks.log"
    script = tmp_path / "bin" / "entire"
    script.parent.mkdir()
    script.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"rec = {{'argv': sys.argv[1:], 'stdin': json.loads(sys.stdin.read())}}\n"
        f"open({str(log)!r}, 'a').write(json.dumps(rec) + '\\n')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def hooks(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_lifecycle_fires_hooks_in_order_and_keeps_transcript_outside_worktree(worktree, fake_entire) -> None:
    script, log = fake_entire
    rec = EntireSessionRecorder(worktree, entire_bin=str(script), extra_path=[])
    sid = rec.start(
        investigation_id="inv 0001/x", prompt="Migrate refunds.py", model="google/gemma-4-26b-a4b-it"
    )
    assert sid.startswith("vw-inv-0001-x-")

    # A second recorder (e.g. another Temporal activity) continues the same session from disk.
    rec2 = EntireSessionRecorder(worktree, entire_bin=str(script), extra_path=[])
    rec2.record_turn(
        session_id=sid, content="Renamed it.", model="m", usage={"input_tokens": 812, "output_tokens": 64}
    )
    rec2.record_tool(session_id=sid, name="apply_patch", input={"path": "payments/refunds.py"}, ok=True)
    (worktree / "payments" / "refunds.py").write_text("import stripe\n# TransferReversal\n")
    sha = rec2.commit(session_id=sid, message="migrate: stripe.Reversal -> stripe.TransferReversal")
    result = rec2.end(session_id=sid)

    assert [h["argv"] for h in hooks(log)] == [
        ["hooks", "vendorwake", "session-start"],
        ["hooks", "vendorwake", "turn-start"],
        ["hooks", "vendorwake", "turn-end"],
        ["hooks", "vendorwake", "session-end"],
    ]
    payloads = [h["stdin"] for h in hooks(log)]
    assert payloads[1]["user_prompt"] == "Migrate refunds.py"
    assert all(p["session_id"] == sid and p["metadata"]["investigation_id"] == "inv 0001/x" for p in payloads)

    transcript = Path(payloads[0]["session_ref"])
    assert worktree not in transcript.parents and transcript.parent.name == "vendorwake-sessions"
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert [r["type"] for r in records] == ["user", "assistant", "tool"]
    assert records[1]["usage"] == {"input_tokens": 812, "output_tokens": 64}
    assert "vendorwake-sessions" not in git(worktree, "show", "--stat", "HEAD")

    assert result["commit_sha"] == sha and result["checkpoint_id"] is None  # fake CLI adds no trailer
    assert "assistant: Renamed it." in result["transcript_excerpt"]


def test_end_reads_trailer_and_checkpoint_ref(worktree, fake_entire) -> None:
    script, _ = fake_entire
    rec = EntireSessionRecorder(worktree, entire_bin=str(script), extra_path=[])
    sid = rec.start(investigation_id="inv-2", prompt="p", model="m")
    (worktree / "x.txt").write_text("x\n")
    cp = "01K8ABCDEFGHJKMNPQRSTVWXYZ"
    rec.commit(session_id=sid, message=f"migrate\n\nEntire-Checkpoint: {cp}\n")

    # Build a checkpoint ref with the layout Entire 0.12.0 produced in test_real_entire_cli_session:
    # root metadata.json listing sessions, and per-session files under 0/.
    session_meta = json.dumps({"session_id": sid, "agent": "Vendorwake"})
    root_meta = json.dumps(
        {"sessions": [{"metadata": "/0/metadata.json", "transcript": "/0/full.jsonl",
                       "prompt": "/0/prompt.txt", "compact_transcript": "/0/transcript.jsonl"}]}
    )  # fmt: skip
    files = {
        "prompt.txt": "Migrate refunds.py\n",
        "metadata.json": session_meta,
        "full.jsonl": json.dumps({"type": "user", "content": "from checkpoint"}) + "\n",
        "transcript.jsonl": json.dumps({"v": 1, "type": "user", "content": [{"text": ""}]}) + "\n",
    }
    blob = {n: git(worktree, "hash-object", "-w", "--stdin", stdin=c).strip() for n, c in files.items()}
    inner = git(worktree, "mktree", stdin="".join(f"100644 blob {s}\t{n}\n" for n, s in blob.items())).strip()
    root_blob = git(worktree, "hash-object", "-w", "--stdin", stdin=root_meta).strip()
    tree = git(
        worktree, "mktree", stdin=f"040000 tree {inner}\t0\n100644 blob {root_blob}\tmetadata.json\n"
    ).strip()
    commit = git(worktree, "commit-tree", tree, "-m", "checkpoint").strip()
    git(worktree, "update-ref", f"refs/entire/checkpoints/01/{cp}", commit)

    result = rec.end(session_id=sid)
    assert result["checkpoint_id"] == cp
    assert result["checkpoint_ref"] == f"refs/entire/checkpoints/01/{cp}"
    assert result["prompt"] == "Migrate refunds.py\n"
    assert result["metadata"] == {"session_id": sid, "agent": "Vendorwake"}
    assert result["transcript_excerpt"] == "user: from checkpoint"


def test_errors(worktree, fake_entire, tmp_path) -> None:
    script, _ = fake_entire
    rec = EntireSessionRecorder(worktree, entire_bin=str(script), extra_path=[])
    sid = rec.start(investigation_id="inv-3", prompt="p", model="m")
    with pytest.raises(EntireError, match="nothing to commit"):
        rec.commit(session_id=sid, message="empty")
    with pytest.raises(EntireError, match="invalid session id"):
        rec.end(session_id="../../etc/passwd")
    with pytest.raises(EntireError, match="unknown session"):
        rec.end(session_id="vw-nope-123")
    failing = tmp_path / "bin" / "entire-fail"
    failing.write_text("#!/bin/sh\necho 'token=abc123 broke' >&2\nexit 3\n")
    failing.chmod(0o755)
    with pytest.raises(EntireError, match="exited with 3") as info:
        EntireSessionRecorder(worktree, entire_bin=str(failing), extra_path=[]).start(
            investigation_id="i", prompt="p", model="m"
        )
    assert "abc123" not in str(info.value)


def test_from_settings(worktree) -> None:
    with pytest.raises(NotConfiguredError, match="disabled"):
        entire.from_settings(make_settings(entire_enabled=False), worktree)
    with pytest.raises(NotConfiguredError, match="not on PATH"):
        entire.from_settings(make_settings(entire_enabled=True), worktree, entire_bin="entire-does-not-exist")


def test_adapter_info_is_protocol_v1() -> None:
    adapter = DEFAULT_ADAPTER_DIR / "entire-agent-vendorwake"
    if not adapter.exists():
        pytest.skip("adapter not present in this checkout")
    info = json.loads(
        subprocess.run([str(adapter), "info"], capture_output=True, text=True, check=True).stdout
    )
    assert info["protocol_version"] == 1 and info["name"] == "vendorwake"
    assert set(info["hook_names"]) == {"session-start", "turn-start", "turn-end", "session-end"}


@pytest.mark.skipif(shutil.which("entire") is None, reason="needs the Entire CLI on PATH")
def test_real_entire_cli_session(worktree) -> None:
    if not (DEFAULT_ADAPTER_DIR / "entire-agent-vendorwake").exists():
        pytest.skip("adapter not present in this checkout")
    entire.ensure_entire_enabled(worktree)
    entire.ensure_entire_enabled(worktree)  # idempotent

    rec = entire.from_settings(make_settings(entire_enabled=True), worktree)
    prompt = (
        "Migrate payments/refunds.py for stripe-python 16.0.0: stripe.Reversal -> stripe.TransferReversal."
    )
    sid = rec.start(investigation_id="inv-live", prompt=prompt, model="google/gemma-4-26b-a4b-it")
    target = worktree / "payments" / "refunds.py"
    target.write_text(target.read_text().replace("stripe.Reversal", "stripe.TransferReversal"))
    rec.record_tool(session_id=sid, name="apply_patch", input={"path": "payments/refunds.py"}, ok=True)
    rec.record_turn(
        session_id=sid,
        content="Renamed stripe.Reversal to stripe.TransferReversal.",
        model="google/gemma-4-26b-a4b-it",
        usage={"input_tokens": 812, "output_tokens": 64},
    )
    rec.record_summary(session_id=sid, content="Updated one reference.")
    sha = rec.commit(session_id=sid, message="migrate: stripe.Reversal -> stripe.TransferReversal")
    result = rec.end(session_id=sid)

    print(json.dumps(result, indent=2))  # visible with -s; useful when Entire's storage format drifts
    assert result["commit_sha"] == sha
    assert result["checkpoint_id"] and len(result["checkpoint_id"]) == 26
    assert result["checkpoint_ref"] and result["checkpoint_ref"].startswith("refs/entire/checkpoints/")
    assert result["prompt"] and "stripe.TransferReversal" in result["prompt"]
    assert isinstance(result["metadata"], dict)
    assert "Renamed stripe.Reversal" in result["transcript_excerpt"]
