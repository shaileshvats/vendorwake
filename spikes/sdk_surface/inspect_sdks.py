"""M0 spike: record the real public surface of the installed auth0-ai and backboard-sdk packages,
so integration code is written against methods that exist rather than ones we assume exist.

Prints modules, public classes/functions and their signatures, filtered by keyword.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys

KEYWORDS = {
    "auth0_ai": ("vault", "token", "ciba", "async", "authoriz", "fga", "interrupt", "connection"),
    "backboard": ("assistant", "thread", "message", "memory", "model", "provider", "llm"),
}


def walk(pkg_name: str) -> list[str]:
    pkg = importlib.import_module(pkg_name)
    names = [pkg_name]
    if hasattr(pkg, "__path__"):
        for m in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg_name}."):
            names.append(m.name)
    return names


def sig(obj) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(?)"


def main() -> None:
    for pkg_name, keys in KEYWORDS.items():
        print(f"\n===== {pkg_name} =====")
        for mod_name in walk(pkg_name):
            try:
                mod = importlib.import_module(mod_name)
            except Exception as exc:  # optional extras (langchain, llama_index) may be absent
                print(f"[skip] {mod_name}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            hits = []
            for name, obj in vars(mod).items():
                if name.startswith("_") or getattr(obj, "__module__", None) != mod_name:
                    continue
                if not any(k in name.lower() or k in mod_name.lower() for k in keys):
                    continue
                if inspect.isclass(obj):
                    methods = [
                        f"    .{m}{sig(f)}"
                        for m, f in vars(obj).items()
                        if not m.startswith("_") and callable(f)
                    ]
                    hits.append(f"  class {name}{sig(obj)}" + ("\n" + "\n".join(methods) if methods else ""))
                elif inspect.isfunction(obj):
                    hits.append(f"  def {name}{sig(obj)}")
            if hits:
                print(f"-- {mod_name}")
                print("\n".join(hits))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
