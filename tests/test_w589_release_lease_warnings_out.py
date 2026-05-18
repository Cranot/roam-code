"""W589 — ``release_lease`` plumbs ``warnings_out`` for silent-error sites.

W425 added ``warnings_out`` to ``list_leases``; W448 mirrored it on
``read_lease``. W589 closes the sibling gap on ``release_lease`` — three
silent-error sites previously returned ``True`` / ``False`` without
disclosing WHY the operation degenerated:

  1. ``release_lease`` called on a non-existent lease → returned False
     silently (no signal to distinguish "agent typo on lease_id" from
     "race with another release").
  2. ``release_lease`` called twice on the same lease → returned True
     silently (idempotence is correct behavior, but the caller has no
     way to learn "this was already a no-op").
  3. ``release_lease`` called on a corrupt lease file → returned False
     silently (treated as "lease not found" when it's actually "lease
     file exists but unparseable" — a more urgent operational signal).

The release-site warning vocabulary is a closed enum:

  * ``lease_not_found:<path>``
  * ``lease_already_released:<lease_id>``
  * ``lease_corrupt:<path>:<exc_class>``

Each kind is a deliberate string-prefix so a caller can grep / filter
without parsing free-form text. The format diverges from W448's
``read_lease`` warnings (free-form ``"lease file X.json skipped: ..."``)
because release-site semantics are different — see the docstring on
``release_lease`` for the rationale.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.leases.store import (  # noqa: E402
    claim_lease,
    leases_root,
    release_lease,
)


@pytest.fixture
def lease_project(tmp_path: Path) -> Path:
    """A minimal git-initialised project mirroring ``test_lease_system.py``."""
    proj = tmp_path / "leaseproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# (1) Missing-lease silent return → structured ``lease_not_found:<path>``
# ---------------------------------------------------------------------------


def test_release_missing_lease_warns(lease_project: Path) -> None:
    """Release on a non-existent lease emits ``lease_not_found:<path>``."""
    warnings: list[str] = []
    result = release_lease(
        lease_project,
        "lease_20990101_deadbe",
        warnings_out=warnings,
    )
    assert result is False, "missing lease must still return False (existing contract)"
    assert len(warnings) == 1, f"expected exactly one warning on missing lease; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("lease_not_found:"), msg
    assert "lease_20990101_deadbe.json" in msg, msg


def test_release_missing_lease_default_no_warnings_arg_silent(lease_project: Path) -> None:
    """Default ``warnings_out=None`` preserves pre-W589 silent behaviour.

    Existing callers (``test_release_unknown_lease_returns_false``) rely on
    the bare-signature return-False shape; they must not regress.
    """
    # No exception, no warnings collected (we don't pass a list).
    result = release_lease(lease_project, "lease_20990101_deadbe")
    assert result is False


# ---------------------------------------------------------------------------
# (2) Already-released double-release → structured ``lease_already_released:<id>``
# ---------------------------------------------------------------------------


def test_release_already_released_warns(lease_project: Path) -> None:
    """Releasing twice emits ``lease_already_released:<lease_id>`` on the second call.

    The first release stays clean (the lease was active) — only the
    redundant second call is the operational anomaly worth disclosing.
    Return value stays True both times to preserve the W589 docstring
    idempotence contract.
    """
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None

    first_warnings: list[str] = []
    assert release_lease(lease_project, claimed.lease_id, warnings_out=first_warnings) is True
    assert first_warnings == [], f"first (clean) release must NOT emit warnings; got {first_warnings!r}"

    second_warnings: list[str] = []
    assert release_lease(lease_project, claimed.lease_id, warnings_out=second_warnings) is True
    assert len(second_warnings) == 1, (
        f"expected one warning on double-release; got {len(second_warnings)}: {second_warnings!r}"
    )
    msg = second_warnings[0]
    assert msg.startswith("lease_already_released:"), msg
    assert claimed.lease_id in msg, msg


# ---------------------------------------------------------------------------
# (3) Corrupt lease file → structured ``lease_corrupt:<path>:<exc_class>``
# ---------------------------------------------------------------------------


def test_release_corrupt_lease_warns_malformed_json(lease_project: Path) -> None:
    """Malformed JSON emits ``lease_corrupt:<path>:JSONDecodeError``."""
    leases_dir = leases_root(lease_project)
    leases_dir.mkdir(parents=True, exist_ok=True)
    lease_id = "lease_20260514_bad001"
    (leases_dir / f"{lease_id}.json").write_text("{not valid json", encoding="utf-8")

    warnings: list[str] = []
    result = release_lease(lease_project, lease_id, warnings_out=warnings)
    assert result is False, "corrupt lease must return False (existing contract)"
    assert len(warnings) == 1, f"expected one corrupt-lease warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("lease_corrupt:"), msg
    assert f"{lease_id}.json" in msg, msg
    assert "JSONDecodeError" in msg, msg


def test_release_corrupt_lease_warns_non_dict_root(lease_project: Path) -> None:
    """Top-level JSON list emits ``lease_corrupt:<path>:NotAJsonObject``."""
    leases_dir = leases_root(lease_project)
    leases_dir.mkdir(parents=True, exist_ok=True)
    lease_id = "lease_20260514_bad002"
    (leases_dir / f"{lease_id}.json").write_text("[1, 2, 3]", encoding="utf-8")

    warnings: list[str] = []
    result = release_lease(lease_project, lease_id, warnings_out=warnings)
    assert result is False
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("lease_corrupt:"), msg
    assert f"{lease_id}.json" in msg, msg
    assert "NotAJsonObject" in msg, msg


def test_release_corrupt_lease_warns_schema_invalid(lease_project: Path) -> None:
    """Dict missing the required ``agent`` field emits ``lease_corrupt:<path>:SchemaInvalid``."""
    leases_dir = leases_root(lease_project)
    leases_dir.mkdir(parents=True, exist_ok=True)
    lease_id = "lease_20260514_bad003"
    (leases_dir / f"{lease_id}.json").write_text(
        json.dumps(
            {
                "lease_id": lease_id,
                # NOTE: ``agent`` deliberately missing → schema-invalid.
                "subject_kind": "files",
                "subject": ["src/foo.py"],
                "ttl_seconds": 3600,
                "acquired_at": "2026-05-14T00:00:00Z",
                "expires_at": "2030-01-01T00:00:00Z",
                "state": "active",
            }
        ),
        encoding="utf-8",
    )

    warnings: list[str] = []
    result = release_lease(lease_project, lease_id, warnings_out=warnings)
    assert result is False
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("lease_corrupt:"), msg
    assert f"{lease_id}.json" in msg, msg
    assert "SchemaInvalid" in msg, msg


# ---------------------------------------------------------------------------
# (4) Happy path — clean release on an active lease emits no warnings
# ---------------------------------------------------------------------------


def test_release_clean_emits_no_warning(lease_project: Path) -> None:
    """A normal release on an active lease appends nothing to ``warnings_out``.

    Sanity check that the W589 plumbing only fires on degenerate paths.
    """
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None

    warnings: list[str] = []
    assert release_lease(lease_project, claimed.lease_id, warnings_out=warnings) is True
    assert warnings == [], f"clean release must NOT emit warnings; got {warnings!r}"
