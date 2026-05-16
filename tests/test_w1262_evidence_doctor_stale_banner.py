"""W1262 - ``cmd_evidence_doctor`` / ``cmd_evidence_diff`` surface the
W1254 staleness signal alongside their coverage banners.

W1254 added ``stale`` + ``stale_reasons`` keys to the dicts returned
by :meth:`ChangeEvidence.evidence_completeness` and
:meth:`ChangeEvidence.assurance_floor`. W1262 lands the consumer-side
wire-up for the two reviewer-facing surfaces that present the coverage
banner today:

* ``roam evidence-doctor`` - gains a top-level ``staleness`` block in
  JSON, ``summary.stale`` + ``summary.stale_reasons_count`` mirrors,
  and a ``[STALE] EVIDENCE STALE: N reason(s)`` text-banner block
  emitted IMMEDIATELY after the Q-coverage line. Skipped on fresh
  packets so the existing banner stays the load-bearing signal.
* ``roam evidence-diff`` - gains a top-level ``staleness`` block in
  JSON (old / new / drift triple), ``summary.old_stale`` +
  ``summary.new_stale`` + ``summary.stale_drift`` mirrors, and a
  ``[STALE] evidence_stale: X -> Y`` text-banner block. Stale flags
  on either side or a drift between sides also count as "drift" for
  the tail "(no drift detected)" line.

Test inventory:

1. ``test_doctor_fresh_packet_no_stale_banner`` - non-stale packet
   produces no ``[STALE]`` banner; JSON ``summary.stale == False``.
2. ``test_doctor_stale_packet_emits_banner_and_json_signal`` - stale
   packet with 2 reasons surfaces both reasons in the text banner and
   the JSON envelope's ``staleness`` block.
3. ``test_doctor_json_mode_consumer_reads_stale_signal_without_text`` -
   JSON-mode call returns ``staleness.stale = True`` + reasons even
   though no text banner is emitted (machine-consumer path).
4. ``test_diff_stale_drift_surfaces_in_banner_and_json`` - packet pair
   where new packet flips ``evidence_stale`` to True surfaces a
   ``stale_drift`` signal in JSON + a ``[STALE]`` banner in text.
"""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    """Invoke ``roam evidence-doctor`` (or evidence-diff) via Click."""
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + list(args)
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


def _hash_packet(payload: dict) -> str:
    """Recompute content_hash the way ChangeEvidence does, so the doctor
    treats the synthetic packet as hash-verified rather than hash-FAIL.
    """
    from roam.evidence.change_evidence import (
        _W182_OMIT_WHEN_EMPTY_FIELDS,
        _W210_OMIT_WHEN_DEFAULT_FIELDS,
    )

    stripped = dict(payload)
    stripped["content_hash"] = None
    for k in _W182_OMIT_WHEN_EMPTY_FIELDS:
        if stripped.get(k) == []:
            stripped.pop(k, None)
    for k, default in _W210_OMIT_WHEN_DEFAULT_FIELDS.items():
        if k in stripped and stripped[k] == default:
            stripped.pop(k, None)
    canonical = _json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_packet(
    *,
    evidence_stale: bool = False,
    stale_reasons: tuple[str, ...] = (),
    evidence_id: str = "ev_w1262",
) -> dict:
    """Synthesize a minimum-shape ChangeEvidence packet with W1254 fields."""
    payload: dict = {
        "evidence_id": evidence_id,
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:test",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-14T10:00:00Z",
        "completed_at": "2026-05-14T10:05:00Z",
        "verdict": "REVIEW",
        "risk_level": "low",
        "context_refs": [
            {
                "artifact_id": "raw_envelope:preflight",
                "kind": "raw_envelope",
                "path": ".roam/runs/test/preflight.json",
                "content_hash": "c" * 64,
                "content_inline": None,
                "extra": {},
                "redactions": [],
            }
        ],
        "changed_subjects": [
            {
                "kind": "symbol",
                "qualified_name": "app/svc::do_thing",
                "repo_id": None,
                "extra": {},
            }
        ],
        "findings": [
            {
                "finding_id_str": "test::finding:1",
                "claim": "low-severity finding",
                "severity": "low",
            }
        ],
        "policy_decisions": [{"rule_id": "test:rule", "outcome": "allowed"}],
        "tests_required": ["tests/test_foo.py::test_one"],
        "tests_run": [{"test_id": "tests/test_foo.py::test_one", "outcome": "passed"}],
        "approvals": [{"approval_id": "ap:1", "approver": "alice", "scope": "merge"}],
        "accepted_risks": [],
        "artifacts": [],
        "redactions": [],
        "actor_refs": [
            {
                "actor_id": "agent:test",
                "actor_kind": "agent",
                "display_name": "Test agent",
                "trust_tier": "verified_ci",
                "extra": {},
            }
        ],
        "authority_refs": [
            {
                "authority_id": "mode:safe_edit",
                "authority_kind": "mode",
                "granted_by": "system",
                "source": "mode",
                "extra": {},
            }
        ],
        "environment_refs": [{"env_id": "local", "env_kind": "local_run", "extra": {}}],
        "signature_ref": None,
        "evidence_stale": evidence_stale,
        "stale_reasons": list(stale_reasons),
        "content_hash": None,
    }
    payload["content_hash"] = _hash_packet(payload)
    return payload


def _write_packet(path: Path, payload: dict) -> Path:
    path.write_text(_json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# evidence-doctor tests
# ---------------------------------------------------------------------------


def test_doctor_fresh_packet_no_stale_banner(tmp_path: Path) -> None:
    """Non-stale packet produces no ``[STALE]`` banner in text mode.

    JSON envelope still always-emits the staleness block (Pattern-2),
    so the keys exist but reflect ``stale=False`` + empty reasons.
    """
    payload = _build_packet(evidence_stale=False)
    packet_path = _write_packet(tmp_path / "fresh.json", payload)

    # Text mode: no [STALE] banner present.
    rc, text_output = _invoke("evidence-doctor", str(packet_path))
    assert rc == 0, text_output
    assert "[STALE]" not in text_output
    assert "EVIDENCE STALE" not in text_output

    # JSON mode: staleness block present + reflects non-stale state.
    rc, json_output = _invoke("evidence-doctor", str(packet_path), json_mode=True)
    assert rc == 0, json_output
    envelope = _json.loads(json_output)
    assert envelope["staleness"]["stale"] is False
    assert envelope["staleness"]["stale_reasons"] == []
    assert envelope["summary"]["stale"] is False
    assert envelope["summary"]["stale_reasons_count"] == 0


def test_doctor_stale_packet_emits_banner_and_json_signal(tmp_path: Path) -> None:
    """Stale packet with 2 reasons emits ``[STALE]`` banner with both reasons.

    The text banner names the reason count; each reason is listed below
    so a reviewer scanning the output can route directly without
    re-parsing the JSON.
    """
    reasons = (
        "context_read_at >= edits_started_at",
        "preflight_older_than_edits",
    )
    payload = _build_packet(evidence_stale=True, stale_reasons=reasons)
    packet_path = _write_packet(tmp_path / "stale.json", payload)

    # Text mode: [STALE] banner + per-reason lines.
    rc, text_output = _invoke("evidence-doctor", str(packet_path))
    assert rc == 0, text_output
    assert "[STALE] EVIDENCE STALE: 2 reason(s)" in text_output
    assert "context_read_at >= edits_started_at" in text_output
    assert "preflight_older_than_edits" in text_output

    # JSON mode: staleness block carries the full pair.
    rc, json_output = _invoke("evidence-doctor", str(packet_path), json_mode=True)
    assert rc == 0, json_output
    envelope = _json.loads(json_output)
    assert envelope["staleness"]["stale"] is True
    assert envelope["staleness"]["stale_reasons"] == list(reasons)
    assert envelope["summary"]["stale"] is True
    assert envelope["summary"]["stale_reasons_count"] == 2


def test_doctor_json_mode_consumer_reads_stale_signal_without_text(
    tmp_path: Path,
) -> None:
    """JSON consumers see ``staleness.stale`` even when text banner suppressed.

    Pattern-2 always-emit contract: a machine consumer that ONLY parses
    the JSON envelope must be able to route on staleness without first
    paying for the text-mode rendering. Verifies key presence + value
    on both the summary mirror and the top-level block.
    """
    payload = _build_packet(
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits",),
    )
    packet_path = _write_packet(tmp_path / "stale.json", payload)

    rc, json_output = _invoke("evidence-doctor", str(packet_path), json_mode=True)
    assert rc == 0, json_output
    envelope = _json.loads(json_output)
    # Top-level block populated.
    assert "staleness" in envelope
    assert envelope["staleness"]["stale"] is True
    assert "preflight_older_than_edits" in envelope["staleness"]["stale_reasons"]
    # Summary mirror populated.
    assert envelope["summary"]["stale"] is True
    assert envelope["summary"]["stale_reasons_count"] == 1


# ---------------------------------------------------------------------------
# evidence-diff test (the W1262 drive-by sibling surface)
# ---------------------------------------------------------------------------


def test_diff_stale_drift_surfaces_in_banner_and_json(tmp_path: Path) -> None:
    """Packet pair where new packet introduces ``evidence_stale=True`` shows
    ``stale_drift=True`` in JSON + a ``[STALE]`` banner in text mode.

    The drift signal lets reviewers spot a re-run that USED to be
    trustworthy and now isn't (or vice versa) without re-walking the
    raw fields themselves.
    """
    old_payload = _build_packet(evidence_stale=False, evidence_id="ev_old")
    new_payload = _build_packet(
        evidence_stale=True,
        stale_reasons=("context_read_at >= edits_started_at",),
        evidence_id="ev_new",
    )
    old_path = _write_packet(tmp_path / "old.json", old_payload)
    new_path = _write_packet(tmp_path / "new.json", new_payload)

    # Text mode: [STALE] banner mentions the drift.
    rc, text_output = _invoke("evidence-diff", str(old_path), str(new_path))
    assert rc == 0, text_output
    assert "[STALE]" in text_output
    assert "evidence_stale: False -> True" in text_output
    assert "context_read_at >= edits_started_at" in text_output

    # JSON mode: staleness block triple + summary mirrors.
    rc, json_output = _invoke("evidence-diff", str(old_path), str(new_path), json_mode=True)
    assert rc == 0, json_output
    envelope = _json.loads(json_output)
    assert envelope["staleness"]["old"]["stale"] is False
    assert envelope["staleness"]["new"]["stale"] is True
    assert envelope["staleness"]["drift"] is True
    assert envelope["staleness"]["new"]["stale_reasons"] == ["context_read_at >= edits_started_at"]
    assert envelope["summary"]["old_stale"] is False
    assert envelope["summary"]["new_stale"] is True
    assert envelope["summary"]["stale_drift"] is True
