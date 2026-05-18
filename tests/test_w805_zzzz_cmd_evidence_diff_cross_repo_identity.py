"""W805-ZZZZ -- verifier-side cross-repo identity-skip pin for ``cmd_evidence_diff``.

Hundred-and-fourth-in-batch W805 sweep, ``cmd_evidence_diff.py``. THIRD member
of the *verifier-side* identity-skip slice of the lineage-disclosure family,
alongside W805-PPPP (cmd_cga verify subject.name skip) and W805-UUUU
(cmd_audit_trail_verify actor/repo/git_sha skip). The wider lineage-disclosure
family is now 8-STRONG:

- Producer-side gap:
    - W805-BBBB cmd_simulate    (counterfactual TARGET-side resolution)
    - W805-DDDD cmd_orchestrate (partition output vacuous)
    - W805-GGGG cmd_capsule     (snapshot freshness disclosure)
    - W805-IIII cmd_fingerprint (cross-repo fingerprint compare lineage)
    - W805-LLLL cmd_runs        (replay artefact-resolution lineage)
- Verifier-side identity-skip:
    - W805-PPPP cmd_cga                  (predicate.subject[0].name never checked)
    - W805-UUUU cmd_audit_trail_verify   (actor / repo / git_sha never cross-checked)
    - W805-ZZZZ cmd_evidence_diff (THIS file: two-packet identity never
      cross-checked between old/new repo_id + commit_sha lineage)

Hypothesis from W805-UUUU agent (verified live below): ``cmd_evidence_diff``
loads two ``ChangeEvidence`` JSON packets and emits a ladder-based delta
(regressions / improvements / drift). Both packets carry the load-bearing
identity fields ``repo_id`` + ``commit_sha`` + ``schema_version`` (see
``src/roam/evidence/change_evidence.py`` line ~74; these are top-level
``ChangeEvidence`` fields stamped at producer time), but the diff NEVER
cross-checks them. A packet from ``org-A/repo-A`` at SHA ``aaaa...``
diffed against a packet from ``org-B/repo-B`` at SHA ``bbbb...`` returns
verdict ``"content_hash changed with no completeness regressions"`` /
``partial_success: False`` / exit 0 -- i.e., a structurally meaningless
diff is silently rendered as a "valid diff" the reviewer can act on.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Module surface probe.** Read ``cmd_evidence_diff.py`` in full (648
   lines).  The diff produces: ``hash_drift``, ``schema_drift``,
   ``timing_drift``, ``stale_drift``, ref-set diffs (W182), findings /
   artifacts set-diffs by id, and 8-question completeness regressions /
   improvements.  ``schema_drift`` IS surfaced as an informational block
   when ``schema_version`` differs, BUT it never sets
   ``summary.partial_success = True`` and the verdict downgrades
   schema_version drift below regressions/changed_verdicts in
   ``_build_verdict`` (lines 280-304). More structurally: ``repo_id`` +
   ``commit_sha`` are NEVER compared between the two packets at all --
   they are not in the ``_diff_scalar_fields`` calls anywhere in the
   module.

2. **Live cross-repo probe.** Hand-built two minimal packets:
   ``pkt_a`` (``repo_id='org-A/repo-A'``, ``commit_sha='a'*40``) vs
   ``pkt_b`` (``repo_id='org-B/repo-B'``, ``commit_sha='b'*40``) --
   different repos, different commit histories, but identical
   ``schema_version`` + ``mode`` + ``verdict``. ``roam --json
   evidence-diff a.json b.json`` returns exit 0, verdict ``"content_hash
   changed with no completeness regressions"``, ``partial_success:
   False``. The envelope has zero identity-lineage keys (no
   ``repo_id_mismatch``, ``cross_repo``, ``identity_lineage``,
   ``commit_lineage``, ``old_repo_id``, ``new_repo_id`` -- nothing).
   THIS is the bug.

3. **Schema-version drift sub-probe.** Same shape as cross-repo, but
   with packets at ``schema_version='1.0.0'`` vs ``'2.5.0'``. Verdict
   becomes ``"schema_version changed between packets"`` (better!) but
   ``partial_success`` STAYS ``False`` and no ``compatibility_state`` /
   ``resolution`` field appears -- the diff still produces ladder-based
   regressions / improvements counts even though comparing across
   schema-version boundaries is structurally meaningless. Pattern-1-V-D:
   silent success on degraded resolution.

4. **Distinctness from W805-PPPP (cmd_cga).** cmd_cga verifies ONE
   artifact against the LIVE repo; the bug is the missing cross-check
   between predicate.subject.name and ``_git_remote_url(project_root)``.
   cmd_evidence_diff compares TWO PACKETS to each other, not against
   the live repo. The axis is "two-packet identity cross-check between
   each other" -- distinct from "one-artifact identity vs live repo".

5. **Distinctness from W805-UUUU (cmd_audit_trail_verify).** Audit-trail
   verify walks a SHA-256 ledger chain inside ONE on-disk audit-trail
   file; the bug is that recorded ``actor`` / ``repo`` / ``git_sha`` are
   never re-derived from the live git. cmd_evidence_diff doesn't touch
   any live state -- it's pure two-packet comparison. The axis is
   "between-packet identity" vs UUUU's "within-record-vs-live identity".
   AXIS CONFIRMED DISTINCT.

6. **Distinctness from existing test_evidence_diff_cmd.py.** That suite
   exercises hash drift, schema drift, ref diffs, completeness
   regressions / improvements, changed verdicts, v0 compat, text mode
   -- but never two packets from DIFFERENT repos. The identity-skip
   axis is orthogonal to the W225 test inventory.

7. **Reproducibility.** Any two evidence packets sourced from different
   repos, different forks, or different replay scopes can be diffed and
   the reviewer sees a "valid diff" envelope. In a CI / fleet scenario
   where evidence packets travel between repos (org-policy attestation,
   cross-fork compliance audits, control-plane batch verification),
   this is exactly the failure mode the diff exists to prevent.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` on ``src/roam/commands/cmd_evidence_diff.py`` +
``src/roam/evidence/change_evidence.py`` + ``src/roam/evidence/
completeness_compat.py`` == ONE MATCH, and it is the POSITIVE
counter-example (W880's sealed hoist comment: "``_parse_iso`` was
previously duplicated here; it now lives on" -- the duplication has
already been hoisted to ``roam.evidence.approval``). No live false-cycle
hedges. W907 clean.

Pinned via ``xfail(strict=True)`` so a future fix is detected (xpass ->
test failure -> unwrap and seal). The non-xfail tests pin today-good
behaviours (schema_drift block exists, hash_drift block exists, the W225
deliverables stay green) so the fix has to be additive.

Run isolation:
    python -m pytest tests/test_w805_zzzz_cmd_evidence_diff_cross_repo_identity.py -x -n 0

Regression baseline:
    python -m pytest tests/test_evidence_diff_cmd.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_pppp_cmd_cga_attestation_lineage.py \\
        tests/test_w805_uuuu_cmd_audit_trail_verify_identity_skip.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_DIFF_SPEC = importlib.util.find_spec("roam.commands.cmd_evidence_diff")
_CHANGE_EV_SPEC = importlib.util.find_spec("roam.evidence.change_evidence")


def test_command_and_substrate_exist():
    """W978/W907 gate: cmd_evidence_diff + change_evidence import cleanly."""
    if _CMD_DIFF_SPEC is None:
        pytest.skip("roam.commands.cmd_evidence_diff not installed")
    assert _CHANGE_EV_SPEC is not None, "roam.evidence.change_evidence missing"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_packet(**overrides) -> dict:
    """Minimal ChangeEvidence packet shape (mirrors test_evidence_diff_cmd)."""
    packet = {
        "evidence_id": "ev_test_zzzz",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:claude",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-14T10:00:00Z",
        "completed_at": "2026-05-14T10:05:00Z",
        "verdict": "SAFE",
        "risk_level": None,
        "context_refs": [],
        "changed_subjects": [{"kind": "symbol", "qualified_name": "src/foo.py::bar"}],
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
        "content_hash": "c" * 64,
    }
    packet.update(overrides)
    return packet


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _invoke(args, json_mode=True):
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["evidence-diff", *args]
    return runner.invoke(cli, cli_args, catch_exceptions=False)


def _parse_json(result):
    assert result.exit_code in (0, 2, 5), f"unexpected exit={result.exit_code}:\n{result.output}"
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# POSITIVE pins -- the W225 deliverable shape must STAY green. The fix
# must be additive: it adds identity disclosure, it does not break the
# existing hash_drift / schema_drift / completeness ladder semantics.
# ---------------------------------------------------------------------------


class TestEvidenceDiffExistingDriftSignalsPreserved:
    def test_hash_drift_block_emitted_today(self, tmp_path):
        a = _write(tmp_path / "a.json", _base_packet(content_hash="a" * 64))
        b = _write(tmp_path / "b.json", _base_packet(content_hash="b" * 64))
        r = _invoke([str(a), str(b)])
        data = _parse_json(r)
        assert data["summary"]["hash_drift"] is True
        assert data["hash_drift"] == {"old": "a" * 64, "new": "b" * 64}

    def test_schema_drift_block_emitted_today(self, tmp_path):
        a = _write(tmp_path / "a.json", _base_packet(schema_version="1.0.0"))
        b = _write(tmp_path / "b.json", _base_packet(schema_version="2.5.0"))
        r = _invoke([str(a), str(b)])
        data = _parse_json(r)
        assert data["summary"]["schema_drift"] is True
        assert data["schema_drift"] == {"old": "1.0.0", "new": "2.5.0"}

    def test_identical_packets_no_drift(self, tmp_path):
        a = _write(tmp_path / "a.json", _base_packet())
        b = _write(tmp_path / "b.json", _base_packet())
        r = _invoke([str(a), str(b)])
        data = _parse_json(r)
        assert data["summary"]["hash_drift"] is False
        assert data["summary"]["schema_drift"] is False


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805PpppInvariantsPreserved:
    """W805-PPPP (cmd_cga verify subject.name) sister cross-check.

    Baseline: ``cmd_cga`` module imports cleanly. We do NOT re-assert
    W805-PPPP's xfail-strict cross-repo subject.name pin.
    """

    def test_cga_module_imports(self):
        spec = importlib.util.find_spec("roam.commands.cmd_cga")
        if spec is None:
            pytest.skip("roam.commands.cmd_cga not installed")
        assert spec is not None


class TestW805UuuuInvariantsPreserved:
    """W805-UUUU (cmd_audit_trail_verify identity-skip) sister cross-check.

    Baseline: ``cmd_audit_trail_verify`` module imports cleanly. We do NOT
    re-assert W805-UUUU's xfail-strict actor/repo/git_sha pin.
    """

    def test_audit_trail_verify_module_imports(self):
        spec = importlib.util.find_spec("roam.commands.cmd_audit_trail_verify")
        if spec is None:
            pytest.skip("roam.commands.cmd_audit_trail_verify not installed")
        assert spec is not None


# ---------------------------------------------------------------------------
# REAL BUG -- Pattern-1-V-D + CP45/CP46 lineage-disclosure rule
# Pinned xfail(strict=True): fix will flip to xpass -> test failure -> unwrap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-ZZZZ Pattern-1-V-D bug: src/roam/commands/cmd_evidence_diff.py "
        "loads two ChangeEvidence JSON packets and emits a delta envelope, "
        "but NEVER cross-checks the load-bearing identity fields between "
        "them: repo_id, commit_sha, schema_version. _diff_scalar_fields() "
        "is invoked on ('verdict', 'risk_level') at line 393 and on the "
        "three timing timestamps at line 403-407 -- but NEVER on the "
        "packet-identity tuple. When two packets from different repos / "
        "different commit SHAs are diffed (mirrored forks, cross-fork "
        "compliance audits, fleet-scoped replay verification), the diff "
        "returns 'content_hash changed with no completeness regressions' "
        "/ partial_success=False / exit 0 with zero identity-lineage "
        "disclosure in the envelope. A reviewer cannot tell from the "
        "envelope whether the two packets even DESCRIBE the same code-"
        "change scope. The schema_drift block exists today but is purely "
        "informational -- partial_success stays False on a 1.0.0 vs "
        "2.5.0 schema version delta even though comparing across schema "
        "boundaries is structurally meaningless (Pattern-1-V-D: silent "
        "success on degraded resolution). Fix: add a _diff_identity() "
        "step that cross-checks (repo_id, commit_sha, git_range) "
        "between packets, emits an 'identity_lineage' / "
        "'cross_repo_diff' field on the envelope, sets "
        "partial_success=True + degraded verdict ('cross-repo evidence "
        "diff: packets describe different code-change scopes') when the "
        "identity tuple disagrees, and similarly sets "
        "partial_success=True + 'schema_version_incompatible' state "
        "when schema_version differs across major-version boundaries. "
        "cmd_evidence_diff is the THIRD verifier-side family member -- "
        "axis 'between-packet identity', distinct from W805-PPPP "
        "(one-artifact subject.name vs live repo) and W805-UUUU "
        "(audit-trail records vs live git). LINEAGE-DISCLOSURE FAMILY "
        "8-STRONG, verifier-side identity-skip slice 3-STRONG. See "
        "CLAUDE.md Pattern-1-V-D + 'Make fallback chains loud' "
        "(CP45/CP46) + W805-PPPP/UUUU sister pins."
    ),
)
class TestEvidenceDiffCrossRepoIdentityDisclosureBug:
    def test_cross_repo_evidence_packet_identity_check(self, tmp_path):
        """Pattern-1-V-D core probe: diff packets from two structurally
        different repos (different repo_id + different commit_sha). The
        diff must disclose the cross-repo identity mismatch -- either via
        partial_success=True OR a dedicated identity-lineage field on the
        envelope. Today: silent valid diff with partial_success=False."""
        pkt_a = _base_packet(
            evidence_id="ev_A",
            repo_id="org-A/repo-A",
            commit_sha="a" * 40,
            git_range="aaa..aaaaa",
            run_ids=["run_A"],
            content_hash="A" * 64,
        )
        pkt_b = _base_packet(
            evidence_id="ev_B",
            repo_id="org-B/repo-B",
            commit_sha="b" * 40,
            git_range="bbb..bbbbb",
            run_ids=["run_B"],
            content_hash="B" * 64,
        )
        a = _write(tmp_path / "a.json", pkt_a)
        b = _write(tmp_path / "b.json", pkt_b)
        r = _invoke([str(a), str(b)])
        data = _parse_json(r)
        summary = data.get("summary") or {}
        keys = set(data.keys()) | set(summary.keys())

        # The fix would produce ANY of these signals:
        identity_keys = {
            "repo_id_mismatch",
            "commit_lineage",
            "identity_lineage",
            "cross_repo_diff",
            "cross_repo",
            "old_repo_id",
            "new_repo_id",
            "old_commit_sha",
            "new_commit_sha",
            "repo_id_match",
            "commit_sha_match",
        }
        verdict = (summary.get("verdict") or "").lower()
        identity_signal = (
            summary.get("partial_success") is True
            or bool(identity_keys & keys)
            or "cross-repo" in verdict
            or "different repos" in verdict
            or "identity mismatch" in verdict
            or "different code-change scope" in verdict
        )
        assert identity_signal, (
            f"Pattern-1-V-D: evidence-diff silently accepts cross-repo "
            f"diff. summary={summary}, top-level keys={sorted(keys)}. "
            f"pkt_a repo_id={pkt_a['repo_id']!r} commit_sha={pkt_a['commit_sha']!r}; "
            f"pkt_b repo_id={pkt_b['repo_id']!r} commit_sha={pkt_b['commit_sha']!r}. "
            f"Diff never cross-checks the identity tuple."
        )

    def test_mismatched_schema_versions_disclosure(self, tmp_path):
        """Pattern-1-V-D sub-axis: when schema_version differs across a
        major-version boundary (1.0.0 vs 2.5.0), comparing the ladder
        is structurally meaningless. The diff must set partial_success
        OR a 'schema_version_incompatible' state. Today: schema_drift
        block exists but partial_success stays False and no
        compatibility state field appears."""
        pkt_a = _base_packet(schema_version="1.0.0", content_hash="A" * 64)
        pkt_b = _base_packet(schema_version="2.5.0", content_hash="B" * 64)
        a = _write(tmp_path / "a.json", pkt_a)
        b = _write(tmp_path / "b.json", pkt_b)
        r = _invoke([str(a), str(b)])
        data = _parse_json(r)
        summary = data.get("summary") or {}
        keys = set(data.keys()) | set(summary.keys())
        compat_keys = {
            "schema_version_incompatible",
            "schema_incompatible",
            "compatibility_state",
            "compatibility",
            "resolution",
            "resolution_state",
        }
        compat_signal = summary.get("partial_success") is True or bool(compat_keys & keys)
        assert compat_signal, (
            f"Pattern-1-V-D: schema_version 1.0.0 -> 2.5.0 (major-version "
            f"boundary) silently produces a ladder-based diff. "
            f"summary={summary}, top-level keys={sorted(keys)}. The diff "
            f"never declares compatibility resolution state."
        )

    def test_mismatched_commit_lineage_state(self, tmp_path):
        """Pattern-1-V-D sub-axis: when commit_sha differs but repo_id
        matches, the diff is plausibly meaningful (re-runs against the
        same repo at different commits) but the envelope must still
        carry a commit-lineage state field so the reviewer can tell
        from the envelope whether the two packets describe the same
        commit. Today: commit_sha is not in any _diff_scalar_fields
        call, so no commit-lineage signal appears at all."""
        pkt_a = _base_packet(
            evidence_id="ev_A",
            commit_sha="a" * 40,
            git_range="aaa..aaaaa",
            content_hash="A" * 64,
        )
        pkt_b = _base_packet(
            evidence_id="ev_B",
            commit_sha="b" * 40,
            git_range="bbb..bbbbb",
            content_hash="B" * 64,
        )
        a = _write(tmp_path / "a.json", pkt_a)
        b = _write(tmp_path / "b.json", pkt_b)
        r = _invoke([str(a), str(b)])
        data = _parse_json(r)
        summary = data.get("summary") or {}
        keys = set(data.keys()) | set(summary.keys())
        lineage_keys = {
            "commit_lineage",
            "commit_sha_drift",
            "commit_drift",
            "old_commit_sha",
            "new_commit_sha",
            "commit_sha_match",
            "identity_lineage",
        }
        lineage_signal = bool(lineage_keys & keys)
        assert lineage_signal, (
            f"Pattern-1-V-D: evidence-diff never surfaces a commit-"
            f"lineage signal even when commit_sha differs (a -> b). "
            f"summary={summary}, top-level keys={sorted(keys)}. "
            f"_diff_scalar_fields is invoked on ('verdict', 'risk_level') "
            f"and on the timing timestamps -- but never on commit_sha."
        )


# ---------------------------------------------------------------------------
# Advisory probe (passing today) -- documents the current cross-repo pass
# semantics so the fix is verifiably additive.
# ---------------------------------------------------------------------------


def test_cross_repo_diff_today_returns_no_partial_success(tmp_path):
    """Documents the today-shape that the bug pin above asserts a fix would
    flip. When the bug is fixed, this test will need updating -- the fix
    must produce partial_success=True here. For now it pins the current
    silent-acceptance failure mode.
    """
    pkt_a = _base_packet(
        repo_id="org-A/repo-A",
        commit_sha="a" * 40,
        content_hash="A" * 64,
    )
    pkt_b = _base_packet(
        repo_id="org-B/repo-B",
        commit_sha="b" * 40,
        content_hash="B" * 64,
    )
    a = _write(tmp_path / "a.json", pkt_a)
    b = _write(tmp_path / "b.json", pkt_b)
    r = _invoke([str(a), str(b)])
    if r.exit_code == 0:
        data = _parse_json(r)
        # Today: partial_success=False, hash_drift block but no identity
        # block. Pin it so a future regression away from this state is
        # visible. When the fix lands, the xfail above will flip to xpass
        # and force the unwrap.
        assert data["summary"]["partial_success"] is False
        assert data["summary"]["hash_drift"] is True
    else:
        pytest.skip(
            "evidence-diff no longer silently passes cross-repo diff -- "
            "the W805-ZZZZ bug appears to be fixed. Unwrap the xfail above."
        )
