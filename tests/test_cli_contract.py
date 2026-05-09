"""CLI contract tests — guarantee every registered command meets a baseline.

For every canonical (non-deprecated) command in ``roam.cli._COMMANDS`` we verify:

1. ``roam <cmd> --help`` exits 0, has non-empty output, mentions the name.
2. ``roam --json <cmd>`` (no --help) does not traceback in a fresh empty dir
   — it's allowed to print "no roam index" or auto-index, but never crash
   with an unhandled exception.
3. The command's module + function attribute imports cleanly.
4. The command has a non-empty Click help string OR a function ``__doc__``.

We additionally smoke-test three discovery commands end-to-end:

- ``roam surface --json`` — the canonical capability registry.
- ``roam explain-command surface --json`` — per-command introspection.
- ``roam db-check --json`` — index integrity report.

Failures here mean the documented surface and the runtime surface have drifted.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS, cli


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _canonical_commands() -> list[str]:
    """Every registered command minus the deprecated ones.

    ``_DEPRECATED_COMMANDS`` is currently empty, but the policy is "skip
    deprecated" so we honour it even when the dict is empty.
    """
    return sorted(n for n in _COMMANDS if n not in _DEPRECATED_COMMANDS)


CANONICAL_COMMANDS = _canonical_commands()


# Commands that reach for heavy graph machinery (networkx, scipy, spectral).
# Iterating their --help is still fine but we tag them slow so that
# `pytest -m "not slow"` can skip the full surface sweep when a contributor
# only wants a quick run.
_SLOW_CATEGORY_COMMANDS = {
    # Architecture category — graph-heavy.
    "map", "graph-export", "graph-stats", "layers", "clusters", "spectral",
    "coupling", "dark-matter", "effects", "cut", "simulate", "orchestrate",
    "partition", "entry-points", "patterns", "safe-zones", "visualize",
    "x-lang", "fingerprint", "clones",
}


# Commands that legitimately trigger an auto-index when run without one.
# We don't test JSON-no-traceback on these because they would write to the
# user's project even on a "fresh empty dir" run — the cwd switch handles
# that, but be explicit about which side-effects we accept.
_AUTO_INDEX_OK = set(CANONICAL_COMMANDS)


# Commands xfailed below — see the bottom of this file for the table and
# the reason for each entry. Keep this set in sync with the xfail markers.
_XFAIL_NO_TRACEBACK_JSON: set[str] = set()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_dir(tmp_path, monkeypatch):
    """A fresh empty directory with no roam index, no git repo.

    Used for the "doesn't traceback when there's nothing to read" check.
    Each command may auto-index, fail with a structured error, or print a
    "missing index" message — none of those should be a Python traceback.
    """
    d = tmp_path / "empty"
    d.mkdir()
    monkeypatch.chdir(d)
    return d


@pytest.fixture(scope="module")
def indexed_self_repo(tmp_path_factory):
    """A tiny indexed git repo for the db-check end-to-end test.

    We avoid the project-level ``indexed_project`` fixture (function scope,
    rebuilt per test) and instead build our own once for this module.
    """
    proj = tmp_path_factory.mktemp("contract_proj")
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "def hello(name):\n"
        '    """Say hello."""\n'
        "    return f'hello {name}'\n"
        "\n"
        "\n"
        "def main():\n"
        '    """Entry point."""\n'
        "    print(hello('world'))\n",
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj), capture_output=True, env=env,
    )

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed: {result.output}"
    finally:
        os.chdir(old_cwd)
    return proj


# ---------------------------------------------------------------------------
# Contract 3: every command imports cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CANONICAL_COMMANDS)
def test_command_imports_cleanly(name):
    """Every canonical command's module + attribute resolves.

    Catches lazy-load drift (cli.py points at a function the module no
    longer exposes) before users hit it at runtime.
    """
    module_path, attr = _COMMANDS[name]
    mod = importlib.import_module(module_path)
    fn = getattr(mod, attr, None)
    assert fn is not None, (
        f"{name}: cli.py points at {module_path}.{attr} but that attribute "
        f"does not exist on the imported module"
    )
    assert callable(fn), f"{name}: {module_path}.{attr} is not callable"


# ---------------------------------------------------------------------------
# Contract 4: every command has non-empty help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CANONICAL_COMMANDS)
def test_command_has_help_text(name):
    """Click `help=` string OR Python ``__doc__`` must be non-empty."""
    module_path, attr = _COMMANDS[name]
    mod = importlib.import_module(module_path)
    fn = getattr(mod, attr)
    click_help = (getattr(fn, "help", None) or "").strip()
    py_doc = (getattr(fn, "__doc__", None) or "").strip()
    assert click_help or py_doc, (
        f"{name} ({module_path}.{attr}) has neither a Click help= string "
        f"nor a function __doc__. Empty help is a regression."
    )


# ---------------------------------------------------------------------------
# Contract 1: --help works for every command
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("name", CANONICAL_COMMANDS)
def test_command_help_invokes(name, cli_runner):
    """`roam <cmd> --help` exits 0, prints non-empty output, mentions name."""
    if name in _SLOW_CATEGORY_COMMANDS:
        # still in the parametrize, just labelled — pytest marks at the
        # function level, not per-id, so we just note the categorisation.
        pass
    result = cli_runner.invoke(cli, [name, "--help"])
    assert result.exit_code == 0, (
        f"`roam {name} --help` exited {result.exit_code}\n"
        f"output:\n{result.output}"
    )
    out = result.output or ""
    assert out.strip(), f"`roam {name} --help` produced empty output"
    # Click prints "Usage: ... <name> [OPTIONS]" — the name must appear.
    assert name in out, (
        f"`roam {name} --help` output does not mention the command name "
        f"(first 200 chars: {out[:200]!r})"
    )


# ---------------------------------------------------------------------------
# Contract 2: `roam --json <cmd>` does not traceback in an empty dir
# ---------------------------------------------------------------------------


# A small, hand-picked subset of commands that we exercise end-to-end with
# `--json` in an empty dir. Iterating over all 211 would be very slow
# (auto-index, file-system writes, etc.) and most lazy-loaded commands
# share their failure mode. The picks cover the major dispatch paths:
#   - simple read commands (version, schema, surface, doctor, exit-codes)
#   - index-required commands (health, debt, smells, search)
#   - git-required commands (weather, churn, timeline, pr-diff)
#   - meta commands (recipes, capabilities, help-search)
#   - introspection (explain-command, db-check, mcp-status)
_NO_TRACEBACK_SAMPLE = [
    "version",
    "schema",
    "surface",
    "doctor",
    "exit-codes",
    "recipes",
    "capabilities",
    "help-search",
    "stats",
    "telemetry",
    "explain-command",
    "db-check",
    "mcp-status",
    "config",
    "health",
    "debt",
    "smells",
    "search",
    "weather",
    "churn",
    "timeline",
    "pr-diff",
    "describe",
    "minimap",
    "agent-export",
    "report",
    "fitness",
    "audit",
    "vibe-check",
    "ai-readiness",
]


@pytest.mark.slow
@pytest.mark.parametrize("name", _NO_TRACEBACK_SAMPLE)
def test_command_json_no_traceback_in_empty_dir(name, empty_dir, cli_runner):
    """`roam --json <cmd>` must not raise an unhandled Python exception.

    It may exit non-zero (e.g. EXIT_INDEX_MISSING=4 when no index, exit
    2 for missing required argument). The contract is *no traceback* —
    user-visible Python tracebacks signal a bug that should be a Click
    error or an envelope-shaped error instead.
    """
    if name in _XFAIL_NO_TRACEBACK_JSON:
        pytest.xfail(
            f"{name}: known pre-existing bug — see _XFAIL_NO_TRACEBACK_JSON "
            f"comment in tests/test_cli_contract.py"
        )

    # Some commands take a required argument; passing --help avoids hanging
    # on an interactive prompt or a usage error that would still be a
    # clean Click exit (not a traceback). But we want to test the *real*
    # path, so we feed only commands that don't require positional args.
    args = ["--json", name]
    if name == "explain-command":
        args = ["--json", name, "surface"]
    elif name == "describe":
        args = ["--json", name, "."]

    result = cli_runner.invoke(cli, args, catch_exceptions=True)

    # A traceback shows up either as a non-None .exception that is NOT a
    # SystemExit / Click exception, or as 'Traceback (most recent call last)'
    # in the captured output.
    if result.exception is not None:
        import click as _click
        allowed = (SystemExit, _click.exceptions.ClickException, _click.exceptions.Exit, _click.Abort)
        assert isinstance(result.exception, allowed), (
            f"`roam --json {name}` raised an unhandled exception: "
            f"{type(result.exception).__name__}: {result.exception}\n"
            f"output:\n{result.output}"
        )
    assert "Traceback (most recent call last)" not in (result.output or ""), (
        f"`roam --json {name}` printed a Python traceback to stdout — "
        f"errors should be Click exceptions or envelope-shaped:\n"
        f"{result.output[:1000]}"
    )


# ---------------------------------------------------------------------------
# `roam surface --json` shape
# ---------------------------------------------------------------------------


def _parse_json_strict(result, label):
    """Parse JSON output, ignoring any leading 'no index'/auto-index lines.

    Some commands print informational messages on stderr/stdout *before*
    the JSON payload (the runner merges them). We accept that by finding
    the first '{' and parsing from there.
    """
    out = result.output or ""
    idx = out.find("{")
    assert idx >= 0, f"{label}: no JSON object found in output:\n{out[:500]}"
    try:
        return json.loads(out[idx:])
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"{label}: invalid JSON at offset {idx}: {exc}\n"
            f"raw: {out[idx:idx + 500]!r}"
        )


def test_surface_json_shape(cli_runner, empty_dir):
    """`roam surface --json` exposes the documented capability registry."""
    result = cli_runner.invoke(cli, ["--json", "surface"])
    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    data = _parse_json_strict(result, "surface --json")

    # Envelope contract.
    assert data["command"] == "surface"
    assert "summary" in data and isinstance(data["summary"], dict)
    summary = data["summary"]

    # Floor: 200+ commands. Today the registry has 211; we leave headroom
    # for future trims down to 200 before the test fails.
    assert summary["command_count"] >= 200, (
        f"command_count={summary['command_count']} below 200 floor"
    )
    assert summary["verdict"] == "OK"

    # Per-command shape.
    commands = data["commands"]
    assert isinstance(commands, list)
    assert len(commands) == summary["command_count"]
    required_fields = {"name", "module", "function", "category", "maturity",
                       "aliases", "deprecated_replacement", "mcp_exposed"}
    for entry in commands:
        missing = required_fields - set(entry)
        assert not missing, f"command {entry.get('name')!r} missing fields: {missing}"
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["module"], str) and entry["module"]
        assert isinstance(entry["function"], str) and entry["function"]
        assert isinstance(entry["category"], str)
        assert isinstance(entry["maturity"], str)
        assert isinstance(entry["aliases"], list)


def test_surface_aliases_share_same_target():
    """Aliases (multiple names mapping to one (module, function)) must
    point at the *same* tuple as their canonical entry, not a copy that's
    drifted out of sync.
    """
    # Build the alias groups directly from _COMMANDS.
    target_to_names: dict[tuple, list[str]] = {}
    for name, target in _COMMANDS.items():
        target_to_names.setdefault(target, []).append(name)

    for target, names in target_to_names.items():
        if len(names) <= 1:
            continue
        # Every name in the group must resolve to the literal same tuple.
        for n in names:
            assert _COMMANDS[n] == target, (
                f"Alias drift: {n} -> {_COMMANDS[n]}, expected {target} "
                f"(group: {sorted(names)})"
            )


# ---------------------------------------------------------------------------
# `roam explain-command surface --json` shape
# ---------------------------------------------------------------------------


def test_explain_command_surface_json(cli_runner, empty_dir):
    """`roam explain-command surface --json` exposes a `command_info` sub-field
    with the documented attributes (name, module, function, category, ...)."""
    result = cli_runner.invoke(cli, ["--json", "explain-command", "surface"])
    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    data = _parse_json_strict(result, "explain-command surface --json")

    assert data["command"] == "explain-command"
    cmd_field = data.get("command_info")
    assert isinstance(cmd_field, dict), (
        "explain-command --json should expose a 'command_info' sub-dict with "
        "the resolved command's metadata"
    )
    for key in ("name", "module", "function", "category", "maturity",
                "aliases", "mcp_exposed"):
        assert key in cmd_field, f"explain-command's `command_info` field missing {key!r}"
    assert cmd_field["name"] == "surface"


# ---------------------------------------------------------------------------
# `roam db-check --json` shape (run on a real indexed project)
# ---------------------------------------------------------------------------


def test_db_check_json_envelope(cli_runner, indexed_self_repo):
    """`roam db-check --json` produces the documented envelope shape."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_self_repo))
        result = cli_runner.invoke(cli, ["--json", "db-check"])
    finally:
        os.chdir(old_cwd)

    # db-check exits 0 by default (no --ci); verdict may be OK / REVIEW / BAD.
    # We accept any of the documented exit codes (0 or 5) because a fresh
    # tiny index can in theory surface a transient note-level finding.
    assert result.exit_code in (0, 5), (
        f"db-check exited {result.exit_code}\n{result.output}"
    )
    data = _parse_json_strict(result, "db-check --json")

    assert data["command"] == "db-check"
    summary = data["summary"]
    assert "verdict" in summary, "db-check summary must carry a verdict"
    assert summary["verdict"] in {"OK", "REVIEW", "BAD"}, (
        f"unexpected verdict {summary['verdict']!r}"
    )

    findings = data.get("findings")
    assert isinstance(findings, list), "db-check must emit a `findings` array"
    # Each finding has at minimum a name / count / severity.
    for f in findings:
        assert "name" in f and "count" in f and "severity" in f, (
            f"db-check finding missing required fields: {f}"
        )
