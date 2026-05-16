"""Regression guard — ``git_dirty_hash`` + ``git_commit_sha1`` MUST bind into the CGA predicate.

ROADMAP S5: the manifest collects the dirty-hash of the working tree at
sign time but, pre-fix, never embedded it into the signed predicate. A
signed CGA could claim a clean commit on a tree with uncommitted edits —
the attestation implied a property the artifact did not carry.

Four behaviours pinned here:

1. ``cga emit`` on a dirty tree → refuses by default (exit non-zero,
   ``DIRTY_TREE`` in the surfaced error).
2. ``cga emit --allow-dirty`` on a dirty tree → succeeds AND embeds the
   live ``git_dirty_hash`` into the predicate.
3. ``cga verify`` re-derives the live ``git_dirty_hash`` and refuses when
   the predicate's embedded hash diverges from the live tree.
4. ``cga verify`` on a clean tree with an unchanged signed predicate
   passes — the happy path is not over-tightened.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Pre-elect autonomous_pr so privileged `roam cga emit` works under future
    `ROAM_MODE_ENFORCEMENT` default-on (W23.3 staged-rollout PR-B). Every test
    here invokes `roam cga emit|verify`, which is gated under `safe_edit`."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


@pytest.fixture
def cga_project(tmp_path):
    """Indexed git-initialised project mirroring ``test_cga.py``'s fixture."""
    proj = make_src_project(
        tmp_path,
        {
            "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token
                def handle_login(user):
                    return UserSession()
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Emit-side: dirty tree refusal + --allow-dirty opt-in
# ---------------------------------------------------------------------------


class TestEmitRefusesDirtyTree:
    def test_emit_refuses_dirty_tree_by_default(self, cga_project, tmp_path):
        """Untracked file dirties the tree → emit MUST refuse.

        Pre-fix: emit happily proceeded and signed a statement whose
        ``subject.git_commit_sha1`` pointed at a commit that didn't reflect
        the analysed state.
        """
        (cga_project / "untracked.py").write_text("def newly_added(): pass\n", encoding="utf-8")
        runner = CliRunner()
        out = tmp_path / "cga.json"
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert result.exit_code != 0, f"dirty-tree emit must refuse; got exit {result.exit_code}\n{result.output}"
        # The error code or text must surface the dirty-tree reason so the
        # user knows the actionable opt-in (commit / stash / --allow-dirty).
        out_lower = result.output.lower()
        assert "dirty" in out_lower or "dirty_tree" in out_lower, (
            f"dirty-tree refusal must surface in output; got: {result.output[:300]}"
        )

    def test_emit_allow_dirty_works(self, cga_project, tmp_path):
        """--allow-dirty opts in — emit succeeds AND records the dirty-hash
        so the predicate cryptographically commits to "this was signed
        against a tree with these uncommitted edits".
        """
        (cga_project / "untracked.py").write_text("def newly_added(): pass\n", encoding="utf-8")
        runner = CliRunner()
        out = tmp_path / "cga.json"
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out), "--allow-dirty"])
        assert result.exit_code == 0, result.output

        statement = json.loads(out.read_text(encoding="utf-8"))
        dirty_hash = statement["predicate"].get("git_dirty_hash")
        # sha256 hex is 64 chars; non-None signals the predicate caught
        # the dirty state.
        assert dirty_hash is not None, "predicate must embed the live dirty-hash when --allow-dirty"
        assert isinstance(dirty_hash, str) and len(dirty_hash) == 64, (
            f"git_dirty_hash must be a sha256 hex digest; got {dirty_hash!r}"
        )

        # And the subject must carry the commit SHA so the verifier
        # can re-derive both halves.
        subject_digest = statement["subject"][0]["digest"]
        assert "git_commit_sha1" in subject_digest
        sha = subject_digest["git_commit_sha1"]
        assert sha and sha != "unknown", f"subject.git_commit_sha1 must be a real SHA; got {sha!r}"


# ---------------------------------------------------------------------------
# Verify-side: dirty-hash + commit-SHA mismatch detection
# ---------------------------------------------------------------------------


class TestVerifyDetectsMismatch:
    def test_verify_detects_dirty_hash_mismatch(self, cga_project, tmp_path):
        """Emit on a clean tree → modify the tree → verify MUST refuse.

        The predicate asserts ``git_dirty_hash: null`` (clean). Once the
        tree gains uncommitted edits, the live dirty-hash is non-null and
        the verifier sees the mismatch and exits 5.
        """
        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output

        statement = json.loads(out.read_text(encoding="utf-8"))
        assert statement["predicate"]["git_dirty_hash"] is None, "fixture must emit on a clean tree"

        # Dirty the tree post-emit.
        (cga_project / "untracked.py").write_text("def smuggled(): pass\n", encoding="utf-8")

        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out), "--no-cosign"])
        assert verify.exit_code == 5, f"dirty-hash mismatch must exit 5; got {verify.exit_code}\n{verify.output}"

        data = json.loads(verify.output)
        assert data["summary"]["ok"] is False
        joined = " ".join(data.get("errors", []))
        assert "git_dirty_hash" in joined or "dirty" in joined.lower(), (
            f"verify must name the dirty-hash mismatch in errors; got: {joined}"
        )

    def test_verify_clean_tree_unchanged_passes(self, cga_project, tmp_path):
        """Emit + verify on a clean tree with no changes → passes.

        This is the negative control — without it, an over-eager dirty-hash
        check could break the happy path silently.
        """
        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output

        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out), "--no-cosign"])
        assert verify.exit_code == 0, verify.output
        data = json.loads(verify.output)
        assert data["summary"]["ok"] is True

    def test_verify_detects_commit_sha_mismatch(self, cga_project, tmp_path, monkeypatch):
        """Subject claims commit X, live tree at commit Y → refuse.

        ROADMAP S5 part (b): bind both ``git_commit_sha1`` AND
        ``git_dirty_hash`` so swapping HEAD between sign and verify is
        also caught — not just uncommitted-edit drift.
        """
        from roam.attest import cga as cga_mod

        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output

        # Spoof a different live HEAD at verify time.
        spoofed_sha = "feedface" + "0" * 32
        monkeypatch.setattr(cga_mod, "_git_commit_sha", lambda root: spoofed_sha)

        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out), "--no-cosign"])
        assert verify.exit_code == 5, verify.output
        data = json.loads(verify.output)
        joined = " ".join(data.get("errors", []))
        assert "git_commit_sha1" in joined, f"verify must name the commit-SHA mismatch; got: {joined}"


# ---------------------------------------------------------------------------
# Predicate-shape contract
# ---------------------------------------------------------------------------


class TestPredicateBindsDirtyHashField:
    """The predicate MUST carry ``git_dirty_hash`` on every emit so the
    verifier can re-derive and compare. A field-absent predicate would
    let the verifier silently skip the dirty check — the original bug.
    """

    def test_predicate_carries_git_dirty_hash_field_on_clean_emit(self, cga_project, tmp_path):
        out = tmp_path / "cga.json"
        runner = CliRunner()
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert result.exit_code == 0, result.output

        statement = json.loads(out.read_text(encoding="utf-8"))
        # Field must be present even when value is None (clean tree).
        assert "git_dirty_hash" in statement["predicate"], (
            "predicate MUST always carry git_dirty_hash so the verifier "
            "knows whether to compare; missing-field would let a hostile "
            "actor produce a predicate that silently skips the check."
        )

    def test_subject_carries_git_commit_sha1(self, cga_project, tmp_path):
        out = tmp_path / "cga.json"
        runner = CliRunner()
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert result.exit_code == 0, result.output

        statement = json.loads(out.read_text(encoding="utf-8"))
        subject = statement["subject"][0]
        assert "git_commit_sha1" in subject["digest"], (
            "subject.digest must carry git_commit_sha1 so the verifier can compare to the live HEAD."
        )


# ---------------------------------------------------------------------------
# Skip the whole module when git is unavailable.
# ---------------------------------------------------------------------------


def _git_on_path() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_on_path(), reason="git not on PATH")
