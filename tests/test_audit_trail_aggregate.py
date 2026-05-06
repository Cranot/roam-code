"""Tests for ``roam audit-trail-export --aggregate``."""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_audit_trail_export import (
    _aggregate_records,
    _render_aggregate_csv,
    _render_aggregate_markdown,
)


def _record(verdict: str, actor: str, repo: str, ts: str) -> dict:
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "actor": actor,
        "repo": repo,
        "verdict": verdict,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
    }


def test_aggregate_empty_returns_zero_total():
    agg = _aggregate_records([])
    assert agg["total_records"] == 0
    assert agg["by_verdict"] == {}
    assert agg["by_actor"] == {}
    assert agg["by_repo"] == {}
    assert agg["by_month"] == {}


def test_aggregate_single_record():
    agg = _aggregate_records([_record("BLOCK", "alice@x", "github.com/o/r", "2026-05-05T00:00:00Z")])
    assert agg["total_records"] == 1
    assert agg["by_verdict"]["BLOCK"] == 1
    assert agg["by_actor"]["alice@x"]["BLOCK"] == 1
    assert agg["by_actor"]["alice@x"]["_total"] == 1
    assert agg["by_repo"]["github.com/o/r"]["BLOCK"] == 1
    assert agg["by_month"]["2026-05"]["BLOCK"] == 1


def test_aggregate_buckets_across_months():
    records = [
        _record("BLOCK", "a", "r", "2026-04-01T00:00:00Z"),
        _record("REVIEW", "a", "r", "2026-04-15T00:00:00Z"),
        _record("BLOCK", "a", "r", "2026-05-01T00:00:00Z"),
    ]
    agg = _aggregate_records(records)
    assert agg["by_month"]["2026-04"]["_total"] == 2
    assert agg["by_month"]["2026-05"]["_total"] == 1
    assert agg["by_verdict"]["BLOCK"] == 2
    assert agg["by_verdict"]["REVIEW"] == 1


def test_aggregate_unknown_verdict_goes_to_other():
    records = [_record("WEIRD", "a", "r", "2026-05-05T00:00:00Z")]
    agg = _aggregate_records(records)
    # Verdict bucket: WEIRD shows up in by_verdict directly.
    assert agg["by_verdict"]["WEIRD"] == 1
    # In per-actor buckets, unknown verdicts roll into OTHER.
    assert agg["by_actor"]["a"]["OTHER"] == 1


def test_aggregate_handles_missing_timestamp():
    records = [_record("SAFE", "a", "r", "")]
    agg = _aggregate_records(records)
    assert "<undated>" in agg["by_month"]


def test_aggregate_handles_missing_actor_and_repo():
    records = [{"verdict": "SAFE", "timestamp": "2026-05-05T00:00:00Z"}]
    agg = _aggregate_records(records)
    assert agg["by_actor"]["<unknown>"]["SAFE"] == 1
    assert agg["by_repo"]["<unknown>"]["SAFE"] == 1


def test_render_aggregate_markdown_has_three_dimension_tables():
    agg = _aggregate_records(
        [
            _record("BLOCK", "alice@x", "github.com/o/r", "2026-05-05T00:00:00Z"),
            _record("REVIEW", "bob@x", "github.com/o/r", "2026-05-06T00:00:00Z"),
        ]
    )
    md = _render_aggregate_markdown(agg, Path("trail.jsonl"))
    assert "## By verdict" in md
    assert "## By month" in md
    assert "## By actor" in md
    assert "## By repo" in md
    assert "alice@x" in md
    assert "bob@x" in md


def test_render_aggregate_csv_includes_all_dimensions():
    agg = _aggregate_records(
        [
            _record("BLOCK", "alice@x", "r", "2026-05-05T00:00:00Z"),
        ]
    )
    csv_text = _render_aggregate_csv(agg)
    lines = csv_text.strip().splitlines()
    assert lines[0] == "dimension,key,verdict,count"
    # Should include verdict, by_month, by_actor, by_repo rows.
    dims_seen = {row.split(",")[0] for row in lines[1:]}
    assert "verdict" in dims_seen
    assert "by_month" in dims_seen
    assert "by_actor" in dims_seen
    assert "by_repo" in dims_seen


def test_cli_aggregate_md_smoke(tmp_path):
    """End-to-end CLI: --aggregate emits the procurement summary tables."""
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(_record("BLOCK", "alice@x", "github.com/o/r", "2026-05-05T00:00:00Z"))
        + "\n"
        + _json.dumps(_record("REVIEW", "bob@x", "github.com/o/r", "2026-05-06T00:00:00Z"))
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["audit-trail-export", "--input", str(trail), "--aggregate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Aggregate Report" in result.output
    assert "By verdict" in result.output
    assert "alice@x" in result.output


def test_cli_aggregate_json_includes_aggregate_block(tmp_path):
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(_record("BLOCK", "a", "r", "2026-05-05T00:00:00Z")) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "audit-trail-export", "--input", str(trail), "--aggregate"],
        catch_exceptions=False,
    )
    env = _json.loads(result.output)
    assert env["summary"]["aggregate"] is True
    assert "aggregate" in env
    assert env["aggregate"]["total_records"] == 1


def test_cli_aggregate_csv(tmp_path):
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(_record("BLOCK", "a", "r", "2026-05-05T00:00:00Z")) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["audit-trail-export", "--input", str(trail), "--aggregate", "--format", "csv"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "dimension,key,verdict,count" in result.output


def test_aggregate_snapshot_includes_top_actor_and_month():
    records = [
        _record("BLOCK", "alice@x", "github.com/a/b", "2026-04-01T00:00:00Z"),
        _record("REVIEW", "alice@x", "github.com/a/b", "2026-04-15T00:00:00Z"),
        _record("BLOCK", "bob@x", "github.com/c/d", "2026-05-01T00:00:00Z"),
    ]
    agg = _aggregate_records(records)
    snap = agg["snapshot"]
    assert snap["top_actor"]["key"] == "alice@x"
    assert snap["top_actor"]["count"] == 2
    assert snap["top_month"]["key"] == "2026-04"
    assert snap["top_month"]["count"] == 2
    assert snap["top_verdict"]["key"] == "BLOCK"
    assert snap["top_verdict"]["count"] == 2


def test_aggregate_snapshot_handles_empty():
    agg = _aggregate_records([])
    snap = agg["snapshot"]
    assert snap["top_actor"] is None
    assert snap["top_repo"] is None
    assert snap["top_month"] is None
    assert snap["top_verdict"] is None


def test_top_actors_ranks_by_block_count():
    from roam.commands.cmd_audit_trail_export import _build_top_actors

    records = [
        _record("BLOCK", "alice@x", "r", "2026-05-05T00:00:00Z"),
        _record("BLOCK", "alice@x", "r", "2026-05-06T00:00:00Z"),
        _record("REVIEW", "alice@x", "r", "2026-05-07T00:00:00Z"),
        _record("BLOCK", "bob@x", "r", "2026-05-05T00:00:00Z"),
        _record("SAFE", "carol@x", "r", "2026-05-05T00:00:00Z"),
    ]
    out = _build_top_actors(records, limit=10)
    assert out[0]["actor"] == "alice@x"
    assert out[0]["BLOCK"] == 2
    assert out[1]["actor"] == "bob@x"
    assert out[1]["BLOCK"] == 1
    # Tiebreaker: carol has 0 BLOCK + 1 total → after bob
    assert out[2]["actor"] == "carol@x"


def test_top_actors_truncates_to_limit():
    from roam.commands.cmd_audit_trail_export import _build_top_actors

    records = [_record("BLOCK", f"actor{i}@x", "r", "2026-05-05T00:00:00Z") for i in range(10)]
    out = _build_top_actors(records, limit=3)
    assert len(out) == 3


def test_top_actors_handles_empty():
    from roam.commands.cmd_audit_trail_export import _build_top_actors

    assert _build_top_actors([], limit=5) == []


def test_cli_top_actors_md(tmp_path):
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(_record("BLOCK", "alice@x", "r", "2026-05-05T00:00:00Z"))
        + "\n"
        + _json.dumps(_record("REVIEW", "bob@x", "r", "2026-05-06T00:00:00Z"))
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["audit-trail-export", "--input", str(trail), "--top-actors", "5"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Top actors by BLOCK" in result.output
    assert "alice@x" in result.output
    assert "bob@x" in result.output


def test_cli_top_actors_json(tmp_path):
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(_record("BLOCK", "alice@x", "r", "2026-05-05T00:00:00Z")) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "audit-trail-export", "--input", str(trail), "--top-actors", "3"],
        catch_exceptions=False,
    )
    env = _json.loads(result.output)
    assert "top_actors" in env
    assert env["top_actors"][0]["actor"] == "alice@x"
    assert env["summary"]["top_actors_limit"] == 3


def test_render_aggregate_markdown_includes_snapshot_line():
    records = [_record("BLOCK", "alice@x", "github.com/o/r", "2026-05-01T00:00:00Z")]
    md = _render_aggregate_markdown(_aggregate_records(records), Path("trail.jsonl"))
    assert "top verdict" in md
    assert "top actor" in md
    assert "alice@x" in md


def test_cli_aggregate_with_filter(tmp_path):
    """Aggregate respects --since / --verdict filters."""
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(_record("BLOCK", "a", "r", "2026-04-05T00:00:00Z"))
        + "\n"
        + _json.dumps(_record("REVIEW", "a", "r", "2026-05-05T00:00:00Z"))
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "audit-trail-export",
            "--input",
            str(trail),
            "--aggregate",
            "--since",
            "2026-05-01",
        ],
        catch_exceptions=False,
    )
    env = _json.loads(result.output)
    assert env["aggregate"]["total_records"] == 1  # filter applied
    assert "REVIEW" in env["aggregate"]["by_verdict"]
    assert "BLOCK" not in env["aggregate"]["by_verdict"]
