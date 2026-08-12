"""Sweep `-h` across every roam command and report exit codes.

Read-only. Prints `ok/total` plus every non-zero command.
Help is an eager option, so this never runs a command body.
"""

from __future__ import annotations

import sys

import click
from click.testing import CliRunner

from roam.cli import cli as root_cli


def _walk(cmd: click.Command, ctx: click.Context, path: list[str], out: list[list[str]]) -> None:
    """Recurse to ANY depth.

    A two-level walk missed the nine third-level commands under
    ``roam pr-bundle add`` / ``roam pr-bundle set`` — a denominator that
    silently excludes the deepest commands is the shape this whole lane is
    about, so the sweep recurses instead of assuming a depth.
    """
    if path:
        out.append(list(path))
    if isinstance(cmd, click.Group):
        for name in cmd.list_commands(ctx):
            sub = cmd.get_command(ctx, name)
            if sub is None:
                continue
            _walk(sub, click.Context(sub, info_name=name, parent=ctx), [*path, name], out)


def enumerate_paths() -> tuple[list[list[str]], list[list[str]]]:
    all_paths: list[list[str]] = []
    # context_settings is applied by make_context, not by Context(); pass it
    # explicitly or help_option_names silently falls back to ["--help"].
    ctx = click.Context(root_cli, info_name="roam", **root_cli.context_settings)
    _walk(root_cli, ctx, [], all_paths)
    top = [p for p in all_paths if len(p) == 1]
    sub = [p for p in all_paths if len(p) > 1]
    return top, sub


def sweep(paths: list[list[str]], label: str) -> tuple[int, list[tuple[str, int]]]:
    runner = CliRunner()
    bad: list[tuple[str, int]] = []
    ok = 0
    for path in paths:
        result = runner.invoke(root_cli, [*path, "-h"], catch_exceptions=True)
        if result.exit_code == 0 and "Usage:" in (result.output or ""):
            ok += 1
        else:
            bad.append((" ".join(path), result.exit_code))
    print(f"{label}: {ok}/{len(paths)} exit 0 with help text")
    for name, code in bad:
        print(f"  FAIL {name}: exit {code}")
    return ok, bad


def main() -> int:
    top, sub = enumerate_paths()

    if "--before" in sys.argv:
        # Reproduce the pre-fix baseline without touching git: put
        # help_option_names back to Click's default and re-resolve. Every
        # command caches its help option on first use, so clear those too or
        # the "before" run measures whatever a previous run left behind.
        print("[--before] help_option_names forced back to Click's ['--help'] default")
        root_cli.context_settings["help_option_names"] = ["--help"]
        ctx = click.Context(root_cli, info_name="roam", **root_cli.context_settings)
        stack = [(root_cli, ctx)]
        while stack:
            cmd, cctx = stack.pop()
            cmd._help_option = None
            if isinstance(cmd, click.Group):
                for name in cmd.list_commands(cctx):
                    sub_cmd = cmd.get_command(cctx, name)
                    if sub_cmd is not None:
                        stack.append((sub_cmd, click.Context(sub_cmd, info_name=name, parent=cctx)))

    ok_top, bad_top = sweep(top, "top-level commands")
    ok_sub, bad_sub = sweep(sub, "subcommands")
    print(f"TOTAL: {ok_top + ok_sub}/{len(top) + len(sub)}")
    return 1 if (bad_top or bad_sub) else 0


if __name__ == "__main__":
    sys.exit(main())
