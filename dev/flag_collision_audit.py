"""Enumerate short-flag conventions across every roam command via the live Click parser.

Read-only audit. Emits JSON on stdout:
  - every command (top-level + subcommands of groups)
  - every single-dash short option it declares
  - which universal CLI conventions collide (hard = exit 2, silent = exit 0 wrong answer)

Usage:
    python dev/flag_collision_audit.py            # JSON dump
    python dev/flag_collision_audit.py --summary  # human counts
"""

from __future__ import annotations

import json
import sys

import click

from roam.cli import cli as root_cli

# Conventions an agent (or a human) reasonably assumes, and — critically — the
# long options that SATISFY each one. "Collision" is only meaningful against a
# stated expectation: `-o` bound to `--output` is the convention working, not a
# defect, and counting it as one inflates the number until it stops meaning
# anything. `takes_value` is the shape the convention has elsewhere; a shape
# mismatch is what decides whether a real mismatch is loud or silent.
CONVENTIONS = {
    "-h": {"means": "help", "takes_value": False, "satisfied_by": ("--help",)},
    "-v": {"means": "verbose", "takes_value": False, "satisfied_by": ("--verbose", "--version")},
    "-q": {"means": "quiet", "takes_value": False, "satisfied_by": ("--quiet", "--silent")},
    "-o": {"means": "output file", "takes_value": True, "satisfied_by": ("--output", "--out", "--output-file")},
    "-f": {"means": "force / file", "takes_value": False, "satisfied_by": ("--force", "--file", "--files")},
    "-n": {
        "means": "dry-run / count",
        "takes_value": False,
        "satisfied_by": ("--dry-run", "--limit", "--top", "--number", "--count"),
    },
    "-r": {"means": "recursive", "takes_value": False, "satisfied_by": ("--recursive",)},
    "-l": {"means": "list / long", "takes_value": False, "satisfied_by": ("--list", "--long")},
    "-a": {"means": "all", "takes_value": False, "satisfied_by": ("--all",)},
    "-d": {"means": "debug / directory", "takes_value": False, "satisfied_by": ("--debug", "--dir", "--directory")},
    "-c": {"means": "config / count", "takes_value": True, "satisfied_by": ("--config", "--count")},
    "-i": {
        "means": "ignore-case / interactive",
        "takes_value": False,
        "satisfied_by": ("--ignore-case", "--interactive"),
    },
    "-e": {
        "means": "expression / exclude",
        "takes_value": True,
        "satisfied_by": ("--regex", "--expression", "--exclude", "--pattern"),
    },
    "-p": {"means": "path / port", "takes_value": True, "satisfied_by": ("--path", "--port")},
    "-s": {"means": "silent / short", "takes_value": False, "satisfied_by": ("--silent", "--short")},
    "-t": {"means": "type / tag", "takes_value": True, "satisfied_by": ("--type", "--tag")},
    "-u": {"means": "user / update", "takes_value": False, "satisfied_by": ("--user", "--update")},
    "-x": {"means": "exclude / execute", "takes_value": True, "satisfied_by": ("--exclude", "--execute")},
    "-V": {"means": "version", "takes_value": False, "satisfied_by": ("--version",)},
}


def classify(flag: str, declared: dict) -> tuple[str, str]:
    """Return (verdict, why) for one declared short option against its convention.

    CONVENTIONAL — roam's meaning is the convention. Not a defect.
    SILENT       — meaning differs AND roam's option is a flag, so the parse
                   always succeeds: exit 0, different answer, no signal.
    LOUD         — meaning differs AND roam's option takes a value, so a caller
                   using the convention gets exit 2 and can see the failure.
    """
    conv = CONVENTIONS[flag]
    if set(declared["long"]) & set(conv["satisfied_by"]):
        return "CONVENTIONAL", f"{flag} -> {','.join(declared['long'])} is what {flag} means elsewhere"

    # MEASURED 2026-08-12, and it refuted the shape rule this function first
    # used. Whether the parse succeeds is decided by ROAM's shape alone, not by
    # the convention's. `-t` conventionally takes a value (`rg -t py`) while
    # roam binds it to the `--test-only` FLAG; the first guess called that a
    # shape mismatch and predicted exit 2. `roam grep -t TODO` in fact exits 0
    # and returns 72 matches where the unflagged call returns 203.
    if not declared["takes_value"]:
        return "SILENT", (
            f"caller means {conv['means']!r}; roam applies {','.join(declared['long']) or declared['name']!r}. "
            "It is a flag here, so the parse always succeeds: exit 0, different answer, no signal."
        )
    return "LOUD", (
        f"{flag} takes a value here but means {conv['means']!r} elsewhere -> "
        "exit 2 'requires an argument', which the caller can see"
    )


def _short_opts(param: click.Parameter) -> list[str]:
    out = []
    for opt in tuple(param.opts or ()) + tuple(param.secondary_opts or ()):
        if opt.startswith("-") and not opt.startswith("--") and len(opt) == 2:
            out.append(opt)
    return out


def _takes_value(param: click.Parameter) -> bool:
    return not getattr(param, "is_flag", False) and getattr(param, "nargs", 1) != 0


def walk(cmd: click.Command, ctx: click.Context, path: list[str], out: list[dict]) -> None:
    entry = {
        "command": " ".join(path),
        "is_group": isinstance(cmd, click.Group),
        "shorts": {},
        "declares_h": False,
    }
    # ``cmd.params`` holds only what the command declares itself; the help
    # option is synthesised per-Context from ``help_option_names`` and appended
    # by ``get_params``. Reading ``cmd.params`` alone reports `-h` as unbound
    # even after the root group aliases it, so the audit must ask for the
    # resolved set. ``declares_h`` above still reads ``cmd.params``, because
    # "this command claims -h for itself" is a different question.
    for param in cmd.get_params(ctx):
        for short in _short_opts(param):
            entry["shorts"][short] = {
                "name": param.name,
                "long": [o for o in (param.opts or ()) if o.startswith("--")],
                "takes_value": _takes_value(param),
                "is_flag": bool(getattr(param, "is_flag", False)),
            }
    for param in cmd.params:
        for short in _short_opts(param):
            if short == "-h":
                entry["declares_h"] = True
    out.append(entry)

    if isinstance(cmd, click.Group):
        for name in cmd.list_commands(ctx):
            try:
                sub = cmd.get_command(ctx, name)
            except Exception as exc:  # noqa: BLE001 — audit must not die on one bad module
                out.append({"command": " ".join([*path, name]), "load_error": repr(exc), "shorts": {}})
                continue
            if sub is None:
                continue
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            walk(sub, sub_ctx, [*path, name], out)


def main() -> int:
    # ``click.Context(cmd, ...)`` does NOT apply ``cmd.context_settings`` — only
    # ``cmd.make_context`` does. Building the root context bare left
    # ``help_option_names`` at Click's ``["--help"]`` default, so the audit
    # reported `-h` as unbound on all 381 commands even after the alias shipped.
    ctx = click.Context(root_cli, info_name="roam", **root_cli.context_settings)
    rows: list[dict] = []
    walk(root_cli, ctx, ["roam"], rows)

    # Root itself is not a "command" for the 285 count.
    commands = [r for r in rows if r["command"] != "roam"]

    unbound = []  # convention letter not declared at all -> exit 2 "No such option"
    triaged = []  # convention letter IS declared -> CONVENTIONAL / SILENT / LOUD
    for row in commands:
        for letter, conv in CONVENTIONS.items():
            declared = row.get("shorts", {}).get(letter)
            if declared is None:
                unbound.append({"command": row["command"], "flag": letter, "convention": conv["means"]})
                continue
            verdict, why = classify(letter, declared)
            triaged.append(
                {
                    "command": row["command"],
                    "flag": letter,
                    "convention": conv["means"],
                    "actually": declared["long"] or [declared["name"]],
                    "takes_value": declared["takes_value"],
                    "verdict": verdict,
                    "why": why,
                }
            )

    report = {
        "total_commands": len(commands),
        "commands_declaring_dash_h": sorted(r["command"] for r in commands if r.get("declares_h")),
        "load_errors": [r for r in rows if "load_error" in r],
        "commands": commands,
        "unbound_conventions": unbound,
        "triaged_collisions": triaged,
    }

    if "--summary" in sys.argv:
        silent = [t for t in triaged if t["verdict"] == "SILENT"]
        print(f"commands: {report['total_commands']}")
        print(
            f"declare -h themselves: {len(report['commands_declaring_dash_h'])} {report['commands_declaring_dash_h']}"
        )
        print(f"load errors: {len(report['load_errors'])}")
        for verdict in ("CONVENTIONAL", "SILENT", "LOUD"):
            print(f"{verdict}: {sum(1 for t in triaged if t['verdict'] == verdict)}")
        print("\nSILENT (exit 0, wrong answer):")
        for s in silent:
            print(f"  {s['command']} {s['flag']} -> {','.join(s['actually'])}  [{s['why']}]")
        by_flag: dict[str, int] = {}
        for s in silent:
            by_flag[s["flag"]] = by_flag.get(s["flag"], 0) + 1
        for flag, count in sorted(by_flag.items(), key=lambda kv: -kv[1]):
            print(f"  {flag}: {count}")
        return 0

    json.dump(report, sys.stdout, indent=2, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
