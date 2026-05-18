"""W592 — ``gc_expired_leases`` plumbs ``warnings_out`` for swallowed I/O errors.

W589 plumbed ``warnings_out`` through ``release_lease``; W592 closes the
SIBLING silent-fail one floor down: ``gc_expired_leases`` previously
caught ``OSError`` and bare-``continue``'d the loop, leaving stale lease
files on disk with NO signal to the operator. "GC ran clean" was
indistinguishable from "GC ran, hit 3 OSErrors, left 3 stale leases
blocking future claims".

The closed-enum warning kind:

  * ``lease_gc_failed:<lease_id>.json:<exc_class>:<detail>``

The ``continue`` semantic is PRESERVED — best-effort sweep is the
documented contract. The marker just surfaces WHICH expired lease
couldn't be cleaned so the caller can decide whether to retry, alert,
or escalate.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.leases import store as store_mod  # noqa: E402 — for monkeypatching
from roam.leases.store import (  # noqa: E402
    claim_lease,
    gc_expired_leases,
    leases_root,
    read_lease,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lease_project(tmp_path: Path) -> Path:
    """A minimal git-initialised project mirroring ``test_lease_system.py``."""
    proj = tmp_path / "leaseproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


def _backdate_expiry(repo_root: Path, lease_id: str, seconds_ago: int = 60) -> None:
    """Rewrite a lease's ``expires_at`` to seconds_ago in the past.

    Mirrors the helper in ``test_lease_system.py`` so the on-disk shape
    that ``_iter_leases`` returns is one ``gc_expired_leases`` will
    actually try to rewrite.
    """
    lpath = leases_root(repo_root) / f"{lease_id}.json"
    raw = json.loads(lpath.read_text(encoding="utf-8"))
    past = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    raw["expires_at"] = past.isoformat().replace("+00:00", "Z")
    lpath.write_text(json.dumps(raw, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# (1) Happy path — clean GC emits no warnings
# ---------------------------------------------------------------------------


def test_gc_clean_emits_no_warnings(lease_project: Path) -> None:
    """A normal GC pass on expired leases appends nothing to warnings_out.

    Sanity check that the W592 plumbing only fires on degenerate paths.
    """
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    _backdate_expiry(lease_project, claimed.lease_id, seconds_ago=60)

    warnings: list[str] = []
    freed = gc_expired_leases(lease_project, warnings_out=warnings)

    assert claimed.lease_id in freed, "expired lease must have transitioned"
    assert warnings == [], f"clean GC must NOT emit warnings; got {warnings!r}"

    # State on disk transitioned (sanity — same contract as the existing
    # test_lease_system.py::test_expired_lease_no_longer_conflicts_after_gc).
    refreshed = read_lease(lease_project, claimed.lease_id)
    assert refreshed is not None and refreshed.state == "expired"


def test_gc_default_warnings_out_none_silent(lease_project: Path) -> None:
    """Default ``warnings_out=None`` preserves pre-W592 silent behaviour.

    All existing callers (cmd_lease.cleanup_cmd, cmd_lease.list_cmd's
    opportunistic GC, claim_lease's opportunistic GC, test_lease_system
    helpers) call ``gc_expired_leases(repo_root)`` with no kwargs — they
    must NOT regress.
    """
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    _backdate_expiry(lease_project, claimed.lease_id, seconds_ago=60)

    # No exception, no warnings collected (we don't pass a list).
    freed = gc_expired_leases(lease_project)
    assert claimed.lease_id in freed


# ---------------------------------------------------------------------------
# (2) OSError during _write_lease → structured ``lease_gc_failed:<id>:<exc>``
# ---------------------------------------------------------------------------


def test_gc_oserror_during_release_emits_warning(lease_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``_write_lease`` raises ``OSError`` on an expired lease, the
    GC pass emits ``lease_gc_failed:<lease_id>.json:<exc_class>:<detail>``.

    We monkeypatch ``_write_lease`` (rather than ``atomic_write_json``
    one level deeper) because the catch site is inside
    ``gc_expired_leases`` and that's the boundary we're testing.
    """
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    _backdate_expiry(lease_project, claimed.lease_id, seconds_ago=60)

    def _raise_permission_error(lease) -> None:
        raise PermissionError("synthetic-disk-full from W592 test")

    monkeypatch.setattr(store_mod, "_write_lease", _raise_permission_error)

    warnings: list[str] = []
    freed = gc_expired_leases(lease_project, warnings_out=warnings)

    # The failed lease is NOT in the transitioned list (it never wrote).
    assert claimed.lease_id not in freed, f"failed-write lease must not be reported as transitioned; got {freed!r}"
    # Exactly one warning, structured per W592 kind format.
    assert len(warnings) == 1, f"expected one lease_gc_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("lease_gc_failed:"), msg
    assert f"{claimed.lease_id}.json" in msg, msg
    assert "PermissionError" in msg, msg
    assert "synthetic-disk-full from W592 test" in msg, msg


# ---------------------------------------------------------------------------
# (3) ``continue`` semantic preserved — partial failure doesn't abort sweep
# ---------------------------------------------------------------------------


def test_gc_continues_after_failed_lease(lease_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single OSError must NOT abort the whole GC pass — best-effort
    sweep contract is preserved by W592 (the ``continue`` semantic).

    Strategy: claim TWO leases, backdate BOTH, monkeypatch ``_write_lease``
    to raise on the FIRST lease only and succeed on the SECOND. After GC:
      * ``freed`` contains the second lease's id (sweep continued).
      * ``warnings_out`` carries one ``lease_gc_failed`` marker for the
        first lease.
    """
    first, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    second, _ = claim_lease(lease_project, agent="agent-b", subject=["src/bar.py"])
    assert first is not None and second is not None
    _backdate_expiry(lease_project, first.lease_id, seconds_ago=60)
    _backdate_expiry(lease_project, second.lease_id, seconds_ago=60)

    # Capture the real ``_write_lease`` so we can call through on the
    # second lease.
    real_write = store_mod._write_lease

    failures: list[str] = []

    def _selective_raise(lease) -> None:
        if lease.lease_id == first.lease_id:
            failures.append(lease.lease_id)
            raise OSError("synthetic-eio from W592 continue test")
        real_write(lease)

    monkeypatch.setattr(store_mod, "_write_lease", _selective_raise)

    warnings: list[str] = []
    freed = gc_expired_leases(lease_project, warnings_out=warnings)

    # The failing lease did NOT transition; the surviving one did.
    assert first.lease_id not in freed, f"failed-write lease must not be reported as transitioned; got {freed!r}"
    assert second.lease_id in freed, f"sweep must continue after a failure; expected {second.lease_id} in {freed!r}"
    # Exactly one warning naming the failing lease.
    assert len(warnings) == 1, f"expected one lease_gc_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("lease_gc_failed:"), msg
    assert f"{first.lease_id}.json" in msg, msg
    assert "OSError" in msg, msg
    # Sanity — the selective raiser fired exactly once on the first lease.
    assert failures == [first.lease_id], failures
