"""W805-KKKKK -- cross-artifact identity-consistency pin for ``cga --also-vsa``.

Hundred-and-fifteenth-in-batch W805 sweep. Establishes a NEW
cross-artifact consistency family: when ``roam cga emit --also-vsa``
fires, TWO sibling attestations land on disk in a single invocation
(the CGA at ``<stem>.intoto.json`` and the VSA at ``<stem>.vsa.json``).
The W805-KKKKK axis is: do their identity fields stay coherent, or
does the VSA sibling silently diverge on the identity surface that
downstream verifiers will compare?

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **CGA predicate inventory** (from ``src/roam/attest/cga.py:322-375``
   ``build_cga_statement``). Subject:
   ``{"name": remote_url OR str(project_root), "digest": {"git_commit_sha1": sha}}``.
   Predicate carries ``indexed_at`` (datetime stamp at build time),
   ``git_dirty_hash`` (sha256 of ``git status --porcelain`` when dirty,
   ``None`` when clean), ``tool.name``, ``tool.version``, plus the
   merkle / edges / counts / claims content.

2. **VSA sibling inventory** (from ``src/roam/attest/vsa.py:328-355``
   ``build_vsa_statement`` + ``src/roam/attest/emit_vsa.py:275-397``
   ``emit_cga_vsa_sibling``). Subject:
   ``{"name": _resource_uri(ev), "digest": {"sha1": commit_sha,
   "sha256": content_hash}}``. Predicate carries ``timeVerified``
   (a SEPARATE ``_utc_now_iso()`` call at VSA build time),
   ``resourceUri``, ``policy.uri``, ``verifier.version.roam-code``,
   etc. NO ``git_dirty_hash``. NO ``indexed_at``.

3. **CGA <-> VSA sibling identity drift (live probe)**. Indexed a 1-symbol
   corpus, ran ``roam --json cga emit --also-vsa --allow-dirty`` with the
   working tree intentionally dirty (uncommitted edit). Read both files:

   * **`git_dirty_hash`**: CGA predicate carries the hash; VSA sibling
     has NO equivalent field. A downstream verifier handed only the VSA
     CANNOT see the tree was dirty at sign time. CGA + VSA disagree
     about clean-tree state.

   * **`indexed_at` vs `timeVerified`**: stamped from SEPARATE
     ``datetime.now(timezone.utc)`` calls -- separated by the
     ChangeEvidence collection + VSA build pipeline. At second
     granularity they happen to coincide on small repos but the
     contract permits drift. Each is its own stamp, never the
     other's mirror.

   * **`subject.name`** divergence by design:
     * CGA: ``project_root`` path string (e.g.
       ``D:/Safe/tmp/proj``) OR sanitised remote URL.
     * VSA: ``git+{repo_id}@{sha}`` URI OR ``urn:roam:evidence:<id>``
       fallback.

   These ARE the same invocation -- but a verifier that compares
   CGA.subject.name to VSA.subject.name to confirm "these two
   attestations describe the same artifact" gets a structural
   mismatch even on a healthy run.

4. **Cross-CGA <-> CGA-VSA-sibling sha1 IS coherent (positive)**. The
   commit-anchored ``sha1`` flows from CGA subject.digest.git_commit_sha1
   into the VSA via the W520 fallback at
   ``src/roam/attest/emit_vsa.py:320-340``. Confirmed: VSA's
   ``subject[0].digest.sha1`` matches CGA's
   ``subject[0].digest.git_commit_sha1`` byte-for-byte. The commit
   identity is the ONE field that crosses the boundary correctly.

W978 axis-distinctness
======================

W805-KKKKK is **structurally distinct** from sibling probes:

* **W805-PPPP** (cmd_cga predicate identity-skip) is a SINGLE-artifact
  verifier-side bug: ``verify_cga_statement`` skips subject.name
  resolution against the live repo. ONE attestation, ONE verifier.
* **W805-WWWW** (VSA verifier disconfirmed) verified that no in-roam
  VSA verifier exists -- there's no second verifier for the sibling
  to drift against in-tree. External (slsa-verifier / cosign) consumers
  receive the bytes-as-emitted.
* **W805-GGGGG** (cmd_attest sign-surface producer-coverage) is the
  SIGN-LAYER content_hash gap on the legacy cmd_attest path.
  Single-artifact, content-hash-shape gap.
* **W805-KKKKK** (this) is a NEW axis: TWO sibling attestations,
  emitted from ONE invocation, MUST carry coherent identity claims
  for verifiers that compare them. The asymmetries are listed above.

This establishes the **cross-artifact consistency** family as a fresh
W805 axis. The fix shape is also distinct: the sibling VSA should
either (a) mirror CGA's ``git_dirty_hash`` so a verifier reading only
the VSA sees the same clean-or-dirty state, OR (b) embed a
``related_attestation`` link in the VSA predicate pointing at the
sibling CGA's content hash so verifiers can fetch the dirty-state
disclosure from the source attestation. Pre-fix, the VSA silently
emits as if the tree were clean, regardless of the CGA's actual
clean-tree state at sign time.

W907 verify-cycle check
=======================

grep ``-i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` over ``src/roam/attest/cga.py`` +
``src/roam/attest/emit_vsa.py`` + ``src/roam/attest/vsa.py`` +
``src/roam/commands/cmd_cga.py`` + ``src/roam/commands/cmd_pr_bundle.py``
yields ZERO false cycle hedges. Lazy imports present in the emit
modules (``from roam.attest.cga import _git_commit_sha`` inside the
W509/W520 fallback paths; ``from roam.evidence.config_hashes_producer
import gather_hash_kwargs`` inside the W1279 hash wire-up) are
genuinely conditional (flag-conditional or fallback-conditional) and
ship with explicit ``# noqa: BLE001`` / ``# pragma: no cover --
defensive`` markers documenting the intent. W907 clean.

Pinned via ``xfail(strict=True)`` so a future fix flips xpass ->
test failure -> unwrap and seal.

Run isolation
=============

    python -m pytest tests/test_w805_kkkkk_cga_vsa_sibling_consistency.py -x -n 0

Regression baseline
===================

    python -m pytest tests/test_attest.py tests/test_attest_vsa.py \\
        tests/test_emit_vsa*.py -x -n 0

Sister parity
=============

    python -m pytest tests/test_w805_pppp_cmd_cga_attestation_lineage.py \\
        tests/test_w805_ggggg_cmd_attest_signing_surface_producer_coverage.py \\
        -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_CGA_SPEC = importlib.util.find_spec("roam.commands.cmd_cga")
_ATTEST_CGA_SPEC = importlib.util.find_spec("roam.attest.cga")
_EMIT_VSA_SPEC = importlib.util.find_spec("roam.attest.emit_vsa")
_VSA_SPEC = importlib.util.find_spec("roam.attest.vsa")


def test_substrate_modules_present():
    """W978/W907 gate: cga + emit_vsa + vsa substrate import cleanly."""
    if _CMD_CGA_SPEC is None:
        pytest.skip("roam.commands.cmd_cga not installed")
    assert _ATTEST_CGA_SPEC is not None, "roam.attest.cga missing"
    assert _EMIT_VSA_SPEC is not None, "roam.attest.emit_vsa missing"
    assert _VSA_SPEC is not None, "roam.attest.vsa missing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_repo(tmp_path: Path, name: str, files: dict) -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    git_init(proj)
    return proj


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    """Small indexed corpus -- fresh index, clean git HEAD."""
    proj = _make_repo(
        tmp_path,
        "fresh_cga_kkkkk",
        {
            "app.py": ("def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n"),
        },
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


def _invoke(runner, cwd: Path, args: list[str]):
    """Invoke ``roam ...`` from inside *cwd* via the top-level group."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _parse_json(result):
    assert result.exit_code in (0, 2, 5), f"unexpected exit={result.exit_code}:\n{result.output}"
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


def _emit_pair(runner, project_root: Path, *, allow_dirty: bool = False):
    """Emit a CGA + VSA-sibling pair in one invocation; return both dicts."""
    cga_out = project_root / ".roam" / "attestations" / "w805kkkkk.intoto.json"
    cga_out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "--json",
        "cga",
        "emit",
        "--output",
        str(cga_out),
        "--also-vsa",
    ]
    if allow_dirty:
        args.append("--allow-dirty")
    r = _invoke(runner, project_root, args)
    assert r.exit_code == 0, f"cga emit --also-vsa failed: {r.output}"
    payload = json.loads(r.output)
    vsa_path = Path(payload["vsa_result"]["vsa_path"])
    assert cga_out.exists()
    assert vsa_path.exists()
    cga = json.loads(cga_out.read_text(encoding="utf-8"))
    vsa = json.loads(vsa_path.read_text(encoding="utf-8"))
    return cga, vsa, cga_out, vsa_path


# ---------------------------------------------------------------------------
# POSITIVE pins -- shared-source guarantees that DO hold today.
# These must stay green forever.
# ---------------------------------------------------------------------------


class TestCgaVsaSiblingCoherentSha1:
    """The ONE identity field that flows correctly between CGA and the
    sibling VSA: the commit sha1. CGA puts it at
    ``subject[0].digest.git_commit_sha1`` -- VSA mirrors it at
    ``subject[0].digest.sha1`` via the W520 fallback. Verifies the
    happy-path positive contract.
    """

    def test_cga_and_vsa_carry_same_commit_sha1(self, indexed_project, cli_runner):
        cga, vsa, _, _ = _emit_pair(cli_runner, indexed_project)
        cga_sha = (cga.get("subject") or [{}])[0].get("digest", {}).get("git_commit_sha1")
        vsa_sha = (vsa.get("subject") or [{}])[0].get("digest", {}).get("sha1")
        assert cga_sha, f"CGA subject missing git_commit_sha1: {cga.get('subject')}"
        assert vsa_sha, f"VSA subject missing sha1: {vsa.get('subject')}"
        assert cga_sha == vsa_sha, (
            f"W520 invariant: VSA sibling sha1 MUST mirror CGA git_commit_sha1; cga={cga_sha!r} vsa={vsa_sha!r}"
        )


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims).
# ---------------------------------------------------------------------------


class TestW805PpppInvariantsPreserved:
    """W805-PPPP (cmd_cga predicate identity-skip) sister cross-check.

    Baseline: ``cga emit --no-write --json`` produces a parseable envelope
    with the lineage fields populated. We do NOT re-assert W805-PPPP's
    cross-repo identity-skip xfail-strict claim.
    """

    def test_cga_emit_baseline_envelope(self, indexed_project, cli_runner):
        r = _invoke(cli_runner, indexed_project, ["--json", "cga", "emit", "--no-write"])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        pred = (data.get("statement") or {}).get("predicate") or {}
        # Existing lineage fields W805-PPPP regression-pins:
        assert "indexed_at" in pred
        assert "git_dirty_hash" in pred
        assert (pred.get("tool") or {}).get("name") == "roam-code"


class TestW805GggggInvariantsPreserved:
    """W805-GGGGG (cmd_attest sign-surface producer-coverage) sister
    cross-check. Baseline: ``attest --help`` resolves through the CLI;
    a deeper invocation is W805-GGGGG-owned and not re-asserted here.
    """

    def test_attest_help_resolves(self, cli_runner):
        from roam.cli import cli

        r = cli_runner.invoke(cli, ["attest", "--help"], catch_exceptions=False)
        # exit_code in (0, 2) -- 0 if help renders, 2 if the group needs
        # a subcommand. Either is fine for the sister cross-check; we
        # only need to confirm the command surface resolves.
        assert r.exit_code in (0, 2), r.output


# ---------------------------------------------------------------------------
# REAL BUG axis A -- subject.name divergence between sibling CGA + VSA.
# Pinned xfail(strict=True): a future fix that aligns the names will
# flip xpass -> failure -> unwrap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KKKKK axis A -- cross-artifact subject.name drift: "
        "src/roam/attest/cga.py:346-352 sets CGA subject.name = "
        "remote_url OR str(project_root.resolve()), while "
        "src/roam/attest/vsa.py:340-344 sets VSA subject.name = "
        "_resource_uri(change_evidence) = 'git+{repo_id}@{sha}' OR "
        "'urn:roam:evidence:<id>'. Same invocation, same workspace, but "
        "the two sibling attestations carry STRUCTURALLY DIFFERENT "
        "subject.name strings on the standard happy path (no remote URL). "
        "A downstream verifier that compares the names to confirm 'these "
        "two attestations describe the same artifact' sees a mismatch "
        "even on a healthy emit. Fix template: either align cmd_cga's "
        "subject.name to _resource_uri shape, OR add an explicit "
        "'related_attestation' field in the VSA pointing at the CGA's "
        "content hash so verifiers can correlate without name-matching. "
        "Family: cross-artifact consistency (NEW)."
    ),
)
class TestCgaVsaSiblingSubjectNameDrift:
    def test_cga_and_vsa_carry_same_subject_name(self, indexed_project, cli_runner):
        cga, vsa, _, _ = _emit_pair(cli_runner, indexed_project)
        cga_name = (cga.get("subject") or [{}])[0].get("name")
        vsa_name = (vsa.get("subject") or [{}])[0].get("name")
        assert cga_name, f"CGA subject missing name: {cga.get('subject')}"
        assert vsa_name, f"VSA subject missing name: {vsa.get('subject')}"
        assert cga_name == vsa_name, (
            "CGA + VSA sibling subject.name MUST agree for cross-artifact "
            f"verifier correlation. cga={cga_name!r} vsa={vsa_name!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG axis B -- git_dirty_hash NOT mirrored from CGA to VSA sibling.
# This is the load-bearing asymmetric-disclosure axis: a verifier handed
# ONLY the VSA cannot tell the tree was dirty at sign time.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KKKKK axis B -- asymmetric dirty-tree disclosure: "
        "src/roam/attest/cga.py:269 stamps 'git_dirty_hash' on the CGA "
        "predicate (sha256 of 'git status --porcelain' when dirty, None "
        "when clean). The W472 sibling VSA path at "
        "src/roam/attest/emit_vsa.py:275-397 (emit_cga_vsa_sibling) and "
        "the VSA builder at src/roam/attest/vsa.py:274-355 "
        "(build_vsa_predicate) do NOT propagate that hash. A downstream "
        "verifier handed only the VSA file (the standard SLSA workflow "
        "-- slsa-verifier consumes the VSA, not the roam-specific CGA) "
        "cannot tell whether the tree was clean or dirty at sign time. "
        "The CGA disclosure is LOST in the projection. Fix template: "
        "extend build_vsa_predicate to read a 'git_dirty_hash' field "
        "off ChangeEvidence (collector populates from CGA envelope) "
        "and stamp it into the VSA predicate's verifier block OR a new "
        "predicate.evidenceMetadata.git_dirty_hash field. Family: "
        "cross-artifact consistency + Pattern-2 silent fallback (the "
        "VSA looks clean-state-compliant when the sibling CGA says "
        "otherwise)."
    ),
)
class TestCgaVsaSiblingDirtyHashAsymmetricDisclosure:
    def test_vsa_mirrors_cga_git_dirty_hash_on_dirty_tree(self, indexed_project, cli_runner):
        # Dirty the tree before emit so CGA records a non-None dirty_hash.
        (indexed_project / "app.py").write_text(
            "def alpha():\n    return 1\n\ndef beta(x):\n    return x\n\n# uncommitted edit\n",
            encoding="utf-8",
        )
        cga, vsa, _, _ = _emit_pair(cli_runner, indexed_project, allow_dirty=True)

        cga_dirty = (cga.get("predicate") or {}).get("git_dirty_hash")
        assert cga_dirty is not None and isinstance(cga_dirty, str) and len(cga_dirty) == 64, (
            "Setup invariant: CGA predicate should record a sha256 dirty hash "
            f"after uncommitted edit; got {cga_dirty!r}"
        )

        # The VSA sibling MUST carry the same dirty-state disclosure
        # somewhere a downstream VSA-only verifier can find it. Today the
        # bug is that no path exists.
        vsa_pred = vsa.get("predicate") or {}
        # Accept any of: top-level git_dirty_hash, a nested evidenceMetadata
        # block, or a verifier.evidenceMetadata block. Today none of these
        # surfaces carry the hash -- xfail asserts the absence.
        found = (
            vsa_pred.get("git_dirty_hash")
            or (vsa_pred.get("evidenceMetadata") or {}).get("git_dirty_hash")
            or ((vsa_pred.get("verifier") or {}).get("evidenceMetadata") or {}).get("git_dirty_hash")
        )
        assert found == cga_dirty, (
            "VSA sibling MUST mirror CGA git_dirty_hash so VSA-only "
            "verifiers can audit clean-tree state. "
            f"cga_dirty={cga_dirty!r}, vsa_dirty={found!r}"
        )


# ---------------------------------------------------------------------------
# W978 first-hypothesis disconfirmed -- timestamp axis is NOT a current bug.
#
# Initial hypothesis was that CGA's indexed_at and VSA's timeVerified would
# drift because they're stamped from SEPARATE datetime.now() calls. Live
# probe showed they coincide at second granularity in single in-process
# invocations -- the contract PERMITS drift but the implementation does
# not currently exhibit it. Demoted from xfail-strict to a positive pin
# that documents the current shared-second behaviour. If a future refactor
# introduces a step between the two stamps that pushes them across a
# second boundary, THIS test trips and the W805-KKKKK axis C bug becomes
# real -- at which point the fix template (collect one timestamp at the
# top of cga_emit and pass it into both builders) becomes load-bearing.
# ---------------------------------------------------------------------------


class TestCgaVsaSiblingTimestampCurrentlyCoincide:
    """Positive pin: CGA indexed_at and VSA timeVerified coincide at
    second granularity in the standard ``cga emit --also-vsa`` flow.
    The two stamps come from SEPARATE wall-clock calls (cga.py:258 +
    vsa.py:84) but the pipeline between them runs in well under a
    second, so the ISO-8601 strings collide. If a future refactor
    breaks this coincidence the test trips and the cross-artifact
    timestamp axis becomes a real W805-KKKKK axis C bug.
    """

    def test_indexed_at_and_time_verified_coincide_today(self, indexed_project, cli_runner):
        cga, vsa, _, _ = _emit_pair(cli_runner, indexed_project)
        indexed_at = (cga.get("predicate") or {}).get("indexed_at")
        time_verified = (vsa.get("predicate") or {}).get("timeVerified")
        assert indexed_at, f"CGA missing indexed_at: {cga.get('predicate')}"
        assert time_verified, f"VSA missing timeVerified: {vsa.get('predicate')}"
        assert indexed_at == time_verified, (
            "Current behaviour: CGA indexed_at + VSA timeVerified collide "
            "at second granularity because the pipeline between the two "
            "stamps completes well under a second. If this trips, the "
            "W805-KKKKK axis C bug (independent wall-clock stamps with no "
            "shared-source guarantee) has become real; fix by collecting "
            "one timestamp at the top of cga_emit and threading it into "
            "both builders. "
            f"cga.indexed_at={indexed_at!r} vsa.timeVerified={time_verified!r}"
        )
