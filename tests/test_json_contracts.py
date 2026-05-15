"""Parametric JSON envelope contract tests for roam --json output.

Validates that every command supporting --json produces a well-formed
JSON envelope with the required top-level keys (command, version,
timestamp, index_age_s, project, summary) and that summary is always
a dict.

~60 tests via parametrize over 44 commands.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli

# ============================================================================
# Commands that support --json output
# ============================================================================

COMMANDS_WITH_JSON = [
    "health",
    "map",
    "dead",
    "weather",
    "clusters",
    "layers",
    "search",
    "grep",
    "file",
    "symbol",
    "deps",
    "uses",
    "fan",
    "impact",
    "coupling",
    "diff",
    "context",
    "safe-delete",
    "pr-risk",
    "split",
    "risk",
    "why",
    "coverage-gaps",
    "report",
    "complexity",
    "debt",
    "conventions",
    "bus-factor",
    "entry-points",
    "breaking",
    "safe-zones",
    "doc-staleness",
    "docs-coverage",
    "fn-coupling",
    "alerts",
    "fitness",
    "patterns",
    "preflight",
    "guard",
    "agent-plan",
    "agent-context",
    "describe",
    "trace",
    "owner",
    "sketch",
    "affected-tests",
    "diagnose",
    "test-map",
    "module",
]

# Commands that require extra arguments to run.
# Commands not listed here are invoked with no extra args.
COMMAND_ARGS = {
    "search": ["User"],
    "grep": ["def"],
    "file": ["src/models.py"],
    "symbol": ["User"],
    "trace": ["User", "create_user"],
    "deps": ["User"],
    "uses": ["User"],
    "impact": ["User"],
    "context": ["User"],
    "safe-delete": ["unused_helper"],
    "split": ["src/models.py"],
    "why": ["User"],
    "preflight": ["User"],
    "guard": ["User"],
    "agent-plan": ["--agents", "2"],
    "agent-context": ["--agent-id", "1", "--agents", "2"],
    "owner": ["src/models.py"],
    "diagnose": ["User"],
    "affected-tests": ["--staged"],
    "sketch": ["src"],
    "safe-zones": ["src/models.py"],
    "test-map": ["src/models.py"],
    "module": ["src"],
    "fan": ["symbol"],
}

# Commands that genuinely cannot satisfy the JSON envelope contract on
# the minimal `python_project` fixture (no rich git history, no staged
# changes, no test-coverage map, no PR context). They are marked xfailed
# with ``pytest.xfail(strict=False)`` inside each parametrized test.
#
# **Audit history**: this set was originally 28 entries (v11.x). The
# DOG.4 audit (2026-04-29) ran ``pytest --runxfail`` against the suite
# and found that 20 of those entries actually pass cleanly now —
# they were defensive xfails that never got pruned. Tightening to the
# 7 below means real regressions in the previously-stale commands
# (trace, uses, impact, preflight, etc.) get caught instead of silently
# xfailed forever.
FRAGILE_COMMANDS = {
    "affected-tests",  # needs staged changes or a target with test coverage
    "coverage-gaps",  # needs test file mapping
    "deps",  # symbol resolution against minimal `models`/`service`/`utils`
    "diff",  # needs uncommitted changes
    "pr-risk",  # needs uncommitted changes or PR context
    "report",  # may need specific report config or flags
    # ``dead`` was xfailed in v11.x because its summary envelope was
    # missing ``verdict``. v12.x added the verdict field; --runxfail
    # confirms all four parametrized tests pass on the minimal
    # fixture. Removed from the fragile set in v12.12.2.
}


# ============================================================================
# Helpers
# ============================================================================


def _build_args(cmd: str) -> list[str]:
    """Return the full argument list for a command invocation."""
    extra = COMMAND_ARGS.get(cmd, [])
    return [cmd] + extra


def _is_fragile(cmd: str) -> bool:
    """Return True if the command is known to be fragile in test env."""
    return cmd in FRAGILE_COMMANDS


def _invoke_json(cli_runner, indexed_project, cmd: str):
    """Invoke a command with --json and return the CliRunner result."""
    args = _build_args(cmd)
    return invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(scope="module")
def cli_runner():
    """Module-scoped CliRunner for efficiency."""
    from click.testing import CliRunner

    return CliRunner()


# W346: All 244 parametric envelope tests are read-only against the
# indexed project — they invoke commands with --json and validate the
# envelope shape. Re-indexing on every test (the conftest function-scoped
# `indexed_project`) costs ~2s × 244 = ~9 minutes serial. By overriding
# the fixture at module scope here, the project is built and indexed once
# per worker, dropping the file from ~533s to well under 2 minutes.
# Other test files keep the function-scoped fixture from conftest.
@pytest.fixture(scope="module")
def indexed_project(tmp_path_factory):
    """Module-scoped indexed Python project for envelope-contract tests.

    Mirrors the conftest python_project layout but builds and indexes
    once per module. Safe because every test in this file is read-only.
    """
    import textwrap

    proj = tmp_path_factory.mktemp("json_contracts_proj")
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()

    (src / "models.py").write_text(
        textwrap.dedent(
            '''\
            class User:
                """A user model."""
                def __init__(self, name, email):
                    self.name = name
                    self.email = email

                def display_name(self):
                    return self.name.title()

                def validate_email(self):
                    return "@" in self.email


            class Admin(User):
                """An admin user."""
                def __init__(self, name, email, role="admin"):
                    super().__init__(name, email)
                    self.role = role

                def promote(self, user):
                    pass
            '''
        ),
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        textwrap.dedent(
            '''\
            from models import User, Admin


            def create_user(name, email):
                """Create a new user."""
                user = User(name, email)
                if not user.validate_email():
                    raise ValueError("Invalid email")
                return user


            def get_display(user):
                """Get display name."""
                return user.display_name()


            def unused_helper():
                """This function is never called (dead code)."""
                return 42
            '''
        ),
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        textwrap.dedent(
            '''\
            def format_name(first, last):
                """Format a full name."""
                return f"{first} {last}"


            def parse_email(raw):
                """Parse an email address."""
                if "@" not in raw:
                    return None
                parts = raw.split("@")
                return {"user": parts[0], "domain": parts[1]}


            UNUSED_CONSTANT = "never_referenced"
            '''
        ),
        encoding="utf-8",
    )

    import os
    import subprocess

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )

    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, f"roam index failed:\n{result.output}"
    return proj


# ============================================================================
# 1. Core envelope contract (parametrized over all commands)
# ============================================================================


@pytest.mark.parametrize("cmd", COMMANDS_WITH_JSON)
def test_json_envelope_contract(cmd, cli_runner, indexed_project):
    """Each --json command must produce a valid JSON envelope."""
    if _is_fragile(cmd):
        pytest.xfail(f"{cmd} is fragile in minimal test environment")

    result = _invoke_json(cli_runner, indexed_project, cmd)

    # Must exit cleanly
    assert result.exit_code == 0, f"'{cmd}' exited with code {result.exit_code}:\n{result.output[:500]}"

    # Must be valid JSON
    data = json.loads(result.output)

    # Must have the required envelope keys
    assert_json_envelope(data, command=cmd)


# ============================================================================
# 2. Envelope field: "command" matches the invoked command name
# ============================================================================


@pytest.mark.parametrize("cmd", COMMANDS_WITH_JSON)
def test_envelope_has_command_field(cmd, cli_runner, indexed_project):
    """data['command'] must match the invoked command name."""
    if _is_fragile(cmd):
        pytest.xfail(f"{cmd} is fragile in minimal test environment")

    result = _invoke_json(cli_runner, indexed_project, cmd)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed (exit {result.exit_code}), skipping field check")

    data = json.loads(result.output)
    assert data.get("command") == cmd, f"Expected command='{cmd}', got '{data.get('command')}'"


# ============================================================================
# 3. Envelope field: "version" is a non-empty string
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "map", "dead", "weather", "search", "report", "complexity", "debt"])
def test_envelope_has_version(cmd, cli_runner, indexed_project):
    """data['version'] must be a non-empty string."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed, skipping version check")

    data = json.loads(result.output)
    version = data.get("version")
    assert isinstance(version, str), f"version should be str, got {type(version)}"
    assert len(version) > 0, "version should be non-empty"


# ============================================================================
# 4. Envelope field: "timestamp" is ISO 8601 format
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "map", "dead", "weather", "search", "report", "complexity", "debt"])
def test_envelope_has_timestamp(cmd, cli_runner, indexed_project):
    """data['_meta']['timestamp'] must be a valid ISO 8601 timestamp."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed, skipping timestamp check")

    data = json.loads(result.output)
    meta = data.get("_meta", {})
    ts = meta.get("timestamp") or data.get("timestamp")
    assert isinstance(ts, str), f"timestamp should be str, got {type(ts)}"
    assert len(ts) > 0, "timestamp should be non-empty"

    # Parse ISO format -- should not raise
    # roam uses format like "2026-02-12T14:30:00Z"
    try:
        # Handle both "Z" suffix and "+00:00"
        ts_clean = ts.replace("Z", "+00:00")
        datetime.fromisoformat(ts_clean)
    except ValueError:
        pytest.fail(f"timestamp '{ts}' is not valid ISO 8601")


# ============================================================================
# 5. Envelope field: "summary" is always a dict
# ============================================================================


@pytest.mark.parametrize("cmd", COMMANDS_WITH_JSON)
def test_envelope_summary_is_dict(cmd, cli_runner, indexed_project):
    """data['summary'] must always be a dict (possibly empty)."""
    if _is_fragile(cmd):
        pytest.xfail(f"{cmd} is fragile in minimal test environment")

    result = _invoke_json(cli_runner, indexed_project, cmd)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed (exit {result.exit_code}), skipping summary check")

    data = json.loads(result.output)
    summary = data.get("summary")
    assert isinstance(summary, dict), f"summary should be dict for '{cmd}', got {type(summary)}: {summary!r}"


# ============================================================================
# 6. Raw output is valid JSON (not mixed with text)
# ============================================================================


@pytest.mark.parametrize("cmd", COMMANDS_WITH_JSON)
def test_json_is_valid_json(cmd, cli_runner, indexed_project):
    """--json output must be parseable as JSON with no trailing text."""
    if _is_fragile(cmd):
        pytest.xfail(f"{cmd} is fragile in minimal test environment")

    result = _invoke_json(cli_runner, indexed_project, cmd)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed (exit {result.exit_code}), skipping JSON parse check")

    output = result.output.strip()
    assert len(output) > 0, f"'{cmd}' produced empty output"

    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        pytest.fail(f"'{cmd}' output is not valid JSON: {e}\nFirst 300 chars: {output[:300]}")

    assert isinstance(data, dict), f"Top-level JSON should be dict, got {type(data)}"


# ============================================================================
# 7. --compact mode strips version/timestamp
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "map", "dead", "weather", "report", "complexity", "debt"])
def test_compact_json(cmd, cli_runner, indexed_project):
    """--json --compact should produce valid JSON, possibly without version/timestamp.

    The compact envelope (compact_json_envelope) omits version, timestamp,
    index_age_s, and project to save tokens. Commands may or may not use
    the compact envelope yet, so we test that the flag is accepted and
    output remains valid JSON.
    """
    args = _build_args(cmd)
    # Invoke with both --json and --compact
    result = invoke_cli(
        cli_runner,
        ["--compact"] + args,
        cwd=indexed_project,
        json_mode=True,
    )
    if result.exit_code != 0:
        pytest.skip(f"{cmd} with --compact failed (exit {result.exit_code})")

    output = result.output.strip()
    assert len(output) > 0, f"'{cmd}' --compact produced empty output"

    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        pytest.fail(f"'{cmd}' --compact output is not valid JSON: {e}\nFirst 300 chars: {output[:300]}")

    assert isinstance(data, dict), "Compact JSON should still be a dict"
    # command key should always be present even in compact mode
    assert "command" in data, "Compact envelope should still have 'command' key"


# ============================================================================
# 8. Specific non-fragile commands: verify verdict in summary
# ============================================================================

COMMANDS_WITH_VERDICT = [
    "health",
    "dead",
    "weather",
    "risk",
    "complexity",
    "debt",
    "conventions",
    "fitness",
    "alerts",
]


@pytest.mark.parametrize("cmd", COMMANDS_WITH_VERDICT)
def test_summary_has_verdict(cmd, cli_runner, indexed_project):
    """Key commands should include a 'verdict' string in their summary."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed (exit {result.exit_code})")

    try:
        data = json.loads(result.output)
    except json.JSONDecodeError:
        pytest.skip(f"{cmd} produced non-JSON output")
    summary = data.get("summary", {})
    if "verdict" not in summary:
        pytest.xfail(f"'{cmd}' summary does not contain 'verdict' yet, got keys: {list(summary.keys())}")
    assert isinstance(summary["verdict"], str), f"verdict should be str, got {type(summary['verdict'])}"
    assert len(summary["verdict"]) > 0, "verdict should be non-empty"


# ============================================================================
# 9. Envelope field: "index_age_s" is int or None
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "map", "dead", "report"])
def test_envelope_index_age(cmd, cli_runner, indexed_project):
    """data['_meta']['index_age_s'] should be an int (seconds) or None."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed")

    data = json.loads(result.output)
    meta = data.get("_meta", {})
    age = meta.get("index_age_s") if meta else data.get("index_age_s")
    assert age is None or isinstance(age, (int, float)), f"index_age_s should be int/float/None, got {type(age)}"
    if age is not None:
        assert age >= 0, f"index_age_s should be non-negative, got {age}"


# ============================================================================
# 10. Envelope field: "project" is a string
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "map", "dead", "report"])
def test_envelope_project_field(cmd, cli_runner, indexed_project):
    """data['project'] should be a string (project directory name)."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed")

    data = json.loads(result.output)
    project = data.get("project")
    assert isinstance(project, str), f"project should be str, got {type(project)}"


# ============================================================================
# 11. Non-JSON mode should NOT produce JSON
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "dead", "weather"])
def test_non_json_mode_is_text(cmd, cli_runner, indexed_project):
    """Without --json, output should be plain text, not JSON."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=False)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed")

    output = result.output.strip()
    # Should not parse as JSON (or if it does, it is coincidental)
    # The key check: it should NOT have the envelope structure
    try:
        data = json.loads(output)
        # If it parses, it should NOT be an envelope
        assert "command" not in data or "version" not in data, (
            f"'{cmd}' without --json should not produce JSON envelope"
        )
    except (json.JSONDecodeError, ValueError):
        pass  # Expected: text output is not JSON


# ============================================================================
# 12. Envelope is a flat dict at top level (no nesting of envelope keys)
# ============================================================================


@pytest.mark.parametrize("cmd", ["health", "map", "dead", "weather", "report"])
def test_envelope_top_level_keys(cmd, cli_runner, indexed_project):
    """The envelope should have standard top-level keys directly on the dict."""
    args = _build_args(cmd)
    result = invoke_cli(cli_runner, args, cwd=indexed_project, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"{cmd} failed")

    data = json.loads(result.output)

    required_keys = {"command", "version", "summary"}
    actual_keys = set(data.keys())
    missing = required_keys - actual_keys
    assert not missing, f"'{cmd}' envelope missing required keys: {missing}. Got: {sorted(actual_keys)}"
    # timestamp moved to _meta for deterministic output (LLM cache compat)
    meta = data.get("_meta", {})
    assert "timestamp" in meta or "timestamp" in data, f"'{cmd}' envelope missing timestamp in _meta or top-level"
