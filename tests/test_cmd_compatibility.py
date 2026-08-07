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

import pytest
from click.testing import CliRunner

import roam.commands.cmd_compatibility as compat_cmd
from roam.cli import cli
from roam.commands.cmd_compatibility import _build_snapshot, _diff, _introspect_flags, _verdict_for

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


def test_introspect_flags_only_degrades_on_import_errors(monkeypatch):
    """Missing optional imports degrade to no flags; other import-time bugs surface."""

    def missing_optional(_module_path: str):
        raise ImportError("optional extra missing")

    monkeypatch.setattr(compat_cmd.importlib, "import_module", missing_optional)
    assert _introspect_flags("roam.commands.missing_optional", "cmd") == []

    def broken_module(_module_path: str):
        raise RuntimeError("import-time command bug")

    monkeypatch.setattr(compat_cmd.importlib, "import_module", broken_module)
    with pytest.raises(RuntimeError, match="import-time command bug"):
        _introspect_flags("roam.commands.broken", "cmd")


# ---------------------------------------------------------------------------
# (a) Baseline-vs-self -> no regressions
# ---------------------------------------------------------------------------


def test_baseline_vs_self_no_regressions(tmp_path):
    """The live build compared against a snapshot of itself must report
    ``no regressions`` and zero breaking entries."""
    baseline = _live_baseline(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
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
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
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
    snap["commands"][target]["flags"] = sorted(set(snap["commands"][target]["flags"]) | {"--_fake_dropped_flag_w1293"})
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert any(e["command"] == target and e["flag"] == "--_fake_dropped_flag_w1293" for e in payload["removed_flags"])


def test_synthetic_envelope_field_removal_is_breaking(tmp_path):
    """Inject a fake envelope-summary field into baseline. The live
    build lists only the canonical fields, so the injected one appears
    under ``removed_envelope_fields``."""
    snap = _build_snapshot()
    snap["envelope_summary_keys"] = list(snap["envelope_summary_keys"]) + ["_fake_dropped_field_w1293"]
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert "_fake_dropped_field_w1293" in payload["removed_envelope_fields"]


def test_synthetic_mcp_tool_removal_is_breaking(tmp_path):
    """Inject a fake MCP tool name into baseline. Live build is missing
    it -> ``removed_mcp_tools``."""
    snap = _build_snapshot()
    snap["mcp_tools"] = dict(snap["mcp_tools"], roam__fake_w1293=["root"])
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert "roam__fake_w1293" in payload["removed_mcp_tools"]


# ---------------------------------------------------------------------------
# (b2) MCP tool PARAMETERS are a recorded dimension (W1496)
# ---------------------------------------------------------------------------
#
# Pre-fix, ``mcp_tools`` was a flat list of 244 names, so a removed tool
# PARAMETER could not be reported by this gate at all -- while the envelope
# asserted ``surface_coverage: complete`` and ``0 entries outside baseline
# coverage``. Commit 67a09fd1 is the live instance: it removed three CLI
# flags AND their two MCP wrapper mirrors (``staged`` from
# ``roam_budget_check``, ``model_tier`` from ``roam_compile``). The flags
# went red; the parameters passed the same gate in the same run, and
# snapshots built from the two revisions were byte-identical.


def test_67a09fd1_parameter_removal_is_a_breaking_entry():
    """NEGATIVE CONTROL, shape-independent: green pre-fix, red post-fix.

    Two hand-built snapshots reproducing commit 67a09fd1's MCP half. The
    tool NAME set is identical on both sides -- only ``staged`` and
    ``model_tier`` are gone -- which is precisely why the pre-fix
    comparator saw nothing: it subtracted name sets, and ``set(dict)``
    yields keys, so this exact input produced ``breaking=0`` even with the
    parameters sitting in the data. Asserted through ``_diff`` rather than
    the CLI so no part of it depends on what ``_build_snapshot`` returns.
    """
    before = {
        "schema_version": "1.1.0",
        "commands": {},
        "deprecated_aliases": {},
        "mcp_tools": {
            "roam_budget_check": ["commit_range", "config", "root", "staged"],
            "roam_compile": ["artifact", "brief", "model_tier", "root", "task"],
        },
        "mcp_preset_counts": {},
        "categories": [],
        "envelope_summary_keys": [],
    }
    after = {
        **before,
        "mcp_tools": {
            "roam_budget_check": ["commit_range", "config", "root"],
            "roam_compile": ["artifact", "brief", "root", "task"],
        },
    }

    delta = _diff(before, after)
    # Asserted FIRST and by value, so the pre-fix failure is the finding
    # itself ("breaking_count == 0") rather than a missing-key TypeError
    # that would prove only that the test is newer than the code.
    assert delta["breaking_count"] == 2, delta["breaking_count"]
    assert _verdict_for(delta) == ("breaking changes", "blocker")
    assert delta["removed_mcp_tools"] == [], delta["removed_mcp_tools"]
    assert delta["unrecorded_mcp_tool_params"] == [], delta["unrecorded_mcp_tool_params"]
    assert delta["removed_mcp_tool_params"] == [
        {"tool": "roam_budget_check", "parameter": "staged"},
        {"tool": "roam_compile", "parameter": "model_tier"},
    ], delta["removed_mcp_tool_params"]


def test_mcp_tool_params_agrees_with_mcp_tool_names():
    """The parameter dimension and the name dimension must read the same
    tool set. Two enumerators over the same file are two chances to
    disagree, and a tool present in one and absent from the other is a
    silent hole in whichever dimension lost it."""
    from roam.surface_counts import mcp_tool_names, mcp_tool_params

    params = mcp_tool_params()
    names = mcp_tool_names()
    assert sorted(params) == names, set(params).symmetric_difference(names)
    assert all(isinstance(v, list) for v in params.values())


def test_snapshot_records_a_parameter_list_per_mcp_tool():
    """``mcp_tools`` is ``{tool: [parameter, ...]}``, not a bare name list.
    Removal detection is set subtraction, so a dimension the snapshot never
    records can never be reported as removed."""
    snap = _build_snapshot()
    assert isinstance(snap["mcp_tools"], dict), type(snap["mcp_tools"])
    assert snap["mcp_tools"], "no MCP tools captured"
    assert all(isinstance(v, list) for v in snap["mcp_tools"].values())
    # The two parameters commit 67a09fd1 removed are gone from HEAD; the
    # tools that carried them are still recorded, which is exactly the
    # state in which the removal must have been reportable.
    assert "staged" not in snap["mcp_tools"]["roam_budget_check"]
    assert "model_tier" not in snap["mcp_tools"]["roam_compile"]


def test_synthetic_mcp_tool_param_removal_is_breaking(tmp_path):
    """NEGATIVE CONTROL: green pre-fix, red post-fix.

    Add a parameter to a live tool's BASELINE entry. The live build no
    longer accepts it, which breaks any agent still passing it, so it is a
    breaking entry and not a coverage note.
    """
    snap = _build_snapshot()
    snap["mcp_tools"]["roam_budget_check"] = sorted(
        set(snap["mcp_tools"]["roam_budget_check"]) | {"_fake_dropped_param_w1496"}
    )
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert payload["summary"]["breaking"] >= 1, payload["summary"]
    assert {"tool": "roam_budget_check", "parameter": "_fake_dropped_param_w1496"} in payload[
        "removed_mcp_tool_params"
    ], payload["removed_mcp_tool_params"]
    assert "0 removed MCP tool parameters" not in payload["agent_contract"]["facts"]


def test_ci_exits_5_on_a_removed_mcp_tool_param(tmp_path):
    """The gate's exit contract, not just its envelope. A finding that does
    not move the exit code does not block a merge."""
    snap = _build_snapshot()
    snap["mcp_tools"]["roam_compile"] = sorted(set(snap["mcp_tools"]["roam_compile"]) | {"_fake_model_tier_w1496"})
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--ci"])
    assert result.exit_code == 5, (result.exit_code, result.output)
    assert "roam_compile(_fake_model_tier_w1496)" in result.output, result.output


def test_added_mcp_tool_param_is_coverage_gap_not_breaking(tmp_path):
    """POSITIVE CONTROL for the other direction: a NEW parameter breaks no
    existing caller, so it is lost reach rather than a regression. Without
    this a gate that called every delta breaking would look correct."""
    snap = _build_snapshot()
    target = "roam_budget_check"
    snap["mcp_tools"][target] = [p for p in snap["mcp_tools"][target] if p != "root"]
    baseline = _write(tmp_path / "baseline.json", snap)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    payload = json.loads(result.output)
    assert payload["summary"]["breaking"] == 0, payload["summary"]
    assert payload["summary"]["coverage_gap"] >= 1, payload["summary"]
    assert {"tool": target, "parameter": "root"} in payload["added_mcp_tool_params"]


def test_write_baseline_refuses_to_erase_a_recorded_mcp_tool_param(tmp_path):
    """The ratchet must cover the parameter dimension too.

    ``_erasures`` delegates to ``_diff`` by design, so a parameter diff that
    lived in a wrapper instead of inside ``_diff`` would leave
    ``--write-baseline`` free to overwrite a parameter-recording baseline
    with one that silently drops a parameter -- and regeneration is what
    someone reaches for when the gate is red.
    """
    snap = _build_snapshot()
    snap["mcp_tools"]["roam_budget_check"] = sorted(
        set(snap["mcp_tools"]["roam_budget_check"]) | {"_w1496_param_no_longer_accepted"}
    )
    baseline = _write(tmp_path / "baseline.json", snap)
    before = baseline.read_text(encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline)])
    assert result.exit_code == 5, (result.exit_code, result.output)
    assert "roam_budget_check(_w1496_param_no_longer_accepted)" in result.output, result.output
    assert baseline.read_text(encoding="utf-8") == before, "refused write still mutated the baseline"


def test_a_pre_1_1_0_baseline_reports_parameters_unrecorded_not_absent(tmp_path):
    """The migration rule, and the one that must not be softened.

    A schema-1.0.0 baseline records ``mcp_tools`` as a flat list. Reading
    that as "these tools have no parameters" would report ``0 removed MCP
    tool parameters`` over a dimension nothing looked at -- an absent
    measurement republished as a benign definite value, which is the exact
    defect widening the snapshot exists to close. It must count as coverage
    gap, flip ``surface_coverage`` to ``partial``, and fail
    ``--require-coverage``.
    """
    snap = _build_snapshot()
    legacy = dict(snap, schema_version="1.0.0", mcp_tools=sorted(snap["mcp_tools"]))
    baseline = _write(tmp_path / "legacy.json", legacy)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["breaking"] == 0, summary
    assert summary["surface_coverage"] == "partial", summary
    assert summary["partial_success"] is True, summary
    assert summary["coverage_gap"] == len(snap["mcp_tools"]), summary
    assert summary["verdict"] != "no regressions", summary
    assert sorted(payload["unrecorded_mcp_tool_params"]) == sorted(snap["mcp_tools"])
    assert payload["removed_mcp_tool_params"] == []

    gated = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--require-coverage"])
    assert gated.exit_code == 5, (gated.exit_code, gated.output)


def test_a_pre_1_1_0_baseline_still_detects_a_removed_tool_NAME(tmp_path):
    """Back-compat control: the dimensions an old baseline DOES record keep
    working. Migration must not convert a stale baseline into a dead one."""
    snap = _build_snapshot()
    legacy = dict(
        snap,
        schema_version="1.0.0",
        mcp_tools=sorted([*snap["mcp_tools"], "roam__legacy_gone_w1496"]),
    )
    baseline = _write(tmp_path / "legacy.json", legacy)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "breaking changes"
    assert "roam__legacy_gone_w1496" in payload["removed_mcp_tools"]


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
        "deprecated_aliases": {"oldname": {"replacement": "newname", "reason": "alias for newname"}},
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
    result = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--ci"])
    assert result.exit_code == 5, (result.exit_code, result.output)


def test_ci_exits_0_on_clean(tmp_path):
    """``--ci`` exits 0 when no breaking entries are detected."""
    baseline = _live_baseline(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--ci"])
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
    write_result = runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline)])
    assert write_result.exit_code == 0, write_result.output
    assert baseline.exists()

    diff_result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline)])
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
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(missing)])
    # No --ci, so exit 0 even on missing baseline.
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "baseline missing"
    assert payload["summary"]["partial_success"] is True
    assert payload.get("next_command", "").startswith("roam compatibility")
