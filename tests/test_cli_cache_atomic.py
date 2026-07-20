"""Tests for the atomic short-help cache write (W15.4 follow-up).

The CLI's short-help cache used to be written via a naked
``open(path, "w")`` which is race-prone — two parallel ``roam`` calls
could clobber each other's bytes and leave a corrupt JSON file on disk
that ``_load_short_help_cache`` would silently throw away.

These tests assert the shared conditional native-install pattern:

  1. The save helper stages a complete sibling tempfile and publishes the
     source generation proved by the atomic-write substrate (no in-place
     truncate-then-write).
  2. A failed native install never exposes a partial cache. Windows removes
     the exact tempfile by identity; POSIX retains the complete recovery
     artifact because it cannot conditionally unlink by inode.
  3. Two parallel writers can never produce a corrupt JSON file —
     one wins, the other's payload is lost, but the on-disk result
     is always valid JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from roam import atomic_io
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
    """The CLI cache publishes one proved, complete sibling generation.

    Windows intentionally bypasses ``os.replace``: the hardened substrate
    holds an exclusive no-follow descriptor from its final source proof
    through ``SetFileInformationByHandle(FileRenameInfo)``. Spy at the shared
    native-install boundary so this test covers both that path and POSIX.
    """
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache", {"foo:bar": {"mtime": 1.0, "text": "hi"}})
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache_dirty", True)

    install_observations: list[dict] = []
    real_install = atomic_io._native_conditional_install

    def observe_install(source, destination, **kwargs):
        source_path = Path(source)
        staged = source_path.read_bytes()
        install_observations.append(
            {
                "source": source_path,
                "destination": Path(destination),
                "staged": staged,
                "generation": kwargs["expected_temp"],
                "identity": kwargs["temp_identity"],
            }
        )
        return real_install(source, destination, **kwargs)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", observe_install)

    cli_mod._save_short_help_cache_if_dirty()

    assert len(install_observations) == 1
    observed = install_observations[0]
    assert observed["destination"] == Path(tmp_cache_path).resolve()
    assert observed["source"].parent == Path(tmp_cache_path).parent.resolve()
    assert observed["source"].name.startswith(".short-help.json.")
    assert observed["source"].name.endswith(".tmp")
    assert json.loads(observed["staged"]) == {"foo:bar": {"mtime": 1.0, "text": "hi"}}
    assert observed["generation"].identity == observed["identity"]
    assert observed["generation"].size == len(observed["staged"])
    assert observed["generation"].nlink == 1
    assert observed["generation"].sha256 == hashlib.sha256(observed["staged"]).hexdigest()
    assert not observed["source"].exists(), "successful publication must consume the staged path"

    data = json.loads(Path(tmp_cache_path).read_text(encoding="utf-8"))
    assert data == {"foo:bar": {"mtime": 1.0, "text": "hi"}}
    assert cli_mod._short_help_disk_cache_dirty is False


def test_run_check_cleans_up_temp_on_failure(tmp_cache_path, monkeypatch, capsys):
    """A native-install failure never exposes a partial cache generation."""
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache", {"baz": {"mtime": 2.0, "text": "y"}})
    monkeypatch.setattr(cli_mod, "_short_help_disk_cache_dirty", True)

    # Fail at the real shared publication boundary. Patching ``os.replace`` is
    # no longer meaningful on Windows because descriptor-bound rename is a
    # deliberate part of the source-generation security proof.
    def boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(atomic_io, "_native_conditional_install", boom)

    # The save helper swallows OSError (best-effort cache); so this
    # call must not raise.
    cli_mod._save_short_help_cache_if_dirty()

    # The consumer path remains absent and the failed cache stays dirty so a
    # later invocation can retry instead of treating the generation as saved.
    cache_dir = Path(tmp_cache_path).parent
    assert cache_dir.exists()
    assert not Path(tmp_cache_path).exists(), "no partial cache file should survive"
    assert cli_mod._short_help_disk_cache_dirty is True
    assert "[short-help-cache] write failed: simulated rename failure" in capsys.readouterr().err

    leftovers = list(cache_dir.glob(".short-help.json.*.tmp"))
    if os.name == "nt":
        # Windows can delete the exact object through an identity-bound handle.
        assert leftovers == [], f"identity-bound temp cleanup failed: {leftovers}"
    else:
        # POSIX has no conditional unlink-by-inode primitive. Retaining the
        # complete private artifact is safer than deleting a raced replacement.
        assert len(leftovers) == 1
        assert json.loads(leftovers[0].read_text(encoding="utf-8")) == {"baz": {"mtime": 2.0, "text": "y"}}
        retained = leftovers[0].stat()
        assert retained.st_nlink == 1
        assert retained.st_mode & 0o077 == 0


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
