"""Tests for ``roam compatibility`` (W1293).

Coverage:

  (a) baseline-vs-self -> ``no regressions`` verdict + breaking=0.
  (b) synthetic-removal scenarios -> ``breaking changes`` verdict +
      breaking>0; one assertion per closed-enum removal category
      (command / flag / envelope field / MCP tool).
  (c) renamed-command via ``deprecated_aliases`` does NOT count as
      breaking (graceful rename).
  (d) ``--ci`` exits 5 (EXIT_GATE_FAILURE) on breaking; 0 on clean.
  (e) ``--write-baseline`` produces a JSON file consumable by a
      follow-up diff.
  (f) missing baseline emits a structured envelope (Pattern-1 variant C
      compliance).

The tests deliberately avoid asserting absolute counts (240 commands,
224 MCP tools) because those are env-derived and change as the surface
evolves. Instead they assert STRUCTURAL invariants (verdict enum,
breaking>0 vs =0, presence of specific removed entries).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_compatibility import _build_snapshot, _diff, _verdict_for

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _live_baseline(tmp_path: Path) -> Path:
    """Capture the live build's snapshot into ``tmp_path/baseline.json``."""
    snapshot = _build_snapshot()
    return _write(tmp_path / "baseline.json", snapshot)


# ---------------------------------------------------------------------------
# (a) Baseline-vs-self -> no regressions
# ---------------------------------------------------------------------------


def test_baseline_vs_self_no_regressions(tmp_path):
    """The live build compared against a snapshot of itself must report
    ``no regressions`` and zero breaking entries."""
    baseline = _live_baseline(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(baseline)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    assert payload["command"] == "compatibility"
    assert payload["summary"]["verdict"] == "no regressions"
    assert payload["summary"]["breaking"] == 0
    assert payload["summary"]["removed"] == 0
    assert payload["summary"]["partial_success"] is False
    # Closed-enum top-level keys must be present.
    for key in (
        "removed_commands",
        "added_commands",
        "renamed_commands",
        "removed_flags",
        "added_flags",
        "removed_envelope_fields",
        "added_envelope_fields",
        "removed_mcp_tools",
        "added_mcp_tools",
        "changed_presets",
    ):
        assert key in payload, key


# ---------------------------------------------------------------------------
# (b) Synthetic-removal scenarios
# ---------------------------------------------------------------------------


def test_synthetic_command_removal_is_breaking(tmp_path):
    """Remove one command from the live snapshot, write it as baseline,
    then diff the live build against it. The MISSING command (present in
    baseline, absent from current) appears under ``removed_commands``."""
    snap = _build_snapshot()
    # Pick any canonical command, inject it as a fake baseline entry so
    # the live build looks like it removed it.
    fake = "_fake_dropped_cmd_w1293"
    snap["commands"][fake] = {
        "module": "roam.commands.cmd_does_not_exist",
        "function": "missing",
        "flags": ["--foo"],
    }
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(baseline)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert payload["summary"]["breaking"] >= 1
    assert fake in payload["removed_commands"]


def test_synthetic_flag_removal_is_breaking(tmp_path):
    """Inject a fake flag onto an existing command's baseline entry. The
    live build is missing that flag, so it appears under
    ``removed_flags``."""
    snap = _build_snapshot()
    # Pick any existing command.
    target = next(iter(sorted(snap["commands"].keys())))
    snap["commands"][target]["flags"] = sorted(
        set(snap["commands"][target]["flags"]) | {"--_fake_dropped_flag_w1293"}
    )
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(baseline)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert any(
        e["command"] == target and e["flag"] == "--_fake_dropped_flag_w1293"
        for e in payload["removed_flags"]
    )


def test_synthetic_envelope_field_removal_is_breaking(tmp_path):
    """Inject a fake envelope-summary field into baseline. The live
    build lists only the canonical fields, so the injected one appears
    under ``removed_envelope_fields``."""
    snap = _build_snapshot()
    snap["envelope_summary_keys"] = list(snap["envelope_summary_keys"]) + [
        "_fake_dropped_field_w1293"
    ]
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(baseline)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert "_fake_dropped_field_w1293" in payload["removed_envelope_fields"]


def test_synthetic_mcp_tool_removal_is_breaking(tmp_path):
    """Inject a fake MCP tool name into baseline. Live build is missing
    it -> ``removed_mcp_tools``."""
    snap = _build_snapshot()
    snap["mcp_tools"] = sorted(set(snap["mcp_tools"]) | {"roam__fake_w1293"})
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(baseline)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert "roam__fake_w1293" in payload["removed_mcp_tools"]


# ---------------------------------------------------------------------------
# (c) Renamed-via-alias does NOT count as breaking
# ---------------------------------------------------------------------------


def test_alias_rename_is_not_breaking():
    """A command removed from canonical names BUT now present as a
    deprecated alias pointing to a live name surfaces under
    ``renamed_commands`` and does NOT count toward ``breaking``."""
    baseline = {
        "schema_version": "1.0.0",
        "commands": {
            "oldname": {"module": "x", "function": "y", "flags": []},
            "newname": {"module": "x", "function": "y", "flags": []},
        },
        "deprecated_aliases": {},
        "mcp_tools": [],
        "mcp_preset_counts": {},
        "categories": [],
        "envelope_summary_keys": [],
    }
    current = {
        "schema_version": "1.0.0",
        "commands": {
            "newname": {"module": "x", "function": "y", "flags": []},
        },
        "deprecated_aliases": {
            "oldname": {"replacement": "newname", "reason": "alias for newname"}
        },
        "mcp_tools": [],
        "mcp_preset_counts": {},
        "categories": [],
        "envelope_summary_keys": [],
    }
    diff = _diff(baseline, current)
    assert diff["breaking_count"] == 0
    assert diff["renamed_commands"] == [{"from": "oldname", "to": "newname"}]
    assert diff["removed_commands"] == []
    verdict, _level = _verdict_for(diff)
    assert verdict == "surface drift"


# ---------------------------------------------------------------------------
# (d) --ci exits 5 on breaking; 0 on clean
# ---------------------------------------------------------------------------


def test_ci_exits_5_on_breaking(tmp_path):
    """``--ci`` exits with EXIT_GATE_FAILURE (5) on any breaking entry."""
    snap = _build_snapshot()
    snap["commands"]["_fake_dropped_cmd_w1293"] = {
        "module": "roam.commands.cmd_does_not_exist",
        "function": "missing",
        "flags": [],
    }
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["compatibility", "--baseline", str(baseline), "--ci"]
    )
    assert result.exit_code == 5, (result.exit_code, result.output)


def test_ci_exits_0_on_clean(tmp_path):
    """``--ci`` exits 0 when no breaking entries are detected."""
    baseline = _live_baseline(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["compatibility", "--baseline", str(baseline), "--ci"]
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (e) --write-baseline round-trip
# ---------------------------------------------------------------------------


def test_write_baseline_then_diff_clean(tmp_path):
    """``--write-baseline`` produces a snapshot that, when used as the
    baseline of an immediate follow-up diff against the same live
    build, reports ``no regressions``."""
    baseline = tmp_path / "snap.json"
    runner = CliRunner()
    write_result = runner.invoke(
        cli, ["compatibility", "--write-baseline", str(baseline)]
    )
    assert write_result.exit_code == 0, write_result.output
    assert baseline.exists()

    diff_result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(baseline)]
    )
    assert diff_result.exit_code == 0, diff_result.output
    payload = json.loads(diff_result.output)
    assert payload["summary"]["verdict"] == "no regressions"
    assert payload["summary"]["breaking"] == 0


# ---------------------------------------------------------------------------
# (f) Missing baseline emits a structured envelope (Pattern-1 variant C)
# ---------------------------------------------------------------------------


def test_missing_baseline_emits_structured_envelope(tmp_path):
    """When the baseline path doesn't exist, the command emits a
    structured envelope with verdict='baseline missing' and a
    next_command instructing how to capture one. No empty stdout."""
    missing = tmp_path / "does-not-exist.json"
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--json", "compatibility", "--baseline", str(missing)]
    )
    # No --ci, so exit 0 even on missing baseline.
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "baseline missing"
    assert payload["summary"]["partial_success"] is True
    assert payload.get("next_command", "").startswith("roam compatibility")
