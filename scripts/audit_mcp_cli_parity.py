#!/usr/bin/env python3
"""Advisory audit: MCP wrapper ↔ CLI flag parity.

Each ``@_tool`` MCP wrapper forwards to a CLI verb via ``_run_roam(["<verb>", ...])``.
When the CLI command grows a DATA flag (e.g. ``deps --multi`` / ``deps --full``) that
the wrapper doesn't expose, agents can't reach it over MCP and fall back to shelling
or hand-querying the index (codex nav A/B q2/q5, 2026-06-07). This reports, per tool,
CLI options NOT exposed as wrapper params — minus a format/IO/CI allowlist that
legitimately stays CLI-only.

ADVISORY: prints a report; exit 0 always. Run on demand:
    python scripts/audit_mcp_cli_parity.py
Not a CI gate — many omissions are intentional; a human triages the list.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys
from unittest.mock import patch

# Flags that legitimately stay CLI-only (output format / IO / CI / setup / cwd).
_ALLOWLIST = {
    "json", "sarif", "detail", "plain", "no_color", "color", "quiet", "verbose",
    "pretty", "format", "fmt", "output_format", "output", "out", "persist", "apply",
    "force", "dry_run", "write", "emit_guard_findings", "save", "export",
    "update_baseline", "no_rotate_card_hash", "ci", "strict", "fail_on",
    "exit_code", "exit_zero", "help", "yes", "interactive", "root", "budget",
    "limit", "top_n", "mermaid_mode",
}
# Prefixes/suffixes that mark format / pagination / filter / setup flags — these
# shape PRESENTATION not WHICH DATA the agent can reach, so they're CLI-only by
# design. The signal we want is a DATA flag (like deps --multi) the agent can't get.
_ALLOW_PREFIX = ("list_", "show_", "do_", "no_", "sort_", "include_", "exclude_")
_ALLOW_SUFFIX = ("_filter", "_mode", "_path", "_output", "_baseline", "_ref", "_spec", "_flag", "_only")


def _is_allowed(name: str) -> bool:
    return (
        name in _ALLOWLIST
        or name.startswith(_ALLOW_PREFIX)
        or name.endswith(_ALLOW_SUFFIX)
    )


def _discover():
    import roam.mcp_server as m

    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "_tool":
                t = next((k.value.value for k in dec.keywords
                          if k.arg == "name" and isinstance(k.value, ast.Constant)
                          and isinstance(k.value.value, str)), None)
                if t:
                    out.append((t, node.name))
                break
    return out


def _unwrap(o):
    seen = set()
    while o is not None and id(o) not in seen:
        seen.add(id(o))
        inner = getattr(o, "fn", None)
        if callable(inner):
            o = inner
            continue
        w = getattr(o, "__wrapped__", None)
        if w is not None:
            o = w
            continue
        break
    return o


def _capture_verb(raw, sig):
    """Call the raw fn with dummy args under a mocked _run_roam; return the CLI verb."""
    import roam.mcp_server as m

    call = {}
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            call[p.name] = "x"
    with patch.object(m, "_run_roam") as mock:
        mock.return_value = {"summary": {}}
        try:
            res = raw(**call)
            if inspect.iscoroutine(res):
                import asyncio
                asyncio.run(res)
        except Exception:
            return None
        if not mock.called:
            return None
        args = mock.call_args[0][0]
        return args[0] if isinstance(args, list) and args else None


def main() -> int:
    import click

    import roam.mcp_server as m
    from roam.cli import cli

    gaps: list[tuple[str, str, list[str]]] = []
    no_verb: list[str] = []
    for tname, fname in _discover():
        raw = _unwrap(getattr(m, fname, None))
        if not callable(raw):
            continue
        try:
            sig = inspect.signature(raw)
        except (TypeError, ValueError):
            continue
        verb = _capture_verb(raw, sig)
        if not verb:
            no_verb.append(tname)
            continue
        try:
            cmd = cli.get_command(None, verb)
        except Exception:
            cmd = None
        if cmd is None:
            continue
        cli_opts = {
            p.name for p in cmd.params
            if isinstance(p, click.Option) and not _is_allowed(p.name)
        }
        wrapper_params = set(sig.parameters)
        missing = sorted(cli_opts - wrapper_params)
        if missing:
            gaps.append((tname, verb, missing))

    print(f"MCP↔CLI flag-parity audit — {len(gaps)} tool(s) with un-exposed CLI data flags\n")
    for tname, verb, missing in sorted(gaps):
        print(f"  {tname:28} (roam {verb}): missing {missing}")
    if no_verb:
        print(f"\n  ({len(no_verb)} tools: verb not captured — compound/inline; skipped)")
    print("\nAdvisory only. Triage: expose the flag on the wrapper, or add it to _ALLOWLIST.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
