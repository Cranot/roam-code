"""W593 -- ``list_permits`` + ``load_permits_from_disk`` plumb ``warnings_out``.

W589/W592 closed the lease silent-fail cluster (release / GC). W593 closes
the SIBLING cluster in the permits substrate -- three silent-error sites
previously returned ``[]`` without disclosing WHY the reader degraded:

  W593a. ``list_permits`` iterdir OSError -> returned ``[]`` silently
         (no signal to distinguish "no permits issued" from "permits
         dir is unreadable / permission-denied").
  W593b. ``list_permits`` per-file JSON-decode failure -> bare
         ``continue`` swallowed the offending file with no signal.
  W593c. ``load_permits_from_disk`` iterdir OSError -> returned ``[]``
         silently. W383 already plumbed ``warnings_out`` for per-permit
         failures; only the iterdir-level failure was still mute.

The ``list_permits`` warning vocabulary is a closed enum (mirrors the
W589 release-site shape):

  * ``permits_root_unreadable:<exc_class>:<detail>``
  * ``permit_corrupt:<filename>.json:<exc_class>``

The ``load_permits_from_disk`` warning rides the existing W383 channel
with one new closed-enum marker:

  * ``permits_dir_unreadable:<exc_class>:<detail>``

The ``[]`` return semantic is PRESERVED on every site -- the empty-return
is the caller contract. The per-file ``continue`` is also preserved so
one corrupt permit can't poison the whole iteration (best-effort).

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589 / W592).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.permits.store import (  # noqa: E402
    PermitRequest,
    issue_permit,
    list_permits,
    load_permits_from_disk,
    permits_root,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def permit_project(tmp_path: Path) -> Path:
    """A minimal git-initialised project mirroring ``test_cmd_permit_persist``."""
    proj = tmp_path / "w593_permitproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


def _issue_one(repo_root: Path, *, scope: str = "w593-scope", issued_to: str = "agent:w593") -> str:
    """Issue + persist one permit; return its permit_id."""
    record, _path = issue_permit(
        repo_root,
        PermitRequest(
            scope=scope,
            expires_at="2099-01-01T00:00:00Z",
            issued_to=issued_to,
            issued_by="human:w593-operator",
            reason="w593 sibling-silent-fail test",
        ),
    )
    return record.permit_id


# ===========================================================================
# W593a -- ``list_permits`` iterdir OSError
# ===========================================================================


class TestW593a_ListPermitsIterdirOSError:
    """``list_permits`` discloses iterdir failures via structured marker."""

    def test_list_permits_clean_emits_no_warning(self, permit_project: Path) -> None:
        """A normal list pass on a clean permits dir appends nothing.

        Sanity check that the W593a plumbing only fires on degenerate paths.
        """
        _issue_one(permit_project)
        warnings: list[str] = []
        records = list_permits(permit_project, warnings_out=warnings)
        assert len(records) == 1, records
        assert warnings == [], f"clean list_permits must NOT emit warnings; got {warnings!r}"

    def test_list_permits_iterdir_oserror_emits_structured_marker(
        self, permit_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``permits_root_unreadable:<exc_class>:<detail>`` on iterdir failure.

        The permits dir must EXIST before the iterdir call -- otherwise the
        ``not root.exists()`` short-circuit fires and we never reach the
        try/except. Issue a permit first to ensure the dir exists.
        """
        _issue_one(permit_project)
        proot = permits_root(permit_project)
        assert proot.exists(), "fixture must produce a permits dir"

        # Monkeypatch Path.iterdir to raise for THIS specific path. We use
        # an instance-aware wrapper so unrelated iterdir calls (e.g. on a
        # different tmp dir) keep working.
        target = proot.resolve()
        original_iterdir = Path.iterdir

        def _raising_iterdir(self):
            if self.resolve() == target:
                raise PermissionError("synthetic-EACCES from W593a test")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raising_iterdir)

        warnings: list[str] = []
        records = list_permits(permit_project, warnings_out=warnings)

        assert records == [], f"iterdir failure must preserve [] return; got {records!r}"
        assert len(warnings) == 1, f"expected one permits_root_unreadable warning; got {len(warnings)}: {warnings!r}"
        msg = warnings[0]
        assert msg.startswith("permits_root_unreadable:"), msg
        assert "PermissionError" in msg, msg
        assert "synthetic-EACCES from W593a test" in msg, msg

    def test_list_permits_default_none_silent(self, permit_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default ``warnings_out=None`` preserves pre-W593a silent behaviour.

        Existing callers (``cmd_permit list_cmd``, ``cmd_pr_bundle``) call
        ``list_permits(repo_root)`` with no kwargs -- they must NOT regress.
        """
        _issue_one(permit_project)
        proot = permits_root(permit_project)
        target = proot.resolve()
        original_iterdir = Path.iterdir

        def _raising_iterdir(self):
            if self.resolve() == target:
                raise PermissionError("synthetic-EACCES from W593a default-none test")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raising_iterdir)

        # No exception, no warnings collected (we don't pass a list).
        records = list_permits(permit_project)
        assert records == []


# ===========================================================================
# W593b -- ``list_permits`` per-file JSON-decode silent ``continue``
# ===========================================================================


class TestW593b_ListPermitsCorruptJson:
    """Malformed per-file JSON emits ``permit_corrupt:<file>:<exc>``."""

    def test_list_permits_corrupt_json_emits_per_file_marker(self, permit_project: Path) -> None:
        """A malformed permit file emits the marker NAMING the file."""
        proot = permits_root(permit_project)
        proot.mkdir(parents=True, exist_ok=True)
        bad = proot / "permit_20990101_bad001.json"
        bad.write_text("{not valid json", encoding="utf-8")

        warnings: list[str] = []
        records = list_permits(permit_project, warnings_out=warnings)

        # No records returned (the only file was corrupt).
        assert records == [], records
        assert len(warnings) == 1, f"expected one permit_corrupt warning; got {len(warnings)}: {warnings!r}"
        msg = warnings[0]
        assert msg.startswith("permit_corrupt:"), msg
        # Marker NAMES the file so an operator can locate the bad permit.
        assert "permit_20990101_bad001.json" in msg, msg
        assert "JSONDecodeError" in msg, msg

    def test_list_permits_continues_after_corrupt(self, permit_project: Path) -> None:
        """A clean permit AFTER a corrupt one still surfaces (continue semantic).

        Strategy: issue a clean permit FIRST, then drop a corrupt file in
        the same dir. The clean permit must still appear in the returned
        list, and the corrupt file must emit one structured marker.
        """
        clean_id = _issue_one(permit_project, scope="clean-after-corrupt")

        proot = permits_root(permit_project)
        bad = proot / "permit_20990101_bad002.json"
        bad.write_text('{"not": "a complete permit"', encoding="utf-8")

        warnings: list[str] = []
        records = list_permits(permit_project, warnings_out=warnings)

        # Clean permit still surfaces despite the corrupt sibling.
        assert len(records) == 1, f"continue semantic must keep the clean permit; got {records!r}"
        assert records[0].permit_id == clean_id
        # Exactly one warning, naming the corrupt file.
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        assert msg.startswith("permit_corrupt:"), msg
        assert "permit_20990101_bad002.json" in msg, msg


# ===========================================================================
# W593c -- ``load_permits_from_disk`` iterdir OSError
# ===========================================================================


class TestW593c_LoadPermitsFromDiskIterdirOSError:
    """``load_permits_from_disk`` discloses iterdir failures via W383's channel."""

    def test_load_permits_from_disk_iterdir_oserror_emits_marker(
        self, permit_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The iterdir failure routes through the EXISTING W383 channel.

        No new ``warnings_out`` kwarg was added -- ``load_permits_from_disk``
        already accepts it for per-permit failures (W379/W380/W382). This
        test pins that the iterdir-level failure now flows through the same
        bucket with a closed-enum ``permits_dir_unreadable:`` marker.
        """
        _issue_one(permit_project)
        proot = permits_root(permit_project)
        target = proot.resolve()
        original_iterdir = Path.iterdir

        def _raising_iterdir(self):
            if self.resolve() == target:
                raise PermissionError("synthetic-EACCES from W593c test")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raising_iterdir)

        warnings: list[str] = []
        result = load_permits_from_disk(permit_project, warnings_out=warnings)

        assert result == [], f"iterdir failure must preserve [] return; got {result!r}"
        assert len(warnings) == 1, f"expected one permits_dir_unreadable warning; got {len(warnings)}: {warnings!r}"
        msg = warnings[0]
        assert msg.startswith("permits_dir_unreadable:"), msg
        assert "PermissionError" in msg, msg
        assert "synthetic-EACCES from W593c test" in msg, msg

    def test_load_permits_from_disk_returns_empty_on_iterdir_failure(
        self, permit_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ``[]``-return semantic on iterdir failure is preserved.

        Existing callers (``cmd_pr_bundle``, ``cmd_pr_replay``) rely on
        the bare-signature ``[]`` shape so a degraded permits dir doesn't
        crash the bundle/replay pipeline. ``warnings_out=None`` (default)
        must also preserve the silent contract.
        """
        _issue_one(permit_project)
        proot = permits_root(permit_project)
        target = proot.resolve()
        original_iterdir = Path.iterdir

        def _raising_iterdir(self):
            if self.resolve() == target:
                raise OSError("synthetic-EIO from W593c default-none test")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raising_iterdir)

        # No exception, no warnings collected (we don't pass a list).
        result = load_permits_from_disk(permit_project)
        assert result == []
