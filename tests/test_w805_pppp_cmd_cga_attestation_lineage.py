"""W805-PPPP -- attestation-axis Pattern-1-V-D lineage-disclosure pin for ``cmd_cga``.

Ninety-fourth-in-batch W805 sweep, ``cmd_cga.py``. SIXTH member of the
counterfactual / snapshot-state / lineage-disclosure family alongside:

- W805-BBBB cmd_simulate    (counterfactual TARGET-side resolution)
- W805-DDDD cmd_orchestrate (partition output vacuous)
- W805-GGGG cmd_capsule     (snapshot freshness disclosure)
- W805-IIII cmd_fingerprint (cross-repo fingerprint compare lineage)
- W805-LLLL cmd_runs        (replay artefact-resolution lineage)

Hypothesis from W805-LLLL agent (verified live below): ``cmd_cga`` PRODUCES
in-toto v1 predicates while ``cmd_runs`` CONSUMES via ledger. The axis is
"predicate-emit-time lineage" vs "replay-time lineage" -- a DISTINCT slice of
the family. cmd_cga already disclosed more lineage than siblings (``indexed_at``
+ ``git_dirty_hash`` + ``tool`` block in the predicate; ``git_commit_sha1`` in
the subject digest); the bug surfaces ONE tier higher -- the verifier path
silently accepts a CGA emitted against repo A when run from repo B if both
repos happen to be at the same commit SHA, because ``verify_cga_statement``
checks ``git_commit_sha1`` and the recomputed digests but NEVER cross-checks
the predicate's ``subject.name`` against the live ``project_root`` / remote URL.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **CGA predicate lineage probe.** Indexed a clean 1-symbol corpus, ran
   ``roam --json cga emit --no-write``. Predicate keys: ``edge_bundle_digest /
   edge_count / git_dirty_hash / indexed_at / languages / merkle_root /
   openvex_justification_set / openvex_status_set / reachability_claims /
   schema_version / symbol_count / tool``. Subject:
   ``{"name": "<project_root>", "digest": {"git_commit_sha1": "<sha>"}}``.
   **cmd_cga DOES carry lineage in the predicate** -- unlike siblings, this
   is NOT a "no lineage at all" bug. The fix-vector is more specific.

2. **Same-repo, post-modification verify.** Emitted CGA at commit A, modified
   ``app.py`` + reindexed at commit B, ran ``roam --json cga verify <att>``.
   Result: exit 5, 3 errors disclosed (``merkle_root mismatch``, ``symbol_count
   mismatch``, ``git_commit_sha1 mismatch``). **EXEMPLARY** on this axis --
   verify correctly detects index drift via the existing merkle + sha checks.

3. **Cross-repo verify, distinct content.** Verified probe3's attestation from
   probe4 (same shape but different commit history). Result: exit 5, 1 error
   (``git_commit_sha1 mismatch``). Exemplary: the SHA mismatch is the
   load-bearing check.

4. **Cross-repo verify, IDENTICAL content + reproducible commit SHA.** Built
   probeA + probeB with byte-identical app.py + pinned commit timestamp ->
   both repos at SHA ``5aa3d0cfb2ed...``. Verified probeA's attestation
   *from probeB's working directory*. Result: **exit 0,
   ``"ok": true``, verdict ``"CGA verified -- predicate matches live index
   (cosign skipped per --no-cosign)"``**. The predicate's ``subject.name``
   field embeds probeA's path (``.../w805_pppp_probeA``); the live repo is
   probeB (``.../w805_pppp_probeB``); verify NEVER cross-checks subject.name.
   THIS is the bug.

5. **Reproducibility.** This isn't just a synthetic timestamp-pinning trick.
   Any two repos with identical content at the same commit SHA (mirrored
   forks, vendored dependencies pulled at the same tag, reproducible builds)
   will all verify each other's CGAs as ``ok=true`` even though the
   ``subject.name`` records the *original* repo's identity. A signed CGA
   crossed-published in a sibling-org workflow would carry the source repo's
   ``subject.name`` while the verifier reads it under the consumer repo's
   project_root with no disclosure of the identity mismatch.

W978 axis-distinctness finding
==============================

cmd_cga is **structurally distinct** from siblings:

- Siblings (capsule, fingerprint, runs) miss ``indexed_at`` / ``git_sha``
  entirely from their envelopes -- the fix is "stamp lineage everywhere".
- cmd_cga **already stamps lineage** in the predicate. The bug is one tier
  higher: the verifier path silently skips ``subject.name`` resolution
  against the live project identity. The fix is "extend
  ``verify_cga_statement`` to cross-check predicate.subject.name against the
  live ``_git_remote_url(project_root)`` / canonical project path".

This is family member #6 with a **DIFFERENT mitigation shape** -- which is
exactly what CP45/CP46 predicts ("make fallback chains loud" applies at every
verifier boundary, not only at producer boundaries). Family 6-STRONG with
distinct axes confirmed.

W907 verify-cycle check
=======================

grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here' on ``src/roam/commands/cmd_cga.py`` + ``src/roam/attest/
cga.py`` + ``src/roam/attest/emit_vsa.py`` == NO MATCHES. The lazy imports
in cmd_cga (``from roam.attest.cga import _git_dirty_hash`` at line 226;
``from roam.attest.emit_vsa import emit_cga_vsa_sibling`` at line 444; the
``from roam.output.errors import DIRTY_TREE`` at line 230, etc.) are benign
deferred imports -- path-conditional (DIRTY_TREE only on dirty check) or
flag-conditional (``--also-vsa`` import only when the flag is set), not
cargo-cult false cycles. W907 clean.

Pinned via ``xfail(strict=True)`` so a future fix is detected
(xpass -> test failure -> unwrap and seal).

Run isolation:
    python -m pytest tests/test_w805_pppp_cmd_cga_attestation_lineage.py -x -n 0

Regression baseline:
    python -m pytest tests/test_cga.py tests/test_cga_dirty_hash_binding.py \\
        tests/test_cga_edge_digest_sort_stability.py tests/test_cga_fail_closed.py \\
        tests/test_attest.py tests/test_attest_vsa.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_iiii_cmd_fingerprint_snapshot_state.py \\
        tests/test_w805_llll_cmd_runs_replay_lineage.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_CGA_SPEC = importlib.util.find_spec("roam.commands.cmd_cga")
_ATTEST_CGA_SPEC = importlib.util.find_spec("roam.attest.cga")
_ATTEST_VSA_SPEC = importlib.util.find_spec("roam.attest.emit_vsa")


def test_command_and_substrate_exist():
    """W978/W907 gate: cmd_cga + attest substrate import cleanly."""
    if _CMD_CGA_SPEC is None:
        pytest.skip("roam.commands.cmd_cga not installed")
    assert _ATTEST_CGA_SPEC is not None, "roam.attest.cga missing"
    assert _ATTEST_VSA_SPEC is not None, "roam.attest.emit_vsa missing"


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


def _make_repo_with_pinned_commit(
    tmp_path: Path, name: str, files: dict, *, commit_date: str = "2026-01-01 00:00:00 +0000"
) -> Path:
    """Create a git repo with a deterministic commit timestamp so two repos
    with byte-identical content land at the same commit SHA. Required for
    the cross-repo identity-skip probe.
    """
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = commit_date
    env["GIT_COMMITTER_DATE"] = commit_date
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )
    return proj


@pytest.fixture
def fresh_indexed_project(tmp_path, monkeypatch):
    """Small indexed corpus -- fresh index, clean git HEAD."""
    proj = _make_repo(
        tmp_path,
        "fresh_cga_pppp",
        {
            "app.py": ("def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n"),
        },
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def emitted_attestation(fresh_indexed_project, cli_runner, tmp_path):
    """Indexed project + a CGA attestation written OUTSIDE the project tree
    (so it doesn't dirty the working tree). Returns
    ``(proj, attestation_path)``.
    """
    proj = fresh_indexed_project
    # Land the attestation outside the project so verify against a clean
    # tree stays clean. tmp_path is a pytest builtin per-test fixture
    # already isolated from proj.
    att_path = tmp_path / "att_outside_tree.intoto.json"
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        r = cli_runner.invoke(cli, ["cga", "emit", "--output", str(att_path)], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert r.exit_code == 0, f"cga emit failed: {r.output}"
    assert att_path.exists()
    return proj, att_path


@pytest.fixture
def cross_repo_pair(tmp_path, monkeypatch):
    """Two repos with byte-identical content + pinned commit date -> same SHA.

    Returns ``(repo_a, att_from_a, repo_b)``. The attestation is emitted
    from repo_a; the bug probe verifies it from repo_b's working directory.
    """
    files = {"app.py": "def alpha():\n    return 1\n"}
    repo_a = _make_repo_with_pinned_commit(tmp_path, "repoA_cga_pppp", files)
    repo_b = _make_repo_with_pinned_commit(tmp_path, "repoB_cga_pppp", files)
    # Index both repos in-process so each has a roam.db usable for verify.
    monkeypatch.chdir(repo_a)
    out_a, rc_a = index_in_process(repo_a, "--force")
    assert rc_a == 0, f"index failed at A: {out_a}"
    monkeypatch.chdir(repo_b)
    out_b, rc_b = index_in_process(repo_b, "--force")
    assert rc_b == 0, f"index failed at B: {out_b}"
    # Sanity: confirm both repos really do share a commit SHA -- if git
    # internals ever change and pinned-date no longer yields identical
    # SHA, skip rather than emit a misleading xfail.
    sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_a), capture_output=True, text=True).stdout.strip()
    sha_b = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_b), capture_output=True, text=True).stdout.strip()
    if sha_a != sha_b:
        pytest.skip(
            f"pinned-date commits diverged ({sha_a[:8]} != {sha_b[:8]}); cross-repo "
            "SHA-collision probe requires deterministic commit hash"
        )
    # Emit the attestation from repo_a.
    monkeypatch.chdir(repo_a)
    att_path = repo_a / "from_a.intoto.json"
    from roam.cli import cli

    runner = CliRunner()
    r = runner.invoke(cli, ["cga", "emit", "--output", str(att_path)], catch_exceptions=False)
    assert r.exit_code == 0, f"emit from A failed: {r.output}"
    assert att_path.exists()
    return repo_a, att_path, repo_b


# ---------------------------------------------------------------------------
# Invoke helpers
# ---------------------------------------------------------------------------


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
    # Accept 0 (clean), 2 (unknown_run / usage), and 5 (gate failure /
    # mismatch). Any other exit code is a hard failure.
    assert result.exit_code in (0, 2, 5), f"unexpected exit={result.exit_code}:\n{result.output}"
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# POSITIVE shape pins -- CGA emit MUST keep stamping today's lineage fields.
# These guarantee the existing predicate-side disclosure is regression-pinned.
# ---------------------------------------------------------------------------


class TestCgaEmitPredicateCarriesExistingLineage:
    """cmd_cga is STRUCTURALLY DIFFERENT from siblings: it already stamps
    ``indexed_at`` / ``git_dirty_hash`` / ``tool.version`` in the predicate
    and ``git_commit_sha1`` in subject.digest. These positive pins prevent
    a future refactor from regressing the producer-side disclosure that
    siblings still lack.
    """

    def test_predicate_carries_indexed_at(self, fresh_indexed_project, cli_runner):
        """CP45 lineage rule -- producer side. The predicate already
        records WHEN the CGA was emitted (this is positive disclosure)."""
        r = _invoke(cli_runner, fresh_indexed_project, ["--json", "cga", "emit", "--no-write"])
        data = _parse_json(r)
        pred = (data.get("statement") or {}).get("predicate") or {}
        assert "indexed_at" in pred, (
            f"REGRESSION: CGA predicate dropped indexed_at. Predicate keys: {sorted(pred.keys())}"
        )
        # The value must be parseable as ISO-8601 UTC.
        from datetime import datetime

        ts = pred["indexed_at"]
        # Accept "Z" suffix or "+00:00".
        ts_norm = ts.replace("Z", "+00:00")
        datetime.fromisoformat(ts_norm)  # raises if malformed

    def test_predicate_carries_git_dirty_hash_field(self, fresh_indexed_project, cli_runner):
        """CP45 lineage rule -- the predicate records whether the tree was
        clean at sign time. None on clean (positive disclosure of clean
        state); sha256 string when dirty."""
        r = _invoke(cli_runner, fresh_indexed_project, ["--json", "cga", "emit", "--no-write"])
        data = _parse_json(r)
        pred = (data.get("statement") or {}).get("predicate") or {}
        assert "git_dirty_hash" in pred, (
            f"REGRESSION: CGA predicate dropped git_dirty_hash. Keys: {sorted(pred.keys())}"
        )

    def test_subject_carries_git_commit_sha1(self, fresh_indexed_project, cli_runner):
        """CP45 lineage rule -- the in-toto subject records the commit SHA."""
        r = _invoke(cli_runner, fresh_indexed_project, ["--json", "cga", "emit", "--no-write"])
        data = _parse_json(r)
        subject_list = (data.get("statement") or {}).get("subject") or []
        assert subject_list, "REGRESSION: statement.subject is empty"
        first = subject_list[0]
        digest = (first or {}).get("digest") or {}
        assert "git_commit_sha1" in digest, (
            f"REGRESSION: subject.digest dropped git_commit_sha1. digest keys: {sorted(digest.keys())}"
        )

    def test_predicate_carries_tool_version_block(self, fresh_indexed_project, cli_runner):
        """CP45 lineage rule -- the predicate names the producing tool +
        version so a downstream verifier can reject statements produced by
        unknown / forked tools."""
        r = _invoke(cli_runner, fresh_indexed_project, ["--json", "cga", "emit", "--no-write"])
        data = _parse_json(r)
        pred = (data.get("statement") or {}).get("predicate") or {}
        tool = pred.get("tool") or {}
        assert tool.get("name") == "roam-code", f"REGRESSION: predicate.tool.name not roam-code. tool={tool}"
        assert tool.get("version"), f"REGRESSION: predicate.tool.version missing/empty. tool={tool}"


# ---------------------------------------------------------------------------
# POSITIVE pins on the EXISTING verify-side lineage checks. These must stay
# green forever -- the bug is that ONE additional check (subject.name) is
# missing, not that the existing checks are wrong.
# ---------------------------------------------------------------------------


class TestCgaVerifyExistingChecksExemplary:
    """Pin the merkle / symbol_count / git_commit_sha1 verifier checks as
    today-good. A future "fix" that breaks any of these would be a worse
    bug than the missing subject.name check.
    """

    def test_verify_detects_post_modification_drift(self, emitted_attestation, cli_runner):
        """Modify source + recommit after emit -> verify must surface
        at least 2 of the load-bearing mismatch errors (merkle_root +
        git_commit_sha1). The symbol_count / edge_count assertions vary
        with the indexer's handling of the modification and aren't load-
        bearing for the lineage axis."""
        proj, att = emitted_attestation
        (proj / "app.py").write_text(
            "def alpha():\n    return 1\n\ndef gamma():\n    return 99\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "drift"], cwd=str(proj), capture_output=True)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, out
        r = _invoke(cli_runner, proj, ["--json", "cga", "verify", str(att), "--no-cosign"])
        assert r.exit_code == 5, r.output
        data = _parse_json(r)
        assert data["summary"]["ok"] is False
        errs = data.get("errors") or []
        joined = " | ".join(errs)
        # Two load-bearing checks: the merkle (content) and the commit SHA
        # (history). Both must fire on a post-commit reindex.
        assert "merkle_root mismatch" in joined, f"errors={errs}"
        assert "git_commit_sha1 mismatch" in joined, f"errors={errs}"

    def test_verify_clean_round_trip_exemplary(self, emitted_attestation, cli_runner):
        """Same repo, same commit, no edits -> verify must succeed."""
        proj, att = emitted_attestation
        r = _invoke(cli_runner, proj, ["--json", "cga", "verify", str(att), "--no-cosign"])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        assert data["summary"]["ok"] is True


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805IiiiInvariantsPreserved:
    """W805-IIII (cmd_fingerprint snapshot-state) sister cross-check.

    Baseline: ``roam fingerprint`` emits a parseable envelope. We do NOT
    re-assert W805-IIII's xfail-strict lineage pins.
    """

    def test_fingerprint_baseline_parseable(self, fresh_indexed_project, cli_runner):
        r = _invoke(cli_runner, fresh_indexed_project, ["--json", "fingerprint"])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        assert "summary" in data
        assert "verdict" in data["summary"]


class TestW805LlllInvariantsPreserved:
    """W805-LLLL (cmd_runs replay) sister cross-check.

    Baseline: ``runs verify <bogus>`` returns ``state="unknown_run"`` + exit 2.
    We do NOT re-assert W805-LLLL's xfail-strict lineage pins.
    """

    def test_runs_verify_bogus_id_exemplary(self, fresh_indexed_project, cli_runner):
        r = _invoke(
            cli_runner,
            fresh_indexed_project,
            ["--json", "runs", "verify", "run_99999999_zzzzzz"],
        )
        assert r.exit_code == 2, f"bogus runs verify should exit 2; got {r.exit_code}\n{r.output}"
        data = _parse_json(r)
        assert (data.get("summary") or {}).get("state") == "unknown_run"


# ---------------------------------------------------------------------------
# REAL BUG -- Pattern-1-V-D + CP45/CP46 lineage-disclosure rule
# Pinned xfail(strict=True): fix will flip to xpass -> test failure -> unwrap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-PPPP Pattern-1-V-D bug: src/roam/attest/cga.py:383-458 "
        "(verify_cga_statement) checks predicate.merkle_root + edge_bundle_digest "
        "+ symbol_count + edge_count + subject.digest.git_commit_sha1 + "
        "predicate.git_dirty_hash -- but NEVER cross-checks predicate.subject.name "
        "against the live repo's _git_remote_url(project_root) / canonical "
        "project path. When two repos (mirrored forks, vendored copies at "
        "the same tag, reproducible builds) share content + commit SHA, a "
        "CGA emitted by repo A verifies cleanly under repo B with verdict "
        "'CGA verified -- predicate matches live index'. The subject.name "
        "field (set at emit time from project_root / git remote) is "
        "structurally a load-bearing identity claim -- it goes into every "
        "signed cosign bundle -- yet the verifier treats it as cosmetic. Fix: "
        "extend verify_cga_statement to compare predicate's subject[0].name "
        "to _git_remote_url(project_root) (preferring remote URL, falling "
        "back to canonical project path); emit a 'subject.name mismatch' "
        "error like the existing git_commit_sha1 mismatch when they "
        "disagree; set ok=False + exit 5. cmd_cga is STRUCTURALLY DIFFERENT "
        "from W805-IIII/LLLL siblings -- it ALREADY carries indexed_at + "
        "git_commit_sha1 in the predicate (positive disclosure); the bug "
        "is on the verifier boundary, not the emitter. See CLAUDE.md "
        "Pattern-1-V-D + 'Make fallback chains loud' (CP45/CP46) + "
        "W805-IIII/LLLL/GGGG sister pins. Family 6-STRONG confirmed."
    ),
)
class TestCgaCrossRepoIdentityDisclosureBug:
    def test_verify_flags_subject_name_mismatch_across_repos(self, cross_repo_pair, cli_runner):
        """Pattern-1-V-D core probe: verify the attestation from repo A
        in repo B's working directory. The two repos have identical content
        + identical pinned commit SHA but DIFFERENT filesystem paths (and
        would have different remote URLs in production). Verify must flag
        the subject.name mismatch -- either via an explicit error or by
        setting ``ok=False`` / exit 5."""
        repo_a, att, repo_b = cross_repo_pair
        # Read the attestation BEFORE invoking verify -- confirm the predicate's
        # subject.name actually embeds repo_a's identity (so the bug claim
        # is grounded in the live shape, not a stale fixture).
        att_payload = json.loads(att.read_text(encoding="utf-8"))
        subject_name = (att_payload.get("subject") or [{}])[0].get("name") or ""
        assert "repoA" in subject_name, (
            f"fixture assumption broken: subject.name={subject_name!r} does "
            "not embed 'repoA'; pinning the lineage-disclosure bug requires "
            "the source repo's identity to be in the predicate"
        )
        r = _invoke(cli_runner, repo_b, ["--json", "cga", "verify", str(att), "--no-cosign"])
        data = _parse_json(r)
        summary = data.get("summary") or {}
        errors = data.get("errors") or []
        joined = " | ".join(errors).lower()
        # The fix would produce ANY of these signals:
        identity_signal = (
            summary.get("ok") is False
            or "subject" in joined
            or "name mismatch" in joined
            or "repo identity" in joined
            or "identity mismatch" in joined
        )
        assert identity_signal, (
            f"Pattern-1-V-D: cmd_cga verify silently accepts repo_a's "
            f"attestation under repo_b. summary={summary}, errors={errors}. "
            f"subject.name embedded in predicate: {subject_name!r}. Live "
            f"project_root: {repo_b}. Verifier never cross-checks the two."
        )

    def test_verify_envelope_carries_repo_identity_disclosure(self, cross_repo_pair, cli_runner):
        """CP45 lineage rule: the verify envelope should disclose BOTH
        sides of the identity check -- predicate_subject_name (what the
        attestation claims) AND live_project_identity (what we're verifying
        against). Today neither field appears anywhere in the envelope,
        so an agent reading the envelope cannot tell whether repo identity
        was checked at all."""
        repo_a, att, repo_b = cross_repo_pair
        r = _invoke(cli_runner, repo_b, ["--json", "cga", "verify", str(att), "--no-cosign"])
        data = _parse_json(r)
        keys = set(data.keys()) | set((data.get("summary") or {}).keys())
        identity_keys = {
            "predicate_subject_name",
            "subject_name",
            "live_project_identity",
            "live_subject_name",
            "live_repo_identity",
            "subject_name_match",
            "repo_identity_match",
            "identity_lineage",
        }
        overlap = identity_keys & keys
        assert overlap, (
            f"CP45 lineage: cmd_cga verify envelope discloses NO predicate-"
            f"vs-live subject identity field. Looked for one of "
            f"{sorted(identity_keys)}; envelope/summary had {sorted(keys)}."
        )


# ---------------------------------------------------------------------------
# Advisory probe (passing today) -- documents the current cross-repo pass
# semantics so the fix is verifiably additive (the failure modes the fix
# preserves are the merkle / symbol_count / git_commit_sha1 mismatches).
# ---------------------------------------------------------------------------


def test_cross_repo_attestation_today_verifies_ok_when_shas_match(cross_repo_pair, cli_runner):
    """Documents the today-shape that the bug pin above asserts a fix would
    flip. When the bug is fixed, this test will need updating (or removal)
    -- the fix must produce ``ok=False`` here. For now it pins the
    current silent-acceptance failure mode, providing a positive-test
    baseline for the xfail-strict pin's complementary assertion.
    """
    repo_a, att, repo_b = cross_repo_pair
    r = _invoke(cli_runner, repo_b, ["--json", "cga", "verify", str(att), "--no-cosign"])
    # Today: ok=True, exit 0 (the bug). Pin it so a future regression
    # away from this state is visible. NOTE: the xfail above asserts this
    # SHOULD become ok=False; when the fix lands, the two tests will
    # contradict each other -- exactly the signal the unwrap process needs.
    if r.exit_code == 0:
        data = _parse_json(r)
        assert data["summary"]["ok"] is True
    else:
        # The fix has landed -- emit a clear marker. The xfail-strict test
        # above will flip to xpass and force test-failure handling.
        pytest.skip(
            "cross-repo verify no longer silently passes -- the W805-PPPP "
            "bug appears to be fixed. Unwrap the xfail above."
        )
