"""W805-OOOOO -- 3-artifact identity-coherence pin for ``pr-bundle emit --slsa-l3``.

Hundred-and-nineteenth-in-batch W805 sweep. Extends the W805-KKKKK
"cross-artifact consistency" family from the 2-artifact case (CGA +
sibling VSA, written by ``cga emit --also-vsa``) to the 3-artifact case
(pr-bundle envelope + SLSA VSA statement + run-ledger-root statement,
written by ``pr-bundle emit --slsa-l3`` when ``ROAM_RUN_ID`` is set and
the run's HMAC chain is signed).

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Producer inventory.** ``roam pr-bundle emit --slsa-l3`` is the
   ONE invocation that yields all three artifacts in one shot:

   * **Artifact 1 -- the pr-bundle envelope.** Emitted by
     ``src/roam/commands/cmd_pr_bundle.py:pr_bundle_emit``. Stamps a
     top-level ``commit_sha`` via ``_git_commit_sha`` (W509/W521) and
     a ``bundle_meta.git`` block. Has NO ``repo_id`` field (the
     collector synthesises one from the workspace at collection time).
     Has no explicit ``git_dirty_hash``; dirty state lives on the
     ``git`` block as ``is_dirty`` / ``status_porcelain_hash``.
   * **Artifact 2 -- the SLSA VSA statement.** Emitted by
     ``src/roam/attest/emit_vsa.py:emit_pr_bundle_slsa_l3`` ->
     ``build_vsa_statement``. Subject:
     ``{"name": _resource_uri(ev), "digest": {"sha1": commit_sha,
     "sha256": content_hash}}``. ``_resource_uri`` shape is
     ``git+<repo_id>@<commit_sha>`` (when both populated), then
     ``<repo_id>``, then ``urn:roam:evidence:<id>``.
   * **Artifact 3 -- the run-ledger-root statement.** Emitted by
     ``src/roam/attest/vsa.py:build_run_ledger_root_statement``.
     Subject: ``{"name": "urn:roam:run:<run_id>", "digest":
     {"sha256": <final_signature>}}``. Predicate carries ``run_id``,
     ``final_signature``, ``event_count``, ``signature_algorithm``,
     plus optional ``agent`` / ``started_at`` / ``ended_at`` /
     ``status`` / ``repo_id``. **No commit_sha** in the predicate.
     **No git_dirty_hash** either. The call site
     ``build_run_ledger_root_statement`` does not even propagate
     ``repo_id`` -- that field can only land via direct calls to
     :func:`build_run_ledger_root_predicate`.

2. **The five identity axes the three artifacts must agree on.**
   (Axis E was a live-probe surprise: the hypothesis was that
   bundle-envelope and VSA would agree on commit_sha; the W509 fallback
   inside emit_pr_bundle_slsa_l3 means VSA gets the real sha, but the
   envelope's top-level ``commit_sha`` field stays ``None`` on
   hand-crafted bundles. Per W978: re-run before declaring a fix.)

   * **Axis A -- commit_sha on run-ledger-root.** Bundle envelope:
     top-level ``commit_sha``. VSA: ``subject[0].digest.sha1``.
     Run-ledger-root: NOT PRESENT. A verifier handed only the
     run-ledger-root attestation cannot tell which commit it covers.
   * **Axis B -- subject/repo identity.** Bundle envelope: implicit
     (workspace path). VSA: ``subject[0].name`` = ``_resource_uri(ev)``
     (typically ``urn:roam:evidence:<id>`` when no remote URL is
     resolved). Run-ledger-root: ``subject[0].name`` =
     ``urn:roam:run:<run_id>`` -- a structurally different identifier
     that names the RUN, not the change.
   * **Axis C -- repo_id propagation.** Bundle envelope: not stamped.
     VSA: derived from ``ChangeEvidence.repo_id`` via the collector.
     Run-ledger-root: the predicate FIELD exists but
     ``build_run_ledger_root_statement`` does NOT pass ``repo_id``
     to ``build_run_ledger_root_predicate`` -- so the field is
     never populated even when a repo identity is known to the emit
     path. (Pattern-2 silent fallback: the artifact emits as if no
     repo identity exists; a downstream verifier cannot bind the
     run to a repository.)
   * **Axis D -- dirty-tree disclosure.** Bundle envelope: stamped
     on ``bundle_meta.git`` via the producer. VSA: NOT PRESENT
     (already pinned as the W805-KKKKK axis B asymmetric-disclosure
     bug on the CGA<->VSA sibling pair; same shape here). Run-ledger
     root: NOT PRESENT either. The dirty-tree disclosure exists on
     ONE of the three artifacts -- the other two emit as if the
     tree were clean.

   * **Axis E (live-probe surprise) -- envelope commit_sha drops to
     ``None`` while VSA carries the real sha.** On a hand-crafted
     bundle (no ``commit_sha`` persisted at init), the emit path's
     ``_build_envelope`` returns ``commit_sha=None`` even though
     ``emit_pr_bundle_slsa_l3``'s W509 fallback resolves the same
     ``git rev-parse HEAD`` immediately afterward and stamps it on
     the VSA. The two artifacts emitted from the SAME invocation
     therefore disagree on the commit axis. Pattern-2 silent
     fallback: the envelope claims commit_sha is unknown when the
     emit-path orchestrator knows otherwise.

3. **Cross-artifact sha1 coherence (positive).** When the run-ledger
   chain is signed AND the bundle envelope carries a real
   ``commit_sha``, the VSA's ``subject[0].digest.sha1`` mirrors the
   bundle's ``commit_sha``. The run-ledger-root's
   ``subject[0].digest.sha256`` is the HMAC ``final_signature``,
   which is structurally NOT a commit hash and intentionally
   different.

W978 axis-distinctness
======================

W805-OOOOO is **structurally distinct** from W805-KKKKK:

* **W805-KKKKK** (CGA<->sibling-VSA): TWO artifacts, both VSA-family
  attestations, written by ``cga emit --also-vsa``.
* **W805-OOOOO** (this): THREE artifacts of TWO different KINDS
  (envelope JSON + SLSA VSA + run-ledger root), written by
  ``pr-bundle emit --slsa-l3``. The run-ledger-root attestation has
  no analogue on the cga path and is the NEW divergence surface.

The strongest new axis is **C: repo_id propagation failure**. The
``build_run_ledger_root_predicate`` API accepts a ``repo_id``
parameter but the canonical caller ``build_run_ledger_root_statement``
never passes one -- so the field is dead-on-arrival in the live emit
path. Pre-fix, every shipped run-ledger-root attestation looks like
it covers an anonymous run with no repository binding.

Pinned via ``xfail(strict=True)`` on each drift axis; a future fix
flips xpass -> failure -> unwrap and seal.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` over ``src/roam/attest/emit_vsa.py`` +
``src/roam/attest/vsa.py`` + ``src/roam/runs/ledger.py`` +
``src/roam/commands/cmd_pr_bundle.py:_emit_slsa_l3_attestations``
yields ZERO false cycle hedges. W907 clean.

Run isolation
=============

    python -m pytest tests/test_w805_ooooo_pr_bundle_slsa_l3_three_artifact_identity_coherence.py -x -n 0

Sister parity
=============

    python -m pytest tests/test_w805_kkkkk_cga_vsa_sibling_consistency.py \\
        tests/test_attest_vsa.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_PR_BUNDLE_SPEC = importlib.util.find_spec("roam.commands.cmd_pr_bundle")
_EMIT_VSA_SPEC = importlib.util.find_spec("roam.attest.emit_vsa")
_VSA_SPEC = importlib.util.find_spec("roam.attest.vsa")
_RUNS_LEDGER_SPEC = importlib.util.find_spec("roam.runs.ledger")


def test_substrate_modules_present():
    """W978/W907 gate: pr_bundle + emit_vsa + vsa + runs.ledger import."""
    if _CMD_PR_BUNDLE_SPEC is None:
        pytest.skip("roam.commands.cmd_pr_bundle not installed")
    assert _EMIT_VSA_SPEC is not None, "roam.attest.emit_vsa missing"
    assert _VSA_SPEC is not None, "roam.attest.vsa missing"
    assert _RUNS_LEDGER_SPEC is not None, "roam.runs.ledger missing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _git(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo_with_bundle(tmp_path):
    """Initialise a tiny git repo + hand-craft a minimal pr-bundle on disk.

    Mirrors ``test_attest_vsa.py::TestPrBundleEmitSlsaL3`` setup so the
    test harness is byte-identical with the sister W451 wiring test.
    """
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "test"], tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], tmp_path).stdout.strip()
    bundle_dir = tmp_path / ".roam" / "pr-bundles"
    bundle_dir.mkdir(parents=True)
    bundle = {
        "intent": "demo intent",
        "context_cmd": "roam preflight f",
        "affected_symbols": [{"symbol": "f", "kind": "function", "file": "a.py"}],
        "tests": [{"test_id": "t1", "passed": True}],
        "verdict": "PASS",
        "risk_level": "low",
    }
    (bundle_dir / f"{branch}.json").write_text(json.dumps(bundle), encoding="utf-8")
    return tmp_path


def _emit_triple(runner, repo: Path, monkeypatch, *, run_id: str = "r_ooooo_demo"):
    """Drive ``pr-bundle emit --slsa-l3`` with a stubbed signed run-ledger
    chain so ALL THREE artifacts (envelope + VSA + run-ledger-root) land
    in one invocation.

    Returns ``(envelope_dict, vsa_dict, run_root_dict, vsa_path, run_root_path)``.
    """
    # Stub cosign so the sign path is never reached (we're not testing
    # signing here).
    from roam.attest import cga as cga_mod

    monkeypatch.setattr(cga_mod, "cosign_available", lambda: (False, ""))

    # Force the run-ledger root path to fire by stubbing read_run_meta to
    # return a fake signed RunMeta. The real ledger needs ROAM_RUN_ID +
    # an HMAC-signed events.jsonl; stubbing is the cleanest way to keep
    # the test hermetic (matches test_attest_vsa.py's
    # test_build_statement_succeeds_with_signed_chain pattern).
    class _StubMeta:
        run_id = "r_ooooo_demo"
        agent = "claude"
        started_at = "2026-05-18T00:00:00+00:00"
        ended_at = "2026-05-18T00:01:00+00:00"
        status = "completed"
        # 64-hex (32 bytes) -- HMAC-SHA256 output shape.
        final_signature = "cd" * 32
        event_count = 3

    monkeypatch.setattr(
        "roam.runs.ledger.read_run_meta",
        lambda root, run_id_arg: _StubMeta() if run_id_arg == run_id else None,
    )
    monkeypatch.setenv("ROAM_RUN_ID", run_id)
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")
    monkeypatch.chdir(repo)

    from roam.cli import cli

    result = runner.invoke(
        cli,
        ["--json", "pr-bundle", "emit", "--no-auto-collect", "--slsa-l3"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert "slsa_l3" in envelope, envelope
    slsa = envelope["slsa_l3"]
    vsa_path_str = slsa.get("vsa_path")
    run_path_str = slsa.get("run_ledger_root_path")
    assert vsa_path_str, f"VSA path missing: {slsa}"
    assert run_path_str, (
        f"run-ledger-root path missing -- the stub run-ledger meta did "
        f"not surface a signed chain to the emit path: {slsa}"
    )
    vsa_path = Path(vsa_path_str)
    run_path = Path(run_path_str)
    assert vsa_path.exists()
    assert run_path.exists()
    vsa = json.loads(vsa_path.read_text(encoding="utf-8"))
    run_root = json.loads(run_path.read_text(encoding="utf-8"))
    return envelope, vsa, run_root, vsa_path, run_path


# ---------------------------------------------------------------------------
# REAL BUG axis E (live-probe surprise) -- bundle envelope's top-level
# ``commit_sha`` is None on a hand-crafted bundle, while the SLSA VSA
# emitted in the SAME invocation carries the real commit sha (via the
# W509 git rev-parse fallback inside ``emit_pr_bundle_slsa_l3``).
# Run-ledger-root is intentionally excluded because its digest is a HMAC
# final_signature, not a commit hash.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOOOO axis E (live-probe surprise) -- intra-invocation "
        "bundle/VSA commit_sha drift: on a hand-crafted bundle (no "
        "``commit_sha`` field, no ``git`` block), ``pr-bundle emit "
        "--slsa-l3`` produces a VSA whose ``subject[0].digest.sha1`` "
        "carries the real ``git rev-parse HEAD`` sha (resolved by the "
        "W509 fallback inside ``emit_pr_bundle_slsa_l3``), but the "
        "envelope's own top-level ``commit_sha`` field remains ``None``. "
        "The two artifacts emitted from the SAME invocation disagree on "
        "the commit axis: the VSA says ``<sha>``, the envelope says "
        "``None``. A downstream consumer that reads ``commit_sha`` off "
        "the envelope sees a different value than a verifier consuming "
        "the VSA. Fix template: lift the W509 fallback resolver out of "
        "``emit_pr_bundle_slsa_l3`` into ``_build_envelope`` (or the "
        "emit-path orchestrator) so the envelope's ``commit_sha`` is "
        "resolved with the same fallback chain BEFORE the VSA reads it. "
        "Pattern-2 silent fallback: the envelope claims ``commit_sha`` "
        "is absent when the emit path knows otherwise. Family: "
        "cross-artifact consistency + Pattern-2 silent fallback."
    ),
)
class TestBundleEnvelopeAndVsaCommitShaCoherent:
    """The pr-bundle envelope's top-level ``commit_sha`` MUST equal the
    SLSA VSA's ``subject[0].digest.sha1`` -- this is the W509/W520
    commit-anchoring invariant carried into the pr-bundle path. The
    run-ledger-root attestation is NOT included in this comparison; its
    subject digest is an HMAC root, not a commit hash, by design.
    """

    def test_envelope_commit_sha_equals_vsa_sha1(self, repo_with_bundle, cli_runner, monkeypatch):
        envelope, vsa, _run, _vp, _rp = _emit_triple(cli_runner, repo_with_bundle, monkeypatch)
        env_sha = envelope.get("commit_sha")
        vsa_sha = (vsa.get("subject") or [{}])[0].get("digest", {}).get("sha1")
        assert env_sha, f"bundle envelope missing top-level commit_sha: {sorted(envelope.keys())}"
        assert vsa_sha, f"VSA subject missing sha1: {vsa.get('subject')}"
        assert env_sha == vsa_sha, (
            "W509 invariant: pr-bundle envelope.commit_sha MUST mirror VSA "
            f"subject[0].digest.sha1; env={env_sha!r} vsa={vsa_sha!r}"
        )


# ---------------------------------------------------------------------------
# POSITIVE pin -- the run-ledger-root attestation's subject DOES carry
# the final_signature digest as expected (sibling-W451 invariant check).
# Not a drift axis -- just confirms the HMAC root reaches disk.
# ---------------------------------------------------------------------------


class TestRunLedgerRootCarriesFinalSignature:
    def test_run_root_subject_digest_is_final_signature(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, run_root, _vp, _rp = _emit_triple(cli_runner, repo_with_bundle, monkeypatch)
        subject = (run_root.get("subject") or [{}])[0]
        digest = subject.get("digest", {})
        assert subject.get("name") == "urn:roam:run:r_ooooo_demo"
        assert digest.get("sha256") == "cd" * 32, (
            f"Run-ledger-root subject digest MUST be the HMAC final_signature; got {digest!r}"
        )
        # Predicate carries the same final_signature in a different field
        # (so consumers can read it without parsing subject.digest).
        assert (run_root.get("predicate") or {}).get("final_signature") == "cd" * 32


# ---------------------------------------------------------------------------
# REAL BUG axis A -- commit_sha NOT propagated to run-ledger-root.
# A verifier handed ONLY the run-ledger-root attestation cannot tell
# which commit the run covered.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOOOO axis A -- run-ledger-root attestation has NO "
        "commit_sha in either subject.digest or predicate body. "
        "src/roam/attest/vsa.py:build_run_ledger_root_predicate "
        "accepts only run_id/final_signature/event_count/agent/"
        "started_at/ended_at/status/repo_id; commit_sha is not in "
        "the parameter list. ``pr-bundle emit --slsa-l3`` emits three "
        "artifacts in one shot (envelope + VSA + run-ledger-root) -- "
        "the first two anchor the change to a commit, the third anchors "
        "the operational record to a run but loses the commit binding "
        "entirely. A consumer that ingests only the run-ledger-root "
        "attestation cannot bind it back to a code change. Fix template: "
        "extend build_run_ledger_root_predicate to accept commit_sha "
        "and thread it through build_run_ledger_root_statement from the "
        "calling emit path (the bundle envelope or the W509 git rev-parse "
        "fallback already resolved it). Family: cross-artifact consistency."
    ),
)
class TestRunLedgerRootCarriesCommitSha:
    def test_run_root_predicate_carries_commit_sha(self, repo_with_bundle, cli_runner, monkeypatch):
        envelope, _vsa, run_root, _vp, _rp = _emit_triple(cli_runner, repo_with_bundle, monkeypatch)
        env_sha = envelope.get("commit_sha")
        assert env_sha, "setup invariant: bundle envelope must carry commit_sha"
        # The run-ledger-root should carry the same commit_sha SOMEWHERE
        # a verifier can find it -- predicate body OR subject.digest.sha1.
        pred = run_root.get("predicate") or {}
        subj = (run_root.get("subject") or [{}])[0]
        digest = subj.get("digest", {})
        found = pred.get("commit_sha") or digest.get("sha1")
        assert found == env_sha, (
            "Cross-artifact: run-ledger-root MUST carry commit_sha "
            "so a verifier handed only this artifact can bind it to a "
            f"code change. env_sha={env_sha!r} run_root_sha={found!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG axis C -- repo_id parameter wired into predicate API but the
# canonical builder ``build_run_ledger_root_statement`` never passes it.
# Dead-on-arrival field; every shipped attestation lacks repo binding.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOOOO axis C -- repo_id propagation failure: "
        "src/roam/attest/vsa.py:build_run_ledger_root_predicate accepts "
        "a ``repo_id: str | None`` parameter and stamps it into the "
        "predicate when set, BUT the canonical caller "
        "build_run_ledger_root_statement at vsa.py:409-456 does NOT pass "
        "``repo_id`` (it only forwards run_id / final_signature / "
        "event_count / agent / started_at / ended_at / status from the "
        "meta). The field is therefore dead-on-arrival on the live emit "
        "path: every shipped run-ledger-root attestation looks like it "
        "covers an anonymous run with no repository binding, even when "
        "the emit context (pr-bundle) knows exactly which workspace "
        "the run executed in. Pattern-2 silent fallback: the artifact "
        "emits as if no repo identity exists. Fix template: have "
        "build_run_ledger_root_statement resolve repo_id from the "
        "ChangeEvidence packet (or the env) and forward it into "
        "build_run_ledger_root_predicate; the predicate field already "
        "exists. Family: cross-artifact consistency + Pattern-2 silent "
        "fallback."
    ),
)
class TestRunLedgerRootCarriesRepoId:
    def test_run_root_predicate_carries_repo_id(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, run_root, _vp, _rp = _emit_triple(cli_runner, repo_with_bundle, monkeypatch)
        pred = run_root.get("predicate") or {}
        repo_id = pred.get("repo_id")
        assert isinstance(repo_id, str) and repo_id, (
            "Cross-artifact: run-ledger-root predicate MUST carry a "
            "repo_id so a verifier can bind the run to a repository. "
            f"predicate={pred!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG axis D -- dirty-tree disclosure asymmetry across THREE artifacts.
# Stronger than W805-KKKKK axis B (which pinned the same shape on 2 artifacts);
# here, only ONE of three carries the signal.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOOOO axis D -- asymmetric dirty-tree disclosure across "
        "THREE artifacts: the bundle envelope's bundle_meta.git block "
        "(populated by ``pr-bundle init`` via the producer) carries the "
        "git porcelain hash, but BOTH the SLSA VSA (already pinned by "
        "W805-KKKKK axis B for the cga<->vsa sibling pair, same shape "
        "here on the pr-bundle path) AND the run-ledger-root attestation "
        "drop the dirty-tree signal entirely. A downstream verifier "
        "consuming any single one of {VSA, run-ledger-root} cannot tell "
        "whether the tree was clean or dirty at sign time -- only the "
        "envelope holds that disclosure. Fix template: thread "
        "git_dirty_hash from ChangeEvidence into BOTH build_vsa_predicate "
        "(W805-KKKKK axis B fix) AND build_run_ledger_root_predicate so "
        "every artifact carries symmetric clean-tree-state disclosure. "
        "Family: cross-artifact consistency + Pattern-2 silent fallback."
    ),
)
class TestThreeArtifactDirtyTreeDisclosureSymmetric:
    def test_dirty_signal_present_on_all_three_artifacts(self, repo_with_bundle, cli_runner, monkeypatch):
        # Dirty the tree before emit so the bundle producer (which runs
        # at emit time on a hand-crafted bundle's git block update path)
        # records a porcelain hash.
        (repo_with_bundle / "a.py").write_text("def f():\n    return 2\n# uncommitted edit\n", encoding="utf-8")
        envelope, vsa, run_root, _vp, _rp = _emit_triple(cli_runner, repo_with_bundle, monkeypatch)

        # Envelope side: bundle_meta.git carries the dirty signal.
        git_block = (envelope.get("bundle_meta") or {}).get("git") or {}
        env_dirty = (
            git_block.get("status_porcelain_hash") or git_block.get("is_dirty") or envelope.get("git_dirty_hash")
        )
        assert env_dirty, (
            "Setup invariant: bundle envelope should record some "
            f"dirty-tree signal after the uncommitted edit; got git_block={git_block!r}"
        )

        # VSA side: must carry git_dirty_hash somewhere a VSA-only verifier
        # can find it (top-level predicate, evidenceMetadata block, or
        # verifier.evidenceMetadata block).
        vsa_pred = vsa.get("predicate") or {}
        vsa_dirty = (
            vsa_pred.get("git_dirty_hash")
            or (vsa_pred.get("evidenceMetadata") or {}).get("git_dirty_hash")
            or ((vsa_pred.get("verifier") or {}).get("evidenceMetadata") or {}).get("git_dirty_hash")
        )
        assert vsa_dirty, (
            "Cross-artifact: VSA MUST carry git_dirty_hash so a "
            f"VSA-only verifier can audit clean-tree state. vsa_pred={vsa_pred!r}"
        )

        # Run-ledger-root side: same requirement.
        run_pred = run_root.get("predicate") or {}
        run_dirty = run_pred.get("git_dirty_hash") or (run_pred.get("evidenceMetadata") or {}).get("git_dirty_hash")
        assert run_dirty, (
            "Cross-artifact: run-ledger-root MUST carry git_dirty_hash "
            "so a run-ledger-only verifier can audit clean-tree state. "
            f"run_pred={run_pred!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG axis B -- subject-name structural divergence across THREE
# artifacts. Sister to W805-KKKKK axis A on the 2-artifact case; here
# the run-ledger-root introduces a THIRD shape (``urn:roam:run:<id>``)
# that cannot be string-matched against either of the other two.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOOOO axis B -- three structurally different identity "
        "shapes across the three artifacts: "
        "(1) the bundle envelope has no top-level subject name -- its "
        "identity is implicit (workspace path + commit_sha). "
        "(2) the SLSA VSA's subject.name = _resource_uri(ev) = "
        "``git+<repo_id>@<sha>`` OR ``<repo_id>`` OR "
        "``urn:roam:evidence:<id>`` per src/roam/attest/vsa.py:87-103. "
        "(3) the run-ledger-root's subject.name = ``urn:roam:run:<run_id>`` "
        "per src/roam/attest/vsa.py:447-450 -- a fundamentally different "
        "identifier that names the RUN, not the code change. "
        "A downstream verifier comparing subject.name strings to confirm "
        "'these two attestations describe the same artifact' sees a "
        "structural mismatch between VSA and run-ledger-root on the "
        "standard happy path. The run identity is intentionally "
        "different (orthogonal predicate types are intentional, per "
        "vsa.py:383-388), but a CORRELATION link is missing: neither "
        "artifact references the other's subject.name. Fix template: "
        "add an explicit ``related_attestation`` field on the run-ledger "
        "root predicate referencing the VSA subject.name (and "
        "symmetrically on the VSA predicate referencing the run-ledger "
        "subject.name) so a verifier can correlate the pair without "
        "string-matching mismatched identifiers. Family: cross-artifact "
        "consistency."
    ),
)
class TestThreeArtifactSubjectCorrelation:
    def test_vsa_and_run_root_carry_correlation_link(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, vsa, run_root, _vp, _rp = _emit_triple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_name = (vsa.get("subject") or [{}])[0].get("name", "")
        run_name = (run_root.get("subject") or [{}])[0].get("name", "")
        assert vsa_name and run_name

        # Neither artifact references the other today. The fix is a
        # ``related_attestation`` field on both predicates.
        vsa_pred = vsa.get("predicate") or {}
        run_pred = run_root.get("predicate") or {}

        vsa_refers_run = vsa_pred.get("related_attestation") == run_name or run_name in str(
            vsa_pred.get("inputAttestations") or []
        )
        run_refers_vsa = run_pred.get("related_attestation") == vsa_name

        assert vsa_refers_run and run_refers_vsa, (
            "Cross-artifact correlation: VSA + run-ledger-root MUST "
            "carry an explicit reference to each other's subject.name so "
            "a verifier can correlate the pair. "
            f"vsa.name={vsa_name!r} run.name={run_name!r} "
            f"vsa_refers_run={vsa_refers_run} run_refers_vsa={run_refers_vsa}"
        )
