"""Tests for deterministic speedscope profiler ingestion."""

from __future__ import annotations

import json

from tests.conftest import assert_json_envelope, invoke_cli, parse_json_output


def _write_speedscope(path, frames, samples, weights):
    path.write_text(
        json.dumps(
            {
                "$schema": "https://www.speedscope.app/file-format-schema.json",
                "shared": {"frames": frames},
                "profiles": [
                    {
                        "type": "sampled",
                        "name": "fixture",
                        "unit": "seconds",
                        "startValue": 0,
                        "endValue": sum(weights),
                        "samples": samples,
                        "weights": weights,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_profile_import_ranks_known_hot_span(project_factory, cli_runner, tmp_path):
    project = project_factory(
        {"worker.py": ("def cold():\n    return 1\n\ndef hot():\n    total = sum(range(100))\n    return total\n")}
    )
    trace = _write_speedscope(
        tmp_path / "profile.json",
        [
            {"name": "hot", "file": "worker.py", "line": 5},
            {"name": "cold", "file": "worker.py", "line": 2},
        ],
        [[0], [1]],
        [80, 20],
    )

    result = invoke_cli(cli_runner, ["profile-import", str(trace)], cwd=project, json_mode=True)

    assert result.exit_code == 0, result.output
    data = parse_json_output(result, "profile-import")
    assert_json_envelope(data, "profile-import")
    assert data["spans"][0]["symbol_name"] == "hot"
    assert data["spans"][0]["file"] == "worker.py"
    assert data["spans"][0]["line_start"] == 4
    assert data["spans"][0]["line_end"] == 6
    assert data["spans"][0]["runtime_share_pct"] == 80.0
    assert data["summary"]["runtime_share_definition"] == "cumulative_sample_weight / total_sample_weight"


def test_profile_import_reports_unmapped_frame(project_factory, cli_runner, tmp_path):
    project = project_factory({"worker.py": "def known():\n    return 1\n"})
    trace = _write_speedscope(
        tmp_path / "unmapped.json",
        [{"name": "external_work", "file": "site-packages/external.py", "line": 10}],
        [[0]],
        [1],
    )

    result = invoke_cli(cli_runner, ["profile-import", str(trace)], cwd=project, json_mode=True)

    assert result.exit_code == 0, result.output
    data = parse_json_output(result, "profile-import")
    assert data["spans"] == []
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["unmapped_frames"] == 1
    assert data["unmapped_frames"] == [
        {
            "name": "external_work",
            "file": "site-packages/external.py",
            "line": 10,
            "reason": "file_not_indexed",
            "cumulative_weight": 1.0,
            "runtime_share_pct": 100.0,
        }
    ]
