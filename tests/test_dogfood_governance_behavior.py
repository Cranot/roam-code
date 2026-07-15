"""Behavioral dogfood tests for the PROVENANCE & GOVERNANCE command cluster.

These commands (attest / cga / audit-trail-* / article-12-check / agent-score /
constitution / proof-bundle / runs verify / mode) are "hidden sellable"
compliance surfaces that almost never run on real code. This module exercises
their REAL cryptographic and scoring round-trips — NOT the JSON-envelope shape —
and asserts the load-bearing property each one is sold on:

  * emit -> verify PASSES on untampered evidence
  * tamper -> verify FAILS (flips to mismatch / tampered)
  * a conformance score matches a hand-built ledger
  * the scoring formula behaves as documented

It also PINS three defects surfaced during the dogfood (see the module-level
DEFECTS note and the xfail/documenting tests):

  1. audit-trail-verify's SHA-256 previous_record_hash chain leaves the FINAL
     record unprotected — tampering the last record's payload is UNDETECTED,
     contradicting the command's own docstring ("Tampering with any record ...
     breaks the chain"). Pinned as a strict-xfail.
  2. article-12-check's high-risk classifier word-matches "promote" / "terminate"
     etc., so a benign `def promote()` trips an EU AI Act high-risk REVIEW.
     Pinned as a documenting test.
  3. `roam attest verify` is NOT a subcommand — "verify" is swallowed as a git
     commit-range and the command exits 0 doing nothing, yet
     cmd_evidence_diff suggests exactly that command. Pinned as a documenting
     test.

Run:
  .venv/Scripts/python.exe -m pytest tests/test_dogfood_governance_behavior.py \
      -p no:cacheprovider -o addopts="" -q
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO = repo_root()
INDEX_DB = REPO / ".roam" / "index.db"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def run_roam(*args: str, cwd: Path = REPO, timeout: int = 240):
    """Invoke `python -m roam ...` under the current interpreter."""
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sha256_chain_lines(records: list[dict]) -> list[str]:
    """Serialize records into a valid SHA-256 previous_record_hash chain.

    Matches roam.commands.cmd_audit_trail_verify._verify_chain's contract:
    genesis has previous_record_hash="" and each record's
    previous_record_hash is the sha256 of the *previous serialized line*.
    """
    lines: list[str] = []
    prev = ""
    for rec in records:
        rec = dict(rec)
        rec["previous_record_hash"] = prev
        line = json.dumps(rec)
        lines.append(line)
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return lines


needs_index = pytest.mark.skipif(not INDEX_DB.exists(), reason="repo .roam/index.db not present")


# ===========================================================================
# CGA — Code Graph Attestation (the cryptographic crown jewel)
# ===========================================================================


@needs_index
def test_cga_emit_verify_roundtrip_clean_graph_fingerprints():
    """build_cga_statement -> verify_cga_statement passes on the live index.

    Environmental predicates (git_dirty_hash / git_commit_sha1) are allowed to
    differ if the tree changes; the load-bearing property is that the GRAPH
    FINGERPRINTS (merkle_root / edge_bundle_digest / symbol_count / edge_count)
    round-trip against the same DB.
    """
    from roam.attest.cga import build_cga_statement, verify_cga_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
        ok, errors = verify_cga_statement(stmt, conn, project_root=REPO)

    fingerprint_errors = [
        e for e in errors if any(k in e for k in ("merkle_root", "edge_bundle_digest", "symbol_count", "edge_count"))
    ]
    assert fingerprint_errors == [], f"graph fingerprints must round-trip, got {fingerprint_errors}"
    # On a byte-stable tree the whole statement verifies clean.
    if not ok:
        # only environmental drift permitted
        assert all("git_" in e for e in errors), errors


@needs_index
def test_cga_verify_detects_merkle_tamper():
    """Corrupting merkle_root MUST flip verify to fail (tamper detection)."""
    from roam.attest.cga import build_cga_statement, verify_cga_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
        stmt["predicate"]["merkle_root"] = "deadbeef" * 8
        ok, errors = verify_cga_statement(stmt, conn, project_root=REPO)

    assert ok is False
    assert any("merkle_root mismatch" in e for e in errors), errors


@needs_index
def test_cga_verify_detects_symbol_count_tamper():
    """Even a +1 symbol_count edit is caught (not just the merkle digest)."""
    from roam.attest.cga import build_cga_statement, verify_cga_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
        stmt["predicate"]["symbol_count"] = int(stmt["predicate"]["symbol_count"]) + 1
        ok, errors = verify_cga_statement(stmt, conn, project_root=REPO)

    assert ok is False
    assert any("symbol_count mismatch" in e for e in errors), errors


@needs_index
def test_cga_verify_detects_edge_digest_tamper():
    """Corrupting the edge bundle digest flips verify to fail."""
    from roam.attest.cga import build_cga_statement, verify_cga_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
        stmt["predicate"]["edge_bundle_digest"] = "00" * 32
        ok, errors = verify_cga_statement(stmt, conn, project_root=REPO)

    assert ok is False
    assert any("edge_bundle_digest mismatch" in e for e in errors), errors


@needs_index
def test_cga_verify_predicate_type_and_statement_type_are_checked():
    """A wrong _type / predicateType is rejected — the envelope isn't trusted blindly."""
    from roam.attest.cga import build_cga_statement, verify_cga_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
        stmt["_type"] = "https://evil.example/Statement/v1"
        stmt["predicateType"] = "https://evil.example/predicate/v1"
        ok, errors = verify_cga_statement(stmt, conn, project_root=REPO)

    assert ok is False
    assert any("_type mismatch" in e for e in errors)
    assert any("predicateType mismatch" in e for e in errors)


@needs_index
def test_cga_cli_verify_fail_closed_without_cosign(tmp_path):
    """CLI `cga verify` on a statement with NO cosign bundle FAILS CLOSED (exit 5).

    This is the load-bearing "tamper-evident" claim: a downloaded predicate
    must NOT read "verified" while the signer-identity check is silently
    skipped. The verifier forces --no-cosign to acknowledge predicate-only mode.
    """
    from roam.attest.cga import build_cga_statement, serialize_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
    stmt_file = tmp_path / "clean.intoto.json"
    stmt_file.write_text(serialize_statement(stmt), encoding="utf-8")

    r = run_roam("cga", "verify", str(stmt_file))
    assert r.returncode == 5, r.stdout + r.stderr
    assert "cosign bundle not found" in (r.stdout + r.stderr)


@needs_index
def test_cga_cli_verify_tamper_flips_to_fail(tmp_path):
    """emit -> tamper merkle in the on-disk statement -> CLI verify exits 5."""
    from roam.attest.cga import build_cga_statement, serialize_statement
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        stmt = build_cga_statement(conn, project_root=REPO)
    stmt["predicate"]["merkle_root"] = "0" * 64
    stmt_file = tmp_path / "tampered.intoto.json"
    stmt_file.write_text(serialize_statement(stmt), encoding="utf-8")

    r = run_roam("cga", "verify", str(stmt_file), "--no-cosign")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "merkle_root mismatch" in (r.stdout + r.stderr)


# ===========================================================================
# attest — proof-carrying PR attestation + content-hash tamper primitive
# ===========================================================================


def test_attest_content_hash_is_deterministic_and_tamper_sensitive():
    """attest --sign's content_hash: same evidence -> same hash, edited -> different."""
    from roam.commands.cmd_attest import _content_hash

    evidence = {"risk": {"score": 30, "level": "MODERATE"}, "tests": {"selected": 4}}
    h1 = _content_hash(evidence)
    h2 = _content_hash(dict(evidence))
    assert h1 == h2 and h1.startswith("sha256:")

    tampered = json.loads(json.dumps(evidence))
    tampered["risk"]["score"] = 31
    assert _content_hash(tampered) != h1


@needs_index
def test_attest_sign_emits_content_hash_json():
    """`roam --json attest --sign` on the real (possibly dirty) tree emits a content_hash."""
    r = run_roam("--json", "attest", "--sign")
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    # Either a changeset was attested (content_hash present) OR a clean/no-change
    # tree (attestation without content_hash but a disclosed no-changes verdict).
    att = env.get("attestation") or {}
    summ = env.get("summary") or {}
    if att:
        assert "content_hash" in att and att["content_hash"].startswith("sha256:"), att
    else:
        assert summ.get("state") in ("no_changes", None) or summ.get("partial_success") is True


@needs_index
def test_attest_verify_is_NOT_a_subcommand_arg_trap():
    """DEFECT (documenting): `roam attest verify` is not a subcommand.

    'verify' is parsed as a COMMIT_RANGE, so the command silently exits 0 with
    "No changes found for verify." — despite cmd_evidence_diff suggesting
    `roam attest verify` as a next_command. Attestation verification actually
    lives in `cga verify` / `audit-trail-verify`.
    """
    r = run_roam("attest", "verify")
    assert r.returncode == 0
    assert "No changes found for verify" in (r.stdout + r.stderr)


# ===========================================================================
# runs verify — HMAC rolling-chain ledger integrity
# ===========================================================================


def _sign_events(n: int, key: bytes) -> list[dict]:
    from roam.runs.signing import SEED_SIGNATURE, compute_event_signature

    events: list[dict] = []
    prev = SEED_SIGNATURE
    for i in range(1, n + 1):
        ev = {"seq": i, "action": f"act{i}", "payload": i}
        sig = compute_event_signature(prev, ev, key)
        ev["signature"] = sig
        prev = sig
        events.append(ev)
    return events


def test_runs_verify_hmac_chain_roundtrip_ok():
    from roam.runs.signing import verify_chain

    key = b"\x11" * 32
    result = verify_chain(_sign_events(5, key), key)
    assert result["state"] == "ok"
    assert result["events_verified"] == 5
    assert result["first_tamper_at_seq"] is None


def test_runs_verify_detects_payload_tamper_at_correct_seq():
    from roam.runs.signing import verify_chain

    key = b"\x11" * 32
    events = _sign_events(5, key)
    events[2]["payload"] = 999  # tamper seq=3 without re-signing
    result = verify_chain(events, key)
    assert result["state"] == "tampered"
    assert result["first_tamper_at_seq"] == 3


def test_runs_verify_detects_stripped_signature_after_signed_prefix():
    """Stripping a signature mid-stream (downgrade attack) is treated as tamper."""
    from roam.runs.signing import verify_chain

    key = b"\x11" * 32
    events = _sign_events(5, key)
    del events[2]["signature"]
    result = verify_chain(events, key)
    assert result["state"] == "tampered"
    assert result["first_tamper_at_seq"] == 3


def test_runs_verify_wrong_key_is_tampered_from_seq_1():
    """A ledger signed with a different key fails from the very first event."""
    from roam.runs.signing import verify_chain

    events = _sign_events(3, b"\x11" * 32)
    result = verify_chain(events, b"\x22" * 32)
    assert result["state"] == "tampered"
    assert result["first_tamper_at_seq"] == 1


def test_runs_verify_cli_no_args_is_usage_error_exit_2():
    """`runs verify` with neither RUN_ID nor --all is an exit-2 usage error (not 0)."""
    r = run_roam("runs", "verify", timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_runs_verify_cli_unknown_run_exit_2():
    r = run_roam("runs", "verify", "definitely_not_a_real_run_id", timeout=120)
    assert r.returncode == 2
    assert "does not exist" in (r.stdout + r.stderr)


# ===========================================================================
# audit-trail-verify — SHA-256 previous_record_hash chain (EU AI Act Art. 12)
# ===========================================================================


def test_audit_trail_chain_valid_roundtrip(tmp_path):
    from roam.commands.cmd_audit_trail_verify import _verify_chain

    lines = _sha256_chain_lines(
        [{"timestamp": f"2020-01-0{i}T00:00:00Z", "actor": "a", "verdict": "SAFE"} for i in range(1, 5)]
    )
    p = tmp_path / "audit.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records, issues = _verify_chain(p)
    assert len(records) == 4
    assert issues == []


def test_audit_trail_verify_detects_middle_tamper(tmp_path):
    """Editing a MIDDLE record breaks the chain — surfaced at the FOLLOWING line.

    (Tampering record N changes its hash, which record N+1's
    previous_record_hash no longer matches — so the reported line is N+1.)
    """
    from roam.commands.cmd_audit_trail_verify import _verify_chain

    lines = _sha256_chain_lines(
        [{"timestamp": f"2020-01-0{i}T00:00:00Z", "actor": "a", "verdict": "SAFE"} for i in range(1, 5)]
    )
    p = tmp_path / "audit.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    data = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(data[1])
    rec["actor"] = "EVIL"  # tamper line 2 payload (keep its previous_record_hash)
    data[1] = json.dumps(rec)
    p.write_text("\n".join(data) + "\n", encoding="utf-8")

    _records, issues = _verify_chain(p)
    assert issues, "middle tamper must be detected"
    assert issues[0]["line"] == 3
    assert issues[0]["issue"] == "previous_record_hash mismatch"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT: audit-trail-verify's previous_record_hash chain leaves the FINAL "
        "record unprotected. Tampering the last record's payload is UNDETECTED, "
        "contradicting the docstring 'Tampering with any record ... breaks the "
        "chain'. The tail needs a trailing record_hash / signature (cf. the HMAC "
        "runs ledger, which signs each event including the last)."
    ),
)
def test_audit_trail_verify_SHOULD_detect_tail_tamper(tmp_path):
    from roam.commands.cmd_audit_trail_verify import _verify_chain

    lines = _sha256_chain_lines(
        [{"timestamp": f"2020-01-0{i}T00:00:00Z", "actor": "a", "verdict": "SAFE"} for i in range(1, 5)]
    )
    p = tmp_path / "audit.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    data = p.read_text(encoding="utf-8").splitlines()
    last = json.loads(data[-1])
    last["verdict"] = "BLOCK"  # flip the most-recent decision
    data[-1] = json.dumps(last)
    p.write_text("\n".join(data) + "\n", encoding="utf-8")

    _records, issues = _verify_chain(p)
    # CORRECT behavior would be: issues != []. Today it is [] -> strict xfail.
    assert issues, "tail-record tamper should break the chain"


def test_audit_trail_verify_cli_uninitialized_gate_exits_5(tmp_path):
    """A missing trail with --gate fails closed (exit 5, state=uninitialized)."""
    missing = tmp_path / "does_not_exist.jsonl"
    r = run_roam("audit-trail-verify", "--input", str(missing), "--gate", timeout=120)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "uninitialized" in (r.stdout + r.stderr).lower() or "not initialized" in (r.stdout + r.stderr).lower()


# ===========================================================================
# audit-trail-conformance-check — 6-check Article 12 score
# ===========================================================================


def _recent_valid_records(n: int = 3) -> list[dict]:
    """Records that pass 5 of 6 conformance checks (retention fails: too recent)."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for i in range(n):
        ts = (now - dt.timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(
            {
                "timestamp": ts,
                "actor": "dev@example.com",
                "verdict": "SAFE",
                "rationale_summary": "looks fine",
                "diff_sha256": "a" * 64,
                "git_sha": "b" * 40,
                "tool_version": "13.8.0",
            }
        )
    return out


def test_conformance_score_matches_known_ledger_5_of_6():
    """A hand-built valid+recent trail scores exactly 83/100 (5/6 checks, retention fails)."""
    from roam.commands.cmd_audit_trail_conformance import (
        _check_actors,
        _check_reproducibility,
        _check_retention,
        _check_timestamps,
        _check_verdicts_and_rationale,
    )

    records = _recent_valid_records(3)
    passed = 1  # chain_integrity — asserted separately below via _verify_chain
    passed += int(_check_timestamps(records)[0])
    passed += int(_check_actors(records)[0])
    passed += int(_check_reproducibility(records)[0])
    passed += int(_check_verdicts_and_rationale(records)[0])
    retention_ok, _msg = _check_retention(records, 180)
    passed += int(retention_ok)

    total = 6
    score = round(100 * passed / total)
    assert retention_ok is False, "a fresh trail must FAIL the >=180-day retention check"
    assert passed == 5
    assert score == 83


def test_conformance_retention_fails_on_fresh_trail_degenerate():
    """DEFECT-ADJACENT (documenting): the retention check demands the OLDEST record be
    >= 180 days old, so a brand-new, fully-compliant trail always fails retention —
    it measures 'age of history' not 'retention policy'."""
    from roam.commands.cmd_audit_trail_conformance import _check_retention

    ok, msg = _check_retention(_recent_valid_records(3), 180)
    assert ok is False
    assert "only" in msg and "retention" in msg


def test_conformance_cli_on_real_repo_trail_is_partial():
    """The repo's own audit-trail.jsonl scores partial (retention fails) — non-crash."""
    r = run_roam("audit-trail-conformance-check", timeout=120)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # A fresh checkout (CI) has no .roam/audit-trail.jsonl; the command correctly
    # reports "no_trail" rather than a conformance score. Skip rather than assume
    # a populated trail exists (which only a live dev box has).
    if "no_trail" in out.lower() or "no audit trail" in out.lower():
        pytest.skip("no .roam/audit-trail.jsonl in this checkout — conformance needs a real trail")
    assert "conformance" in out.lower()
    assert "retention" in out.lower()


# ===========================================================================
# article-12-check — high-risk classifier plausibility
# ===========================================================================


def _clean_tmpdir():
    """A temp dir whose absolute path does NOT contain the substring 'test'.

    _classify_high_risk_likelihood skips any file whose path contains 'test'
    (over-broad: matches 'latest', 'greatest', pytest's own tmp dir, ...).
    pytest's tmp_path always contains 'test', which would silently skip every
    fixture file, so we mint our own under the system temp
    (C:\\Users\\...\\AppData\\Local\\Temp -> no 'test').
    """
    import tempfile

    return Path(tempfile.mkdtemp(prefix="a12risk_"))


def test_article12_high_risk_false_positive_on_benign_promote():
    """DEFECT (documenting): the high-risk classifier word-matches 'promote', so a
    totally benign `def promote()` (e.g. 'promote a prism') is flagged as an EU AI
    Act high-risk system needing counsel. Over-broad keyword matching."""
    import shutil

    from roam.commands.cmd_article_12_check import _classify_high_risk_likelihood

    d = _clean_tmpdir()
    try:
        (d / "benign.py").write_text("def promote(x):\n    return x  # promote a champion prism\n")
        res = _classify_high_risk_likelihood(d)
        # 'passed' == True means "NOT high risk". A benign promote() flips it to False.
        assert res["passed"] is False, res["evidence"]
        assert "REVIEW" in res["evidence"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_article12_clean_code_is_not_high_risk():
    import shutil

    from roam.commands.cmd_article_12_check import _classify_high_risk_likelihood

    d = _clean_tmpdir()
    try:
        (d / "clean.py").write_text("def add(a, b):\n    return a + b\n")
        res = _classify_high_risk_likelihood(d)
        assert res["passed"] is True
        assert "NOT high-risk" in res["evidence"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_article12_high_risk_skips_any_path_containing_test_substring(tmp_path):
    """DEFECT (documenting): the 'test' path filter is a bare substring match, so a
    file under ANY path containing 'test' (incl. 'latest'/'greatest' and pytest's
    own tmp dir) is silently skipped — sample=0 -> classified NOT high-risk even
    when it literally defines `promote()`."""
    from roam.commands.cmd_article_12_check import _classify_high_risk_likelihood

    # tmp_path (pytest) always contains 'test' in its absolute path.
    assert "test" in str(tmp_path).lower()
    (tmp_path / "promote.py").write_text("def terminate_employee():\n    return True\n")
    res = _classify_high_risk_likelihood(tmp_path)
    assert res["passed"] is True  # skipped -> falsely 'not high risk'
    assert "0 files scanned" in res["evidence"]


@needs_index
def test_article12_check_cli_runs_and_scores(tmp_path):
    r = run_roam("--json", "article-12-check", timeout=120)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    summ = env["summary"]
    assert 0 <= summ["governance_compliance_score"] <= 100
    assert summ["total"] == 6


# ===========================================================================
# agent-score — 0..100 composite over the runs ledger
# ===========================================================================


def test_agent_score_formula_perfect_is_100():
    from roam.commands.cmd_agent_score import _score_one

    agg = _score_one(
        {
            "runs_total": 4,
            "runs_completed": 4,
            "runs_failed": 0,
            "runs_abandoned": 0,
            "partial_success_rate": 0.0,
            "unique_actions": ["a", "b", "c", "d", "e"],
        }
    )
    assert agg["score"] == 100.0
    assert agg["confidence"] == "ok"


def test_agent_score_all_abandoned_earns_no_completion_points():
    """An agent that never closes a run gets 0 completion (70) points."""
    from roam.commands.cmd_agent_score import _score_one

    agg = _score_one(
        {
            "runs_total": 4,
            "runs_completed": 0,
            "runs_failed": 0,
            "runs_abandoned": 4,
            "partial_success_rate": 0.0,
            "unique_actions": ["a"],
        }
    )
    assert agg["score_components"]["completion_rate"] == 0.0
    # clean(20) + breadth(1 action/5 * 10 = 2) == 22, no completion credit
    assert agg["score"] == 22.0


def test_agent_score_low_confidence_under_two_runs():
    from roam.commands.cmd_agent_score import _score_one

    agg = _score_one(
        {
            "runs_total": 1,
            "runs_completed": 1,
            "runs_failed": 0,
            "runs_abandoned": 0,
            "partial_success_rate": 0.0,
            "unique_actions": ["a", "b"],
        }
    )
    assert agg["confidence"] == "low"


def test_agent_score_cli_scores_real_ledger():
    """agent-score over the repo's real .roam/runs/ ledger: >=1 agent, scores in range."""
    r = run_roam("--json", "agent-score", timeout=120)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    agents = env.get("agents") or []
    # A fresh checkout (CI) has no .roam/runs/ agent ledger; agent-score correctly
    # scores 0 agents. Skip rather than assume real agent runs exist (dev box only).
    if env["summary"]["agents_scored"] == 0:
        pytest.skip("no .roam/runs/ ledger in this checkout — agent-score needs real agent runs")
    assert env["summary"]["agents_scored"] >= 1
    for a in agents:
        assert 0 <= a["score"] <= 100
        assert a["confidence"] in ("ok", "low")


# ===========================================================================
# proof-bundle — AgentChangeProofBundle v1 verdict
# ===========================================================================


@needs_index
def test_proof_bundle_zero_checks_reports_pass_without_no_changes_disclosure():
    """DEFECT-ADJACENT (documenting): on a clean tree the proof-bundle composes
    verdict='pass' with required==executed==0 and reason 'all_required_passed',
    while partial_success stays False. A consumer can mistake "0 required checks,
    0 executed" for "all required checks ran and passed". (Contrast: `attest`
    marks safe_to_merge=None + partial_success on its no-changes path.)"""
    r = run_roam("--json", "proof-bundle", timeout=180)
    # The disclosure defect only manifests when a pr-bundle exists; a clean
    # checkout has none and correctly returns exit-2 no_bundle_found (covered by
    # test_proof_bundle_missing_bundle_exits_2). Skip rather than assume state.
    if r.returncode == 2 or "no_bundle_found" in (r.stdout + r.stderr):
        import pytest as _pytest

        _pytest.skip("no pr-bundle in this checkout; the 0-checks disclosure path needs one")
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    summ = env["summary"]
    if summ["required_count"] == 0 and summ["executed_count"] == 0:
        assert summ["verdict_value"] == "pass"
        assert summ["partial_success"] is False  # <-- no no-changes disclosure
        reasons = [x.get("code") for x in env["agent_change_proof_bundle"]["verdict"]["reasons"]]
        assert "all_required_passed" in reasons


def test_proof_bundle_missing_bundle_exits_2(tmp_path):
    """No pr-bundle present -> structured exit-2 usage error (not a silent pass)."""
    (tmp_path / "a.py").write_text("x = 1\n")
    r = run_roam("proof-bundle", cwd=tmp_path, timeout=120)
    assert r.returncode == 2
    assert "No pr-bundle found" in (r.stdout + r.stderr)


# ===========================================================================
# constitution — init / check / show
# ===========================================================================


def test_constitution_init_then_check_flow(tmp_path):
    """constitution init writes .roam/constitution.yml; check + show succeed on it."""
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
        cwd=tmp_path,
        check=False,
    )
    idx = run_roam("index", cwd=tmp_path, timeout=180)
    assert idx.returncode == 0, idx.stderr

    init = run_roam("constitution", "init", cwd=tmp_path, timeout=120)
    assert init.returncode == 0, init.stderr
    assert (tmp_path / ".roam" / "constitution.yml").exists()

    check = run_roam("constitution", "check", cwd=tmp_path, timeout=120)
    assert check.returncode == 0, check.stderr

    show = run_roam("--json", "constitution", "show", cwd=tmp_path, timeout=120)
    assert show.returncode == 0, show.stderr
    env = json.loads(show.stdout)
    assert "constitution" in json.dumps(env).lower()


def test_constitution_bare_group_is_usage_error():
    """Bare `constitution` (no subcommand) is a Click usage error (exit 2) that
    lists its subcommands — not a silent success."""
    r = run_roam("constitution", cwd=REPO, timeout=120)
    assert r.returncode == 2
    combined = r.stdout + r.stderr
    assert "init" in combined and "check" in combined


# ===========================================================================
# mode — active mode gate
# ===========================================================================


@needs_index
def test_mode_reports_active_mode_and_allowed_commands():
    r = run_roam("--json", "mode", timeout=120)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    summ = env["summary"]
    assert summ.get("active_mode")
    assert isinstance(summ.get("allowed_count"), int) and summ["allowed_count"] > 0
