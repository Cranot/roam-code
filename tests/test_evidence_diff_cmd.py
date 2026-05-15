"""W225 - tests for ``roam evidence-diff``.

These tests exercise the CLI command from end to end via ``CliRunner``
against two synthetic ``ChangeEvidence`` packets written to ``tmp_path``.

Test inventory (matches the W225 deliverable list):

1. ``test_evidence_diff_identical_packets_returns_no_drift``
2. ``test_evidence_diff_detects_hash_drift``
3. ``test_evidence_diff_detects_schema_drift``
4. ``test_evidence_diff_finds_added_refs``
5. ``test_evidence_diff_finds_removed_refs``
6. ``test_evidence_diff_classifies_completeness_regression``
7. ``test_evidence_diff_classifies_completeness_improvement``
8. ``test_evidence_diff_detects_changed_verdict``
9. ``test_evidence_diff_handles_v0_packet_without_w210_fields``
10. ``test_evidence_diff_text_mode_renders``
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    """Invoke ``roam evidence-diff`` and return ``(exit_code, captured_output)``."""
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["evidence-diff", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


def _write_packet(path: Path, payload: dict) -> Path:
    """Serialise a packet to disk and return the path."""
    path.write_text(_json.dumps(payload), encoding="utf-8")
    return path


def _base_packet(**overrides) -> dict:
    """Minimal-but-realistic ChangeEvidence packet, dict-shaped.

    Mirrors the on-wire shape of ``ChangeEvidence.to_canonical_json``,
    populated with enough fields to exercise the diff cleanly.
    """
    packet: dict = {
        "evidence_id": "ev_test_1",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "def" + "0" * 37,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:claude-opus-4.7",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-14T10:00:00Z",
        "completed_at": "2026-05-14T10:05:00Z",
        "verdict": "SAFE",
        "risk_level": None,
        "context_refs": [],
        "changed_subjects": [
            {"kind": "symbol", "qualified_name": "src/foo.py::bar"},
        ],
        "findings": [],
        "policy_decisions": [],
        "tests_required": [],
        "tests_run": [],
        "approvals": [],
        "accepted_risks": [],
        "artifacts": [],
        "actor_refs": [],
        "authority_refs": [],
        "environment_refs": [],
        "redactions": [],
        "content_hash": "0" * 64,
        "signature_ref": None,
    }
    packet.update(overrides)
    return packet


# ---------------------------------------------------------------------------
# 1. Identical packets
# ---------------------------------------------------------------------------


def test_evidence_diff_identical_packets_returns_no_drift(tmp_path):
    """Two byte-identical packets diff cleanly with no drift signalled."""
    payload = _base_packet()
    old = _write_packet(tmp_path / "old.json", payload)
    new = _write_packet(tmp_path / "new.json", payload)

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    summary = env["summary"]
    assert summary["hash_drift"] is False
    assert summary["schema_drift"] is False
    assert summary["regressions"] == 0
    assert summary["improvements"] == 0
    assert summary["changed_verdicts"] == 0
    assert env["hash_drift"] is None
    assert env["schema_drift"] is None
    assert env["added_findings"] == []
    assert env["removed_findings"] == []
    assert env["regressions"] == []
    assert env["improvements"] == []
    assert "no drift" in summary["verdict"]


# ---------------------------------------------------------------------------
# 2. Hash drift
# ---------------------------------------------------------------------------


def test_evidence_diff_detects_hash_drift(tmp_path):
    """A different ``content_hash`` triggers ``hash_drift``."""
    old = _write_packet(
        tmp_path / "old.json", _base_packet(content_hash="a" * 64)
    )
    new = _write_packet(
        tmp_path / "new.json", _base_packet(content_hash="b" * 64)
    )

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["hash_drift"] is True
    assert env["hash_drift"] == {"old": "a" * 64, "new": "b" * 64}


# ---------------------------------------------------------------------------
# 3. Schema drift
# ---------------------------------------------------------------------------


def test_evidence_diff_detects_schema_drift(tmp_path):
    """A bumped ``schema_version`` lands in the schema_drift block."""
    old = _write_packet(
        tmp_path / "old.json", _base_packet(schema_version="1.0.0")
    )
    new = _write_packet(
        tmp_path / "new.json", _base_packet(schema_version="1.1.0")
    )

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["schema_drift"] is True
    assert env["schema_drift"] == {"old": "1.0.0", "new": "1.1.0"}
    assert "schema_version" in env["summary"]["verdict"].lower()


# ---------------------------------------------------------------------------
# 4. Added refs
# ---------------------------------------------------------------------------


def test_evidence_diff_finds_added_refs(tmp_path):
    """Refs present in NEW but not OLD land in ``added_refs``."""
    old = _write_packet(tmp_path / "old.json", _base_packet())
    new = _write_packet(
        tmp_path / "new.json",
        _base_packet(
            actor_refs=[
                {
                    "actor_kind": "human",
                    "actor_id": "human:alice@example.com",
                    "display_name": "Alice",
                    "trust_tier": "git_author",
                    "extra": {},
                }
            ],
            authority_refs=[
                {
                    "authority_kind": "mode",
                    "authority_id": "mode:safe_edit",
                    "granted_by": None,
                    "source": "active_mode",
                    "extra": {},
                }
            ],
        ),
    )

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["added_refs_total"] == 2
    assert len(env["added_refs"]["actor_refs"]) == 1
    assert env["added_refs"]["actor_refs"][0]["actor_id"] == (
        "human:alice@example.com"
    )
    assert len(env["added_refs"]["authority_refs"]) == 1
    assert env["added_refs"]["environment_refs"] == []


# ---------------------------------------------------------------------------
# 5. Removed refs
# ---------------------------------------------------------------------------


def test_evidence_diff_finds_removed_refs(tmp_path):
    """Refs present in OLD but not NEW land in ``removed_refs``."""
    actor = {
        "actor_kind": "agent",
        "actor_id": "agent:claude",
        "display_name": None,
        "trust_tier": "unknown",
        "extra": {},
    }
    old = _write_packet(
        tmp_path / "old.json", _base_packet(actor_refs=[actor])
    )
    new = _write_packet(tmp_path / "new.json", _base_packet())

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["removed_refs_total"] == 1
    assert len(env["removed_refs"]["actor_refs"]) == 1
    assert env["removed_refs"]["actor_refs"][0]["actor_id"] == "agent:claude"


# ---------------------------------------------------------------------------
# 6. Completeness regression
# ---------------------------------------------------------------------------


def test_evidence_diff_classifies_completeness_regression(tmp_path):
    """A Q3 transition from complete -> missing is flagged as a regression.

    OLD has populated ``context_refs`` (Q3 = complete); NEW empties it
    (Q3 = missing). That's a regression down the ladder.
    """
    context_ref = {
        "artifact_id": "ctx:abc",
        "kind": "report",
        "content_inline": "context",
        "path": None,
        "content_hash": None,
        "byte_size": 7,
        "redactions": [],
    }
    old = _write_packet(
        tmp_path / "old.json", _base_packet(context_refs=[context_ref])
    )
    new = _write_packet(tmp_path / "new.json", _base_packet(context_refs=[]))

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["regressions"] == 1
    regressions = env["regressions"]
    assert len(regressions) == 1
    assert regressions[0]["q"] == "Q3"
    assert regressions[0]["old"] == "complete"
    assert regressions[0]["new"] == "missing"
    assert "regression" in env["summary"]["verdict"].lower()


# ---------------------------------------------------------------------------
# 7. Completeness improvement
# ---------------------------------------------------------------------------


def test_evidence_diff_classifies_completeness_improvement(tmp_path):
    """A Q1 transition from partial -> complete is flagged as an improvement.

    OLD has only ``agent_id`` (Q1 = partial). NEW adds an ``actor_refs``
    entry (Q1 = complete). That's an improvement up the ladder.
    """
    old = _write_packet(
        tmp_path / "old.json", _base_packet(actor_refs=[])
    )
    new = _write_packet(
        tmp_path / "new.json",
        _base_packet(
            actor_refs=[
                {
                    "actor_kind": "agent",
                    "actor_id": "agent:claude",
                    "display_name": None,
                    "trust_tier": "unknown",
                    "extra": {},
                }
            ]
        ),
    )

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["improvements"] >= 1
    improvements = env["improvements"]
    q1 = [i for i in improvements if i["q"] == "Q1"]
    assert len(q1) == 1
    assert q1[0]["old"] == "partial"
    assert q1[0]["new"] == "complete"


# ---------------------------------------------------------------------------
# 8. Changed verdict
# ---------------------------------------------------------------------------


def test_evidence_diff_detects_changed_verdict(tmp_path):
    """A verdict change is surfaced in ``changed_verdicts``."""
    old = _write_packet(
        tmp_path / "old.json", _base_packet(verdict="SAFE", risk_level=None)
    )
    new = _write_packet(
        tmp_path / "new.json",
        _base_packet(verdict="REVIEW", risk_level="medium"),
    )

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    assert env["summary"]["changed_verdicts"] == 2
    fields = {entry["field"] for entry in env["changed_verdicts"]}
    assert fields == {"verdict", "risk_level"}


# ---------------------------------------------------------------------------
# 9. v0 packet without W210 fields
# ---------------------------------------------------------------------------


def test_evidence_diff_handles_v0_packet_without_w210_fields(tmp_path):
    """A pre-W210 packet (no evidence_stale, no W210 timestamps) loads cleanly.

    The diff must NOT crash when optional W210 fields are missing — it
    should treat them as absent and degrade gracefully.
    """
    # Strip every W210-era field plus the W182 ref lists. Simulates a
    # pre-W182 / pre-W210 packet that was stored before those waves
    # shipped.
    pre_w210: dict = {
        "evidence_id": "ev_old",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "def" + "0" * 37,
        "diff_hash": "h" * 64,
        "run_ids": ["run_old"],
        "agent_id": "agent:legacy",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-01T10:00:00Z",
        "completed_at": "2026-05-01T10:05:00Z",
        "verdict": "SAFE",
        "risk_level": None,
        "context_refs": [],
        "changed_subjects": [],
        "findings": [],
        "policy_decisions": [],
        "tests_required": [],
        "tests_run": [],
        "approvals": [],
        "accepted_risks": [],
        "artifacts": [],
        "redactions": [],
        "content_hash": "z" * 64,
        "signature_ref": None,
        # NOTE: no actor_refs, no authority_refs, no environment_refs,
        # no context_read_at / edits_started_at / edits_completed_at,
        # no evidence_stale, no stale_reasons, no roam_version, etc.
    }
    # NEW packet adds W210 fields plus an actor ref.
    new_packet = dict(pre_w210)
    new_packet.update({
        "actor_refs": [
            {
                "actor_kind": "agent",
                "actor_id": "agent:claude",
                "display_name": None,
                "trust_tier": "unknown",
                "extra": {},
            }
        ],
        "edits_started_at": "2026-05-14T10:00:00Z",
        "content_hash": "y" * 64,
    })

    old = _write_packet(tmp_path / "old.json", pre_w210)
    new = _write_packet(tmp_path / "new.json", new_packet)

    code, out = _invoke(str(old), str(new), json_mode=True)
    assert code == 0, out
    env = _json.loads(out)
    # No crash + the new actor ref is detected as added.
    assert env["summary"]["added_refs_total"] == 1
    # The new edits_started_at is surfaced as timing drift.
    timing_fields = {t["field"] for t in env["timing_drift"]}
    assert "edits_started_at" in timing_fields
    # Hash drift is also caught.
    assert env["summary"]["hash_drift"] is True


# ---------------------------------------------------------------------------
# 10. Text mode renders
# ---------------------------------------------------------------------------


def test_evidence_diff_text_mode_renders(tmp_path):
    """Without ``--json``, the command emits a human-readable verdict line.

    Exercises a packet with one regression + one improvement so both
    headers appear in the text body.
    """
    # OLD: Q3 complete (context_refs populated), Q1 missing.
    # NEW: Q3 missing (context_refs cleared), Q1 complete (actor_refs added).
    old = _write_packet(
        tmp_path / "old.json",
        _base_packet(
            context_refs=[
                {
                    "artifact_id": "ctx:1",
                    "kind": "report",
                    "content_inline": "ctx",
                    "path": None,
                    "content_hash": None,
                    "byte_size": 3,
                    "redactions": [],
                }
            ],
            agent_id=None,
            actor_refs=[],
        ),
    )
    new = _write_packet(
        tmp_path / "new.json",
        _base_packet(
            context_refs=[],
            agent_id=None,
            actor_refs=[
                {
                    "actor_kind": "agent",
                    "actor_id": "agent:claude",
                    "display_name": None,
                    "trust_tier": "unknown",
                    "extra": {},
                }
            ],
        ),
    )

    code, out = _invoke(str(old), str(new), json_mode=False)
    assert code == 0, out
    assert out.startswith("VERDICT:")
    assert "Regressions" in out
    assert "Improvements" in out
    assert "Q3" in out
    assert "Q1" in out
