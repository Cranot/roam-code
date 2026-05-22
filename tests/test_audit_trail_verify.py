"""Tests for ``roam audit-trail-verify`` — chain integrity verifier."""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path

import pytest

from roam.commands.cmd_audit_trail_verify import (
    EXIT_GATE_FAILURE,
    _build_rollup,
    _verify_chain,
)


def _write_chain(path: Path, records: list[dict]) -> None:
    """Write records as JSONL with proper SHA-256 chain linking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)  # don't mutate caller's input
            rec["previous_record_hash"] = prev_hash
            line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()


def _base_record(verdict: str, ts: str) -> dict:
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "tool": "roam-code",
        "tool_version": "12.26",
        "actor": "test@example.com",
        "verdict": verdict,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
    }


def test_verify_empty_path_returns_no_records(tmp_path):
    path = tmp_path / "missing.jsonl"
    records, issues = _verify_chain(path)
    assert records == []
    # Missing file produces a single "not found" issue.
    assert len(issues) == 1
    assert "not found" in issues[0]["issue"]


def test_verify_single_record_valid_chain(tmp_path):
    path = tmp_path / "trail.jsonl"
    _write_chain(path, [_base_record("REVIEW", "2026-05-05T00:00:00Z")])
    records, issues = _verify_chain(path)
    assert len(records) == 1
    assert issues == []


def test_verify_three_record_chain_valid(tmp_path):
    path = tmp_path / "trail.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
            _base_record("BLOCK", "2026-05-05T00:02:00Z"),
        ],
    )
    records, issues = _verify_chain(path)
    assert len(records) == 3
    assert issues == []


def test_verify_detects_tampered_middle_record(tmp_path):
    path = tmp_path / "trail.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
            _base_record("BLOCK", "2026-05-05T00:02:00Z"),
        ],
    )
    # Tamper with line 2: change the verdict, leaving line 3's
    # previous_record_hash pointing at the original line 2's hash.
    lines = path.read_text(encoding="utf-8").splitlines()
    rec2 = _json.loads(lines[1])
    rec2["verdict"] = "TAMPERED"
    lines[1] = _json.dumps(rec2, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records, issues = _verify_chain(path)
    # All 3 records still parse, but line 3's chain link is broken.
    assert len(records) == 3
    assert len(issues) == 1
    assert issues[0]["line"] == 3
    assert "mismatch" in issues[0]["issue"]


def test_verify_detects_invalid_json_line(tmp_path):
    path = tmp_path / "trail.jsonl"
    _write_chain(path, [_base_record("SAFE", "2026-05-05T00:00:00Z")])
    # Append a malformed line.
    with path.open("a", encoding="utf-8") as f:
        f.write("{this is not json}\n")
    records, issues = _verify_chain(path)
    assert len(records) == 1  # the valid record only
    assert len(issues) >= 1
    assert any("invalid JSON" in i["issue"] for i in issues)


def test_verify_skips_blank_lines(tmp_path):
    path = tmp_path / "trail.jsonl"
    _write_chain(path, [_base_record("SAFE", "2026-05-05T00:00:00Z")])
    # Insert a blank line between records.
    with path.open("a", encoding="utf-8") as f:
        f.write("\n\n")
    records, issues = _verify_chain(path)
    assert len(records) == 1
    assert issues == []


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


def test_cli_audit_trail_verify_help_lists_options(cli_runner):
    from roam.cli import cli

    result = cli_runner.invoke(cli, ["audit-trail-verify", "--help"])
    assert "--gate" in result.output
    assert "--input" in result.output


def test_cli_audit_trail_verify_gate_exits_5_on_break(tmp_path, cli_runner):
    from roam.cli import cli

    path = tmp_path / "trail.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )
    # Tamper with line 1.
    lines = path.read_text(encoding="utf-8").splitlines()
    rec1 = _json.loads(lines[0])
    rec1["verdict"] = "TAMPERED"
    lines[0] = _json.dumps(rec1, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = cli_runner.invoke(cli, ["audit-trail-verify", "--input", str(path), "--gate"])
    assert result.exit_code == EXIT_GATE_FAILURE


@pytest.fixture
def tiny_indexed(tmp_path, monkeypatch):
    """Lightweight project with index — needed for pr-analyze."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import git_commit, git_init, index_in_process

    proj = tmp_path / "tiny"
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


def _last_json_object(text: str) -> dict:
    """Extract the last JSON envelope from a stdout that may also contain index logs."""
    # CliRunner buffers everything; find the start of the JSON object.
    idx = text.rfind("\n{\n")
    if idx == -1:
        idx = text.find("{")
    return _json.loads(text[idx:])


def test_pr_analyze_auto_verify_chain_when_audit_trail_used(tmp_path, tiny_indexed, cli_runner):
    """pr-analyze --audit-trail should pre-verify the chain and surface integrity status."""
    from roam.cli import cli

    trail = tiny_indexed / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )

    diff = tmp_path / "x.diff"
    diff.write_text("")

    result = cli_runner.invoke(
        cli,
        ["--json", "pr-analyze", "--audit-trail", "--input", str(diff)],
    )
    env = _last_json_object(result.output)
    assert "audit_trail" in env
    assert env["audit_trail"]["chain_status"]["pre_emission_chain_valid"] is True


def test_pr_analyze_audit_trail_break_escalates_verdict_to_block(tmp_path, tiny_indexed, cli_runner):
    """If chain is tampered before pr-analyze --audit-trail, verdict escalates to BLOCK."""
    from roam.cli import cli

    trail = tiny_indexed / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )
    # Tamper line 1 — breaks chain link to line 2.
    lines = trail.read_text(encoding="utf-8").splitlines()
    rec1 = _json.loads(lines[0])
    rec1["verdict"] = "TAMPERED"
    lines[0] = _json.dumps(rec1, separators=(",", ":"), sort_keys=True)
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")

    diff = tmp_path / "x.diff"
    diff.write_text("")

    result = cli_runner.invoke(
        cli,
        ["--json", "pr-analyze", "--audit-trail", "--input", str(diff)],
    )
    env = _last_json_object(result.output)
    # Verdict should start with BLOCK because the chain was broken before append.
    # W641: pr-risk now appends a "(risk_level <tier>)" suffix to the BLOCK
    # verdict; startswith preserves the original BLOCK semantic while tolerating
    # the suffix.
    assert env["summary"]["verdict"].startswith("BLOCK")
    # Pre-chain verdict should be preserved for transparency.
    assert "verdict_pre_chain_break" in env["summary"]
    assert env["audit_trail"]["chain_status"]["pre_emission_chain_valid"] is False
    assert any("chain broken" in r for r in env["summary"]["reasons"])


def test_pr_analyze_audit_trail_attaches_conformance_score(tmp_path, tiny_indexed, cli_runner):
    """C.1.zz — pr-analyze --audit-trail should auto-run conformance-check
    and attach the score to bundle.audit_trail.conformance.
    """
    from roam.cli import cli

    diff = tmp_path / "x.diff"
    diff.write_text("")
    result = cli_runner.invoke(
        cli,
        ["--json", "pr-analyze", "--audit-trail", "--input", str(diff)],
    )
    env = _last_json_object(result.output)
    conf = (env.get("audit_trail") or {}).get("conformance")
    assert conf is not None, "expected conformance block in audit_trail"
    assert "score" in conf
    assert conf["checks_total"] == 6
    assert "Article 12" in conf["schema_reference"]


def test_pr_analyze_audit_trail_break_with_gate_exits_5(tmp_path, tiny_indexed, cli_runner):
    """The escalated BLOCK verdict from a broken chain should fail --gate."""
    from roam.cli import cli

    trail = tiny_indexed / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )
    lines = trail.read_text(encoding="utf-8").splitlines()
    rec1 = _json.loads(lines[0])
    rec1["verdict"] = "TAMPERED"
    lines[0] = _json.dumps(rec1, separators=(",", ":"), sort_keys=True)
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")

    diff = tmp_path / "x.diff"
    diff.write_text("")

    result = cli_runner.invoke(
        cli,
        ["pr-analyze", "--audit-trail", "--gate", "--input", str(diff)],
    )
    assert result.exit_code == EXIT_GATE_FAILURE


def test_cli_audit_trail_verify_clean_chain_exits_0(tmp_path, cli_runner):
    from roam.cli import cli

    path = tmp_path / "trail.jsonl"
    _write_chain(path, [_base_record("SAFE", "2026-05-05T00:00:00Z")])
    result = cli_runner.invoke(cli, ["audit-trail-verify", "--input", str(path), "--gate"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Missing-signature disclosure (Pattern-2 silent-fallback guard).
#
# Two halves to the fix:
#   (1) `_build_rollup` must NOT count the genesis record as unsigned —
#       an empty `previous_record_hash` on genesis is the chain root
#       marker, by design. The pre-fix code reported a FALSE
#       `missing_signatures: 1` on every well-formed chain, which made
#       the envelope contradict itself (`chain_valid: true` +
#       `missing_signatures: 1`).
#   (2) WHEN `missing_signatures > 0` genuinely (a non-genesis record
#       carrying no integrity hash), the verdict must NAME the gap and
#       `partial_success` must flip — never a silent flat "chain valid".
# ---------------------------------------------------------------------------


def test_rollup_does_not_count_genesis_as_missing_signature():
    """`_build_rollup` exempts the genesis record from the unsigned count.

    Genesis legitimately has an empty `previous_record_hash` and the roam
    audit-trail schema carries no per-record `record_hash`. Counting
    genesis as unsigned produced a FALSE `missing_signatures: 1` on every
    well-formed chain.
    """
    # Build via the real verifier so the records carry real shapes.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "trail.jsonl"
        _write_chain(
            path,
            [
                _base_record("SAFE", "2026-05-05T00:00:00Z"),
                _base_record("REVIEW", "2026-05-05T00:01:00Z"),
                _base_record("BLOCK", "2026-05-05T00:02:00Z"),
            ],
        )
        records, issues = _verify_chain(path)
    rollup = _build_rollup(records, issues, len(records), len(issues))
    # Genesis exempt -> a clean chain has zero missing signatures.
    assert rollup["missing_signatures"] == 0, rollup
    assert rollup["broken_runs"] == 0
    assert rollup["total_runs"] == 3


def test_rollup_counts_genuine_unsigned_non_genesis_record():
    """A non-genesis record with no integrity hash IS counted as unsigned.

    Directly exercises the `missing_signatures` branch with a synthetic
    record list so the disclosure path is provably reachable.
    """
    records = [
        {"previous_record_hash": "", "timestamp": "t0"},  # genesis — exempt
        {"previous_record_hash": "abc", "timestamp": "t1"},  # signed
        {"previous_record_hash": "", "timestamp": "t2"},  # non-genesis, unsigned
    ]
    rollup = _build_rollup(records, [], len(records), 0)
    assert rollup["missing_signatures"] == 1, rollup


def test_verify_clean_chain_envelope_is_self_consistent(tmp_path, cli_runner):
    """A valid chain must NOT contradict itself in the envelope.

    Regression guard for the Defect-2 silent fallback: `chain_valid: true`
    while `chain_rollup.missing_signatures: 1`. Post-fix the well-formed
    chain reports `missing_signatures: 0`, a plain verdict, and
    `partial_success: false` — all three agree.
    """
    from roam.cli import cli

    path = tmp_path / "trail.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )
    result = cli_runner.invoke(cli, ["--json", "audit-trail-verify", "--input", str(path)])
    env = _json.loads(result.output)
    summary = env["summary"]
    assert summary["chain_valid"] is True
    assert summary["partial_success"] is False
    assert summary["unsigned_events"] == 0
    assert summary["chain_rollup"]["missing_signatures"] == 0
    # Verdict stays plain — no false "unsigned" disclosure.
    assert "unsigned" not in summary["verdict"].lower()
