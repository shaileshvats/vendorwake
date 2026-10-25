"""Lightweight JS/TS scan: imports/requires of vendor packages and member chains on the bound names.

Regex-based on purpose (no JS parser dependency); good enough to find integration points, not to prove them.
"""

from __future__ import annotations

import re

from vendorwake.analysis.python_scan import CodeSite

JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")

_IMPORT_FROM = re.compile(r"""\b(?:import|export)\s+(?P<what>[^'";]*?)\s*from\s*['"](?P<pkg>[^'"]+)['"]""")
_SIDE_EFFECT = re.compile(r"""\bimport\s*['"](?P<pkg>[^'"]+)['"]""")
_REQUIRE = re.compile(
    r"""(?:(?:const|let|var)\s+(?P<what>[\w$]+|\{[^}]*\})\s*=\s*)?\b(?:require|import)\s*\(\s*['"](?P<pkg>[^'"]+)['"]\s*\)"""
)


def _package_of(spec: str, packages: set[str]) -> str | None:
    for package in packages:
        if spec == package or spec.startswith(package + "/"):
            return package
    return None


def _bound_names(what: str | None) -> list[str]:
    if not what:
        return []
    names: list[str] = []
    what = what.replace("type ", "")
    star = re.search(r"\*\s+as\s+([\w$]+)", what)
    if star:
        names.append(star.group(1))
    braces = re.search(r"\{([^}]*)\}", what)
    if braces:
        for part in braces.group(1).split(","):
            part = part.strip()
            if part:
                names.append(re.split(r"\s+as\s+|\s*:\s*", part)[-1].strip())
    default = re.match(r"\s*([\w$]+)\s*(?:,|$)", what)
    if default and default.group(1) not in ("type",):
        names.append(default.group(1))
    return [n for n in names if re.fullmatch(r"[\w$]+", n)]


def scan_js(file_path: str, source: str, packages: set[str] | list[str], vendor_root: str) -> list[CodeSite]:
    packages = set(packages)
    lines = source.splitlines()
    sites: list[CodeSite] = []
    bound: dict[str, str] = {}

    def line_of(offset: int) -> int:
        return source.count("\n", 0, offset) + 1

    def snippet(lineno: int) -> str:
        return lines[lineno - 1].strip()[:200] if 0 < lineno <= len(lines) else ""

    for pattern in (_IMPORT_FROM, _SIDE_EFFECT, _REQUIRE):
        for match in pattern.finditer(source):
            package = _package_of(match.group("pkg"), packages)
            if not package:
                continue
            lineno = line_of(match.start())
            sites.append(
                CodeSite(file_path, lineno, "import", match.group("pkg"), vendor_root, snippet(lineno))
            )
            for name in _bound_names(match.groupdict().get("what")):
                bound[name] = package
    for name, package in bound.items():
        for match in re.finditer(rf"(?<![\w$.])(?:new\s+)?{re.escape(name)}((?:\.[\w$]+)+)?\s*(\()?", source):
            chain = match.group(1) or ""
            if not chain and not match.group(2):
                continue
            lineno = line_of(match.start())
            if snippet(lineno).startswith(("import ", "export ")) or "require(" in snippet(lineno):
                continue
            attrs = tuple(a for a in chain.split(".") if a)
            sites.append(
                CodeSite(
                    file_path,
                    lineno,
                    "call" if match.group(2) else "attribute",
                    f"{package}{chain}" if chain else package,
                    vendor_root,
                    snippet(lineno),
                    method=attrs[-1] if attrs else None,
                    chain=attrs,
                )
            )
    sites.sort(key=lambda s: (s.line, s.kind, s.symbol))
    return sites
