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
        assert verify.exit_code == 5, f"expected exit 5 (fail-closed), got {verify.exit_code}\n{verify.output}"

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

        # Verify — pass --cosign-key (offline-keypair path). Auto-detects
        # the sibling bundle, both halves of the verdict must clear.
        # Note: under cosign >= 2.0, keyless verify (no --cosign-key) also
        # requires --cert-identity + --cert-oidc-issuer; the offline path
        # we exercise here only needs the public key.
        fake_pub = tmp_path / "fake.pub"
        fake_pub.write_text("# mock pub", encoding="utf-8")
        verify = runner.invoke(
            cli,
            ["--json", "cga", "verify", str(out), "--cosign-key", str(fake_pub)],
        )
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
        assert "--no-cosign" in result.output, "verify --help must document the --no-cosign opt-out"


class TestVerifyKeylessRequiresCertIdentity:
    """Cosign >= 2.0 refuses keyless verification without
    ``--certificate-identity`` / ``--certificate-oidc-issuer``. Roam mirrors
    that gate at the CLI: if neither ``--cosign-key`` nor BOTH cert flags
    are provided when a bundle is present, refuse with a clear error
    instead of letting cosign emit its own confusing message buried inside
    the envelope. Regression guard for the CGA-Attestation workflow break
    on commits 18ef4318 / bb194972 (cosign verify-blob failed with
    "--certificate-identity or --certificate-identity-regexp is required").
    """

    def test_verify_help_documents_cert_identity_flags(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["cga", "verify", "--help"])
        assert result.exit_code == 0, result.output
        assert "--cert-identity" in result.output, "verify --help must document --cert-identity for keyless mode"
        assert "--cert-oidc-issuer" in result.output, "verify --help must document --cert-oidc-issuer for keyless mode"

    def test_keyless_verify_without_cert_flags_fails_closed(self, cga_project, tmp_path, monkeypatch):
        """Bundle present, no --cosign-key, no --cert-identity — must refuse
        before invoking cosign so the error is actionable, not the raw
        cosign stderr.
        """
        import roam.attest.cga as cga_mod

        out = tmp_path / "cga.json"
        sig = tmp_path / "cga.sig"
        bundle = tmp_path / "cga.bundle"

        real_run = cga_mod.subprocess.run

        def fake_cosign_available():
            return True, "v2.4.0 (mocked)"

        def fake_run(args, *a, **kw):
            argv = args if isinstance(args, (list, tuple)) else [args]
            cmd = argv[0] if argv else ""
            if cmd == "git" or (isinstance(cmd, str) and cmd.endswith("git")):
                return real_run(args, *a, **kw)
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
        # Strip any ambient env that could let the gate pass.
        monkeypatch.delenv("ROAM_CGA_CERT_IDENTITY", raising=False)
        monkeypatch.delenv("ROAM_CGA_CERT_OIDC_ISSUER", raising=False)

        runner = CliRunner()
        fake_key = tmp_path / "fake.key"
        fake_key.write_text("# mock", encoding="utf-8")

        emit = runner.invoke(
            cli,
            ["cga", "emit", "--sign", "--key", str(fake_key), "--output", str(out)],
        )
        assert emit.exit_code == 0, emit.output
        assert bundle.exists()

        # Verify with NO key + NO cert flags must refuse loudly.
        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out)])
        assert verify.exit_code == 5, verify.output
        data = json.loads(verify.output)
        joined = " ".join(data.get("errors", []))
        assert "cert-identity" in joined or "cert-oidc-issuer" in joined, (
            f"refusal error must name the missing cert flag(s); got: {joined}"
        )

    def test_keyless_verify_with_env_cert_flags_passes_through(self, cga_project, tmp_path, monkeypatch):
        """ROAM_CGA_CERT_IDENTITY + ROAM_CGA_CERT_OIDC_ISSUER env vars
        satisfy the gate without command-line flags (the workflow uses
        env-var injection from GitHub context, see cga-attestation.yml)."""
        import roam.attest.cga as cga_mod

        out = tmp_path / "cga.json"
        sig = tmp_path / "cga.sig"
        bundle = tmp_path / "cga.bundle"
        captured_argv: list[list] = []

        real_run = cga_mod.subprocess.run

        def fake_cosign_available():
            return True, "v2.4.0 (mocked)"

        def fake_run(args, *a, **kw):
            argv = args if isinstance(args, (list, tuple)) else [args]
            cmd = argv[0] if argv else ""
            if cmd == "git" or (isinstance(cmd, str) and cmd.endswith("git")):
                return real_run(args, *a, **kw)
            if "sign-blob" in argv:
                sig.write_text("fake-sig\n", encoding="utf-8")
                bundle.write_text('{"mock": "bundle"}', encoding="utf-8")
            if "verify-blob" in argv:
                captured_argv.append(list(argv))

            class _R:
                returncode = 0
                stdout = "Verified OK"
                stderr = ""

            return _R()

        monkeypatch.setattr(cga_mod, "cosign_available", fake_cosign_available)
        monkeypatch.setattr(cga_mod.subprocess, "run", fake_run)
        monkeypatch.setenv(
            "ROAM_CGA_CERT_IDENTITY",
            "https://github.com/owner/repo/.github/workflows/cga.yml@refs/heads/main",
        )
        monkeypatch.setenv(
            "ROAM_CGA_CERT_OIDC_ISSUER",
            "https://token.actions.githubusercontent.com",
        )

        runner = CliRunner()
        fake_key = tmp_path / "fake.key"
        fake_key.write_text("# mock", encoding="utf-8")
        emit = runner.invoke(
            cli,
            ["cga", "emit", "--sign", "--key", str(fake_key), "--output", str(out)],
        )
        assert emit.exit_code == 0, emit.output

        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out)])
        assert verify.exit_code == 0, verify.output
        # And the cosign verify-blob call must carry the env-supplied flags.
        cosign_calls = [a for a in captured_argv if "verify-blob" in a]
        assert cosign_calls, "cosign verify-blob must have been invoked"
        flat = " ".join(cosign_calls[-1])
        assert "--certificate-identity" in flat, f"cert-identity env must reach cosign args; got: {flat}"
        assert "--certificate-oidc-issuer" in flat, f"cert-oidc-issuer env must reach cosign args; got: {flat}"


# ---------------------------------------------------------------------------
# Manual smoke for environments without git — skip silently.
# ---------------------------------------------------------------------------


def _git_on_path() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_on_path(), reason="git not on PATH")
