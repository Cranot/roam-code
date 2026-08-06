#!/usr/bin/env python3
"""Audit DEAD CLI flags — @click.option/@click.argument params that the command
body NEVER reads.

The 2026-06-07 `roam deps --full` bug was exactly this: `--full` was declared (and
its help promised "complete list") but the function never referenced `full`, so the
flag silently did nothing. This scans every command for the same class — a param
declared by a click decorator but not used as a Name anywhere in the function body.

Run::

    python scripts/audit_dead_cli_flags.py                  # advisory report, exit 0
    python scripts/audit_dead_cli_flags.py --fail-on-found  # GATE: exit 1 on any
                                                            # unallowlisted finding
                                                            # or stale allowlist entry

Heuristic (AST Name-usage), so triage hits: a real dead flag = the body genuinely
ignores it; a false positive = used only via **kwargs forwarding or rebound name.
False positives and deliberate accepted no-ops go in
``scripts/dead_cli_flags_allowlist.txt``, WITH a reason, so the exemption is a
decision on the record rather than a silent tolerance.

Why the gate exists: from its first commit this script was advisory-only
("prints a report; exit 0") and had ZERO callers in .github/workflows/,
scripts/prepush_check.py or .githooks/. A detector nothing runs is not a guard —
it reads as covered while catching nothing. Eight dead flags had accumulated
behind it by 2026-08-06.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

# Framework params that are legitimately unused in some bodies.
_SKIP = {"ctx", "self", "cls", "kwargs", "args"}

_ALLOWLIST_PATH = pathlib.Path(__file__).resolve().parent / "dead_cli_flags_allowlist.txt"


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


def load_allowlist(path: pathlib.Path = _ALLOWLIST_PATH) -> set[str]:
    """Parse ``file.py::function::param`` keys. Blank lines and ``#`` comments
    are ignored; a trailing ``# reason`` on an entry line is stripped.

    A MISSING allowlist file is an empty allowlist, never a pass-everything
    default — the gate must get stricter when its config vanishes, not laxer.
    """
    entries: set[str] = set()
    if not path.exists():
        return entries
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        entries.add(line)
    return entries


def scan(root: pathlib.Path) -> tuple[int, list[tuple[str, str, list[str]]]]:
    """Return ``(commands_scanned, [(file, function, [unused params]), ...])``."""
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
    return scanned, dead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--fail-on-found",
        action="store_true",
        help="Exit 1 when any finding is not allowlisted, or any allowlist entry is stale.",
    )
    parser.add_argument(
        "--allowlist",
        default=str(_ALLOWLIST_PATH),
        help="Path to the allowlist file (default: scripts/dead_cli_flags_allowlist.txt).",
    )
    ns = parser.parse_args(argv)

    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"
    scanned, dead = scan(root)

    allowlist = load_allowlist(pathlib.Path(ns.allowlist))
    found_keys: set[str] = set()
    violations: list[str] = []
    exempted: list[str] = []
    for fname, fn, unused in dead:
        for param in unused:
            key = f"{fname}::{fn}::{param}"
            found_keys.add(key)
            (exempted if key in allowlist else violations).append(key)

    stale = sorted(allowlist - found_keys)

    print(f"DEAD-CLI-FLAG audit - scanned {scanned} command fns; {len(dead)} with unused param(s)\n")
    for fname, fn, unused in dead:
        print(f"  {fname}::{fn}  unused param(s): {unused}")
    if exempted:
        print(f"\nAllowlisted ({len(exempted)}) - see {ns.allowlist}:")
        for key in sorted(exempted):
            print(f"  {key}")
    if stale:
        print(f"\nSTALE allowlist entries ({len(stale)}) - no longer found, delete them:")
        for key in stale:
            print(f"  {key}")

    if not ns.fail_on_found:
        print(
            "\nAdvisory mode (exit 0). A true hit = the body ignores the flag (like deps --full "
            "did pre-2026-06-07). False positives: params forwarded via **kwargs or consumed by "
            "a nested closure. Run with --fail-on-found to gate."
        )
        return 0

    if violations or stale:
        print("\nFAIL: dead CLI flag gate.")
        for key in sorted(violations):
            print(f"  DEAD (unallowlisted): {key}")
        for key in stale:
            print(f"  STALE allowlist entry: {key}")
        print(
            "\nFix: wire the param into the command body, or delete the flag. If it is a "
            "heuristic false positive (**kwargs / ctx.params / nested-closure rebind) or a "
            f"deliberate accepted no-op, add it to {ns.allowlist} WITH the reason."
        )
        return 1

    print(f"\nOK: {len(violations)} unallowlisted dead flags, {len(stale)} stale allowlist entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
