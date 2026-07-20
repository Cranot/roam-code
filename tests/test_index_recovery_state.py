"""Crash-safe index lifecycle and consumer recovery regressions."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import click
import pytest


def _state_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_and_capture_options(tmp_path, monkeypatch, *, force: bool, light: bool) -> tuple[bool, bool]:
    from roam.index import indexer as indexer_mod

    observed: list[tuple[bool, bool]] = []
    indexer = indexer_mod.Indexer(tmp_path)

    def observe(run_force, *, verbose=False, include_excluded=False, light=False):
        observed.append((run_force, light))

    monkeypatch.setattr(indexer, "_do_run", observe)
    monkeypatch.setattr(indexer_mod, "_establish_sqlite_durability", lambda _root: None)
    assert indexer.run(force=force, light=light, quiet=True) is True
    assert len(observed) == 1
    return observed[0]


def test_indexer_marks_successful_run_complete_after_sqlite_durability(tmp_path, monkeypatch):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    events: list[tuple[str, bool | None]] = []
    original_atomic_write = indexer_mod.atomic_write_text

    def observed_atomic_write(path, content, *, encoding="utf-8", durable=False):
        events.append((json.loads(content)["state"], durable))
        original_atomic_write(path, content, encoding=encoding, durable=durable)

    indexer = indexer_mod.Indexer(tmp_path)
    monkeypatch.setattr(indexer_mod, "atomic_write_text", observed_atomic_write)
    monkeypatch.setattr(indexer, "_do_run", lambda *args, **kwargs: events.append(("db_mutation", None)))
    monkeypatch.setattr(
        indexer_mod,
        "_establish_sqlite_durability",
        lambda _root: events.append(("sqlite_durable", None)),
    )

    assert indexer.run(quiet=True) is True
    state = _state_payload(roam_dir / "index.state")
    lock = json.loads((roam_dir / "index.lock").read_text(encoding="utf-8"))
    assert state["state"] == "complete"
    assert state["generation"] == lock["owner"]["generation"]
    assert events == [
        ("in_progress", True),
        ("db_mutation", None),
        ("sqlite_durable", None),
        ("complete", True),
    ]


def test_indexer_leaves_owned_in_progress_marker_after_crash(tmp_path, monkeypatch):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    indexer = indexer_mod.Indexer(tmp_path)

    def crash(*_args, **_kwargs):
        raise RuntimeError("simulated indexer crash")

    monkeypatch.setattr(indexer, "_do_run", crash)
    monkeypatch.setattr(
        indexer_mod,
        "_establish_sqlite_durability",
        lambda _root: pytest.fail("crashed index must not publish SQLite durability"),
    )

    with pytest.raises(RuntimeError, match="simulated indexer crash"):
        indexer.run(quiet=True)
    state = _state_payload(roam_dir / "index.state")
    lock = json.loads((roam_dir / "index.lock").read_text(encoding="utf-8"))
    assert state["state"] == "in_progress"
    assert state["owner"] == lock["owner"]
    assert state["owner"]["pid"] == os.getpid()


def test_simultaneous_interprocess_claim_admits_exactly_one_writer(tmp_path):
    lock_path = tmp_path / ".roam" / "index.lock"
    start = tmp_path / "start"
    release = tmp_path / "release"
    result_paths = [tmp_path / f"result-{number}" for number in range(2)]
    script = """
import sys
import time
from pathlib import Path
from roam.index.indexer import _claim_index_lock, _release_index_lock

lock_path, start_path, release_path, result_path = map(Path, sys.argv[1:])
deadline = time.monotonic() + 30
while not start_path.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
claim = _claim_index_lock(lock_path)
result_path.write_text("claimed" if claim is not None else "blocked", encoding="utf-8")
if claim is not None:
    while not release_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    _release_index_lock(claim)
"""
    workers = [
        subprocess.Popen(  # noqa: S603 -- exact interpreter and literal regression argv
            [sys.executable, "-c", script, str(lock_path), str(start), str(release), str(result_path)],
            cwd=Path(__file__).parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for result_path in result_paths
    ]
    start.touch()
    try:
        deadline = time.monotonic() + 30
        while not all(path.exists() for path in result_paths) and time.monotonic() < deadline:
            time.sleep(0.01)
        claims = [path.read_text(encoding="utf-8") for path in result_paths]
        assert sorted(claims) == ["blocked", "claimed"]
    finally:
        release.touch()
        for worker in workers:
            try:
                _stdout, stderr = worker.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                worker.kill()
                _stdout, stderr = worker.communicate(timeout=10)
            assert worker.returncode == 0, stderr


def test_deleted_lock_with_matching_live_in_progress_owner_is_active(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    owner = indexer_mod._IndexOwner(
        pid=os.getpid(),
        process_start=indexer_mod._process_start_identity(os.getpid()),
        generation="ab" * 32,
    )
    (roam_dir / "index.state").write_text(
        indexer_mod._serialize_index_state("in_progress", owner),
        encoding="utf-8",
    )

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)

    # A second writer also refuses the tempting newly-absent lock pathname;
    # the live state owner remains authoritative.
    assert indexer_mod._claim_index_lock(roam_dir / "index.lock") is None
    with pytest.raises(click.ClickException, match="currently being built"):
        resolve_mod.ensure_index(quiet=True)


def test_abrupt_process_exit_leaves_recoverable_generation(tmp_path):
    from roam.index import indexer as indexer_mod

    script = """
import os
import sys
from pathlib import Path
from roam.index.indexer import Indexer

indexer = Indexer(Path(sys.argv[1]))
indexer._do_run = lambda *_args, **_kwargs: os._exit(73)
indexer.run(quiet=True)
"""
    crashed = subprocess.run(  # noqa: S603 -- exact interpreter and literal regression argv
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert crashed.returncode == 73, crashed.stderr
    state = _state_payload(tmp_path / ".roam" / "index.state")
    assert state["state"] == "in_progress"

    recovered = indexer_mod._claim_index_lock(tmp_path / ".roam" / "index.lock")
    assert recovered is not None
    assert recovered.recovery_required is True
    assert recovered.recovery_reason == "interrupted_generation"
    assert recovered.owner.generation != state["generation"]
    indexer_mod._release_index_lock(recovered)


@pytest.mark.parametrize(
    ("requested_force", "requested_light", "caller"),
    [
        (False, True, "verify_light_refresh"),
        (False, False, "watch_incremental_refresh"),
        (True, True, "explicit_force_light_refresh"),
    ],
)
def test_dead_current_owner_forces_full_rebuild_for_direct_callers(
    tmp_path,
    monkeypatch,
    requested_force,
    requested_light,
    caller,
):
    from roam.index import indexer as indexer_mod

    del caller  # The parameter names the production call shape in test IDs.
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    dead_pid = 2_147_483_646
    prior_owner = indexer_mod._IndexOwner(
        pid=dead_pid,
        process_start="dead-process-generation",
        generation="ef" * 32,
    )
    (roam_dir / "index.lock").write_text(
        indexer_mod._serialize_lock_owner(prior_owner),
        encoding="utf-8",
    )
    (roam_dir / "index.state").write_text(
        indexer_mod._serialize_index_state("in_progress", prior_owner),
        encoding="utf-8",
    )
    original_pid_probe = indexer_mod._pid_is_running
    monkeypatch.setattr(
        indexer_mod,
        "_pid_is_running",
        lambda pid: False if pid == dead_pid else original_pid_probe(pid),
    )

    assert _run_and_capture_options(
        tmp_path,
        monkeypatch,
        force=requested_force,
        light=requested_light,
    ) == (True, False)
    assert _state_payload(roam_dir / "index.state")["state"] == "complete"


def test_pre_marker_stale_numeric_lock_forces_full_direct_rebuild(tmp_path, monkeypatch):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    dead_pid = 2_147_483_645
    (roam_dir / "index.lock").write_text(f"{dead_pid}\n", encoding="utf-8")
    original_pid_probe = indexer_mod._pid_is_running
    monkeypatch.setattr(
        indexer_mod,
        "_pid_is_running",
        lambda pid: False if pid == dead_pid else original_pid_probe(pid),
    )

    assert _run_and_capture_options(tmp_path, monkeypatch, force=False, light=True) == (True, False)
    assert _state_payload(roam_dir / "index.state")["state"] == "complete"


def test_pre_marker_live_numeric_lock_without_state_blocks(tmp_path):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")

    assert indexer_mod._claim_index_lock(roam_dir / "index.lock") is None


@pytest.mark.parametrize(
    ("state_kind", "requested_light"),
    [
        ("complete", False),
        ("complete", True),
        ("legacy_released", False),
        ("legacy_released", True),
        ("legacy_missing", False),
        ("legacy_missing", True),
    ],
)
def test_normal_complete_and_released_legacy_runs_preserve_requested_mode(
    tmp_path,
    monkeypatch,
    state_kind,
    requested_light,
):
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    if state_kind == "complete":
        (roam_dir / "index.state").write_text("complete\n", encoding="utf-8")
        (roam_dir / "index.lock").write_text("999999\n", encoding="utf-8")
    elif state_kind == "legacy_released":
        (roam_dir / "index.lock").write_text("released\n", encoding="utf-8")

    assert _run_and_capture_options(
        tmp_path,
        monkeypatch,
        force=False,
        light=requested_light,
    ) == (False, requested_light)


def test_completed_marker_wins_over_reused_live_pid_lock(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (roam_dir / "index.state").write_text("complete\n", encoding="utf-8")

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        indexer_mod.Indexer,
        "run",
        lambda *_args, **_kwargs: pytest.fail("completed index must not rebuild"),
    )

    resolve_mod.ensure_index(quiet=True)


def test_old_release_cannot_remove_or_unlock_successor_claim(tmp_path):
    from roam.index import indexer as indexer_mod

    lock_path = tmp_path / ".roam" / "index.lock"
    first = indexer_mod._claim_index_lock(lock_path)
    assert first is not None
    indexer_mod._release_index_lock(first)
    (lock_path.parent / "index.state").write_text(
        indexer_mod._serialize_index_state("complete", first.owner),
        encoding="utf-8",
    )
    second = indexer_mod._claim_index_lock(lock_path)
    assert second is not None
    assert second.owner.generation != first.owner.generation

    # A delayed/double release from the old owner is a no-op. The successor's
    # bytes and kernel lock remain authoritative.
    indexer_mod._release_index_lock(first)
    os.lseek(second.descriptor, 0, os.SEEK_SET)
    payload = json.loads(os.read(second.descriptor, 4096).decode("utf-8"))
    assert payload["owner"]["generation"] == second.owner.generation
    assert indexer_mod._claim_index_lock(lock_path) is None
    indexer_mod._release_index_lock(second)


def test_ensure_index_force_recovers_pre_marker_stale_lock(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text("999999\n", encoding="utf-8")
    calls: list[dict] = []

    class FakeIndexer:
        def run(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(indexer_mod, "_pid_is_running", lambda _pid: False)
    monkeypatch.setattr(indexer_mod, "Indexer", FakeIndexer)

    resolve_mod.ensure_index(quiet=True)

    assert calls == [{"force": True, "quiet": True}]


def test_ensure_index_rejects_legacy_active_writer(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text("12345\n", encoding="utf-8")
    (roam_dir / "index.state").write_text("in_progress:12345\n", encoding="utf-8")

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(indexer_mod, "_pid_is_running", lambda _pid: True)

    with pytest.raises(click.ClickException, match="currently being built"):
        resolve_mod.ensure_index(quiet=True)


def test_unprovable_process_start_fails_closed(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    owner = indexer_mod._IndexOwner(pid=12345, process_start=None, generation="cd" * 32)
    (roam_dir / "index.state").write_text(
        indexer_mod._serialize_index_state("in_progress", owner),
        encoding="utf-8",
    )
    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(indexer_mod, "_pid_is_running", lambda _pid: True)
    monkeypatch.setattr(indexer_mod, "_process_start_identity", lambda _pid: None)

    with pytest.raises(click.ClickException, match="currently being built"):
        resolve_mod.ensure_index(quiet=True)


def test_sqlite_durability_checkpoints_wal_and_preserves_rows(tmp_path):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db_path = roam_dir / "index.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence VALUES ('complete')")
    connection.commit()
    connection.close()

    indexer_mod._establish_sqlite_durability(tmp_path)

    with sqlite3.connect(db_path) as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reader.execute("SELECT value FROM evidence").fetchone()[0] == "complete"
    assert not Path(f"{db_path}-wal").exists() or Path(f"{db_path}-wal").stat().st_size == 0
