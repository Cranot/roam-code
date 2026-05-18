"""W805-BBBBB -- verifier-side cross-repo identity-skip pin for
``cmd_pr_bundle validate``.

Hundred-and-sixth-in-batch W805 sweep, ``cmd_pr_bundle.py`` (VALIDATE
subcommand only -- distinct from W805-TTTT which probed the EMIT
subcommand's Q1-Q8 coverage matrix). FOURTH member of the
*verifier-side* identity-skip slice of the lineage-disclosure family,
alongside W805-PPPP (cmd_cga verify subject.name skip), W805-UUUU
(cmd_audit_trail_verify actor/repo/git_sha skip), and W805-ZZZZ
(cmd_evidence_diff two-packet identity skip). The wider
lineage-disclosure family is now 9-STRONG:

- Producer-side gap:
    - W805-BBBB cmd_simulate    (counterfactual TARGET-side resolution)
    - W805-DDDD cmd_orchestrate (partition output vacuous)
    - W805-GGGG cmd_capsule     (snapshot freshness disclosure)
    - W805-IIII cmd_fingerprint (cross-repo fingerprint compare lineage)
    - W805-LLLL cmd_runs        (replay artefact-resolution lineage)
- Verifier-side identity-skip:
    - W805-PPPP cmd_cga                  (predicate.subject[0].name never checked)
    - W805-UUUU cmd_audit_trail_verify   (actor/repo/git_sha never cross-checked)
    - W805-ZZZZ cmd_evidence_diff        (two-packet identity never cross-checked)
    - W805-BBBBB cmd_pr_bundle validate  (THIS file: bundle commit_sha
      never cross-checked against live HEAD; bundle git origin never
      cross-checked against live remote.origin.url)

Hypothesis from W805-ZZZZ agent (verified live below):
``cmd_pr_bundle validate`` reads ``.roam/pr-bundles/<branch>.json``,
runs ``_validate_bundle`` for structural completeness, and rebuilds
the envelope via ``_build_envelope``. The bundle carries
``bundle_meta.git.commit_sha`` + ``bundle_meta.git.head_sha`` stamped
at ``pr-bundle init`` time via ``_git_commit_sha`` (Alice's HEAD at
emit time). The validate path NEVER re-derives ``git rev-parse HEAD``
or ``git config remote.origin.url`` from the LIVE workspace and
cross-checks against the persisted values. A bundle physically
copied from repo A into repo B's ``.roam/pr-bundles/`` directory
validates ``state: "complete"`` + ``partial_success: False`` + exit 0
with the bundle's commit_sha (Alice's) silently rendered as if it
were the live workspace's lineage anchor.

Worse: the ``actor`` block is rebuilt from LIVE git config at
validate time (``_resolve_actor_block``) so the envelope shows Bob's
identity while the bundle's commit_sha is still Alice's -- producing
a SILENTLY INCONSISTENT envelope where actor and commit_sha disagree
without any ``identity_lineage`` / ``cross_repo`` / ``commit_sha_mismatch``
field disclosing the gap.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Module surface probe.** Read ``cmd_pr_bundle.py`` validate path
   in full (lines 3104-3238). Validate calls ``_require_bundle`` ->
   ``_mode_blocks_emit`` -> ``_build_envelope`` -> emit. ``_build_envelope``
   stamps ``commit_sha`` from ``bundle_meta.git.head_sha`` (persisted
   at init time, never re-derived at validate time) and rebuilds
   ``actor`` / ``environment_refs`` from LIVE git config. There is
   ZERO comparison between persisted ``bundle_meta.git.head_sha`` and
   live ``git rev-parse HEAD``, ZERO comparison between persisted
   bundle origin and live ``git config remote.origin.url``.

2. **Live cross-repo probe.** Built alice_repo (HEAD aaaa...,
   user.email alice@example.com) + bob_repo (HEAD bbbb...,
   user.email bob@example.com). Init+populate pr-bundle in alice_repo
   (alpha symbol, blast_radius_high). Copy
   ``alice_repo/.roam/pr-bundles/master.json`` to
   ``bob_repo/.roam/pr-bundles/master.json``. Run ``roam pr-bundle
   validate`` from bob_repo:

   - state: complete, partial_success: false, exit 0
   - bundle_meta.git.commit_sha: Alice's aaaa...
   - environment_refs[branch_range].env_id: Alice's aaaa...
   - environment_refs[workspace].env_id: bob_repo path (LIVE)
   - actor.human_actor: bob@example.com (LIVE)
   - NO commit_sha_mismatch / repo_id_mismatch / cross_repo /
     identity_lineage / actor_lineage field anywhere

   Confirms Pattern-1-V-D bug: validate gives a CLEAN success
   verdict on a structurally inconsistent envelope (Alice's
   commit_sha + Alice's branch_range AND Bob's workspace + Bob's
   actor) -- the workflow output that an agent would consume
   downstream silently mixes two repos' lineage.

3. **--strict / --strict-resolved probe.** Both flags still return
   state: complete; --strict-resolved correctly catches the
   ghost-symbol consequence (alpha is not in bob_repo's index) but
   does NOT disclose the underlying cross-repo identity mismatch.
   The ghost-symbol path is the canonical W21.4 strict-resolved
   gate -- it is a symptom of the cross-repo state, not a
   disclosure of it.

4. **Distinctness from W805-TTTT (cmd_pr_bundle emit Q1-Q8 axis).**
   W805-TTTT exercised the EMIT path's eight-question coverage
   matrix (does emit produce all 8 evidence-axis fields). EMIT is
   the producer; identity skip on emit would be a *producer-side*
   bug. W805-BBBBB exercises VALIDATE -- the verifier-side. Same
   module file, distinct subcommand, distinct bug class.
   AXIS CONFIRMED DISTINCT.

5. **Distinctness from W805-PPPP (cmd_cga).** cmd_cga verifies ONE
   in-toto attestation against the LIVE repo; the bug is the missing
   cross-check between predicate.subject[0].name and
   ``_git_remote_url(project_root)``. cmd_pr_bundle validate
   verifies ONE bundle against the LIVE repo; the bug is missing
   cross-check between persisted ``bundle_meta.git.head_sha`` and
   ``git rev-parse HEAD``. Same shape (one-artifact vs live), but
   distinct artifact (in-toto vs pr-bundle), distinct identity
   fields (subject.name vs head_sha/origin), and distinct
   collector-downstream consumer.

6. **Distinctness from W805-UUUU (cmd_audit_trail_verify).**
   Audit-trail verify walks a SHA-256-chained ledger inside ONE
   audit-trail file; identity fields are per-record. pr-bundle
   validate operates on a SINGLE JSON object; identity fields are
   top-level. UUUU verifies chain integrity, BBBBB verifies
   structural completeness -- different verification semantics,
   same verifier-side blind-spot pattern.

7. **Distinctness from W805-ZZZZ (cmd_evidence_diff).**
   evidence_diff compares TWO packets to EACH OTHER without
   touching the live repo. pr-bundle validate verifies ONE bundle
   against the LIVE repo. Two-packet identity (ZZZZ) vs
   one-bundle-vs-live identity (BBBBB). AXIS CONFIRMED DISTINCT.

8. **Reproducibility.** Any pr-bundle physically copied between
   repos (e.g., agent A handing off a bundle to agent B; a
   contractor exporting a bundle for review; CI re-running a
   bundle from a downstream repo) validates cleanly with the
   persisted identity silently masquerading as live. In a
   multi-agent / fleet scenario where bundles travel between
   workspaces, this is exactly the silent-acceptance failure the
   verifier exists to prevent.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a
cycle|duplicated.*here'`` on ``src/roam/commands/cmd_pr_bundle.py``
yields ONE match at line 1564 (the ``_load_permits_from_disk``
deprecated wrapper). Live verification: ``import roam.permits.store``
succeeds without cmd_pr_bundle, and ``roam.permits.store`` does NOT
import ``roam.commands`` at all. The "Local import avoids hard
module-load cycle" comment is a FALSE cycle hedge (W907 cargo-cult
candidate -- the cycle does not exist). HOWEVER, the comment is on a
back-compat wrapper kept for one cycle per W422 docstring, NOT on
the validate path itself; documenting as side observation for a
future W907 sweep, not pinning here (out of W805-BBBBB scope).

W978 re-run
===========

Cross-repo probe re-run in-isolation via
``python -m pytest tests/test_w805_bbbbb_cmd_pr_bundle_validate_identity_skip.py -x -n 0``.

Pinned via ``xfail(strict=True)`` so a future fix is detected (xpass
-> test failure -> unwrap and seal). The non-xfail tests pin
today-good behaviours (mode-block path, completeness check,
--strict-resolved gate for ghost symbols) so the fix has to be
additive.

Run isolation:
    python -m pytest tests/test_w805_bbbbb_cmd_pr_bundle_validate_identity_skip.py -x -n 0

Regression baseline:
    python -m pytest tests/test_pr_bundle*.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_tttt*.py tests/test_w805_zzzz*.py \
        tests/test_w805_pppp*.py tests/test_w805_uuuu*.py -x -n 0
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


# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_PR_BUNDLE_SPEC = importlib.util.find_spec("roam.commands.cmd_pr_bundle")
_GIT_HELPERS_SPEC = importlib.util.find_spec("roam.commands.git_helpers")


def test_command_and_substrate_exist():
    """W978/W907 gate: cmd_pr_bundle + git_helpers import cleanly."""
    if _CMD_PR_BUNDLE_SPEC is None:
        pytest.skip("roam.commands.cmd_pr_bundle not installed")
    assert _GIT_HELPERS_SPEC is not None, "roam.commands.git_helpers missing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_repo(
    tmp_path: Path,
    name: str,
    files: dict,
    *,
    actor_email: str = "t@t.com",
    actor_name: str = "Test",
    origin_url: str | None = None,
) -> Path:
    """Create a git repo with a configurable actor identity + optional origin URL."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", actor_email], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", actor_name], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(proj), capture_output=True)
    if origin_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin_url],
            cwd=str(proj),
            capture_output=True,
        )
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(proj), capture_output=True)
    return proj


def _git_head(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True)
    return (out.stdout or "").strip()


@pytest.fixture
def alice_repo(tmp_path):
    """Alice's repo with a known identity and origin URL."""
    return _make_repo(
        tmp_path,
        "alice_repo_bbbbb",
        {"app.py": "def alpha():\n    return 1\n"},
        actor_email="alice@example.com",
        actor_name="Alice",
        origin_url="https://example.com/alice/repo.git",
    )


@pytest.fixture
def bob_repo(tmp_path):
    """Bob's repo (different actor + origin + HEAD)."""
    return _make_repo(
        tmp_path,
        "bob_repo_bbbbb",
        {"app.py": "def beta():\n    return 2\n"},
        actor_email="bob@example.com",
        actor_name="Bob",
        origin_url="https://example.com/bob/repo.git",
    )


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
    # Accept 0 (clean), 2 (usage), and 5 (gate failure / strict).
    assert result.exit_code in (0, 2, 5), f"unexpected exit={result.exit_code}:\n{result.output}"
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


def _populate_complete_bundle(repo: Path, runner) -> None:
    """Init + populate a structurally complete pr-bundle inside *repo*.

    Adds intent + affected symbol + context-cmd (preflight) + tests
    required/run + roam_verdict signal, so ``validate`` reports
    ``state: complete``.
    """
    # Init via subprocess to share the repo's cwd.
    r = _invoke(runner, repo, ["pr-bundle", "init", "--intent", "Test"])
    assert r.exit_code == 0, r.output
    r = _invoke(runner, repo, ["pr-bundle", "add", "affected", "alpha"])
    assert r.exit_code == 0, r.output
    r = _invoke(runner, repo, ["pr-bundle", "add", "context-cmd", "roam preflight alpha"])
    assert r.exit_code == 0, r.output
    r = _invoke(runner, repo, ["pr-bundle", "add", "test-required", "tests/test_alpha.py"])
    assert r.exit_code == 0, r.output
    r = _invoke(runner, repo, ["pr-bundle", "add", "test-run", "tests/test_alpha.py"])
    assert r.exit_code == 0, r.output

    # Stamp a roam_verdict signal directly on the persisted bundle so
    # _validate_bundle sees has_signal=True without us needing to run
    # the full preflight.
    bundle_dir = repo / ".roam" / "pr-bundles"
    assert bundle_dir.exists(), f"expected .roam/pr-bundles/ in {repo}"
    bundles = list(bundle_dir.glob("*.json"))
    assert bundles, f"no pr-bundle written in {bundle_dir}"
    bp = bundles[0]
    b = json.loads(bp.read_text(encoding="utf-8"))
    b["roam_verdict"] = {"blast_radius_high": True}
    bp.write_text(json.dumps(b), encoding="utf-8")


def _copy_bundle(src_repo: Path, dst_repo: Path) -> Path:
    """Copy the pr-bundle file from src to dst's ``.roam/pr-bundles/``.

    Returns the destination bundle path.
    """
    src_dir = src_repo / ".roam" / "pr-bundles"
    src_files = list(src_dir.glob("*.json"))
    assert src_files, f"no pr-bundle in {src_dir}"
    src = src_files[0]
    dst_dir = dst_repo / ".roam" / "pr-bundles"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


# ---------------------------------------------------------------------------
# POSITIVE shape pins -- today-good behaviours. The fix must stay additive.
# ---------------------------------------------------------------------------


class TestPrBundleValidateStructuralChecksOrthogonalFromIdentityClaim:
    """The structural-completeness check is orthogonal to identity claims.
    Today these tests pin the completeness checks as load-bearing. The
    fix lands the identity layer on top -- it MUST NOT break the
    completeness checks.
    """

    def test_local_complete_bundle_validates_clean(self, alice_repo, cli_runner):
        """A bundle produced in-place by the same actor validates state=complete."""
        _populate_complete_bundle(alice_repo, cli_runner)
        r = _invoke(cli_runner, alice_repo, ["--json", "pr-bundle", "validate"])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        assert data["summary"]["state"] == "complete", data["summary"]
        assert data["summary"]["partial_success"] is False, data["summary"]

    def test_strict_resolved_still_catches_ghost_symbol(self, alice_repo, cli_runner):
        """W21.4 strict-resolved gate stays load-bearing. The identity-skip
        fix must not regress this independent gate."""
        _populate_complete_bundle(alice_repo, cli_runner)
        # alpha is not in the index (no roam init was run). --strict-resolved
        # should flag it as unresolved.
        r = _invoke(
            cli_runner,
            alice_repo,
            ["--json", "pr-bundle", "validate", "--strict", "--strict-resolved"],
        )
        # Exit 5 because ghost symbol + --strict-resolved.
        assert r.exit_code == 5, f"expected exit 5, got {r.exit_code}: {r.output}"


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805ZzzzInvariantsPreserved:
    """W805-ZZZZ (cmd_evidence_diff cross-repo identity) sister cross-check.

    Baseline: cmd_evidence_diff still computes a diff between two packets.
    We do NOT re-assert ZZZZ's xfail-strict pins.
    """

    def test_evidence_diff_module_importable(self):
        spec = importlib.util.find_spec("roam.commands.cmd_evidence_diff")
        assert spec is not None, "cmd_evidence_diff sister module not importable"


class TestW805PpppInvariantsPreserved:
    """W805-PPPP (cmd_cga verify subject.name skip) sister cross-check.

    Baseline: cmd_cga still loads. We do NOT re-assert PPPP's xfail
    pins (which require a real workspace).
    """

    def test_cga_module_importable(self):
        spec = importlib.util.find_spec("roam.attest.cga")
        assert spec is not None, "roam.attest.cga sister module not importable"


class TestW805UuuuInvariantsPreserved:
    """W805-UUUU (cmd_audit_trail_verify identity skip) sister cross-check."""

    def test_audit_trail_verify_module_importable(self):
        spec = importlib.util.find_spec("roam.commands.cmd_audit_trail_verify")
        assert spec is not None, "cmd_audit_trail_verify sister module not importable"


class TestW805TtttEmitAxisOrthogonality:
    """W805-TTTT probed pr-bundle EMIT's Q1-Q8 coverage matrix; this file
    probes VALIDATE's verifier-side identity-skip. Different subcommands,
    different bug classes. This test pins that pr_bundle_emit and
    pr_bundle_validate exist as distinct click commands.
    """

    def test_emit_and_validate_are_distinct_subcommands(self):
        from roam.commands.cmd_pr_bundle import (  # noqa: F401
            pr_bundle_emit,
            pr_bundle_validate,
        )

        # Distinct Click commands (different callbacks, different option sets).
        assert pr_bundle_emit is not pr_bundle_validate
        assert pr_bundle_emit.callback is not pr_bundle_validate.callback


# ---------------------------------------------------------------------------
# REAL BUG -- Pattern-1-V-D + CP45/CP46 verifier-side identity-skip
# Pinned xfail(strict=True): fix will flip to xpass -> test failure -> unwrap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BBBBB Pattern-1-V-D bug: src/roam/commands/cmd_pr_bundle.py:3107-3237 "
        "(pr_bundle_validate) reads .roam/pr-bundles/<branch>.json, runs "
        "_validate_bundle for structural completeness, and rebuilds the "
        "envelope via _build_envelope -- but NEVER cross-checks the "
        "persisted bundle_meta.git.commit_sha / head_sha (stamped by "
        "_git_commit_sha at init time) against the LIVE workspace's "
        "git_head_sha() / git_origin_url() helpers in "
        "src/roam/commands/git_helpers.py:72-79. A pr-bundle physically "
        "copied from repo A into repo B's .roam/pr-bundles/ validates "
        "state='complete' + partial_success=False + exit 0 with NO "
        "commit_sha_mismatch / repo_id_mismatch / cross_repo / "
        "identity_lineage / actor_lineage field disclosing the gap. "
        "Worse: the actor block is rebuilt from LIVE git config at "
        "validate time (_resolve_actor_block) so the envelope shows the "
        "LIVE actor (Bob) while bundle_meta.git.commit_sha is still the "
        "PERSISTED commit_sha (Alice's) -- a silently inconsistent "
        "envelope. cmd_pr_bundle validate is STRUCTURALLY DISTINCT from "
        "W805-TTTT (pr-bundle EMIT's Q1-Q8 coverage matrix, distinct "
        "subcommand + bug class) and from W805-PPPP/UUUU/ZZZZ "
        "(different artifacts, different identity-field axes). Fix: "
        "extend _build_envelope (or wrap pr_bundle_validate) so the "
        "persisted bundle_meta.git.commit_sha / head_sha values are "
        "compared to git_head_sha() at validate time; emit a closed-enum "
        "'commit_sha_mismatch' issue (and a parallel 'repo_origin_mismatch' "
        "for the origin URL) when they disagree; surface the (persisted, "
        "live) pair in the issues[] entry so the envelope discloses both "
        "sides. See CLAUDE.md Pattern-1-V-D + 'Make fallback chains loud' "
        "(CP45/CP46) + W805-PPPP/UUUU/ZZZZ sister pins. Verifier-side "
        "family 4-STRONG, lineage-disclosure family 9-STRONG."
    ),
)
class TestPrBundleValidateCrossRepoIdentityDisclosureBug:
    def test_cross_repo_bundle_validate_identity_check(self, alice_repo, bob_repo, cli_runner):
        """Pattern-1-V-D core probe: build a complete pr-bundle in
        alice_repo, copy it to bob_repo's .roam/pr-bundles/, run
        ``roam pr-bundle validate`` from bob_repo. Validate must flag
        the cross-repo identity mismatch -- either via an explicit
        issue/disclosure field, an explicit non-complete state, or by
        failing the gate."""
        _populate_complete_bundle(alice_repo, cli_runner)
        _copy_bundle(alice_repo, bob_repo)
        r = _invoke(cli_runner, bob_repo, ["--json", "pr-bundle", "validate"])
        data = _parse_json(r)
        summary = data.get("summary") or {}
        missing = summary.get("missing_proofs") or []
        joined = (" | ".join(str(m) for m in missing)).lower()
        # The fix would produce ANY of these signals:
        identity_signal = (
            summary.get("state") != "complete"
            or summary.get("partial_success") is True
            or "identity" in joined
            or "cross_repo" in joined
            or "commit_sha" in joined
            or "mismatch" in joined
            or "lineage" in joined
            or "foreign" in joined
        )
        assert identity_signal, (
            f"Pattern-1-V-D: cmd_pr_bundle validate silently accepts a "
            f"bundle copied from a different repo. The persisted "
            f"bundle_meta.git.commit_sha is Alice's HEAD; the live HEAD "
            f"is Bob's; the bundle's actor was overwritten with Bob's "
            f"identity (silent identity swap). But validate returned "
            f"summary={summary}. The recorded commit_sha was never "
            f"cross-checked against `git rev-parse HEAD`."
        )

    def test_commit_sha_mismatch_disclosure(self, alice_repo, bob_repo, cli_runner):
        """CP45 lineage rule: the validate envelope should disclose
        BOTH sides of the identity check -- the persisted commit_sha
        (from the bundle) AND the live commit_sha (from the live git).
        Today no such field appears in the envelope, so an agent
        reading the envelope cannot tell whether identity was checked
        at all."""
        _populate_complete_bundle(alice_repo, cli_runner)
        _copy_bundle(alice_repo, bob_repo)
        r = _invoke(cli_runner, bob_repo, ["--json", "pr-bundle", "validate"])
        data = _parse_json(r)
        keys = set(data.keys()) | set((data.get("summary") or {}).keys())
        identity_keys = {
            "live_commit_sha",
            "live_head_sha",
            "live_repo_origin",
            "recorded_commit_sha",
            "recorded_head_sha",
            "recorded_repo_origin",
            "commit_sha_match",
            "commit_sha_mismatch",
            "repo_origin_match",
            "repo_origin_mismatch",
            "cross_repo",
            "identity_lineage",
            "identity_mismatch",
            "identity_check",
        }
        overlap = identity_keys & keys
        assert overlap, (
            f"CP45 lineage: cmd_pr_bundle validate envelope discloses "
            f"NO recorded-vs-live identity field. Looked for one of "
            f"{sorted(identity_keys)}; envelope/summary had "
            f"{sorted(keys)}."
        )

    def test_strict_mode_fails_on_cross_repo(self, alice_repo, bob_repo, cli_runner):
        """--strict should fail the gate when the cross-repo identity
        mismatch is detected. Today --strict only catches structural
        incompleteness (missing intent / affected / tests / verdict);
        a cross-repo bundle passes structural completeness and
        --strict silently exits 0. The fix should make --strict
        gate-fail (exit 5) when commit_sha doesn't match live HEAD."""
        _populate_complete_bundle(alice_repo, cli_runner)
        _copy_bundle(alice_repo, bob_repo)
        r = _invoke(cli_runner, bob_repo, ["--json", "pr-bundle", "validate", "--strict"])
        # The fix would land exit 5 (gate-fail) for cross-repo bundles.
        assert r.exit_code == 5, f"--strict should fail on cross-repo bundle, got exit {r.exit_code}: {r.output}"


# ---------------------------------------------------------------------------
# Advisory probe (passing today) -- documents the current cross-repo-pass
# semantics so the fix is verifiably additive. When the bug is fixed,
# this advisory will need updating (or removal) -- the fix must produce
# ``state != 'complete'`` here.
# ---------------------------------------------------------------------------


def test_cross_repo_bundle_today_validates_complete_when_structurally_ok(alice_repo, bob_repo, cli_runner):
    """Documents the today-shape that the bug pin above asserts a fix
    would flip. When the bug is fixed, this test will need updating
    (or removal) -- the fix must produce ``state != 'complete'`` here.
    For now it pins the current silent-acceptance failure mode,
    providing a positive-test baseline for the xfail-strict pin's
    complementary assertion.

    Key observation pinned: the envelope's bundle_meta.git.commit_sha
    stays at Alice's HEAD (because it's a verbatim read from the
    persisted bundle), while ``actor.human_actor`` shows Bob's email
    (because _resolve_actor_block reads LIVE git config). The two
    fields disagree without disclosure.
    """
    _populate_complete_bundle(alice_repo, cli_runner)
    alice_head = _git_head(alice_repo)
    bob_head = _git_head(bob_repo)
    assert alice_head and bob_head and alice_head != bob_head, (
        "test prerequisite: alice and bob must have distinct HEADs"
    )
    _copy_bundle(alice_repo, bob_repo)
    r = _invoke(cli_runner, bob_repo, ["--json", "pr-bundle", "validate"])
    assert r.exit_code in (0, 5), r.output
    data = _parse_json(r)
    summary = data["summary"]
    if summary["state"] == "complete":
        # Today's shape -- cross-repo bundle silently accepted because
        # structural completeness is intact.
        persisted_sha = (data.get("bundle_meta") or {}).get("git", {}).get("commit_sha")
        live_actor = (data.get("actor") or {}).get("human_actor")
        # Pin the silently inconsistent envelope shape:
        assert persisted_sha == alice_head, (
            f"bundle_meta.git.commit_sha should still be Alice's persisted HEAD, got {persisted_sha!r}"
        )
        assert live_actor == "bob@example.com", f"actor.human_actor should be Bob's live identity, got {live_actor!r}"
    else:
        # The fix has landed -- emit a clear marker. The xfail-strict
        # test above will flip to xpass and force test-failure handling.
        pytest.skip(
            "cross-repo pr-bundle validate no longer silently passes -- "
            "the W805-BBBBB bug appears to be fixed. Unwrap the xfail above."
        )
