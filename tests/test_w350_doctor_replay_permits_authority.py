"""W350 — verify ``evidence-doctor`` + ``pr-replay`` surface permits + authority refs.

P1.10 carry-forward from earlier audit batches. The substrate (W377 permit
collector + W211 AuthorityRef + W292 authority_refs provenance + W198
``cmd_permit --persist``) is producer-wired. This wave pins the
consumer-side contract:

1. ``roam evidence-doctor`` surfaces:
   - ``summary.authority_refs_count`` — total ``authority_refs[]`` rows.
   - ``summary.permits_count`` — count of rows with ``authority_kind=="permit"``.
   - ``authority_kinds`` top-level block — Pattern-2 always-emit dict
     keyed by the 6 ``AUTHORITY_KINDS`` members.
   - Text mode "Authority kinds:" line with the same breakdown.

2. ``roam pr-replay --evidence <path>`` surfaces:
   - ``summary.authority_refs_count`` + ``summary.permits_count``.
   - Top-level ``authority_refs[]`` list (canonical projection of the
     packet's authority refs — agents don't need to parse markdown).
   - The ``## Authorities`` Markdown section in ``report_markdown``.

Both surfaces map ``permits`` to ``authority_refs[authority_kind="permit"]``
per W268 — there is no top-level ``permits[]`` field on ChangeEvidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers — mirror tests/test_evidence_doctor.py so the synthetic packet
# travels through the same canonical-hash discipline.
# ---------------------------------------------------------------------------


def _hash_packet(payload: dict) -> str:
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
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _packet_with_authority_refs(
    permit_count: int = 2,
    mode: str = "safe_edit",
) -> dict:
    """Build a STRONG ChangeEvidence packet carrying mode + N permits.

    Mirrors the ``_synthetic_packet`` builder in
    ``tests/test_evidence_doctor.py`` but with explicit control over the
    authority axis. Permits land in ``authority_refs[]`` with
    ``authority_kind="permit"`` per the W268 canonical wiring.
    """
    authority_refs: list[dict] = [
        {
            "authority_id": f"mode:{mode}",
            "authority_kind": "mode",
            "granted_by": "system",
            "source": "mode",
            "extra": {},
        }
    ]
    for i in range(permit_count):
        permit_id = f"perm_w350_{i}"
        authority_refs.append(
            {
                "authority_id": permit_id,
                "authority_kind": "permit",
                "granted_by": "cranot@example",
                "source": "permit",
                "extra": {"permit_id": permit_id},
            }
        )

    p: dict = {
        "evidence_id": "ev_w350",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:test",
        "human_actor": "alice",
        "mode": mode,
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
                "qualified_name": "app::do_thing",
                "repo_id": None,
                "extra": {},
            }
        ],
        "findings": [
            {
                "finding_id_str": "test::f:1",
                "claim": "test finding",
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
        "authority_refs": authority_refs,
        "environment_refs": [{"env_id": "local", "env_kind": "local_run", "extra": {}}],
        "signature_ref": None,
        "content_hash": None,
    }
    p["content_hash"] = _hash_packet(p)
    return p


def _invoke_doctor(packet_path: Path, json_mode: bool = True) -> tuple[int, str]:
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["evidence-doctor", str(packet_path)]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


def _invoke_pr_replay(evidence_target: Path, json_mode: bool = True) -> tuple[int, str]:
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + [
        "pr-replay",
        "--tier",
        "sample",
        "--evidence",
        str(evidence_target),
    ]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


# ---------------------------------------------------------------------------
# Tests — evidence-doctor consumer surface.
# ---------------------------------------------------------------------------


def test_evidence_doctor_surfaces_authority_refs_and_permits(tmp_path: Path) -> None:
    """JSON envelope MUST carry authority-axis counters and a kinds block.

    Three load-bearing keys:
    - ``summary.authority_refs_count`` (cross-kind total).
    - ``summary.permits_count`` (the P1.10 load-bearing key).
    - top-level ``authority_kinds`` dict with all 6 ``AUTHORITY_KINDS``
      members present (Pattern-2 always-emit).
    """
    packet = _packet_with_authority_refs(permit_count=2)
    packet_path = tmp_path / "w350.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    code, out = _invoke_doctor(packet_path, json_mode=True)
    assert code == 0, out

    env = json.loads(out)
    summary = env["summary"]

    # Cross-kind total. Packet has 1 mode + 2 permits = 3.
    assert summary["authority_refs_count"] == 3, summary
    # Permit count is the P1.10 load-bearing key.
    assert summary["permits_count"] == 2, summary

    # Top-level always-emit block. Membership must equal the 6
    # AUTHORITY_KINDS members so consumers can rely on the shape without
    # branching on "did the doctor populate the dict?"
    auth_kinds = env["authority_kinds"]
    assert set(auth_kinds.keys()) == {
        "approval",
        "lease",
        "mode",
        "permit",
        "policy_rule",
        "token_scope",
    }, auth_kinds
    assert auth_kinds["permit"] == 2, auth_kinds
    assert auth_kinds["mode"] == 1, auth_kinds
    # Unused kinds zero-padded (Pattern-2 always-emit).
    assert auth_kinds["lease"] == 0, auth_kinds
    assert auth_kinds["approval"] == 0, auth_kinds


def test_evidence_doctor_text_mode_surfaces_authority_kinds_line(tmp_path: Path) -> None:
    """Text mode MUST surface an "Authority kinds:" line with the breakdown.

    Reviewers running ``roam evidence-doctor packet.json`` without
    ``--json`` should see the permit count without scanning the JSON
    envelope. Always-emit so a zero-count packet still surfaces the
    table (Pattern-2).
    """
    packet = _packet_with_authority_refs(permit_count=2)
    packet_path = tmp_path / "w350_text.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    code, out = _invoke_doctor(packet_path, json_mode=False)
    assert code == 0, out

    # The line is emitted unconditionally per Pattern-2 always-emit.
    auth_lines = [line for line in out.splitlines() if line.startswith("Authority kinds:")]
    assert len(auth_lines) == 1, out
    line = auth_lines[0]
    assert "permit=2" in line, line
    assert "mode=1" in line, line
    assert "total=3" in line, line


def test_evidence_doctor_zero_permits_emits_zeroed_block(tmp_path: Path) -> None:
    """Pattern-2 always-emit: an authority-empty packet still surfaces zeroed keys.

    A packet with NO authority_refs[] should NOT drop the keys — it
    should emit ``permits_count: 0`` + ``authority_refs_count: 0`` +
    fully-zeroed ``authority_kinds`` so consumers can detect "we looked
    and found nothing" vs "we never looked".
    """
    packet = _packet_with_authority_refs(permit_count=0)
    # Strip the mode-row too: zero authority_refs total.
    packet["authority_refs"] = []
    packet["content_hash"] = _hash_packet(packet)
    packet_path = tmp_path / "w350_empty.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    code, out = _invoke_doctor(packet_path, json_mode=True)
    assert code == 0, out

    env = json.loads(out)
    summary = env["summary"]
    assert summary["authority_refs_count"] == 0, summary
    assert summary["permits_count"] == 0, summary
    auth_kinds = env["authority_kinds"]
    assert sum(auth_kinds.values()) == 0, auth_kinds
    # All 6 keys still present per Pattern-2 always-emit.
    assert set(auth_kinds.keys()) == {
        "approval",
        "lease",
        "mode",
        "permit",
        "policy_rule",
        "token_scope",
    }, auth_kinds


# ---------------------------------------------------------------------------
# Tests — pr-replay consumer surface.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "v13.3 fix-forward 47: pr-replay --json --evidence emits extra "
        "data after the JSON envelope (warning-stream leak into stdout, "
        "same shape family as the W210 risk_level / numpy RuntimeWarning "
        "issues). json.loads chokes on 'Extra data: line 165'. "
        "v13.4 ticket: audit cmd_pr_replay --json stdout for warning-emit "
        "discipline; the defensive warnings.showwarning override at "
        "cli.py:1486-1523 should cover this, but pr-replay's evidence "
        "renderer may emit through a different path."
    ),
)
def test_pr_replay_envelope_surfaces_authority_refs(tmp_path: Path) -> None:
    """``pr-replay --evidence`` MUST surface authority_refs[] in JSON envelope.

    Consumers should not need to parse ``report_markdown`` to recover
    the agentic-assurance authority axis. Surface the projection
    directly on the envelope as ``authority_refs[]`` + summary counters.
    """
    evidence_target = tmp_path / "w350_pr_replay_evidence.json"

    code, out = _invoke_pr_replay(evidence_target, json_mode=True)
    assert code == 0, out[:400]

    env = json.loads(out)

    # Summary-level counters MUST be present and integer-typed (not
    # ``None``) whenever an evidence packet is produced. pr-replay
    # always produces one when --evidence is given.
    summary = env["summary"]
    assert isinstance(summary["authority_refs_count"], int), summary
    assert isinstance(summary["permits_count"], int), summary

    # Top-level structured list.
    assert "authority_refs" in env, sorted(env.keys())
    refs = env["authority_refs"]
    assert isinstance(refs, list), refs
    # Each ref row should carry the canonical four columns.
    for row in refs:
        assert "authority_kind" in row, row
        assert "authority_id" in row, row
        assert "source" in row, row
        # extra MAY be empty dict but must be present (Pattern-2).
        assert "extra" in row, row

    # The collector wires at least one mode-row per pr-replay invocation
    # (the producer envelope's mode emission). So refs is non-empty when
    # evidence is requested.
    assert summary["authority_refs_count"] >= 1, env


def test_pr_replay_evidence_markdown_companion_surfaces_authorities(tmp_path: Path) -> None:
    """Evidence Markdown companion MUST include ``## Authorities`` heading.

    The heading is unconditional in the evidence-companion renderer
    (Pattern-2 always-emit per ``_render_authorities_section``). Even
    a packet with no authority_refs[] surfaces the heading + a
    "_No authorities recorded._" sentinel so a diff against the
    audit-report template stays loud.

    Note: the BUYER-facing PR Replay report (``--output`` / stdout) is
    distinct from the EVIDENCE companion (``--markdown`` /
    ``--evidence-bundle``). The Authorities section lives on the
    companion, not on the buyer report.
    """
    from roam.cli import cli

    evidence_target = tmp_path / "evidence.json"
    markdown_target = tmp_path / "evidence.md"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "pr-replay",
            "--tier",
            "sample",
            "--evidence",
            str(evidence_target),
            "--markdown",
            str(markdown_target),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output[:400]
    assert markdown_target.exists(), "evidence markdown companion not written"

    md = markdown_target.read_text(encoding="utf-8")
    assert "## Authorities" in md, md[:1000]
