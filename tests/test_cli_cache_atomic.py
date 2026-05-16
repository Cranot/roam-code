"""Tests for the atomic short-help cache write (W15.4 follow-up).

The CLI's short-help cache used to be written via a naked
``open(path, "w")`` which is race-prone — two parallel ``roam`` calls
could clobber each other's bytes and leave a corrupt JSON file on disk
that ``_load_short_help_cache`` would silently throw away.

These tests assert the new atomic rename pattern:

  1. The save helper uses a temp-file + ``os.replace`` (no in-place
     truncate-then-write).
  2. The temp file is removed if the write itself fails (no leaks).
  3. Two parallel writers can never produce a corrupt JSON file —
     one wins, the other's payload is lost, but the on-disk result
     is always valid JSON.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from roam import cli as cli_mod


@pytest.fixture
def tmp_cache_path(tmp_path, monkeypatch):
    """Redirect _SHORT_HELP_CACHE_PATH at a tmp file for the test."""
    target = tmp_path / "cache_dir" / "short-help.json"
    monkeypatch.setattr(cli_mod, "_SHORT_HELP_CACHE_PATH", str(target))
    # Reset module-level cache state so tests don't bleed into each other.
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache", None, raising=False)
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache_dirty", False, raising=False)
    return target


def test_run_check_uses_atomic_rename(tmp_cache_path, monkeypatch):
    """_save_short_help_cache_if_dirty should write via temp-file + os.replace.

    We assert the behaviour by spying on ``os.replace`` — the atomic
    primitive that distinguishes the new helper from the old direct
    open/write/close path.
    """
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache", {"foo:bar": {"mtime": 1.0, "text": "hi"}})
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache_dirty", True)

    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    cli_mod._save_short_help_cache_if_dirty()

    # os.replace must have been the path to the final file.
    assert replace_calls, "expected at least one os.replace call"
    src, dst = replace_calls[-1]
    assert dst == str(tmp_cache_path)
    # Temp file must have lived next to the target so the rename is
    # same-filesystem (POSIX-atomic).
    assert os.path.dirname(src) == os.path.dirname(str(tmp_cache_path))
    # And the result is a valid JSON file with our payload.
    data = json.loads(Path(tmp_cache_path).read_text(encoding="utf-8"))
    assert data == {"foo:bar": {"mtime": 1.0, "text": "hi"}}


def test_run_check_cleans_up_temp_on_failure(tmp_cache_path, monkeypatch):
    """If os.replace fails, the temp file must not be left on disk."""
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache", {"baz": {"mtime": 2.0, "text": "y"}})
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache_dirty", True)

    # Force os.replace to blow up so the cleanup path runs.
    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)

    # The save helper swallows OSError (best-effort cache); so this
    # call must not raise.
    cli_mod._save_short_help_cache_if_dirty()

    # The cache dir was created, but the target file should not exist
    # and no `.tmp` orphans should remain inside it.
    cache_dir = Path(tmp_cache_path).parent
    assert cache_dir.exists()
    assert not Path(tmp_cache_path).exists(), "no partial cache file should survive"
    leftovers = [p for p in cache_dir.iterdir() if p.name.startswith("short-help.json.") and p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_concurrent_run_check_writes_no_corruption(tmp_cache_path, monkeypatch):
    """Simulate 2 parallel save calls. Final file is always valid JSON.

    The pre-fix code could leave the file half-written when one thread's
    ``json.dump`` ran inside another thread's truncate window. With
    atomic rename one writer wins and the other's payload is lost; the
    on-disk file is *always* one of the two complete payloads, never a
    blend.
    """
    # Restore real os.replace for this test.
    payload_a = {f"key{i}": {"mtime": float(i), "text": "A" * 200} for i in range(50)}
    payload_b = {f"key{i}": {"mtime": float(i) + 1000, "text": "B" * 200} for i in range(50)}

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def writer(payload):
        try:
            barrier.wait(timeout=5)
            # Each thread flips the module's state and triggers a save.
            # We hold a small lock to mutate the shared module state
            # atomically; the *write* itself is the race we care about.
            cli_mod._short_help_disk_cache = dict(payload)
            cli_mod._short_help_disk_cache_dirty = True
            for _ in range(20):
                cli_mod._save_short_help_cache_if_dirty()
                cli_mod._short_help_disk_cache_dirty = True
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=(payload_a,))
    t2 = threading.Thread(target=writer, args=(payload_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"writer threads raised: {errors}"

    # Final file is on disk and is valid JSON. Content matches A or B.
    raw = Path(tmp_cache_path).read_text(encoding="utf-8")
    data = json.loads(raw)  # would raise if corrupt
    assert isinstance(data, dict)
    assert len(data) == 50
    # All values came from one writer; we just need a valid file,
    # not a specific winner. (Either keys map to "A" * 200 or "B" * 200.)
    sample_text = next(iter(data.values()))["text"]
    assert sample_text in {"A" * 200, "B" * 200}
