"""Tests for the R21 Multi-Agent Lease System substrate.

Covers (per the R21 spec):

  1.  claim writes a lease file under .roam/leases/<id>.json
  2.  claim returns a lease_id
  3.  claim on overlapping subject is BLOCKED with a conflict record
  4.  claim on disjoint subjects succeeds for both agents
  5.  release marks state=released
  6.  released lease no longer conflicts (re-claim succeeds)
  7.  expired lease no longer conflicts after gc
  8.  list excludes expired by default
  9.  list with include_expired returns all
 10.  lease_id format is stable / regex-matchable
 11.  CLI claim exits 5 on conflict
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    invoke_cli,
    parse_json_output,
)

from roam.leases.store import (  # noqa: E402
    LEASE_ID_RE,
    claim_lease,
    find_conflict,
    gc_expired_leases,
    leases_root,
    list_leases,
    read_lease,
    release_lease,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lease_project(tmp_path):
    """A minimal git-initialised project with no leases yet."""
    proj = tmp_path / "leaseproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. claim creates a lease file
# ---------------------------------------------------------------------------


def test_claim_creates_lease_file(lease_project):
    claimed, conflict = claim_lease(
        lease_project,
        agent="agent-a",
        subject=["src/foo.py"],
    )
    assert conflict is None, "fresh project should have no conflict"
    assert claimed is not None
    lpath = leases_root(lease_project) / f"{claimed.lease_id}.json"
    assert lpath.exists() and lpath.is_file(), "lease JSON not written to disk"

    raw = json.loads(lpath.read_text(encoding="utf-8"))
    assert raw["lease_id"] == claimed.lease_id
    assert raw["agent"] == "agent-a"
    assert raw["subject"] == ["src/foo.py"]
    assert raw["subject_kind"] == "files"
    assert raw["state"] == "active"
    assert raw["acquired_at"]
    assert raw["expires_at"]
    assert raw["ttl_seconds"] > 0


# ---------------------------------------------------------------------------
# 2. claim returns a lease_id
# ---------------------------------------------------------------------------


def test_claim_returns_lease_id(lease_project):
    claimed, conflict = claim_lease(
        lease_project, agent="agent-a", subject=["src/x.py"]
    )
    assert conflict is None
    assert claimed is not None
    assert claimed.lease_id, "lease_id must be non-empty"
    assert LEASE_ID_RE.match(claimed.lease_id), f"lease_id {claimed.lease_id!r} fails regex"


# ---------------------------------------------------------------------------
# 3. claim on overlapping subject is BLOCKED
# ---------------------------------------------------------------------------


def test_claim_conflict_blocked(lease_project):
    a, conflict_a = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert conflict_a is None
    assert a is not None

    b, conflict_b = claim_lease(lease_project, agent="agent-b", subject=["src/foo.py"])
    assert b is None, "agent-b should NOT have received a lease while agent-a holds it"
    assert conflict_b is not None
    assert conflict_b.lease_id == a.lease_id
    assert conflict_b.agent == "agent-a"
    # The second claim must not have written anything to disk.
    leases_on_disk = list(leases_root(lease_project).iterdir())
    assert len(leases_on_disk) == 1, "conflict claim must NOT persist a second lease file"


def test_claim_partial_overlap_blocked(lease_project):
    """Overlapping element anywhere in the subject set is a conflict."""
    a, _ = claim_lease(lease_project, agent="agent-a", subject=["a.py", "b.py"])
    assert a is not None
    b, conflict = claim_lease(lease_project, agent="agent-b", subject=["b.py", "c.py"])
    assert b is None
    assert conflict is not None
    assert conflict.lease_id == a.lease_id


# ---------------------------------------------------------------------------
# 4. disjoint files succeed for both agents
# ---------------------------------------------------------------------------


def test_claim_disjoint_files_succeeds(lease_project):
    a, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    b, _ = claim_lease(lease_project, agent="agent-b", subject=["src/bar.py"])
    assert a is not None and b is not None
    assert a.lease_id != b.lease_id
    # Both files on disk.
    on_disk = sorted(p.name for p in leases_root(lease_project).iterdir())
    assert len(on_disk) == 2


# ---------------------------------------------------------------------------
# 5. release marks state=released
# ---------------------------------------------------------------------------


def test_release_marks_state_released(lease_project):
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    ok = release_lease(lease_project, claimed.lease_id)
    assert ok is True

    refreshed = read_lease(lease_project, claimed.lease_id)
    assert refreshed is not None
    assert refreshed.state == "released"
    # And the on-disk JSON reflects it.
    lpath = leases_root(lease_project) / f"{claimed.lease_id}.json"
    raw = json.loads(lpath.read_text(encoding="utf-8"))
    assert raw["state"] == "released"


def test_release_is_idempotent(lease_project):
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    assert release_lease(lease_project, claimed.lease_id) is True
    # Second release on the same lease must not error and must remain released.
    assert release_lease(lease_project, claimed.lease_id) is True
    again = read_lease(lease_project, claimed.lease_id)
    assert again is not None and again.state == "released"


def test_release_unknown_lease_returns_false(lease_project):
    assert release_lease(lease_project, "lease_20990101_deadbe") is False


# ---------------------------------------------------------------------------
# 6. released lease no longer conflicts; re-claim succeeds
# ---------------------------------------------------------------------------


def test_released_lease_no_longer_conflicts(lease_project):
    first, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert first is not None
    release_lease(lease_project, first.lease_id)

    second, conflict = claim_lease(lease_project, agent="agent-b", subject=["src/foo.py"])
    assert conflict is None, "released lease must NOT block a re-claim"
    assert second is not None
    assert second.lease_id != first.lease_id
    assert second.agent == "agent-b"


# ---------------------------------------------------------------------------
# 7. expired lease no longer conflicts after gc
# ---------------------------------------------------------------------------


def _backdate_expiry(repo_root: Path, lease_id: str, seconds_ago: int = 60) -> None:
    """Rewrite a lease's ``expires_at`` to seconds_ago in the past."""
    lpath = leases_root(repo_root) / f"{lease_id}.json"
    raw = json.loads(lpath.read_text(encoding="utf-8"))
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))
    raw["expires_at"] = past.isoformat().replace("+00:00", "Z")
    lpath.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def test_expired_lease_no_longer_conflicts_after_gc(lease_project):
    first, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert first is not None

    # Backdate it so wall-clock has elapsed.
    _backdate_expiry(lease_project, first.lease_id, seconds_ago=60)

    freed = gc_expired_leases(lease_project)
    assert first.lease_id in freed

    # State on disk transitioned to expired.
    refreshed = read_lease(lease_project, first.lease_id)
    assert refreshed is not None and refreshed.state == "expired"

    # A fresh claim on the same subject succeeds.
    second, conflict = claim_lease(lease_project, agent="agent-b", subject=["src/foo.py"])
    assert conflict is None
    assert second is not None and second.agent == "agent-b"


def test_expired_lease_treated_as_no_conflict_even_before_gc(lease_project):
    """find_conflict respects wall-clock expiry without waiting for gc.

    A stale active lease should NOT block a fresh claim — the substrate
    treats wall-clock-expired leases as freed immediately, GCs in the
    background.
    """
    first, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert first is not None
    _backdate_expiry(lease_project, first.lease_id, seconds_ago=60)

    # Without gc: find_conflict must already say "no conflict".
    assert find_conflict(lease_project, ["src/foo.py"]) is None


# ---------------------------------------------------------------------------
# 8 + 9. list filters expired vs include_expired
# ---------------------------------------------------------------------------


def test_list_excludes_expired_by_default(lease_project):
    active, _ = claim_lease(lease_project, agent="agent-a", subject=["src/active.py"])
    stale, _ = claim_lease(lease_project, agent="agent-b", subject=["src/stale.py"])
    assert active is not None and stale is not None

    _backdate_expiry(lease_project, stale.lease_id, seconds_ago=60)
    gc_expired_leases(lease_project)

    leases = list_leases(lease_project)
    ids = {lease_obj.lease_id for lease_obj in leases}
    assert active.lease_id in ids
    assert stale.lease_id not in ids, "expired lease leaked into default list output"


def test_list_with_include_expired_returns_all(lease_project):
    active, _ = claim_lease(lease_project, agent="agent-a", subject=["src/active.py"])
    stale, _ = claim_lease(lease_project, agent="agent-b", subject=["src/stale.py"])
    assert active is not None and stale is not None
    _backdate_expiry(lease_project, stale.lease_id, seconds_ago=60)
    gc_expired_leases(lease_project)

    leases = list_leases(lease_project, include_expired=True)
    ids = {lease_obj.lease_id for lease_obj in leases}
    assert active.lease_id in ids
    assert stale.lease_id in ids


def test_list_filter_by_agent(lease_project):
    a, _ = claim_lease(lease_project, agent="agent-a", subject=["src/a.py"])
    b, _ = claim_lease(lease_project, agent="agent-b", subject=["src/b.py"])
    assert a is not None and b is not None
    only_a = list_leases(lease_project, agent="agent-a")
    assert [lease_obj.lease_id for lease_obj in only_a] == [a.lease_id]


# ---------------------------------------------------------------------------
# 10. lease_id format
# ---------------------------------------------------------------------------


def test_lease_id_format_is_stable(lease_project):
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    # Format: lease_YYYYMMDD_<hex>
    assert re.match(r"^lease_\d{8}_[0-9a-f]{6,}$", claimed.lease_id)
    # Date part mirrors today (UTC) — flaky on date boundary, so accept ±1d.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d")
    date_part = claimed.lease_id.split("_")[1]
    assert date_part in {today, yesterday, tomorrow}


# ---------------------------------------------------------------------------
# 11. CLI claim exits 5 on conflict
# ---------------------------------------------------------------------------


def test_cli_claim_exits_5_on_conflict(lease_project, cli_runner):
    # First claim via CLI succeeds.
    r1 = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-a", "--files", "src/foo.py"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r1.exit_code == 0, f"first claim should succeed, got exit {r1.exit_code}: {r1.output}"
    env1 = parse_json_output(r1, command="lease-claim")
    assert env1["summary"]["state"] == "claimed"
    first_id = env1["summary"]["lease_id"]
    assert first_id

    # Second claim on overlapping subject must exit 5 with state=conflict.
    r2 = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-b", "--files", "src/foo.py"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r2.exit_code == 5, f"conflict should exit 5, got {r2.exit_code}\n{r2.output}"
    raw = getattr(r2, "stdout", None) or r2.output
    env2 = json.loads(raw)
    assert env2["summary"]["state"] == "conflict"
    assert env2["summary"]["partial_success"] is True
    assert env2["summary"]["claimed"] is False
    conflict_record = env2.get("conflicting_lease")
    assert isinstance(conflict_record, dict)
    assert conflict_record["lease_id"] == first_id
    assert conflict_record["agent"] == "agent-a"


# ---------------------------------------------------------------------------
# Extra CLI surface coverage — list / release / show / gc
# ---------------------------------------------------------------------------


def test_cli_list_empty_returns_clean_envelope(lease_project, cli_runner):
    """Pattern 1: empty stdout never happens; envelope is always emitted."""
    r = invoke_cli(cli_runner, ["lease", "list"], cwd=lease_project, json_mode=True)
    assert r.exit_code == 0
    env = parse_json_output(r, command="lease-list")
    assert env["summary"]["state"] == "no_leases"
    assert env["summary"]["total"] == 0
    assert env["leases"] == []


def test_cli_release_round_trip(lease_project, cli_runner):
    """Claim via CLI, release via CLI, verify state on disk + re-claim works."""
    r1 = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-a", "--files", "src/foo.py"],
        cwd=lease_project,
        json_mode=True,
    )
    env1 = parse_json_output(r1)
    lease_id = env1["summary"]["lease_id"]

    r2 = invoke_cli(
        cli_runner, ["lease", "release", lease_id], cwd=lease_project, json_mode=True
    )
    assert r2.exit_code == 0
    env2 = parse_json_output(r2, command="lease-release")
    assert env2["summary"]["state"] == "released"
    assert env2["summary"]["released"] is True

    # Re-claim succeeds (released lease cleared the slot).
    r3 = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-b", "--files", "src/foo.py"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r3.exit_code == 0
    env3 = parse_json_output(r3)
    assert env3["summary"]["state"] == "claimed"
    assert env3["summary"]["lease_id"] != lease_id


def test_cli_claim_requires_files_or_partition(lease_project, cli_runner):
    """Mutually-exclusive flag validation surfaces a usage error envelope."""
    r = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-a"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r.exit_code == 2
    raw = getattr(r, "stdout", None) or r.output
    env = json.loads(raw)
    assert env["summary"]["state"] == "usage_error"


def test_cli_partition_subject_uses_partition_prefix(lease_project, cli_runner):
    """partition leases serialise as ['partition:<id>'] strings."""
    r = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-a", "--partition", "1"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r.exit_code == 0
    env = parse_json_output(r)
    assert env["summary"]["state"] == "claimed"
    lease_blob = env["lease"]
    assert lease_blob["subject_kind"] == "partition"
    assert lease_blob["subject"] == ["partition:1"]


def test_cli_gc_freed_envelope_shape(lease_project, cli_runner):
    """gc subcommand surfaces freed_ids + numeric verdict."""
    claimed, _ = claim_lease(lease_project, agent="agent-a", subject=["src/foo.py"])
    assert claimed is not None
    _backdate_expiry(lease_project, claimed.lease_id, seconds_ago=60)

    r = invoke_cli(cli_runner, ["lease", "gc"], cwd=lease_project, json_mode=True)
    assert r.exit_code == 0
    env = parse_json_output(r, command="lease-gc")
    assert env["summary"]["gc_freed"] >= 1
    assert claimed.lease_id in env["freed_ids"]


def test_cli_show_unknown_lease_exits_2(lease_project, cli_runner):
    r = invoke_cli(
        cli_runner,
        ["lease", "show", "lease_20990101_deadbe"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r.exit_code == 2
    raw = getattr(r, "stdout", None) or r.output
    env = json.loads(raw)
    assert env["summary"]["state"] == "unknown_lease"


# ---------------------------------------------------------------------------
# Sanity: the substrate does NOT auto-enforce at dispatch.
# ---------------------------------------------------------------------------


def test_claim_does_not_block_unrelated_commands(lease_project, cli_runner):
    """Substrate-only — holding a lease must NOT cause other roam commands
    to fail. R21 promised: 'Substrate only — no auto-enforcement at
    command-dispatch level.'"""
    r1 = invoke_cli(
        cli_runner,
        ["lease", "claim", "--agent", "agent-a", "--files", "src/foo.py"],
        cwd=lease_project,
        json_mode=True,
    )
    assert r1.exit_code == 0

    # surface should still work cleanly with an active lease in place.
    r2 = invoke_cli(cli_runner, ["surface"], cwd=lease_project, json_mode=True)
    assert r2.exit_code == 0, f"unrelated command broken by lease presence: {r2.output[:300]}"
