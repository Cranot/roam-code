"""Regression guard — `roam cga verify` MUST fail-closed on missing bundle.

ROADMAP S4: a CGA verifier that returns ``verified=True`` while the cosign
half is silently null is a compliance landmine — the verdict cryptographically
verifies nothing. These tests pin the fail-closed default so the silent-skip
regression cannot return.

Three behaviours pinned here:

1. ``cga verify <stmt>`` with no sibling ``.bundle`` and no ``--no-cosign``
   → ``verified: false``, exit 5, error mentions the missing bundle.
2. ``cga verify <stmt> --no-cosign`` → predicate-only verdict can pass; the
   envelope advertises ``cosign_verified: false`` so the caller is never
   confused about which half cleared.
3. ``cga verify <stmt>`` with a valid sibling bundle present → happy path
   verifies both halves; ``cosign_verified: true``, exit 0.

The third case is the contract the silent-skip bug was hiding behind:
without this test, regressing back to "skip cosign on missing bundle" would
make tests 1 and 2 both pass while the real "cosign verified" path stays
unexercised.
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
    """Pre-elect autonomous_pr so privileged `roam cga` works under future
    `ROAM_MODE_ENFORCEMENT` default-on (W23.3 staged-rollout PR-B). Every test
    here invokes `roam cga emit|verify`, which is gated under `safe_edit`."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


@pytest.fixture
def cga_project(tmp_path):
    """A git-initialised project with one indexed source file.

    Mirrors the fixture used by ``test_cga.py`` so behaviours stay
    portable across regression files. We re-declare locally (rather than
    importing across test modules) because pytest fixtures don't cross
    module boundaries by default.
    """
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


class TestVerifyFailsClosedOnMissingBundle:
    """Fix 1 — default behaviour MUST fail-closed when no bundle is present."""

    def test_verify_no_bundle_default_fails_closed(self, cga_project, tmp_path):
        """Unsigned emit → verify with no flags → MUST fail with exit 5.

        Pre-fix bug: verdict was "CGA verified" with cosign silently null.
        Compliance officer would accept a green-light verdict that
        cryptographically verifies nothing.
        """
        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output
        # Sanity: no sibling bundle was written.
        assert not out.with_suffix(".bundle").exists()

        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out)])
        assert verify.exit_code == 5, (
            f"expected exit 5 (fail-closed), got {verify.exit_code}\n{verify.output}"
        )

        data = json.loads(verify.output)
        assert data["summary"]["ok"] is False, "verdict must surface ok=false on missing bundle"
        assert data["summary"]["cosign_verified"] is False
        # The error message must guide the user to the actionable opt-in.
        joined = " ".join(data.get("errors", []))
        assert "--no-cosign" in joined or "bundle not found" in joined, (
            f"error must mention --no-cosign / bundle; got: {joined}"
        )

    def test_verify_no_bundle_with_explicit_opt_in_passes(self, cga_project, tmp_path):
        """``--no-cosign`` lets a clean predicate match pass — but the envelope
        must clearly say cosign was skipped so the caller can't mistake
        predicate-only verification for full cryptographic verification.
        """
        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output

        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out), "--no-cosign"])
        assert verify.exit_code == 0, verify.output

        data = json.loads(verify.output)
        assert data["summary"]["ok"] is True
        # Crucial: the predicate-only verdict MUST NOT lie about cosign.
        assert data["summary"]["cosign_verified"] is False, (
            "with --no-cosign, cosign_verified must be False even when ok=True"
        )

    def test_verify_with_bundle_unchanged(self, cga_project, tmp_path, monkeypatch):
        """Happy path — valid bundle present, verify exercises BOTH halves.

        This is the contract the silent-skip bug was hiding. Without this
        test, regressing back to "skip cosign on missing bundle" would
        leave the cosign-verify path untested. We mock cosign so the test
        runs on machines without the binary installed.
        """
        import roam.attest.cga as cga_mod

        out = tmp_path / "cga.json"
        sig = tmp_path / "cga.sig"
        bundle = tmp_path / "cga.bundle"

        real_run = cga_mod.subprocess.run

        def fake_cosign_available():
            return True, "v2.4.0 (mocked)"

        def fake_run(args, *a, **kw):
            # Pass real git invocations through — the emit path needs the
            # real ``git status``/``git rev-parse`` to compute the
            # dirty-hash and commit SHA that get embedded in the predicate.
            argv = args if isinstance(args, (list, tuple)) else [args]
            cmd = argv[0] if argv else ""
            if cmd == "git" or (isinstance(cmd, str) and cmd.endswith("git")):
                return real_run(args, *a, **kw)
            # Mock cosign — write the expected sibling files on sign-blob.
            if "sign-blob" in argv:
                sig.write_text("fake-sig\n", encoding="utf-8")
                bundle.write_text('{"mock": "bundle"}', encoding="utf-8")

            class _R:
                returncode = 0
                stdout = "Verified OK"
                stderr = ""

            return _R()

        monkeypatch.setattr(cga_mod, "cosign_available", fake_cosign_available)
        monkeypatch.setattr(cga_mod.subprocess, "run", fake_run)

        runner = CliRunner()
        fake_key = tmp_path / "fake.key"
        fake_key.write_text("# mock", encoding="utf-8")

        emit = runner.invoke(
            cli,
            ["cga", "emit", "--sign", "--key", str(fake_key), "--output", str(out)],
        )
        assert emit.exit_code == 0, emit.output
        assert bundle.exists(), "mocked cosign must have written the bundle"

        # Verify — no flags. Auto-detects the sibling bundle, both halves
        # of the verdict must clear.
        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out)])
        assert verify.exit_code == 0, verify.output
        data = json.loads(verify.output)
        assert data["summary"]["ok"] is True
        assert data["summary"]["cosign_verified"] is True, (
            "with bundle + mocked-clean cosign, cosign_verified must be True"
        )


class TestVerifyHelpAdvertisesFailClosedDefault:
    """The fail-closed default is load-bearing — the help text must
    surface ``--no-cosign`` so operators know the opt-out exists.
    """

    def test_verify_help_mentions_no_cosign(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["cga", "verify", "--help"])
        assert result.exit_code == 0, result.output
        assert "--no-cosign" in result.output, (
            "verify --help must document the --no-cosign opt-out"
        )


# ---------------------------------------------------------------------------
# Manual smoke for environments without git — skip silently.
# ---------------------------------------------------------------------------


def _git_on_path() -> bool:
    try:
        subprocess.run(
            ["git", "--version"], capture_output=True, timeout=5, check=False
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_on_path(), reason="git not on PATH")
