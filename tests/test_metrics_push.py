"""Tests for ``roam metrics-push`` — the Roam Cloud Lite CLI engine.

Mostly unit tests on the payload-assembly + helper functions so we
can verify the no-source-code-leaks guarantee without standing up a
real HTTP server. One CLI integration test exercises ``--dry-run``
end-to-end against a tiny indexed fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

from roam.commands.cmd_metrics_push import (  # noqa: E402
    _build_payload,
    _infer_repo_id,
    _path_hash,
)

# --------------------------------------------------------------------------- ---
# Fixtures
# --------------------------------------------------------------------------- ---


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


_FAKE_AUDIT_ENVELOPE = {
    "summary": {
        "verdict": "AUDIT — pressures: 5 danger-zone file(s)",
        "health_score": 88,
        "api_surface": 1278,
        "file_total": 3174,
        "symbol_total": 15603,
    },
    "api_count": 1278,
    "sections": {
        "health": {
            "summary": {
                "health_score": 88,
                "imported_coverage_pct": None,
                "actionable_cycles": 1,
                "tangle_ratio": 0.0,
            }
        },
        "debt": {
            "summary": {
                "total_remediation_minutes": 134843.0,
                "total_remediation_hours": 2247.4,
            }
        },
        "dead": {
            "summary": {
                "safe": 78,
                "review": 302,
                "intentional": 44,
                "test_only": 296,
                "total_dead_loc": 10877,
            }
        },
        "test_pyramid": {
            "summary": {
                "total": 251,
                "unit": 0,
                "integration": 0,
                "e2e": 1,
                "smoke": 1,
                "unknown": 249,
            }
        },
        "hotspots_danger": {
            "summary": {"count": 5},
            "danger_zone": [
                {
                    "path": "src/roam/output/sarif.py",
                    "danger_score": 1.97,
                    "churn": 2998,
                    "complexity": 22.11,
                    "max_fan_in": 16,
                },
                {
                    "path": "src/roam/commands/cmd_dead.py",
                    "danger_score": 1.68,
                    "churn": 3362,
                    "complexity": 25.0,
                    "max_fan_in": 8,
                },
            ],
        },
    },
}


_FAKE_GIT_META = {
    "git_sha": "abc1234deadbeef",
    "git_branch": "main",
    "git_origin": "git@github.com:Cranot/roam-code.git",
}


# --------------------------------------------------------------------------- ---
# Repo inference
# --------------------------------------------------------------------------- ---


def test_infer_repo_id_uses_override():
    assert _infer_repo_id({"git_origin": "git@github.com:foo/bar.git"}, "explicit") == "explicit"


def test_infer_repo_id_normalises_ssh_origin():
    out = _infer_repo_id({"git_origin": "git@github.com:Cranot/roam-code.git"}, None)
    assert out == "github.com/Cranot/roam-code"


def test_infer_repo_id_normalises_https_origin():
    out = _infer_repo_id({"git_origin": "https://github.com/Cranot/roam-code.git"}, None)
    assert out == "github.com/Cranot/roam-code"


def test_infer_repo_id_handles_missing_origin():
    assert _infer_repo_id({}, None) == "<unknown>"


# --------------------------------------------------------------------------- ---
# Path hashing
# --------------------------------------------------------------------------- ---


def test_path_hash_is_deterministic():
    a = _path_hash("src/roam/output/sarif.py")
    b = _path_hash("src/roam/output/sarif.py")
    assert a == b
    assert a.startswith("sha256:")


def test_path_hash_differs_per_path():
    assert _path_hash("a.py") != _path_hash("b.py")


# --------------------------------------------------------------------------- ---
# Payload assembly — the no-source-code guarantee
# --------------------------------------------------------------------------- ---


def test_payload_schema_pinned_to_v1():
    payload = _build_payload(
        _FAKE_AUDIT_ENVELOPE,
        repo_id="example/repo",
        git_meta=_FAKE_GIT_META,
        anonymize=False,
        include_hotspots=True,
    )
    assert payload["schema"] == "roam-metrics-v1"
    assert payload["schema_version"] == "1.0.0"


def test_payload_pulls_metrics_from_each_section():
    payload = _build_payload(
        _FAKE_AUDIT_ENVELOPE,
        repo_id="example/repo",
        git_meta=_FAKE_GIT_META,
        anonymize=False,
        include_hotspots=True,
    )
    m = payload["metrics"]
    assert m["health_score"] == 88
    assert m["debt_total_minutes"] == 134843.0
    assert m["debt_total_hours"] == 2247.4
    assert m["dead_safe"] == 78
    assert m["dead_review"] == 302
    assert m["danger_zone_count"] == 5
    assert m["test_pyramid"]["total"] == 251
    assert m["api_surface"] == 1278
    assert m["file_total"] == 3174


def test_payload_anonymize_replaces_paths_with_hashes():
    plain = _build_payload(
        _FAKE_AUDIT_ENVELOPE,
        repo_id="example/repo",
        git_meta=_FAKE_GIT_META,
        anonymize=False,
        include_hotspots=True,
    )
    anon = _build_payload(
        _FAKE_AUDIT_ENVELOPE,
        repo_id="example/repo",
        git_meta=_FAKE_GIT_META,
        anonymize=True,
        include_hotspots=True,
    )

    plain_hot = plain["hotspots"]
    anon_hot = anon["hotspots"]

    assert all("path" in row for row in plain_hot)
    assert all("path_hash" in row for row in anon_hot)
    assert all("path" not in row for row in anon_hot), "anonymized payload must not retain raw paths"
    assert anon["anonymized"] is True


def test_payload_no_hotspots_omits_section():
    payload = _build_payload(
        _FAKE_AUDIT_ENVELOPE,
        repo_id="example/repo",
        git_meta=_FAKE_GIT_META,
        anonymize=False,
        include_hotspots=False,
    )
    assert "hotspots" not in payload


def test_payload_does_not_leak_source_code():
    """Hard guarantee: the payload must contain only allow-listed metric keys."""
    payload = _build_payload(
        _FAKE_AUDIT_ENVELOPE,
        repo_id="example/repo",
        git_meta=_FAKE_GIT_META,
        anonymize=False,
        include_hotspots=True,
    )
    allowed_top_keys = {
        "schema",
        "schema_version",
        "repo",
        "git_sha",
        "git_branch",
        "timestamp",
        "tool_version",
        "anonymized",
        "metrics",
        "hotspots",
    }
    extra = set(payload.keys()) - allowed_top_keys
    assert not extra, f"payload has unexpected top-level keys: {extra}"

    allowed_metric_keys = {
        "health_score",
        "debt_total_minutes",
        "debt_total_hours",
        "dead_safe",
        "dead_review",
        "dead_intentional",
        "dead_test_only",
        "dead_total_loc",
        "danger_zone_count",
        "test_pyramid",
        "imported_coverage_pct",
        "api_surface",
        "file_total",
        "symbol_total",
        "actionable_cycles",
        "tangle_ratio",
    }
    extra_m = set(payload["metrics"].keys()) - allowed_metric_keys
    assert not extra_m, f"metrics has unexpected keys: {extra_m}"

    allowed_hotspot_keys = {"path", "path_hash", "danger_score", "churn", "complexity", "max_fan_in"}
    for row in payload.get("hotspots", []):
        extra_h = set(row.keys()) - allowed_hotspot_keys
        assert not extra_h, f"hotspot row has unexpected keys: {extra_h}"


def test_payload_handles_missing_sections_gracefully():
    sparse = {"summary": {"health_score": 75}}
    payload = _build_payload(
        sparse,
        repo_id="example/repo",
        git_meta={},
        anonymize=False,
        include_hotspots=True,
    )
    assert payload["metrics"]["health_score"] == 75
    assert payload["metrics"]["dead_safe"] == 0  # default fallback
    assert payload.get("hotspots") == []  # no danger_zone in input


# --------------------------------------------------------------------------- ---
# CLI integration — dry-run path doesn't need a token
# --------------------------------------------------------------------------- ---


@pytest.fixture
def tiny_indexed(tmp_path, monkeypatch):
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def add(a, b):\n    return a + b\n")
    git_init(proj)
    git_commit(proj, "initial")
    monkeypatch.chdir(proj)
    index_in_process(proj)
    return proj


def test_cli_metrics_push_help_lists_options(cli_runner):
    result = invoke_cli(cli_runner, ["metrics-push", "--help"])
    out = result.output
    assert "--dry-run" in out
    assert "--anonymize" in out
    assert "--token" in out
    assert "--endpoint" in out


def test_cli_metrics_push_dry_run_returns_payload(cli_runner, tiny_indexed):
    result = invoke_cli(cli_runner, ["metrics-push", "--dry-run"], json_mode=True)
    assert result.exit_code == 0, result.output
    payload = parse_json_output(result)
    summary = payload.get("summary") or {}
    assert "dry-run" in summary.get("verdict", "")
    inner = payload.get("payload") or {}
    assert inner.get("schema") == "roam-metrics-v1"
    assert "metrics" in inner


def test_cli_metrics_push_requires_token_when_not_dry_run(cli_runner, tiny_indexed):
    result = invoke_cli(cli_runner, ["metrics-push"])
    assert result.exit_code != 0
    assert "--token required" in result.output or "--token required" in (result.stderr or "")


def test_cli_metrics_push_anonymize_dry_run(cli_runner, tiny_indexed):
    result = invoke_cli(cli_runner, ["metrics-push", "--dry-run", "--anonymize"], json_mode=True)
    assert result.exit_code == 0
    payload = parse_json_output(result)
    inner = payload.get("payload") or {}
    assert inner.get("anonymized") is True


# ---- Last-pr-analysis enrichment (Phase 10) -------------------------------


def test_load_last_pr_analysis_returns_none_on_missing(tmp_path, monkeypatch):
    from roam.commands.cmd_metrics_push import _load_last_pr_analysis

    monkeypatch.chdir(tmp_path)
    assert _load_last_pr_analysis() is None


def test_load_last_pr_analysis_returns_none_on_corrupt(tmp_path, monkeypatch):
    import json as _json

    from roam.commands.cmd_metrics_push import _load_last_pr_analysis

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "last-pr-analysis.json").write_text("not json {")
    assert _load_last_pr_analysis() is None
    # A valid envelope round-trips:
    valid = {"summary": {"verdict": "BLOCK"}}
    (tmp_path / ".roam" / "last-pr-analysis.json").write_text(_json.dumps(valid))
    assert _load_last_pr_analysis() == valid


def test_payload_includes_last_pr_analysis_when_present():
    """When --include-pr-analysis is on, _build_payload folds in the summary."""
    from roam.commands.cmd_metrics_push import _build_payload

    audit_envelope = {"summary": {}, "sections": {}}
    last_pr = {
        "summary": {
            "verdict": "BLOCK",
            "blast_radius": 78,
            "ai_likelihood": 92,
            "rule_violations": 3,
            "high_severity_critique": 1,
        },
        "ai_likelihood": {"primary_language": "python"},
        "_meta": {"timestamp": "2026-05-06T12:00:00Z"},
    }
    payload = _build_payload(
        audit_envelope,
        repo_id="github.com/o/r",
        git_meta={},
        anonymize=False,
        include_hotspots=False,
        last_pr_envelope=last_pr,
    )
    assert "last_pr_analysis" in payload
    block = payload["last_pr_analysis"]
    assert block["verdict"] == "BLOCK"
    assert block["blast_radius"] == 78
    assert block["ai_likelihood"] == 92
    assert block["primary_language"] == "python"
    assert block["timestamp"] == "2026-05-06T12:00:00Z"


def test_payload_omits_last_pr_when_none():
    from roam.commands.cmd_metrics_push import _build_payload

    payload = _build_payload(
        {"summary": {}, "sections": {}},
        repo_id="r",
        git_meta={},
        anonymize=False,
        include_hotspots=False,
        last_pr_envelope=None,
    )
    assert "last_pr_analysis" not in payload


def test_cli_metrics_push_includes_pr_analysis_flag_in_help(cli_runner):
    result = invoke_cli(cli_runner, ["metrics-push", "--help"])
    assert "--include-pr-analysis" in result.output
    assert "--no-pr-analysis" in result.output


def test_cli_metrics_push_dry_run_picks_up_last_pr(tmp_path, cli_runner, tiny_indexed):
    """When .roam/last-pr-analysis.json exists, dry-run includes it in the payload."""
    import json as _json

    last_pr = {
        "summary": {"verdict": "REVIEW", "blast_radius": 50, "ai_likelihood": 60, "rule_violations": 1},
        "ai_likelihood": {"primary_language": "python"},
        "_meta": {"timestamp": "2026-05-06T00:00:00Z"},
    }
    (tiny_indexed / ".roam").mkdir(exist_ok=True)
    (tiny_indexed / ".roam" / "last-pr-analysis.json").write_text(_json.dumps(last_pr))

    result = invoke_cli(cli_runner, ["metrics-push", "--dry-run"], json_mode=True)
    assert result.exit_code == 0
    payload = parse_json_output(result)
    inner = payload.get("payload") or {}
    assert "last_pr_analysis" in inner
    assert inner["last_pr_analysis"]["verdict"] == "REVIEW"


def test_cli_metrics_push_no_pr_analysis_omits_section(tmp_path, cli_runner, tiny_indexed):
    """`--no-pr-analysis` skips loading the file."""
    import json as _json

    last_pr = {"summary": {"verdict": "REVIEW"}, "_meta": {}}
    (tiny_indexed / ".roam").mkdir(exist_ok=True)
    (tiny_indexed / ".roam" / "last-pr-analysis.json").write_text(_json.dumps(last_pr))

    result = invoke_cli(cli_runner, ["metrics-push", "--dry-run", "--no-pr-analysis"], json_mode=True)
    assert result.exit_code == 0
    payload = parse_json_output(result)
    inner = payload.get("payload") or {}
    assert "last_pr_analysis" not in inner
