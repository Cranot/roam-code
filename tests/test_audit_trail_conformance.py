"""Tests for ``roam audit-trail-conformance-check`` — EU AI Act Article 12 scorer."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json as _json
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_audit_trail_conformance import (
    EXIT_GATE_FAILURE,
    _check_actors,
    _check_reproducibility,
    _check_retention,
    _check_timestamps,
    _check_verdicts_and_rationale,
    _parse_iso,
)


def _write_chain(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)
            rec["previous_record_hash"] = prev_hash
            line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()


def _full_record(verdict: str, ts: str, actor: str = "alice@x") -> dict:
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "tool": "roam-code",
        "tool_version": "12.26",
        "actor": actor,
        "repo": "github.com/o/r",
        "git_sha": "abc123def456",
        "diff_sha256": "deadbeef" * 8,
        "verdict": verdict,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
        "high_severity_critique": 0,
        "intent_marker": None,
        "rationale_summary": f"Verdict: **{verdict}**. Sample rationale text.",
    }


# ---- Per-check unit tests --------------------------------------------------


def test_parse_iso_handles_z_suffix():
    dt = _parse_iso("2026-05-05T12:34:56Z")
    assert dt is not None
    assert dt.year == 2026


def test_parse_iso_handles_offset_suffix():
    dt = _parse_iso("2026-05-05T12:34:56+00:00")
    assert dt is not None


def test_parse_iso_returns_none_on_garbage():
    assert _parse_iso("not a timestamp") is None
    assert _parse_iso("") is None


def test_check_timestamps_all_present():
    ok, msg = _check_timestamps([_full_record("SAFE", "2026-05-05T00:00:00Z")])
    assert ok
    assert "1 record" in msg


def test_check_timestamps_one_missing():
    records = [_full_record("SAFE", "2026-05-05T00:00:00Z"), _full_record("REVIEW", "")]
    ok, msg = _check_timestamps(records)
    assert not ok
    assert "1 record" in msg


def test_check_actors_unknown_fails():
    records = [_full_record("SAFE", "2026-05-05T00:00:00Z", actor="<unknown>")]
    ok, _ = _check_actors(records)
    assert not ok


def test_check_actors_present():
    records = [_full_record("SAFE", "2026-05-05T00:00:00Z", actor="alice@x")]
    ok, _ = _check_actors(records)
    assert ok


def test_check_reproducibility_full_record_passes():
    ok, _ = _check_reproducibility([_full_record("SAFE", "2026-05-05T00:00:00Z")])
    assert ok


def test_check_reproducibility_missing_diff_hash_fails():
    rec = _full_record("SAFE", "2026-05-05T00:00:00Z")
    rec["diff_sha256"] = ""
    ok, msg = _check_reproducibility([rec])
    assert not ok
    assert "1 record" in msg


def test_check_verdicts_full_passes():
    ok, _ = _check_verdicts_and_rationale([_full_record("SAFE", "2026-05-05T00:00:00Z")])
    assert ok


def test_check_verdicts_missing_rationale_fails():
    rec = _full_record("SAFE", "2026-05-05T00:00:00Z")
    rec["rationale_summary"] = ""
    ok, msg = _check_verdicts_and_rationale([rec])
    assert not ok
    assert "rationale_summary" in msg


def test_check_retention_pass_when_old_record_exists():
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=200)).isoformat().replace("+00:00", "Z")
    records = [_full_record("SAFE", old_ts)]
    ok, _ = _check_retention(records, retention_days=180)
    assert ok


def test_check_retention_fail_when_only_recent_records():
    recent_ts = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    records = [_full_record("SAFE", recent_ts)]
    ok, msg = _check_retention(records, retention_days=180)
    assert not ok
    assert "minimum retention" in msg


def test_check_retention_empty_list():
    ok, msg = _check_retention([], retention_days=180)
    assert not ok
    assert "no records" in msg


# ---- CLI integration tests -------------------------------------------------


def test_cli_help():
    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(cli, ["audit-trail-conformance-check", "--help"])
    assert result.exit_code == 0
    assert "Article 12" in result.output
    assert "--retention-days" in result.output


def test_cli_missing_trail_returns_zero_score(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(
        cli,
        ["audit-trail-conformance-check", "--input", str(tmp_path / "nope.jsonl")],
    )
    assert result.exit_code == 0
    assert "score:   0/100" in result.output


def test_cli_missing_trail_with_gate_exits_5(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(
        cli,
        ["audit-trail-conformance-check", "--input", str(tmp_path / "nope.jsonl"), "--gate"],
    )
    assert result.exit_code == EXIT_GATE_FAILURE


def test_cli_full_conformant_trail_scores_100(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=200)).isoformat().replace("+00:00", "Z")
    recent_ts = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    _write_chain(trail, [_full_record("SAFE", old_ts), _full_record("REVIEW", recent_ts)])

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(trail)],
    )
    env = _json.loads(result.output)
    assert env["summary"]["score"] == 100
    assert env["summary"]["checks_passed"] == 6


def test_cli_partial_conformance_when_actor_missing(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=200)).isoformat().replace("+00:00", "Z")
    rec = _full_record("SAFE", old_ts, actor="<unknown>")
    _write_chain(trail, [rec])

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(trail)],
    )
    env = _json.loads(result.output)
    assert env["summary"]["score"] < 100
    actor_check = next(c for c in env["checks"] if c["id"] == "actor_attribution")
    assert not actor_check["passed"]


def test_cli_chain_break_fails_chain_integrity_check(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=200)).isoformat().replace("+00:00", "Z")
    recent_ts = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    _write_chain(trail, [_full_record("SAFE", old_ts), _full_record("REVIEW", recent_ts)])

    # Tamper with line 1.
    lines = trail.read_text(encoding="utf-8").splitlines()
    rec1 = _json.loads(lines[0])
    rec1["verdict"] = "TAMPERED"
    lines[0] = _json.dumps(rec1, separators=(",", ":"), sort_keys=True)
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(trail)],
    )
    env = _json.loads(result.output)
    chain_check = next(c for c in env["checks"] if c["id"] == "chain_integrity")
    assert not chain_check["passed"]


def test_cli_gate_exits_5_on_partial_conformance(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    rec = _full_record("SAFE", _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"))
    _write_chain(trail, [rec])

    result = runner.invoke(
        cli,
        ["audit-trail-conformance-check", "--input", str(trail), "--gate"],
    )
    # Recent-only timestamps fail the retention check → score < 100 → gate fires.
    assert result.exit_code == EXIT_GATE_FAILURE


def test_cli_sarif_emits_valid_envelope(tmp_path):
    """--sarif emits a valid SARIF 2.1.0 doc with a result per failed check."""
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    rec = _full_record("SAFE", _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"))
    _write_chain(trail, [rec])

    # Global --sarif comes BEFORE the subcommand name
    result = runner.invoke(cli, ["--sarif", "audit-trail-conformance-check", "--input", str(trail)])
    assert result.exit_code == 0
    doc = _json.loads(result.output)
    assert doc["version"] == "2.1.0"
    assert doc["runs"][0]["tool"]["driver"]["name"] == "roam-code"
    # Recent-only timestamps fail retention → at least 1 result
    results = doc["runs"][0]["results"]
    assert len(results) >= 1
    rule_ids = {r["ruleId"] for r in results}
    assert "retention" in rule_ids
    # Each rule has helpUri pointing at the regulation
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    assert all("artificialintelligenceact.eu" in r.get("helpUri", "") for r in rules)


def test_cli_sarif_writes_to_file(tmp_path):
    """--sarif --sarif-output writes to the file + emits a single VERDICT line."""
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    _write_chain(
        trail,
        [_full_record("SAFE", _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"))],
    )
    sarif_path = tmp_path / "out.sarif"
    result = runner.invoke(
        cli,
        [
            "--sarif",
            "audit-trail-conformance-check",
            "--input",
            str(trail),
            "--sarif-output",
            str(sarif_path),
        ],
    )
    assert result.exit_code == 0
    assert sarif_path.exists()
    doc = _json.loads(sarif_path.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"
    assert "VERDICT:" in result.output


def test_cli_sarif_with_perfect_score_emits_no_results(tmp_path):
    """A 100/100 trail produces a SARIF with rules but zero findings."""
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=200)).isoformat().replace("+00:00", "Z")
    recent_ts = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    _write_chain(trail, [_full_record("SAFE", old_ts), _full_record("REVIEW", recent_ts)])

    result = runner.invoke(cli, ["--sarif", "audit-trail-conformance-check", "--input", str(trail)])
    doc = _json.loads(result.output)
    assert doc["runs"][0]["results"] == []  # all 6 checks passed


def test_cli_custom_retention_days(tmp_path):
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    # Record 30 days old; with --retention-days 7, should pass.
    ts_30d = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).isoformat().replace("+00:00", "Z")
    _write_chain(trail, [_full_record("SAFE", ts_30d)])

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(trail), "--retention-days", "7"],
    )
    env = _json.loads(result.output)
    retention_check = next(c for c in env["checks"] if c["id"] == "retention")
    assert retention_check["passed"]
