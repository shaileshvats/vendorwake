"""Read and update a dotenv file in place, without ever printing secret values.

Format handled: ``KEY=VALUE`` lines, ``#`` comment lines and blank lines (as in ``.env.example``).
Values may be wrapped in single or double quotes. Inline comments are not supported, matching the
project convention that comments sit on their own lines.

Updates keep every other line byte-for-byte, replace the value of an existing key on its first
occurrence, append new keys at the end, and write atomically with mode 0600.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# Values of keys matching this pattern are never shown, only their length.
_SECRET_KEY = re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|_KEY$|DSN|CREDENTIALS|DATABASE_URL)", re.I)

# Placeholder values copied from .env.example that count as "not configured".
PLACEHOLDERS = frozenset(
    {
        "",
        "change-me-app",
        "change-me-owner",
        "change-me-worker",
        "your-tenant.us.auth0.com",
    }
)


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse(text: str) -> dict[str, str]:
    """Return KEY -> value; the first occurrence of a key wins (python-dotenv uses the last, but
    duplicates are a bug either way and :func:`update_text` only edits the first)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = _unquote(m.group(2))
    return out


def load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse(path.read_text(encoding="utf-8"))


def _format_value(value: str) -> str:
    if "\n" in value:
        raise ValueError("multi-line values are not supported in .env")
    # Quote only when needed; JSON and URLs are fine unquoted for docker/pydantic/python-dotenv,
    # but a value with spaces or a leading '#' would be misread.
    if value != value.strip() or value.startswith("#") or " " in value:
        if "'" in value:
            raise ValueError("value needs quoting but contains a single quote")
        return f"'{value}'"
    return value


def update_text(text: str, updates: Mapping[str, str], header: str | None = None) -> str:
    remaining = dict(updates)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            lines[i] = f"{key}={_format_value(remaining.pop(key))}"
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        if header:
            lines.append(f"# {header}")
        lines.extend(f"{k}={_format_value(v)}" for k, v in remaining.items())
    return "\n".join(lines) + "\n"


def write_updates(path: Path, updates: Mapping[str, str], header: str | None = None) -> list[str]:
    """Apply ``updates`` to ``path`` atomically (mode 0600). Returns the keys whose value changed."""
    current_text = path.read_text(encoding="utf-8") if path.exists() else ""
    current = parse(current_text)
    changed = [k for k, v in updates.items() if current.get(k) != v]
    if not changed:
        if path.exists():
            os.chmod(path, 0o600)
        return []
    new_text = update_text(current_text, {k: updates[k] for k in changed}, header=header)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.chmod(path, 0o600)
    return changed


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY.search(key))


def mask(key: str, value: str) -> str:
    """Display form of a value: secrets become ``<set, N chars>``; others are shown as-is."""
    if value == "":
        return "<empty>"
    if is_secret_key(key):
        return f"<secret, {len(value)} chars>"
    return value


def is_set(env: Mapping[str, str], key: str) -> bool:
    return env.get(key, "").strip() not in PLACEHOLDERS


def default_env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"
