"""Env-var and API-host references in any text file (code, config, docs, CI files)."""

from __future__ import annotations

import re

from vendorwake.analysis.python_scan import CodeSite

MAX_TEXT_BYTES = 1_000_000


def host_pattern(host: str) -> re.Pattern[str]:
    """``api.stripe.com`` matches exactly; ``.auth0.com`` matches any subdomain (``tenant.auth0.com``)."""
    if host.startswith("."):
        return re.compile(rf"\b[A-Za-z0-9][A-Za-z0-9\-.]*{re.escape(host)}\b", re.IGNORECASE)
    return re.compile(rf"(?<![\w.\-]){re.escape(host)}\b", re.IGNORECASE)


def scan_text(
    file_path: str, source: str, *, env_vars: list[str], api_hosts: list[str], vendor_root: str
) -> list[CodeSite]:
    if len(source) > MAX_TEXT_BYTES or "\x00" in source[:4096]:
        return []
    env_patterns = [
        (name, re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")) for name in env_vars
    ]
    host_patterns = [(host, host_pattern(host)) for host in api_hosts]
    sites: list[CodeSite] = []
    for number, line in enumerate(source.splitlines(), start=1):
        for name, pattern in env_patterns:
            if pattern.search(line):
                sites.append(
                    CodeSite(file_path, number, "attribute", f"env:{name}", vendor_root, line.strip()[:200])
                )
        for _host, pattern in host_patterns:
            match = pattern.search(line)
            if match:
                sites.append(
                    CodeSite(
                        file_path,
                        number,
                        "attribute",
                        f"host:{match.group(0).lower()}",
                        vendor_root,
                        line.strip()[:200],
                    )
                )
    return sites
