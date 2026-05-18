"""W595 -- ``read_permit`` plumbs ``warnings_out`` for silent-error sites.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster (release / GC). W593 closed the permits ``list_permits``
+ ``load_permits_from_disk`` silent-fail sites. W595 closes the LAST
remaining silent-None site in the permits read-path: ``read_permit``
previously swallowed ``(OSError, json.JSONDecodeError)`` with a bare
``return None`` and converted "not on disk" / "file unreadable" /
"malformed JSON" / "schema-invalid" into one indistinguishable None.

The ``read_permit`` warning vocabulary is a closed enum (mirrors the
W589 release-site shape + W593b's per-file ``permit_corrupt:`` prefix
so a caller threading the same bucket sees a uniform marker
vocabulary across every permits read site):

  * ``permit_not_found:<permit_id>.json``
  * ``permit_read_failed:<permit_id>.json:<exc_class>:<detail>``
  * ``permit_corrupt:<permit_id>.json:JSONDecodeError``
  * ``permit_corrupt:<permit_id>.json:NotAJsonObject``
  * ``permit_corrupt:<permit_id>.json:SchemaInvalid``

The ``None`` return on every drop path is PRESERVED -- the None-return
is the caller contract. ``warnings_out=None`` (default) preserves the
pre-W595 silent-drop behaviour.

Marker shape divergence from W448:
``read_lease`` deliberately does NOT warn on the missing-file path
("caller asked for a specific id that simply isn't on disk"). The W595
``read_permit`` plumb DOES warn there, by task spec -- a missing
permit during a ``permit show`` lookup is an operational anomaly worth
surfacing (caller typo / GC race / wrong repo root). The marker
prefix shape mirrors W589's ``lease_not_found:`` and W593b's
``permit_corrupt:`` -- consistent with the rest of the permits substrate.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589 / W592 / W593).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.permits.store import (  # noqa: E402
    issue_permit,
    permits_root,
    read_permit,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def permit_project(tmp_path: Path) -> Path:
    """A minimal git-initialised project mirroring ``test_w593_permits_silent_fails``."""
    proj = tmp_path / "w595_permitproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


def _issue_one(repo_root: Path, *, scope: str = "w595-scope", issued_to: str = "agent:w595") -> str:
    """Issue + persist one permit; return its permit_id."""
    record, _path = issue_permit(
        repo_root,
        scope=scope,
        expires_at="2099-01-01T00:00:00Z",
        issued_to=issued_to,
        issued_by="human:w595-operator",
        reason="w595 read_permit silent-None test",
    )
    return record.permit_id


# ===========================================================================
# (1) Happy path -- clean read on an existing permit emits no warnings
# ===========================================================================


def test_read_clean_emits_no_warning(permit_project: Path) -> None:
    """A normal read on a clean permit appends nothing to ``warnings_out``.

    Sanity check that the W595 plumbing only fires on degenerate paths.
    """
    permit_id = _issue_one(permit_project)

    warnings: list[str] = []
    rec = read_permit(permit_project, permit_id, warnings_out=warnings)

    assert rec is not None, "clean read must return a PermitRecord"
    assert rec.permit_id == permit_id
    assert warnings == [], f"clean read_permit must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (2) Missing permit -- ``permit_not_found:<permit_id>.json``
# ===========================================================================


def test_read_missing_permit_emits_not_found_marker(permit_project: Path) -> None:
    """Read on a non-existent permit emits ``permit_not_found:<file>``.

    Marker shape mirrors W589's ``lease_not_found:`` -- the missing-file
    path is an operational anomaly worth disclosing on a ``permit show``
    lookup (caller typo / GC race / wrong repo root).
    """
    warnings: list[str] = []
    result = read_permit(
        permit_project,
        "permit_20990101_deadbe",
        warnings_out=warnings,
    )

    assert result is None, "missing permit must still return None (existing contract)"
    assert len(warnings) == 1, f"expected exactly one warning on missing permit; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("permit_not_found:"), msg
    assert "permit_20990101_deadbe.json" in msg, msg


# ===========================================================================
# (3) Corrupt JSON -- ``permit_corrupt:<file>:JSONDecodeError``
# ===========================================================================


def test_read_corrupt_json_emits_corrupt_marker(permit_project: Path) -> None:
    """Malformed JSON emits ``permit_corrupt:<file>:JSONDecodeError``.

    Marker prefix matches W593b's ``permit_corrupt:`` shape so a caller
    grepping permits-substrate warnings sees one uniform vocabulary.
    """
    proot = permits_root(permit_project)
    proot.mkdir(parents=True, exist_ok=True)
    permit_id = "permit_20990101_bad001"
    (proot / f"{permit_id}.json").write_text("{not valid json", encoding="utf-8")

    warnings: list[str] = []
    result = read_permit(permit_project, permit_id, warnings_out=warnings)

    assert result is None, "corrupt permit must return None (existing contract)"
    assert len(warnings) == 1, f"expected one corrupt-permit warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("permit_corrupt:"), msg
    assert f"{permit_id}.json" in msg, msg
    assert "JSONDecodeError" in msg, msg


# ===========================================================================
# (4) Other OSError -- ``permit_read_failed:<file>:<exc_class>:<detail>``
# ===========================================================================


def test_read_other_oserror_emits_read_failed_marker(permit_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-decode OSError on read_text emits ``permit_read_failed:<file>:<exc>:<detail>``.

    Monkeypatches ``Path.read_text`` to raise ``PermissionError`` for the
    specific permit path. The file EXISTS on disk (so we get past the
    ``not path.exists()`` short-circuit) but read fails.
    """
    permit_id = _issue_one(permit_project)
    permit_path = (permits_root(permit_project) / f"{permit_id}.json").resolve()
    assert permit_path.exists(), "fixture must produce a permit file"

    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        if self.resolve() == permit_path:
            raise PermissionError("synthetic-EACCES from W595 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = read_permit(permit_project, permit_id, warnings_out=warnings)

    assert result is None, "read_text failure must preserve None return; got non-None"
    assert len(warnings) == 1, f"expected one permit_read_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("permit_read_failed:"), msg
    assert f"{permit_id}.json" in msg, msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W595 test" in msg, msg


# ===========================================================================
# (5) Schema-invalid -- ``permit_corrupt:<file>:SchemaInvalid``
# ===========================================================================


def test_read_schema_invalid_emits_corrupt_marker(permit_project: Path) -> None:
    """A dict missing the required ``scope`` field emits ``permit_corrupt:<file>:SchemaInvalid``.

    Mirrors W589's corrupt-by-validator marker shape so the four corrupt
    sub-cases (JSONDecodeError / NotAJsonObject / SchemaInvalid + read
    failure) are distinguishable from one bucket.
    """
    proot = permits_root(permit_project)
    proot.mkdir(parents=True, exist_ok=True)
    permit_id = "permit_20990101_bad002"
    (proot / f"{permit_id}.json").write_text(
        json.dumps(
            {
                "permit_id": permit_id,
                # NOTE: ``scope`` deliberately missing -> schema-invalid.
                "expires_at": "2099-01-01T00:00:00Z",
                "issued_to": "agent:w595",
                "issued_at": "2026-05-17T00:00:00Z",
                "issued_by": "human:w595",
            }
        ),
        encoding="utf-8",
    )

    warnings: list[str] = []
    result = read_permit(permit_project, permit_id, warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("permit_corrupt:"), msg
    assert f"{permit_id}.json" in msg, msg
    assert "SchemaInvalid" in msg, msg


def test_read_non_dict_top_level_emits_corrupt_marker(permit_project: Path) -> None:
    """Top-level JSON array emits ``permit_corrupt:<file>:NotAJsonObject``.

    Belt-and-braces -- the fourth corrupt sub-case (top-level value is
    valid JSON but not a dict) gets its own structured marker.
    """
    proot = permits_root(permit_project)
    proot.mkdir(parents=True, exist_ok=True)
    permit_id = "permit_20990101_bad003"
    (proot / f"{permit_id}.json").write_text("[1, 2, 3]", encoding="utf-8")

    warnings: list[str] = []
    result = read_permit(permit_project, permit_id, warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("permit_corrupt:"), msg
    assert f"{permit_id}.json" in msg, msg
    assert "NotAJsonObject" in msg, msg


# ===========================================================================
# (6) Default ``warnings_out=None`` preserves pre-W595 silent behaviour
# ===========================================================================


def test_read_default_none_no_crash(permit_project: Path) -> None:
    """Default ``warnings_out=None`` returns None cleanly, no crash, no warnings.

    Existing callers (``cmd_permit show`` at cmd_permit.py:750) call
    ``read_permit(root, permit_id)`` with no kwargs -- they must NOT
    regress on any of the four failure modes covered by the W595 plumb.
    """
    # (a) Missing permit -- the most common silent-None path.
    result = read_permit(permit_project, "permit_20990101_deadbe")
    assert result is None

    # (b) Corrupt JSON -- the second silent-None path.
    proot = permits_root(permit_project)
    proot.mkdir(parents=True, exist_ok=True)
    bad_id = "permit_20990101_bad004"
    (proot / f"{bad_id}.json").write_text("{not valid json", encoding="utf-8")
    result = read_permit(permit_project, bad_id)
    assert result is None

    # (c) Schema-invalid -- the third silent-None path.
    bad_schema_id = "permit_20990101_bad005"
    (proot / f"{bad_schema_id}.json").write_text(
        json.dumps({"permit_id": bad_schema_id}),  # missing required fields
        encoding="utf-8",
    )
    result = read_permit(permit_project, bad_schema_id)
    assert result is None

    # (d) Happy path with default-None still returns the record.
    permit_id = _issue_one(permit_project, scope="default-none-happy")
    rec = read_permit(permit_project, permit_id)
    assert rec is not None
    assert rec.permit_id == permit_id
