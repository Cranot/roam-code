"""Tests for sequence_number + AuditIntegritySummary (P7 / C.1.xxx)."""

from __future__ import annotations

import hashlib
import json as _json

from click.testing import CliRunner

from roam.commands.audit_trail_helpers import (
    AUDIT_TRAIL_SCHEMA,
    INTEGRITY_SUMMARY_SCHEMA,
    next_sequence_number,
)


def test_next_sequence_number_returns_1_on_missing(tmp_path):
    assert next_sequence_number(tmp_path / "missing.jsonl") == 1


def test_next_sequence_number_returns_1_on_empty(tmp_path):
    p = tmp_path / "trail.jsonl"
    p.write_text("", encoding="utf-8")
    assert next_sequence_number(p) == 1


def test_next_sequence_number_skips_blank_lines(tmp_path):
    p = tmp_path / "trail.jsonl"
    p.write_text("{}\n\n   \n{}\n", encoding="utf-8")
    # 2 non-blank lines → next = 3
    assert next_sequence_number(p) == 3


def test_next_sequence_number_counts_malformed_too(tmp_path):
    """Malformed lines still occupy a sequence slot for transparency."""
    p = tmp_path / "trail.jsonl"
    p.write_text("{}\n{garbage\n{}\n", encoding="utf-8")
    assert next_sequence_number(p) == 4


def test_finalize_appends_integrity_summary(tmp_path):
    """audit-trail-export --finalize writes a closing AuditIntegritySummary record."""
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    rec = {
        "schema": AUDIT_TRAIL_SCHEMA,
        "sequence_number": 1,
        "timestamp": "2026-05-05T00:00:00Z",
        "actor": "alice@x",
        "verdict": "SAFE",
        "previous_record_hash": "",
    }
    line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
    trail.write_text(line + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["audit-trail-export", "--input", str(trail), "--finalize", "--format", "json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # File should now have 2 lines: original record + integrity summary
    final_lines = trail.read_text(encoding="utf-8").strip().split("\n")
    assert len(final_lines) == 2
    summary_record = _json.loads(final_lines[1])
    assert summary_record["schema"] == INTEGRITY_SUMMARY_SCHEMA
    assert summary_record["event_count"] == 1
    assert summary_record["hash_algorithm"] == "sha256"
    # chain_head should be the sha256 of the original record line
    assert summary_record["chain_head"] == hashlib.sha256(line.encode("utf-8")).hexdigest()


def test_finalize_no_op_on_missing_trail(tmp_path):
    """--finalize on a non-existent trail should not crash; summary just isn't appended."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["audit-trail-export", "--input", str(tmp_path / "missing.jsonl"), "--finalize"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert not (tmp_path / "missing.jsonl").exists()  # not created


def test_finalize_records_event_count_excludes_blank_and_malformed(tmp_path):
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    valid = {
        "schema": AUDIT_TRAIL_SCHEMA,
        "sequence_number": 1,
        "timestamp": "2026-05-05T00:00:00Z",
        "actor": "a",
        "verdict": "SAFE",
        "previous_record_hash": "",
    }
    trail.write_text(
        _json.dumps(valid) + "\n\n   \n{not valid json\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    runner.invoke(cli, ["audit-trail-export", "--input", str(trail), "--finalize", "--format", "json"])
    final_lines = [line for line in trail.read_text(encoding="utf-8").split("\n") if line.strip()]
    summary = _json.loads(final_lines[-1])
    # event_count should be the count of *parseable* records (load_records skips invalid JSON)
    assert summary["event_count"] == 1
