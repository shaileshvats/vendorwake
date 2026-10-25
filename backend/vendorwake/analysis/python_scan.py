"""Static scan of Python source for vendor SDK usage (AST only; the code is never imported or run).

Produces ``CodeSite``s:
- ``import``: ``import stripe`` / ``from stripe import Reversal`` (symbol = qualified name)
- ``attribute``: module-qualified references such as ``stripe.Reversal`` in annotations or expressions, and
  names bound by ``from stripe import X``
- ``call``: calls on the vendor module or on vendor-derived objects, with keyword argument names, e.g.
  ``client.v1.customers.retrieve(customer=...)`` -> method ``retrieve``, keywords ``("customer",)``
- ``string``: short identifier-like string literals in vendor-using files (enum values, quoted annotations)

"Vendor-derived" is a deliberately simple, scope-insensitive taint: a name annotated with a vendor type, or
assigned from an expression rooted at the vendor module or another derived name. It over-approximates,
so sites relying on it are marked ``derived=True`` and are never treated as exact symbol matches.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Literal

SiteKind = Literal["import", "attribute", "call", "string"]
_IDENTIFIER_LITERAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{2,99}$")
_IMPORT_FALLBACK = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)


@dataclass(frozen=True)
class CodeSite:
    file_path: str
    line: int
    kind: SiteKind
    symbol: str  # qualified name ("stripe.Reversal") or, for derived calls, the source chain
    vendor_root: str  # the import prefix this site belongs to, e.g. "stripe"
    snippet: str
    method: str | None = None
    keywords: tuple[str, ...] = ()
    derived: bool = False  # receiver is only inferred to be a vendor object
    chain: tuple[str, ...] = ()  # attribute names after the root, e.g. ("v1", "customers", "retrieve")


@dataclass
class PythonScan:
    file_path: str
    sites: list[CodeSite] = field(default_factory=list)
    vendor_roots: set[str] = field(default_factory=set)
    parse_error: str | None = None


def _matches_prefix(module: str, prefixes: set[str]) -> str | None:
    for prefix in prefixes:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


class _Resolver:
    def __init__(self, aliases: dict[str, tuple[str, str]], derived: dict[str, str]) -> None:
        self.aliases = aliases  # local name -> (qualified module/attribute name, vendor root)
        self.derived = derived  # local name -> vendor root, for inferred vendor objects

    def resolve(self, node: ast.AST) -> tuple[str, str, bool, tuple[str, ...]] | None:
        """(qualified name or chain, vendor root, derived?, attribute chain) for a vendor expression."""
        if isinstance(node, ast.Name):
            if node.id in self.aliases:
                qual, root = self.aliases[node.id]
                return qual, root, False, ()
            if node.id in self.derived:
                return node.id, self.derived[node.id], True, ()
            return None
        if isinstance(node, ast.Attribute):
            inner = self.resolve(node.value)
            if inner is None:
                return None
            qual, root, derived, chain = inner
            return f"{qual}.{node.attr}", root, derived, (*chain, node.attr)
        if isinstance(node, ast.Call):
            inner = self.resolve(node.func)
            if inner is None:
                return None
            qual, root, _, chain = inner
            return f"{qual}()", root, True, chain
        if isinstance(node, (ast.Subscript, ast.Await)):
            inner = self.resolve(node.value)
            if inner is None:
                return None
            qual, root, _, chain = inner
            return qual, root, True, chain
        return None


def _collect_aliases(tree: ast.AST, prefixes: set[str]) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _matches_prefix(alias.name, prefixes)
                if not root:
                    continue
                if alias.asname:
                    aliases[alias.asname] = (alias.name, root)
                else:
                    top = alias.name.split(".")[0]
                    aliases[top] = (top, root)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            root = _matches_prefix(node.module, prefixes)
            if not root:
                continue
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = (f"{node.module}.{alias.name}", root)
    return aliases


def _targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [n for elt in node.elts for n in _targets(elt)]
    return []


def _collect_derived(tree: ast.AST, resolver: _Resolver) -> None:
    for _ in range(3):  # a few passes reach a fixpoint for straight-line code
        before = dict(resolver.derived)
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.annotation is not None:
                hit = resolver.resolve(node.annotation)
                if hit and node.arg not in resolver.aliases:
                    resolver.derived[node.arg] = hit[1]
            elif isinstance(node, ast.Assign):
                hit = resolver.resolve(node.value)
                if hit:
                    for target in node.targets:
                        for name in _targets(target):
                            resolver.derived.setdefault(name, hit[1])
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                hit = resolver.resolve(node.annotation) or (node.value and resolver.resolve(node.value))
                if hit:
                    resolver.derived.setdefault(node.target.id, hit[1])
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    hit = resolver.resolve(item.context_expr)
                    if hit and item.optional_vars is not None:
                        for name in _targets(item.optional_vars):
                            resolver.derived.setdefault(name, hit[1])
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                hit = resolver.resolve(node.iter)
                if hit:
                    for name in _targets(node.target):
                        resolver.derived.setdefault(name, hit[1])
        if resolver.derived == before:
            break


def scan_python(file_path: str, source: str, prefixes: set[str] | list[str]) -> PythonScan:
    prefixes = set(prefixes)
    scan = PythonScan(file_path=file_path)
    lines = source.splitlines()

    def snippet(lineno: int) -> str:
        return lines[lineno - 1].strip()[:200] if 0 < lineno <= len(lines) else ""

    try:
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError) as exc:
        scan.parse_error = f"{type(exc).__name__}: {exc}"[:200]
        for match in _IMPORT_FALLBACK.finditer(source):
            module = match.group(1) or match.group(2)
            root = _matches_prefix(module, prefixes)
            if root:
                lineno = source.count("\n", 0, match.start()) + 1
                scan.vendor_roots.add(root)
                scan.sites.append(CodeSite(file_path, lineno, "import", module, root, snippet(lineno)))
        return scan

    aliases = _collect_aliases(tree, prefixes)
    if not aliases:
        return scan
    resolver = _Resolver(aliases, {})
    _collect_derived(tree, resolver)
    seen: set[tuple[int, str, str, str]] = set()
    file_roots = sorted({root for _, root in aliases.values()})

    def add(site: CodeSite) -> None:
        key = (site.line, site.kind, site.symbol, site.vendor_root)
        if key not in seen:
            seen.add(key)
            scan.sites.append(site)
            scan.vendor_roots.add(site.vendor_root)

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _matches_prefix(alias.name, prefixes)
                if root:
                    add(CodeSite(file_path, lineno, "import", alias.name, root, snippet(lineno)))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            root = _matches_prefix(node.module, prefixes)
            if root:
                for alias in node.names:
                    add(
                        CodeSite(
                            file_path, lineno, "import", f"{node.module}.{alias.name}", root, snippet(lineno)
                        )
                    )
        elif isinstance(node, ast.Attribute):
            hit = resolver.resolve(node)
            if hit and not hit[2]:
                add(CodeSite(file_path, lineno, "attribute", hit[0], hit[1], snippet(lineno), chain=hit[3]))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases:
            qual, root = aliases[node.id]
            if "." in qual and qual != root:  # a name bound by `from vendor import X`
                add(CodeSite(file_path, lineno, "attribute", qual, root, snippet(lineno)))
        elif isinstance(node, ast.Call):
            hit = resolver.resolve(node.func)
            if not hit:
                continue
            qual, root, derived, chain = hit
            method = node.func.attr if isinstance(node.func, ast.Attribute) else qual.rsplit(".", 1)[-1]
            keywords = tuple(k.arg for k in node.keywords if k.arg)
            symbol = qual if not derived else ast.unparse(node.func)[:200]
            add(
                CodeSite(
                    file_path, lineno, "call", symbol, root, snippet(lineno), method, keywords, derived, chain
                )
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _IDENTIFIER_LITERAL.match(node.value):
                for root in file_roots:
                    add(CodeSite(file_path, lineno, "string", node.value, root, snippet(lineno)))
    scan.sites.sort(key=lambda s: (s.line, s.kind, s.symbol))
    return scan
