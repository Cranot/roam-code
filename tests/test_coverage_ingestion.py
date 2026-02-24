"""Coverage report ingestion tests (LCOV/Cobertura/coverage.py JSON)."""

from __future__ import annotations

import json

from tests.conftest import invoke_cli, parse_json_output, assert_json_envelope

from roam.coverage_reports import (
    parse_lcov_report,
    parse_cobertura_report,
    parse_coveragepy_json_report,
)


def test_parse_lcov_report(tmp_path):
    report = tmp_path / "coverage.info"
    report.write_text(
        "TN:\n"
        "SF:src/app.py\n"
        "DA:10,1\n"
        "DA:11,0\n"
        "end_of_record\n"
    )

    parsed = parse_lcov_report(report)
    assert "src/app.py" in parsed
    assert parsed["src/app.py"]["coverable"] == {10, 11}
    assert parsed["src/app.py"]["covered"] == {10}


def test_parse_cobertura_report(tmp_path):
    report = tmp_path / "coverage.xml"
    report.write_text(
        "<coverage>\n"
        "  <packages>\n"
        "    <package>\n"
        "      <classes>\n"
        "        <class name='app' filename='src/app.py'>\n"
        "          <lines>\n"
        "            <line number='5' hits='3'/>\n"
        "            <line number='6' hits='0'/>\n"
        "          </lines>\n"
        "        </class>\n"
        "      </classes>\n"
        "    </package>\n"
        "  </packages>\n"
        "</coverage>\n"
    )

    parsed = parse_cobertura_report(report)
    assert "src/app.py" in parsed
    assert parsed["src/app.py"]["coverable"] == {5, 6}
    assert parsed["src/app.py"]["covered"] == {5}


def test_parse_coveragepy_json_report(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({
        "meta": {"version": "7.0.0"},
        "files": {
            "src/app.py": {
                "executed_lines": [1, 2, 4],
                "missing_lines": [3],
                "excluded_lines": [],
            }
        },
    }))

    parsed = parse_coveragepy_json_report(report)
    assert "src/app.py" in parsed
    assert parsed["src/app.py"]["coverable"] == {1, 2, 3, 4}
    assert parsed["src/app.py"]["covered"] == {1, 2, 4}


def test_import_lcov_updates_metrics_health_and_test_gaps(project_factory, cli_runner):
    proj = project_factory({
        "src/app.py": (
            "def process(x):\n"
            "    y = x + 1\n"
            "    return y\n"
        ),
    })

    report = proj / "coverage.info"
    report.write_text(
        "TN:\n"
        "SF:src/app.py\n"
        "DA:1,1\n"
        "DA:2,1\n"
        "DA:3,1\n"
        "end_of_record\n"
    )

    import_result = invoke_cli(
        cli_runner,
        ["coverage-gaps", "--import-report", str(report)],
        cwd=proj,
        json_mode=True,
    )
    import_data = parse_json_output(import_result, "coverage-gaps")
    assert_json_envelope(import_data, "coverage-gaps")
    assert import_data["summary"]["matched_files"] == 1
    assert import_data["summary"]["coverage_pct"] == 100.0

    metrics_result = invoke_cli(
        cli_runner,
        ["metrics", "src/app.py"],
        cwd=proj,
        json_mode=True,
    )
    metrics_data = parse_json_output(metrics_result, "metrics")
    assert metrics_data["metrics"]["coverage_pct"] == 100.0
    assert metrics_data["metrics"]["covered_lines"] == 3
    assert metrics_data["metrics"]["coverable_lines"] == 3

    health_result = invoke_cli(
        cli_runner,
        ["health"],
        cwd=proj,
        json_mode=True,
    )
    health_data = parse_json_output(health_result, "health")
    assert health_data["summary"]["imported_coverage_pct"] == 100.0
    assert health_data["summary"]["imported_coverage_files"] == 1

    test_gaps_result = invoke_cli(
        cli_runner,
        ["test-gaps", "src/app.py"],
        cwd=proj,
        json_mode=True,
    )
    test_gaps_data = parse_json_output(test_gaps_result, "test-gaps")
    assert test_gaps_data["summary"]["total_gaps"] == 0
    assert test_gaps_data["summary"]["actual_only_count"] >= 1
