#!/usr/bin/env python3
"""Advisory audit: DEAD CLI flags — @click.option/@click.argument params that the
command body NEVER reads.

The 2026-06-07 `roam deps --full` bug was exactly this: `--full` was declared (and
its help promised "complete list") but the function never referenced `full`, so the
flag silently did nothing. This scans every command for the same class — a param
declared by a click decorator but not used as a Name anywhere in the function body.

ADVISORY: prints a report; exit 0. Run: python scripts/audit_dead_cli_flags.py
Heuristic (AST Name-usage), so triage hits: a real dead flag = the body genuinely
ignores it; a false positive = used only via **kwargs forwarding or rebound name.
"""
from __future__ import annotations

import ast
import pathlib
import sys

# Framework params that are legitimately unused in some bodies.
_SKIP = {"ctx", "self", "cls", "kwargs", "args"}


def _has_click_decorator(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        # @click.option(...) / @click.argument(...) / @click.command(...)
        t = d.func if isinstance(d, ast.Call) else d
        name = ""
        if isinstance(t, ast.Attribute):
            name = t.attr
        elif isinstance(t, ast.Name):
            name = t.id
        if name in ("option", "argument", "command", "pass_context"):
            return True
    return False


def _declares_option_or_argument(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        t = d.func if isinstance(d, ast.Call) else d
        nm = t.attr if isinstance(t, ast.Attribute) else (t.id if isinstance(t, ast.Name) else "")
        if nm in ("option", "argument"):
            return True
    return False


def _params(fn: ast.FunctionDef) -> list[str]:
    a = fn.args
    out = []
    for grp in (a.posonlyargs, a.args, a.kwonlyargs):
        out += [p.arg for p in grp]
    return out


def _names_used_in_body(fn: ast.FunctionDef) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.arg):  # don't count the param declarations themselves
            pass
    return used


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"
    dead: list[tuple[str, str, list[str]]] = []
    scanned = 0
    for f in sorted(root.glob("cmd_*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if not _has_click_decorator(fn) or not _declares_option_or_argument(fn):
                continue
            scanned += 1
            params = [p for p in _params(fn) if p not in _SKIP]
            # Used names EXCLUDING the parameter list (so a param only "seen" as its
            # own declaration doesn't count). ast.walk includes the args; we collect
            # Name nodes which are USAGES, not the arg declarations.
            used = _names_used_in_body(fn)
            unused = [p for p in params if p not in used]
            if unused:
                dead.append((f.name, fn.name, unused))

    print(f"DEAD-CLI-FLAG audit — scanned {scanned} command fns; {len(dead)} with unused param(s)\n")
    for fname, fn, unused in dead:
        print(f"  {fname}::{fn}  unused param(s): {unused}")
    print("\nAdvisory. A true hit = the body ignores the flag (like deps --full did pre-2026-06-07). "
          "False positives: params forwarded via **kwargs or consumed by a nested closure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
