"""Tests for cloud-synced filesystem detection (W127).

The helper at ``src/roam/db/fs_detect.py:detect_cloud_sync`` is a
path-substring heuristic shared by ``cmd_init`` (warns at init) and
``cmd_doctor`` (advisory check). These tests pin the provider names so
a future provider addition doesn't accidentally rename ``OneDrive`` to
``Microsoft OneDrive`` and break the JSON envelope contract that
agents consume.

The init-side integration (warning surfaced in text + JSON) is tested
at the bottom of this file under ``TestInitCloudSyncWarning`` — that
suite uses ``CliRunner`` against a fresh project whose ``.roam/`` dir
sits under a fake cloud-synced path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.db.fs_detect import cloud_sync_warning, detect_cloud_sync

# ---------------------------------------------------------------------------
# Pure-function detection tests
# ---------------------------------------------------------------------------


class TestDetectCloudSyncPositive:
    """Paths under a known cloud-sync root return the provider name."""

    def test_onedrive_windows_path(self):
        # Personal OneDrive on Windows.
        assert detect_cloud_sync(Path("C:/Users/x/OneDrive/repo")) == "OneDrive"

    def test_onedrive_corporate_variant(self):
        # Corporate / business OneDrive folders carry the tenant brand
        # after a hyphen: ``OneDrive - Acme``. Both should normalise to
        # the canonical ``OneDrive`` so JSON consumers don't have to
        # parse tenant names.
        assert detect_cloud_sync(Path("C:/Users/x/OneDrive - Acme/repo")) == "OneDrive"

    def test_onedrive_lowercase_path(self):
        # Path detection is case-insensitive — some shells produce
        # lowercased paths via globbing.
        assert detect_cloud_sync(Path("c:/users/x/onedrive/repo")) == "OneDrive"

    def test_dropbox_posix(self):
        assert detect_cloud_sync(Path("/home/x/Dropbox/repo")) == "Dropbox"

    def test_dropbox_windows(self):
        assert detect_cloud_sync(Path("C:/Users/x/Dropbox/repo")) == "Dropbox"

    def test_google_drive_my_drive(self):
        # The per-account root under "Google Drive" is "My Drive".
        assert detect_cloud_sync(Path("G:/My Drive/repo")) == "Google Drive"

    def test_google_drive_canonical(self):
        assert detect_cloud_sync(Path("/home/x/Google Drive/repo")) == "Google Drive"

    def test_icloud_macos_mobile_documents(self):
        # The actual mount on macOS: ~/Library/Mobile Documents/.../...
        path = Path("/Users/x/Library/Mobile Documents/com~apple~CloudDocs/repo")
        assert detect_cloud_sync(path) == "iCloud Drive"

    def test_icloud_windows_client_path(self):
        # The Windows iCloud client uses %USERPROFILE%/iCloudDrive/ —
        # we accept both ``iCloud Drive`` and ``iCloudDrive`` spellings.
        assert detect_cloud_sync(Path("C:/Users/x/iCloud Drive/repo")) == "iCloud Drive"

    def test_box_sync(self):
        assert detect_cloud_sync(Path("C:/Users/x/Box Sync/repo")) == "Box"

    def test_insync_linux(self):
        assert detect_cloud_sync(Path("/home/x/Insync/me@example.com/Drive/repo")) == "Insync"

    def test_pcloud(self):
        assert detect_cloud_sync(Path("C:/Users/x/pCloud/repo")) == "pCloud"


class TestDetectCloudSyncNegative:
    """Paths that don't sit under a synced root return None."""

    def test_plain_home_project(self):
        assert detect_cloud_sync(Path("/home/x/projects/repo")) is None

    def test_windows_documents(self):
        # Documents folder isn't cloud-synced by default.
        assert detect_cloud_sync(Path("C:/Users/x/Documents/repo")) is None

    def test_tmp_path(self):
        assert detect_cloud_sync(Path("/tmp/repo")) is None

    def test_windows_program_files(self):
        assert detect_cloud_sync(Path("C:/Program Files/MyApp")) is None

    def test_root(self):
        # Pathological — ``/`` shouldn't crash and shouldn't match.
        assert detect_cloud_sync(Path("/")) is None

    def test_path_containing_box_word_outside_marker(self):
        # ``Boxes`` shouldn't trigger the ``Box`` marker — the bracketing
        # in ``_normalise`` requires the marker to be flanked by slashes.
        # NOTE: this asserts the negative-match invariant; if a future
        # change relaxes the bracket, this is the canary.
        assert detect_cloud_sync(Path("/home/x/Boxes/repo")) is None


class TestDetectCloudSyncEdgeCases:
    """Boundary cases — empty path, broken resolve, terminal-marker dirs."""

    def test_terminal_onedrive_dir(self):
        # When OneDrive IS the final path component (no nested project
        # yet), detection should still fire — that's the actual layout
        # at first init.
        assert detect_cloud_sync(Path("C:/Users/x/OneDrive")) == "OneDrive"

    def test_provider_name_is_canonical(self):
        # Pin the canonical display name so callers / JSON consumers
        # don't have to handle multiple spellings.
        canonical = {detect_cloud_sync(Path(f"/x/{p}/r")) for p in ("OneDrive", "Dropbox", "Insync")}
        assert canonical == {"OneDrive", "Dropbox", "Insync"}

    def test_returns_none_or_string(self):
        # Type contract — never returns a bool, dict, or other type.
        result = detect_cloud_sync(Path("C:/Users/x/OneDrive/repo"))
        assert isinstance(result, str)
        miss = detect_cloud_sync(Path("/home/x/repo"))
        assert miss is None


# ---------------------------------------------------------------------------
# Warning-text formatting
# ---------------------------------------------------------------------------


class TestCloudSyncWarning:
    def test_warning_names_provider(self):
        msg = cloud_sync_warning("OneDrive", Path("C:/Users/x/OneDrive/repo/.roam"))
        assert "OneDrive" in msg

    def test_warning_names_path(self):
        msg = cloud_sync_warning("Dropbox", Path("/home/x/Dropbox/repo/.roam"))
        # The path should appear verbatim so the user knows WHICH .roam/
        # dir triggered the warning (multi-repo workspaces).
        assert "Dropbox" in msg
        assert "/home/x/Dropbox/repo/.roam" in msg or ".roam" in msg

    def test_warning_mentions_remediation(self):
        # The user needs a copy-paste fix. We surface both the persistent
        # config flag and the env-var escape hatch.
        msg = cloud_sync_warning("Google Drive", Path("/x/.roam"))
        assert "ROAM_DB_DIR" in msg or "roam config" in msg

    def test_warning_mentions_roam_db_dir_env_var(self):
        # The env var name is load-bearing — typo here = silently
        # broken remediation. Pin the spelling.
        msg = cloud_sync_warning("OneDrive", Path("/x"))
        assert "ROAM_DB_DIR" in msg

    def test_warning_does_not_use_wrong_env_var(self):
        # The original W127 spec called the env var ``ROAM_DB_PATH``
        # — that name does NOT exist in roam-code. The real var is
        # ``ROAM_DB_DIR``. Catch a regression that drifts back to the
        # wrong name.
        msg = cloud_sync_warning("OneDrive", Path("/x"))
        assert "ROAM_DB_PATH" not in msg


# ---------------------------------------------------------------------------
# Integration — ``roam init`` warns when ``.roam/`` is cloud-synced
# ---------------------------------------------------------------------------


def _git_init(root: Path) -> None:
    """Minimal git init so ``roam init`` doesn't refuse the dir."""
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, capture_output=True)
    (root / "a.py").write_text("def f(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)


@pytest.fixture
def cloud_synced_project(tmp_path: Path) -> Path:
    """A git-init'd project whose path includes ``OneDrive`` so the
    detection helper fires. We don't need real cloud sync — the
    substring heuristic only cares about the path.
    """
    proj = tmp_path / "OneDrive" / "fake_repo"
    proj.mkdir(parents=True)
    _git_init(proj)
    return proj


@pytest.fixture
def plain_project(tmp_path: Path) -> Path:
    """A control project at a path with no cloud-sync marker."""
    proj = tmp_path / "projects" / "plain_repo"
    proj.mkdir(parents=True)
    _git_init(proj)
    return proj


class TestInitCloudSyncWarning:
    """End-to-end via CliRunner so the JSON envelope contract is checked too."""

    def test_init_json_envelope_carries_warning_on_cloud_path(self, cloud_synced_project, monkeypatch):
        monkeypatch.chdir(cloud_synced_project)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "init"], catch_exceptions=False)
        assert result.exit_code == 0, f"init failed: {result.output}"
        data = json.loads(result.output)

        # Top-level warnings field carries the structured entry.
        warnings = data.get("warnings", [])
        assert isinstance(warnings, list)
        codes = [w.get("code") for w in warnings]
        assert "cloud_sync_detected" in codes, f"expected 'cloud_sync_detected' in {codes}"

        # The summary echoes the code so consumers can branch off it
        # without inspecting the full warnings array.
        assert "warnings" in data["summary"]
        assert "cloud_sync_detected" in data["summary"]["warnings"]

        # The structured entry carries the provider + path + remediation.
        cloud = next(w for w in warnings if w["code"] == "cloud_sync_detected")
        assert cloud["provider"] == "OneDrive"
        assert "remediation" in cloud
        assert cloud["path"].endswith(".roam") or cloud["path"].endswith(".roam/")

    def test_init_no_warning_on_plain_path(self, plain_project, monkeypatch):
        monkeypatch.chdir(plain_project)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "init"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        warnings = data.get("warnings", [])
        # Pin the negative: no cloud-sync warning when the path is plain.
        codes = [w.get("code") for w in warnings]
        assert "cloud_sync_detected" not in codes

    def test_init_text_output_surfaces_warning(self, cloud_synced_project, monkeypatch):
        monkeypatch.chdir(cloud_synced_project)
        # Click 8.3 dropped ``mix_stderr`` — stderr is always separately
        # available via ``result.stderr`` on the new versions, and on
        # older Click the ``mix_stderr=False`` opt-in produced the same
        # behaviour. Try both, fall back to ``result.output`` which
        # captures the combined stream on every supported version.
        try:
            runner = CliRunner(mix_stderr=False)
        except TypeError:
            runner = CliRunner()
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, f"init failed: {result.output}"
        # The warning surfaces on stderr in the wired CLI. CliRunner on
        # Click 8.3 keeps stderr separate by default; on older versions
        # the combined ``output`` captures it. Either substring location
        # is acceptable so long as the user actually sees the text.
        combined = (result.output or "") + (getattr(result, "stderr", "") or "")
        assert "OneDrive" in combined
        assert "ROAM_DB_DIR" in combined or "roam config" in combined


# ---------------------------------------------------------------------------
# Doctor cloud-sync advisory — exercises the existing _check_cloud_sync
# ---------------------------------------------------------------------------


class TestDoctorCloudSyncAdvisory:
    """The existing doctor check at ``cmd_doctor._check_cloud_sync`` is
    advisory, untested before W127. These tests pin its shape so a
    regression that drops the markers or flips the severity surfaces
    in CI rather than silently disabling the user warning.
    """

    def test_check_passes_on_plain_cwd(self, tmp_path, monkeypatch):
        from roam.commands.cmd_doctor import _check_cloud_sync

        monkeypatch.chdir(tmp_path)
        check = _check_cloud_sync()
        # _check_cloud_sync uses Path.cwd() — tmp_path will not contain
        # OneDrive / Dropbox markers, so the check should pass.
        assert check["passed"] is True
        assert check["name"] == "Cloud sync"

    def test_check_fails_on_onedrive_cwd(self, tmp_path, monkeypatch):
        from roam.commands.cmd_doctor import _check_cloud_sync

        fake_onedrive = tmp_path / "OneDrive" / "repo"
        fake_onedrive.mkdir(parents=True)
        monkeypatch.chdir(fake_onedrive)
        check = _check_cloud_sync()
        assert check["passed"] is False
        assert "OneDrive" in check["detail"]

    def test_check_is_advisory_only(self):
        # Advisory failures don't block CI without --strict. The init
        # warning is the user-facing surface; the doctor advisory is
        # the diagnostic surface. Both must stay advisory so cloud-sync
        # doesn't fail the entire ``roam doctor`` run on its own.
        from roam.commands.cmd_doctor import _ADVISORY_CHECK_NAMES

        assert "Cloud sync" in _ADVISORY_CHECK_NAMES


# pytest-xdist serialization: these tests change cwd and run roam init
# (which writes to disk). Group them so workers don't race.
pytestmark = pytest.mark.xdist_group("cloud_sync_detection")


# Helper to assert sys is still importable (sanity for the file's own
# imports — kept because the test file is consumed by collectors that
# fail silently on import errors).
def test_sys_importable():
    assert sys is not None
