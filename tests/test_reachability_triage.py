"""First-class zero-egress reachability-triage command contracts."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_reachability_triage as triage
from roam.commands.cmd_vulns import _vuln_finding_id
from tests._helpers.wording_lint import scan_for_overclaims


def _parse_json(result) -> dict:
    assert result.output, "expected a JSON envelope"
    return json.loads(result.output[result.output.find("{") :])


def _injected_compose(*, reachable: bool = True, matched_file: str = "src/app.py") -> dict:
    reachability_code = 1 if reachable else -1
    return {
        "sbom": {
            "summary": {
                "total_dependencies": 1,
                "reachable_count": 1,
                "reachable_direct_count": 1,
                "reachable_heuristic_count": 0,
                "phantom_count": 0,
            }
        },
        "supply_chain": {
            "summary": {
                "total_dependencies": 1,
                "risk_score": 0,
                "unpinned_count": 0,
            }
        },
        "vulns": {
            "summary": {"total": 1, "reachable_count": int(reachable)},
            "vulnerabilities": [
                {
                    "value": {
                        "cve_id": "CVE-2099-0001",
                        "package_name": "example-package",
                        "matched_file": matched_file,
                        "reachable": reachability_code,
                    }
                }
            ],
        },
        "vuln_reach": {
            "summary": {
                "total_vulns": 1,
                "reachable_count": int(reachable),
                "critical_count": 0,
            },
            "vulnerabilities": [
                {
                    "cve": "CVE-2099-0001",
                    "package": "example-package",
                    "reachable": reachable,
                    "hops": 2 if reachable else 0,
                    "blast_radius": 7 if reachable else 0,
                }
            ],
        },
        "taint": {"summary": {"findings": 0}},
        "secrets": {"summary": {"total_findings": 0}},
    }


def _install_compose(monkeypatch, tmp_path: Path, env: dict) -> None:
    monkeypatch.setattr(triage, "ensure_index", lambda: None)
    monkeypatch.setattr(triage, "find_project_root", lambda: tmp_path)
    monkeypatch.setitem(triage._GATHER, "reachability-triage", lambda commit_range: env)


def test_command_runs_on_roam_code_with_service_report_figures():
    """The first-class projection preserves every headline compose figure."""
    runner = CliRunner()
    service_result = runner.invoke(
        cli,
        ["--json", "service-report", "--type", "reachability-triage"],
        catch_exceptions=False,
    )
    command_result = runner.invoke(cli, ["reachability-triage", "--json"], catch_exceptions=False)
    assert service_result.exit_code == 0, service_result.output
    assert command_result.exit_code == 0, command_result.output

    service = _parse_json(service_result)
    command = _parse_json(command_result)
    sections = service["sections"]
    assert command["metrics"] == triage._compose_metrics(sections)
    assert command["primitives"] == list(triage.REACHABILITY_TRIAGE_PRIMITIVES)
    assert command["delegated_compose"] == "service-report:reachability-triage"


def test_gate_exits_5_for_injected_new_reachable_flow(tmp_path, monkeypatch):
    env = _injected_compose()
    _install_compose(monkeypatch, tmp_path, env)
    triage._write_baseline(triage._baseline_path(tmp_path), set())

    result = CliRunner().invoke(
        cli,
        ["reachability-triage", "--gate-on-new-reachable", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 5, result.output
    payload = _parse_json(result)
    expected_id = _vuln_finding_id("CVE-2099-0001", "example-package")
    assert payload["gate"]["new_reachable_finding_ids"] == [expected_id]
    assert payload["flows"][0] == {
        "finding_id": expected_id,
        "cve": "CVE-2099-0001",
        "package": "example-package",
        "reachability": "reachable",
        "hop_distance": 2,
        "blast_radius": 7,
        "files": ["src/app.py"],
    }


def test_gate_exits_0_when_reachable_flow_is_in_baseline(tmp_path, monkeypatch):
    env = _injected_compose()
    _install_compose(monkeypatch, tmp_path, env)
    expected_id = _vuln_finding_id("CVE-2099-0001", "example-package")
    triage._write_baseline(triage._baseline_path(tmp_path), {expected_id})

    result = CliRunner().invoke(
        cli,
        ["reachability-triage", "--gate-on-new-reachable", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = _parse_json(result)
    assert payload["gate"]["evaluated"] is True
    assert payload["gate"]["new_reachable_finding_ids"] == []


def test_gate_fails_open_without_baseline(tmp_path, monkeypatch):
    _install_compose(monkeypatch, tmp_path, _injected_compose())

    result = CliRunner().invoke(
        cli,
        ["reachability-triage", "--gate-on-new-reachable", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = _parse_json(result)
    assert payload["gate"]["evaluated"] is False
    assert payload["gate"]["baseline_state"] == "missing"
    assert payload["gate"]["new_reachable_finding_ids"] == []


def test_gate_fails_closed_on_corrupt_baseline(tmp_path, monkeypatch):
    # A present-but-corrupt/tampered baseline must NOT silently disarm the gate
    # the way a genuinely-missing one does (you should not be able to turn off a
    # security gate by truncating one JSON file). It fails CLOSED -- exit 5 --
    # and is distinguishable from a "new reachable flow" exit 5 by an empty
    # new_reachable_finding_ids + baseline_error=True.
    _install_compose(monkeypatch, tmp_path, _injected_compose())
    baseline = triage._baseline_path(tmp_path)
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("{ this is not valid json ", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["reachability-triage", "--gate-on-new-reachable", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 5, result.output
    payload = _parse_json(result)
    assert payload["gate"]["baseline_state"] == "unreadable"
    assert payload["gate"]["baseline_error"] is True
    assert payload["gate"]["evaluated"] is False
    assert payload["gate"]["new_reachable_finding_ids"] == []


def test_range_scopes_facts_to_changed_files(tmp_path, monkeypatch):
    _install_compose(monkeypatch, tmp_path, _injected_compose(matched_file="src/app.py"))
    monkeypatch.setattr(triage, "get_changed_files", lambda root, commit_range: ["src/other.py"])

    result = CliRunner().invoke(
        cli,
        ["reachability-triage", "--range", "main..HEAD", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = _parse_json(result)
    assert payload["flows"] == []
    assert payload["summary"]["changed_files"] == 1


def test_mcp_wrapper_returns_read_only_envelope_and_forwards_range(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAM_MCP_DISABLE_COLD_START_GUARD", "1")
    envelope = {"command": "reachability-triage", "summary": {"verdict": "0 reachable paths"}}

    from roam.mcp_server import roam_reachability_triage

    params = inspect.signature(roam_reachability_triage).parameters
    assert "write_baseline" not in params
    assert "gate_on_new_reachable" not in params

    with patch("roam.mcp_server._run_roam", return_value=envelope) as run_roam:
        assert roam_reachability_triage(root=str(tmp_path)) == envelope
        run_roam.assert_called_once_with(["reachability-triage"], str(tmp_path))
        assert roam_reachability_triage(commit_range="main..HEAD", root=str(tmp_path)) == envelope
        run_roam.assert_called_with(["reachability-triage", "--range", "main..HEAD"], str(tmp_path))

    assert "error" not in envelope


def test_help_and_output_keep_the_honesty_wording(tmp_path, monkeypatch):
    _install_compose(monkeypatch, tmp_path, _injected_compose(reachable=False))
    runner = CliRunner()
    help_result = runner.invoke(cli, ["reachability-triage", "--help"], catch_exceptions=False)
    output_result = runner.invoke(cli, ["reachability-triage"], catch_exceptions=False)
    combined = f"{help_result.output}\n{output_result.output}"

    assert "Non-reachable does not mean safe" in combined
    assert "reachability filter over your scanner output" in combined
    assert "not a taint-analysis replacement" in combined
    assert "maps to / supports evidence for" in combined
    assert scan_for_overclaims(combined) == []
    for forbidden_claim in ("proves", "ensures", "attests"):
        assert forbidden_claim not in combined.lower()
