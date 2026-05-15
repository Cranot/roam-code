"""Tests for the ``caller_metric_definition`` label on JSON envelopes.

Fix C from the 2026-05-12 dogfood corpus: every command that reports a
"callers" / "consumers" / "fan_in" / "in-degree" count must label which
of the four canonical metrics it emits — see
``docs/concepts/caller-metrics.md``. The canonical labels are:

* ``raw_edge_rows`` — every row in ``edges`` (per-file multiplicity).
* ``direct_in_degree`` — ``graph_metrics.in_degree`` (distinct upstream).
* ``distinct_caller_tuples`` — production-scope deduped tuples.
* ``transitive_upstream_bfs`` — BFS multi-hop reach.

Each test invokes the command in JSON mode against a small fixture
project and asserts that ``summary.caller_metric_definition`` is set to
the expected label (or starts with it, when the label carries
field-name context). A final vocabulary-invariant test scans every
``cmd_*.py`` module for new commands that expose a callers count but
forget the label — this is the regression net for future contributors.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CALLER_METRIC_LABELS = {
    "raw_edge_rows",
    "direct_in_degree",
    "distinct_caller_tuples",
    "transitive_upstream_bfs",
    "transitive_bfs_from_entry",
}


def _parse_json(result, command: str) -> dict:
    """Parse JSON output from a CliRunner result with helpful diagnostics."""
    assert result.exit_code == 0, (
        f"Command {command} failed (exit {result.exit_code}):\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Invalid JSON from {command}: {exc}\n{result.output[:500]}")


def _assert_label(summary: dict, expected_prefix: str, command: str) -> None:
    """Assert ``caller_metric_definition`` is present and matches the expected
    canonical label (prefix-match so trailing field qualifiers are allowed).
    """
    assert "caller_metric_definition" in summary, (
        f"{command} summary missing 'caller_metric_definition': {summary!r}"
    )
    value = summary["caller_metric_definition"]
    assert isinstance(value, str), (
        f"{command}.summary.caller_metric_definition should be str, got {type(value).__name__}"
    )
    assert value.startswith(expected_prefix), (
        f"{command}: expected caller_metric_definition starting with "
        f"{expected_prefix!r}, got {value!r}"
    )


# ---------------------------------------------------------------------------
# Per-command tests — every command that reports a callers count
# ---------------------------------------------------------------------------


class TestCallerMetricLabels:
    """Validate ``caller_metric_definition`` on every relevant command."""

    def test_uses_emits_raw_edge_rows(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["uses", "User"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "uses")
        _assert_label(data["summary"], "raw_edge_rows", "uses")

    def test_context_single_emits_raw_edge_rows(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["context", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "context")
        _assert_label(data["summary"], "raw_edge_rows", "context")

    def test_context_file_emits_raw_edge_rows(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner,
            ["context", "--for-file", "src/models.py"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = _parse_json(result, "context --for-file")
        _assert_label(data["summary"], "raw_edge_rows", "context --for-file")

    def test_diagnose_emits_transitive_upstream_bfs(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["diagnose", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "diagnose")
        _assert_label(data["summary"], "transitive_upstream_bfs", "diagnose")

    def test_understand_emits_direct_in_degree(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["understand"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "understand")
        _assert_label(data["summary"], "direct_in_degree", "understand")

    def test_oracle_is_test_only_emits_distinct_caller_tuples(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner,
            ["oracle", "is-test-only", "create_user"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = _parse_json(result, "oracle is-test-only")
        _assert_label(data["summary"], "distinct_caller_tuples", "oracle is-test-only")

    def test_oracle_is_reachable_emits_transitive_bfs(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner,
            ["oracle", "is-reachable-from-entry", "create_user"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = _parse_json(result, "oracle is-reachable-from-entry")
        _assert_label(
            data["summary"], "transitive_bfs_from_entry", "oracle is-reachable-from-entry"
        )

    def test_deps_emits_raw_edge_rows(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["deps", "src/service.py"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "deps")
        _assert_label(data["summary"], "raw_edge_rows", "deps")

    def test_fan_symbol_emits_direct_in_degree(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["fan", "symbol"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "fan symbol")
        # ``fan`` may legitimately report ``items: 0`` on tiny projects; in
        # that case the empty envelope is exempt because no caller metric
        # was computed. Only assert when items > 0.
        if data["summary"].get("items", 0) > 0:
            _assert_label(data["summary"], "direct_in_degree", "fan symbol")

    def test_symbol_emits_direct_in_degree(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["symbol", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "symbol")
        _assert_label(data["summary"], "direct_in_degree", "symbol")

    def test_metrics_symbol_emits_direct_in_degree(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["metrics", "create_user"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "metrics (symbol)")
        _assert_label(data["summary"], "direct_in_degree", "metrics (symbol)")

    def test_metrics_file_emits_direct_in_degree(self, cli_runner, indexed_project):
        result = invoke_cli(
            cli_runner, ["metrics", "src/service.py"], cwd=indexed_project, json_mode=True
        )
        data = _parse_json(result, "metrics (file)")
        _assert_label(data["summary"], "direct_in_degree", "metrics (file)")

    def test_minimap_emits_direct_in_degree(self, cli_runner, indexed_project):
        result = invoke_cli(cli_runner, ["minimap"], cwd=indexed_project, json_mode=True)
        data = _parse_json(result, "minimap")
        _assert_label(data["summary"], "direct_in_degree", "minimap")


# ---------------------------------------------------------------------------
# Vocabulary invariant — every cmd_*.py that reports a callers count must
# include caller_metric_definition somewhere in its summary block.
# ---------------------------------------------------------------------------


# Files known to legitimately not need the label (no callers/fan_in/etc.
# count surfaced in their JSON summary). Update this set when a new
# command is introduced and audited.
_KNOWN_EXEMPT_FILES: set[str] = {
    # Add cmd_*.py basenames here ONLY after confirming the command does
    # not surface a callers/consumers/fan_in/in_degree number in its JSON
    # summary. Empty by default — the test should detect new offenders.
}

# Files that the audit must include (commands explicitly required by
# Fix C). If any of these stop emitting the label, the test fails even
# if our scanner regex changes.
_REQUIRED_FILES: set[str] = {
    "cmd_uses.py",
    "cmd_context.py",
    "cmd_diagnose.py",
    "cmd_understand.py",
    "cmd_oracle.py",
    "cmd_deps.py",
    "cmd_minimap.py",
    "cmd_fan.py",
    "cmd_symbol.py",
    "cmd_metrics.py",
}


_CALLER_FIELD_RE = re.compile(
    # Match a summary dict literal containing any of the caller-style keys.
    # Conservative: look for the key as a quoted string literal inside a
    # summary={ ... } or summary block. Multi-line summaries are common so
    # this is intentionally a loose substring check on the file content.
    # W335: added ``caller_count`` — cmd_invariants emits this as a raw
    # ``len(callers)`` count (raw_edge_rows shape) so it must be labeled
    # like cmd_uses / cmd_context.
    r"""(?xs)
    summary\s*=\s*\{[^{}]*?
    ['"](
        callers
      | caller_count
      | total_consumers
      | production_consumers
      | upstream_count
      | caller_files
      | fan_in
      | in_degree
    )['"]
    """,
)


def _commands_dir() -> Path:
    """Return the ``src/roam/commands/`` directory path."""
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    return repo_root / "src" / "roam" / "commands"


def _file_reports_callers_count(text: str) -> bool:
    """Return True when the source text appears to emit a callers-style
    count inside a ``summary={...}`` dict literal.
    """
    return bool(_CALLER_FIELD_RE.search(text))


def _file_has_caller_metric_definition(text: str) -> bool:
    """Return True when the source text mentions ``caller_metric_definition``
    anywhere (we trust the surrounding context — false positives here
    would mean someone wrote the label in a comment without using it).
    """
    return "caller_metric_definition" in text


def test_required_files_have_caller_metric_definition() -> None:
    """All Fix-C-target commands emit the label in source."""
    cmds_dir = _commands_dir()
    missing = []
    for fname in sorted(_REQUIRED_FILES):
        path = cmds_dir / fname
        if not path.exists():
            missing.append(f"{fname}: file not found")
            continue
        text = path.read_text(encoding="utf-8")
        if not _file_has_caller_metric_definition(text):
            missing.append(f"{fname}: no 'caller_metric_definition' in source")
    assert not missing, (
        "Fix C requires these files to emit caller_metric_definition:\n  "
        + "\n  ".join(missing)
    )


def test_no_new_commands_forget_caller_metric_definition() -> None:
    """Vocabulary invariant: every cmd_*.py that surfaces a callers-style
    count in a ``summary={...}`` block must also reference
    ``caller_metric_definition`` somewhere in the file.

    This is the regression net for future commands. When you add a new
    command that reports callers, fan_in, etc., add the label too.
    """
    cmds_dir = _commands_dir()
    offenders: list[str] = []
    for cmd_file in sorted(cmds_dir.glob("cmd_*.py")):
        if cmd_file.name in _KNOWN_EXEMPT_FILES:
            continue
        text = cmd_file.read_text(encoding="utf-8")
        if not _file_reports_callers_count(text):
            continue
        if not _file_has_caller_metric_definition(text):
            offenders.append(cmd_file.name)
    assert not offenders, (
        "These commands report a callers/consumers/fan_in count in a "
        "summary block but never label it with caller_metric_definition.\n"
        "Either add the label (see docs/concepts/caller-metrics.md) or add "
        "the file to _KNOWN_EXEMPT_FILES in this test with a comment "
        "explaining why it doesn't apply.\n  Offenders: "
        + ", ".join(offenders)
    )
