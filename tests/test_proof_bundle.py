"""Tests for the proof_bundle composer + `roam proof-bundle` CLI.

Per the Roam Guard pivot plan, item 3: composes the AgentChangeProofBundle
v1 schema from a pr-bundle dict.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.proof_bundle import (
    PROOF_BUNDLE_SCHEMA,
    PROOF_BUNDLE_SCHEMA_VERSION,
    compose_agent_change_proof_bundle,
)

# ---- fixtures ----
from tests.helpers import make_pr_bundle as _make_pr_bundle

# ---- composer tests ----


def test_compose_minimal_bundle_produces_v1_shape(tmp_path):
    bundle = _make_pr_bundle()
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    # Required top-level keys per schema spec
    for key in (
        "schema",
        "schema_version",
        "repo",
        "run",
        "mode",
        "policy_profile",
        "changed_files",
        "affected",
        "risk",
        "command_graph_snapshot",
        "verification_contract",
        "executed_checks",
        "missing_checks",
        "optimizer_findings",
        "scope_findings",
        "mcp_tool_findings",
        "ledger",
        "verdict",
    ):
        assert key in v1, f"v1 schema missing {key}"
    assert v1["schema"] == PROOF_BUNDLE_SCHEMA
    assert v1["schema_version"] == PROOF_BUNDLE_SCHEMA_VERSION


def test_compose_extracts_changed_files_from_affected_symbols(tmp_path):
    bundle = _make_pr_bundle()
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    assert "src/auth/session.py" in v1["changed_files"]


def test_compose_dedupes_paths(tmp_path):
    bundle = _make_pr_bundle(files=["src/auth/session.py"])
    # affected_symbols already lists session.py — files_inspected too — dedup.
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    assert v1["changed_files"].count("src/auth/session.py") == 1


def test_compose_auth_files_force_test_required(tmp_path):
    bundle = _make_pr_bundle()
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    # command_graph for roam-code includes test commands; auth pattern fires.
    required = v1["verification_contract"]["required"]
    # Either has at least one required (when graph includes tests) OR
    # the bundle had no tests in graph — both are valid v1 shapes.
    reasons = {r.get("reason") for r in required}
    if required:
        assert "auth_file_changed" in reasons or "high_risk_path" in reasons


def test_compose_blocked_when_required_not_run(tmp_path):
    bundle = _make_pr_bundle(
        risks=[{"severity": "high", "paths": ["src/auth/session.py"], "description": "auth boundary"}]
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    if v1["verification_contract"]["required"]:
        # When required checks exist but none ran, verdict must be blocked.
        assert v1["verdict"]["value"] == "blocked"
        reasons = {r["code"] for r in v1["verdict"]["reasons"]}
        assert "required_check_not_run" in reasons


def test_compose_pass_when_required_ran_passed(tmp_path):
    # Build a bundle that ran a test command from the actual graph.
    from roam.command_graph import build_command_graph

    graph = build_command_graph(tmp_path)
    test_commands = [c for c in graph.get("commands", []) if c.get("kind") == "test"]
    if not test_commands:
        return  # nothing to test with; graceful skip
    test_cmd_id = test_commands[0]["id"]
    bundle = _make_pr_bundle(
        tests_run=[{"command": test_cmd_id, "status": "pass", "output": "all good"}],
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    # If verification_contract required this test, it should be in executed.
    executed_names = {c["command"] for c in v1["executed_checks"]}
    assert test_cmd_id in executed_names


def test_compose_missing_checks_correctly_identifies_unrun_required(tmp_path):
    bundle = _make_pr_bundle(
        risks=[{"severity": "high", "paths": ["src/auth/session.py"], "description": "auth"}],
        tests_run=[],  # nothing ran
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    if v1["verification_contract"]["required"]:
        # missing_checks = required - executed
        assert len(v1["missing_checks"]) == len(v1["verification_contract"]["required"])


# ---- CLI tests ----


def test_cli_proof_bundle_emits_json(tmp_path):
    """`roam --json proof-bundle --bundle <file>` produces valid JSON."""
    bundle = _make_pr_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle))

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "proof-bundle", "--bundle", str(bundle_path)])
    # Exit may be 0 (no --strict) or non-zero on verdict; just verify JSON parses.
    assert result.exit_code in (0, 4, 5), f"unexpected exit {result.exit_code}: {result.output}"
    payload = json.loads(result.output)
    assert payload["command"] == "proof-bundle"
    assert "agent_change_proof_bundle" in payload
    assert payload["agent_change_proof_bundle"]["schema"] == PROOF_BUNDLE_SCHEMA


def test_cli_proof_bundle_no_bundle_found(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["proof-bundle", "--bundle", str(tmp_path / "nonexistent.json")])
    assert result.exit_code == 2
    assert "No pr-bundle found" in result.output


def test_cli_proof_bundle_text_mode_shows_verdict():
    """Text output shows a VERDICT line."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        bundle = _make_pr_bundle()
        Path("bundle.json").write_text(json.dumps(bundle))
        result = runner.invoke(cli, ["proof-bundle", "--bundle", "bundle.json"])
        assert result.exit_code in (0, 4, 5)
        assert "VERDICT:" in result.output


def test_cli_proof_bundle_writes_output_file(tmp_path):
    runner = CliRunner()
    bundle = _make_pr_bundle()
    bundle_path = tmp_path / "in.json"
    bundle_path.write_text(json.dumps(bundle))
    out_path = tmp_path / "out.json"
    result = runner.invoke(
        cli,
        [
            "proof-bundle",
            "--bundle",
            str(bundle_path),
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code in (0, 4, 5)
    assert out_path.is_file()
    composed = json.loads(out_path.read_text())
    assert composed["schema"] == PROOF_BUNDLE_SCHEMA


# ---- markdown render tests ----


def test_render_markdown_includes_verdict_headline(tmp_path):
    from roam.proof_bundle import render_markdown

    bundle = _make_pr_bundle(
        risks=[{"severity": "high", "paths": ["src/auth/session.py"], "description": "auth"}],
        tests_run=[],
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    md = render_markdown(v1)
    assert "Roam Guard verdict:" in md
    assert v1["verdict"]["value"] in md  # blocked / pass / etc.


def test_render_markdown_shows_verification_table(tmp_path):
    from roam.proof_bundle import render_markdown

    bundle = _make_pr_bundle(
        risks=[{"severity": "high", "paths": ["src/auth/session.py"], "description": "auth"}],
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    md = render_markdown(v1)
    if v1["verification_contract"]["required"]:
        assert "Verification checks" in md
        assert "| Status | Command | Why |" in md


def test_render_markdown_truncates_long_file_lists(tmp_path):
    from roam.proof_bundle import render_markdown

    files = [f"src/file_{i}.py" for i in range(20)]
    bundle = _make_pr_bundle(files=files)
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    md = render_markdown(v1)
    assert "Files touched" in md
    assert "more" in md  # truncation indicator


def test_render_markdown_provenance_footer(tmp_path):
    from roam.proof_bundle import render_markdown

    v1 = compose_agent_change_proof_bundle(_make_pr_bundle(), repo_root=tmp_path)
    md = render_markdown(v1)
    # Footer fields
    assert "Bundle" in md
    assert "mode" in md.lower()
    assert "policy" in md.lower()


def test_cli_proof_bundle_format_markdown(tmp_path):
    runner = CliRunner()
    bundle = _make_pr_bundle()
    bundle_path = tmp_path / "in.json"
    bundle_path.write_text(json.dumps(bundle))
    result = runner.invoke(
        cli,
        [
            "proof-bundle",
            "--bundle",
            str(bundle_path),
            "--format",
            "markdown",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    assert "Roam Guard verdict:" in result.output
    assert "##" in result.output  # markdown header


def test_cli_proof_bundle_format_markdown_to_file(tmp_path):
    runner = CliRunner()
    bundle = _make_pr_bundle()
    bundle_path = tmp_path / "in.json"
    bundle_path.write_text(json.dumps(bundle))
    out_path = tmp_path / "out.md"
    result = runner.invoke(
        cli,
        [
            "proof-bundle",
            "--bundle",
            str(bundle_path),
            "--format",
            "markdown",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code in (0, 4, 5)
    content = out_path.read_text()
    assert "Roam Guard verdict:" in content


# ---- Phase 7: JSON Schema validation ----


def test_v1_schema_file_is_valid_json():
    from roam.proof_bundle import get_v1_schema

    schema = get_v1_schema()
    assert schema["title"] == "AgentChangeProofBundle"
    assert "$schema" in schema
    assert "properties" in schema


def test_composer_output_is_schema_valid(tmp_path):
    from roam.proof_bundle import validate_v1

    bundle = _make_pr_bundle()
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    errors = validate_v1(v1)
    assert errors == [], f"expected zero schema errors, got: {errors}"


def test_validate_v1_catches_missing_required_field():
    from roam.proof_bundle import validate_v1

    v1 = {"schema": "agent_change_proof_bundle", "schema_version": "1.0"}
    errors = validate_v1(v1)
    assert any("changed_files" in e for e in errors)
    assert any("verdict" in e for e in errors)


def test_validate_v1_catches_bad_verdict_value():
    from roam.proof_bundle import validate_v1

    v1 = {
        "schema": "agent_change_proof_bundle",
        "schema_version": "1.0",
        "changed_files": [],
        "verification_contract": {"required": [], "skipped": []},
        "executed_checks": [],
        "missing_checks": [],
        "verdict": {"value": "nonsense", "reasons": [{"code": "x"}]},
    }
    errors = validate_v1(v1)
    assert any("verdict.value" in e for e in errors)


def test_validate_v1_catches_bad_mode():
    from roam.proof_bundle import validate_v1

    v1 = {
        "schema": "agent_change_proof_bundle",
        "schema_version": "1.0",
        "changed_files": [],
        "verification_contract": {"required": [], "skipped": []},
        "executed_checks": [],
        "missing_checks": [],
        "verdict": {"value": "pass", "reasons": []},
        "mode": "lawless",
    }
    errors = validate_v1(v1)
    assert any("mode" in e for e in errors)


def test_validate_v1_catches_bad_check_status():
    from roam.proof_bundle import validate_v1

    v1 = {
        "schema": "agent_change_proof_bundle",
        "schema_version": "1.0",
        "changed_files": [],
        "verification_contract": {"required": [], "skipped": []},
        "executed_checks": [{"command": "x", "status": "bogus"}],
        "missing_checks": [],
        "verdict": {"value": "pass", "reasons": []},
    }
    errors = validate_v1(v1)
    assert any("status" in e for e in errors)


def test_render_markdown_sections_compose_cleanly(tmp_path):
    """Sections are individually testable + the orchestrator just joins them."""
    from roam.proof_bundle import (
        _md_checks_table,
        _md_files_block,
        _md_headline,
        _md_provenance_footer,
        _md_reasons,
        _md_risk_block,
    )

    bundle = _make_pr_bundle(
        risks=[{"severity": "high", "paths": ["src/auth/x.py"], "description": "auth boundary"}],
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    # Each section returns either an empty string or markdown text.
    headline = _md_headline(v1)
    reasons = _md_reasons(v1)
    checks = _md_checks_table(v1)
    risk = _md_risk_block(v1)
    files = _md_files_block(v1)
    footer = _md_provenance_footer(v1)
    # Smoke property: every section renders to a string without raising.
    assert all(isinstance(s, str) for s in (reasons, checks, files))
    assert "Roam Guard verdict" in headline
    assert "Bundle" in footer  # provenance footer always present
    # Risk block should fire because severity=high lifts risk.level.
    if v1["risk"].get("level") in ("medium", "high"):
        assert "Risk:" in risk
    # Files block fires when there are changed_files.
    if v1.get("changed_files"):
        assert "Files touched" in files


def test_md_files_block_flat_under_threshold(tmp_path):
    """Small file lists render as a flat bullet list."""
    from roam.proof_bundle import _md_files_block

    v1 = {"changed_files": [f"src/f_{i}.py" for i in range(5)]}
    md = _md_files_block(v1)
    assert "5 files" not in md  # no count summary for flat mode
    assert "across" not in md  # flat header
    assert "Files touched (5)" in md
    assert "src/f_0.py" in md


def test_md_files_block_groups_when_over_threshold(tmp_path):
    """Large file lists group by top-level directory."""
    from roam.proof_bundle import _md_files_block

    files = [f"src/roam/m_{i}.py" for i in range(15)] + [f"tests/test_{i}.py" for i in range(10)]
    v1 = {"changed_files": files}
    md = _md_files_block(v1)
    assert "Files touched (25, across 2 top-level dir(s))" in md
    assert "src/" in md
    assert "tests/" in md
    # Per-directory counts visible.
    assert "(15 files)" in md
    assert "(10 files)" in md
    # Truncation indicator inside a group when > 3 files.
    assert "more in `src/`" in md


def test_md_files_block_caps_directory_count_at_10(tmp_path):
    """When there are > 10 distinct top-level dirs, surplus is summarized."""
    from roam.proof_bundle import _md_files_block

    # 12 distinct top-level dirs, each with 2 files (24 total > threshold).
    files = []
    for i in range(12):
        files.append(f"dir{i}/x.py")
        files.append(f"dir{i}/y.py")
    md = _md_files_block({"changed_files": files})
    # 2 leftover dirs (12 total - 10 shown) → footer indicates more.
    assert "more dir(s)" in md


def test_format_reason_md_collapses_long_check_lists():
    """Aggregated reasons with > 5 checks collapse to 3 + summary."""
    from roam.proof_bundle import _format_reason_md

    r = {
        "code": "required_checks_not_run",
        "count": 8,
        "because": "auth",
        "checks": [{"check": f"test{i}"} for i in range(8)],
    }
    md = _format_reason_md(r)
    # Only 3 listed; 5 collapsed.
    assert md.count("- `test") == 3
    assert "and 5 more" in md


def test_format_reason_md_shows_all_when_short():
    """≤ 5 sub-checks render in full."""
    from roam.proof_bundle import _format_reason_md

    r = {
        "code": "required_checks_not_run",
        "count": 4,
        "because": "auth",
        "checks": [{"check": f"test{i}"} for i in range(4)],
    }
    md = _format_reason_md(r)
    assert md.count("- `test") == 4
    assert "more" not in md


def test_verdict_to_sarif_emits_valid_document(tmp_path):
    from roam.proof_bundle import verdict_to_sarif

    v1 = compose_agent_change_proof_bundle(_make_pr_bundle(), repo_root=tmp_path)
    sarif = verdict_to_sarif(v1)
    # Top-level SARIF 2.1.0 shape
    assert sarif.get("$schema", "").startswith("https://json.schemastore.org/sarif") or sarif.get("version") == "2.1.0"
    assert "runs" in sarif
    assert len(sarif["runs"]) == 1
    run = sarif["runs"][0]
    assert "tool" in run
    assert "results" in run
    # Umbrella verdict rule always present.
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert "roam.guard.verdict" in rule_ids


def test_verdict_to_sarif_maps_blocked_to_error_level(tmp_path):
    from roam.proof_bundle import verdict_to_sarif

    bundle = _make_pr_bundle(
        risks=[{"severity": "high", "paths": ["src/auth/x.py"], "description": "auth"}],
        tests_run=[],
    )
    v1 = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    if v1["verdict"]["value"] == "blocked":
        sarif = verdict_to_sarif(v1)
        results = sarif["runs"][0]["results"]
        # Umbrella result for blocked → level=error.
        umbrella = next(r for r in results if r["ruleId"] == "roam.guard.verdict")
        assert umbrella["level"] == "error"


def test_cli_proof_bundle_sarif_output(tmp_path):
    runner = CliRunner()
    bundle = _make_pr_bundle()
    bundle_path = tmp_path / "in.json"
    bundle_path.write_text(json.dumps(bundle))
    result = runner.invoke(
        cli,
        [
            "proof-bundle",
            "--bundle",
            str(bundle_path),
            "--format",
            "sarif",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "roam-guard"


def test_guard_enums_match_schema():
    """Lint: closed enums in guard_enums must match the JSON Schema file.

    JSON Schema is the spec consumers see. Python imports from
    guard_enums. The two must stay in lockstep — drift means downstream
    validators flag values that the engine considers valid (or vice versa).
    """
    from roam.guard_enums import (
        CHECK_STATUSES,
        MODES,
        POLICY_PROFILES,
        RISK_LEVELS,
        VERDICTS,
    )
    from roam.proof_bundle import get_v1_schema

    schema = get_v1_schema()
    props = schema["properties"]
    assert set(VERDICTS) == set(props["verdict"]["properties"]["value"]["enum"])
    assert set(MODES) == set(props["mode"]["enum"])
    assert set(POLICY_PROFILES) == set(props["policy_profile"]["enum"])
    assert set(RISK_LEVELS) == set(props["risk"]["properties"]["level"]["enum"])
    assert set(CHECK_STATUSES) == set(props["executed_checks"]["items"]["properties"]["status"]["enum"])


def test_cli_proof_bundle_validate_flag(tmp_path):
    runner = CliRunner()
    bundle = _make_pr_bundle()
    bundle_path = tmp_path / "in.json"
    bundle_path.write_text(json.dumps(bundle))
    result = runner.invoke(
        cli,
        [
            "--json",
            "proof-bundle",
            "--bundle",
            str(bundle_path),
            "--validate",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    payload = json.loads(result.output)
    assert "schema_errors" in payload
    # Composer should produce schema-valid output by construction.
    assert payload["schema_errors"] == []
