"""W830 — Codify ``roam audit-trail-verify --gate`` exit semantics.

Sister to W829 (empty-corpus smoke) and W834 (cmd_health gate). Pins
the fail-closed decision: ``--gate`` exits 5 on both ``broken`` AND
``uninitialized`` chains, and 0 only on ``valid``. The structured JSON
envelope must still emit BEFORE the gate trips so agents can read
``summary.state`` and disambiguate the two failure modes (Pattern 2
always-emit discipline).

LAW 4 anchor terminals exercised: entries, chains, markers.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """Tmp git project with one .py file and no audit trail yet."""
    (tmp_path / "empty.py").write_text("# empty corpus\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "add", "-A"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@e.com",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def _invoke(args: list[str], cwd: Path) -> tuple[int, str]:
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(cwd)
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(prev)
    return result.exit_code, result.output


def _write_chain(path: Path, records: list[dict]) -> None:
    """Write a synthetic audit-trail JSONL with a real SHA-256 chain.

    Each record's ``previous_record_hash`` is the SHA-256 of the
    serialized previous line. Genesis has the empty string.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    lines: list[str] = []
    for rec in records:
        rec = dict(rec)
        rec["previous_record_hash"] = prev_hash
        line = json.dumps(rec, sort_keys=True)
        lines.append(line)
        prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Path 1: uninitialized chain → exit 5 (fail-closed by design)
# --------------------------------------------------------------------------


def test_gate_fails_closed_on_uninitialized_chain(empty_project: Path):
    """No audit trail at all: ``--gate`` MUST exit 5 (fail-closed)."""
    init_code, init_out = _invoke(["init"], empty_project)
    assert init_code == 0, f"roam init failed: {init_out}"

    code, out = _invoke(["--json", "audit-trail-verify", "--gate"], empty_project)

    # Fail-closed: missing trail → exit 5.
    assert code == 5, f"expected exit 5 on uninitialized chain, got {code}; output: {out}"

    # Pattern 2 always-emit: envelope must still print BEFORE exit 5 so
    # agents can disambiguate uninitialized from broken via summary.state.
    payload = json.loads(out)
    summary = payload["summary"]
    assert summary["state"] == "uninitialized"
    assert summary["chain_valid"] is False
    assert summary["partial_success"] is True
    assert summary["total_records"] == 0

    verdict_lc = summary["verdict"].lower()
    assert "not initialized" in verdict_lc or "empty" in verdict_lc, (
        f"verdict must disclose uninitialized state, got: {summary['verdict']!r}"
    )


def test_gate_fails_closed_on_empty_chain_file(empty_project: Path, tmp_path: Path):
    """Trail file exists but contains zero records: ``--gate`` exits 5."""
    init_code, _ = _invoke(["init"], empty_project)
    assert init_code == 0

    trail = empty_project / ".roam" / "audit-trail.jsonl"
    trail.parent.mkdir(parents=True, exist_ok=True)
    trail.write_text("", encoding="utf-8")

    code, out = _invoke(
        ["--json", "audit-trail-verify", "--input", str(trail), "--gate"],
        empty_project,
    )

    assert code == 5, f"expected exit 5 on empty chain file, got {code}; output: {out}"
    payload = json.loads(out)
    assert payload["summary"]["state"] == "uninitialized"


# --------------------------------------------------------------------------
# Path 2: valid chain → exit 0
# --------------------------------------------------------------------------


def test_gate_passes_on_valid_chain(empty_project: Path):
    """Well-formed chain of two records: ``--gate`` MUST exit 0."""
    init_code, _ = _invoke(["init"], empty_project)
    assert init_code == 0

    trail = empty_project / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            {"timestamp": "2026-05-15T00:00:00Z", "verdict": "ok", "actor": "ci"},
            {"timestamp": "2026-05-15T00:00:01Z", "verdict": "ok", "actor": "ci"},
        ],
    )

    code, out = _invoke(
        ["--json", "audit-trail-verify", "--input", str(trail), "--gate"],
        empty_project,
    )

    assert code == 0, f"expected exit 0 on valid chain, got {code}; output: {out}"
    payload = json.loads(out)
    summary = payload["summary"]
    assert summary["state"] == "valid"
    assert summary["chain_valid"] is True
    assert summary["partial_success"] is False
    assert summary["total_records"] == 2
    assert summary["issues_count"] == 0


# --------------------------------------------------------------------------
# Path 3: broken chain → exit 5
# --------------------------------------------------------------------------


def test_gate_fails_closed_on_broken_chain(empty_project: Path):
    """Tampered ``previous_record_hash`` on the second record: exit 5."""
    init_code, _ = _invoke(["init"], empty_project)
    assert init_code == 0

    trail = empty_project / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            {"timestamp": "2026-05-15T00:00:00Z", "verdict": "ok", "actor": "ci"},
            {"timestamp": "2026-05-15T00:00:01Z", "verdict": "ok", "actor": "ci"},
        ],
    )

    # Tamper: rewrite the second record with a wrong previous_record_hash.
    lines = trail.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    second["previous_record_hash"] = "0" * 64  # wrong hash → chain breaks
    lines[1] = json.dumps(second, sort_keys=True)
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")

    code, out = _invoke(
        ["--json", "audit-trail-verify", "--input", str(trail), "--gate"],
        empty_project,
    )

    assert code == 5, f"expected exit 5 on broken chain, got {code}; output: {out}"
    payload = json.loads(out)
    summary = payload["summary"]
    assert summary["state"] == "broken"
    assert summary["chain_valid"] is False
    assert summary["partial_success"] is True
    assert summary["issues_count"] >= 1


# --------------------------------------------------------------------------
# Gate-absence sanity: without --gate, exit 0 on all three states
# --------------------------------------------------------------------------


def test_no_gate_flag_never_exits_5_on_uninitialized(empty_project: Path):
    """Without ``--gate`` the command is purely diagnostic — exit 0."""
    init_code, _ = _invoke(["init"], empty_project)
    assert init_code == 0

    code, out = _invoke(["--json", "audit-trail-verify"], empty_project)

    assert code == 0, f"non-gated run must exit 0, got {code}; output: {out}"
    payload = json.loads(out)
    assert payload["summary"]["state"] == "uninitialized"
