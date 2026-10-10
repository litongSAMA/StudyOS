"""Structural import checker (run from backend/).

Walks app/ and tests/, extracts every intra-project import (absolute `app.*`
and relative), expands relatives, and reports each one that does not resolve to
a real module or attribute in the tree.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
APP = BACKEND / "app"


def dotted_of(py: Path, root: Path) -> str:
    rel = py.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def exists_dotted(dotted: str) -> tuple[bool, str]:
    """Return (ok, detail) for a dotted module path relative to `app`."""
    if not dotted:
        return True, "package level"
    parts = dotted.split(".")
    cur = APP
    for i, part in enumerate(parts):
        mod = cur / f"{part}.py"
        pkg = cur / part
        if mod.exists():
            return True, "module"
        if pkg.is_dir():
            cur = pkg
            continue
        return False, f"missing at {'.'.join(parts[:i + 1])}"
    # ended on a package directory without __init__ -> namespace pkg, still importable
    return True, "package"


def files_under(*roots: Path) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        for p in sorted(r.rglob("*.py")):
            if "__pycache__" in p.parts or "quarantine" in p.parts:
                continue
            out.append(p)
    return out


def resolve_relative(current: str, level: int, tail: str) -> str:
    parts = current.split(".")
    # package of the current module
    if not current.endswith("__init__"):
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)] if level - 1 <= len(parts) else []
    if tail:
        parts = parts + tail.split(".")
    return ".".join(p for p in parts if p)


def main() -> int:
    problems: list[str] = []
    checked = 0

    for py in files_under(APP, BACKEND / "tests", BACKEND / "scripts"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        # inside app/, a module's own dotted name; outside, no relative resolution
        cur_mod = dotted_of(py, APP) if (APP in py.parents or py.parent == APP) else None

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    if cur_mod is None:
                        continue
                    target = resolve_relative(cur_mod, node.level, node.module or "")
                    ok, detail = exists_dotted(target)
                    checked += 1
                    if not ok:
                        problems.append(f"{py.relative_to(BACKEND)}:{node.lineno}  from {'.' * node.level}{node.module or ''}  ->  {target}  [{detail}]")
                    continue
                mod = node.module or ""
                if mod == "app":
                    # `from app import x` -> x must be a real submodule of app
                    for alias in node.names:
                        ok, detail = exists_dotted(alias.name)
                        checked += 1
                        if not ok:
                            problems.append(f"{py.relative_to(BACKEND)}:{node.lineno}  from app import {alias.name}  [{detail}]")
                    continue
                if mod.startswith("app."):
                    target = mod[4:]
                    ok, detail = exists_dotted(target)
                    checked += 1
                    if not ok:
                        problems.append(f"{py.relative_to(BACKEND)}:{node.lineno}  from {mod}  [{detail}]")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app" or alias.name.startswith("app."):
                        target = alias.name[4:]
                        ok, detail = exists_dotted(target)
                        checked += 1
                        if not ok:
                            problems.append(f"{py.relative_to(BACKEND)}:{node.lineno}  import {alias.name}  [{detail}]")

    print(f"checked {checked} intra-project imports")
    if problems:
        print(f"\n{len(problems)} PROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    print("all intra-project imports resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
