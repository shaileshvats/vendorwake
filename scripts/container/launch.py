#!/usr/bin/env python3
"""Container entrypoint for the combined Vendorwake image (API + Temporal worker [+ dev Temporal]).

Render's free tier has no background workers, so one web service runs everything:

  1. optional: `alembic upgrade head`              (VW_MIGRATE_ON_START=true; needs VW_MIGRATION_DATABASE_URL)
  2. optional: `temporal server start-dev`          (VW_TEMPORAL_EMBEDDED=true and VW_TEMPORAL_ADDRESS
                                                     localhost:7233; history is in memory unless
                                                     VW_TEMPORAL_DB_FILE is set, and is lost on restart)
  3. `uvicorn vendorwake.api.app:app` on $PORT     (default 8000)
  4. `python -m vendorwake.worker`                 (unless VW_RUN_WORKER=false)

If any long-running child exits, the others are stopped and the launcher exits with that child's
code, so the platform restarts the whole container. SIGTERM/SIGINT are forwarded to every child.
Override the command to run one process alone, e.g. `python -m vendorwake.worker`.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

EMBEDDED_ADDRESSES = {"localhost:7233", "127.0.0.1:7233"}
GRACE_SECONDS = 20


def truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def log(msg: str) -> None:
    print(f"[launch] {msg}", flush=True)


def wait_for_port(host: str, port: int, timeout: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def build_commands(env: dict[str, str]) -> list[tuple[str, list[str]]]:
    """The long-running processes to start, in order. Pure function (unit-tested)."""
    cmds: list[tuple[str, list[str]]] = []
    address = env.get("VW_TEMPORAL_ADDRESS", "localhost:7233").strip()
    if env.get("VW_TEMPORAL_EMBEDDED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        if address not in EMBEDDED_ADDRESSES:
            raise SystemExit(f"VW_TEMPORAL_EMBEDDED=true needs VW_TEMPORAL_ADDRESS=localhost:7233, got {address}")
        cmd = [
            "temporal",
            "server",
            "start-dev",
            "--ip",
            "127.0.0.1",
            "--port",
            "7233",
            "--headless",
            "--log-level",
            "warn",
        ]
        ns = env.get("VW_TEMPORAL_NAMESPACE", "default").strip() or "default"
        if ns != "default":
            cmd += ["--namespace", ns]
        if env.get("VW_TEMPORAL_DB_FILE", "").strip():
            cmd += ["--db-filename", env["VW_TEMPORAL_DB_FILE"].strip()]
        cmds.append(("temporal", cmd))
    port = env.get("PORT", "8000").strip() or "8000"
    cmds.append(
        (
            "api",
            [
                "uvicorn",
                "vendorwake.api.app:app",
                "--host",
                "0.0.0.0",  # noqa: S104 - container must listen on all interfaces
                "--port",
                port,
                "--proxy-headers",
                "--forwarded-allow-ips",
                "*",
            ],
        )
    )
    if env.get("VW_RUN_WORKER", "true").strip().lower() in {"1", "true", "yes", "on"}:
        cmds.append(("worker", [sys.executable, "-m", "vendorwake.worker"]))
    return cmds


def main() -> int:
    env = dict(os.environ)
    if truthy("VW_MIGRATE_ON_START"):
        log("running alembic upgrade head")
        rc = subprocess.call(["alembic", "upgrade", "head"], cwd="/app")  # noqa: S603, S607
        if rc != 0:
            log(f"migrations failed (exit {rc})")
            return rc

    procs: dict[str, subprocess.Popen] = {}
    stopping = False

    def stop_all(signum: int = signal.SIGTERM) -> None:
        for p in procs.values():
            if p.poll() is None:
                p.send_signal(signum)
        deadline = time.monotonic() + GRACE_SECONDS
        for p in procs.values():
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()

    def on_signal(signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        log(f"received signal {signum}; stopping children")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    for name, cmd in build_commands(env):
        log(f"starting {name}: {cmd[0]} {' '.join(cmd[1:3])} ...")
        # Own session per child: `temporal server start-dev` signals its whole process group on
        # shutdown, which would otherwise SIGKILL the launcher too.
        procs[name] = subprocess.Popen(cmd, cwd="/app", start_new_session=True)  # noqa: S603
        if name == "temporal":
            if not wait_for_port("127.0.0.1", 7233, 60, procs[name]):
                log("embedded Temporal did not open 127.0.0.1:7233 within 60 s")
                stop_all()
                return 1
            log("embedded Temporal is up (history is not durable)")

    exit_code = 0
    while not stopping:
        for name, p in procs.items():
            rc = p.poll()
            if rc is not None:
                log(f"{name} exited with code {rc}; stopping the others")
                exit_code = rc or 1
                stopping = True
                break
        time.sleep(0.5)
    stop_all()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
