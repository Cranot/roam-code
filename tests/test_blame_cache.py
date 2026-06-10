"""Content-hash-keyed blame cache for `roam dead` (2026-06-05). A file's
``git blame`` is invariant while its content is unchanged, so blame it once per
content and reuse — collapsing the repeat-run blame phase from O(files) git
subprocesses to cache reads. These pin the hit / miss / invalidation logic
without needing a real git repo (the git subprocess is stubbed)."""

from __future__ import annotations

from roam.commands import cmd_dead


def test_blame_cache_hit_miss_invalidation(tmp_path, monkeypatch):
    (tmp_path / ".roam").mkdir()

    # Record which files actually get (re)blamed via the git subprocess path.
    reblamed: list[list[str]] = []

    def fake_uncached(_root, paths):
        reblamed.append(list(paths))
        return {p: [{"timestamp": 100 + i, "author": "alice"} for i in range(3)] for p in paths}

    monkeypatch.setattr(cmd_dead, "_blame_uncached", fake_uncached)
    # `conn` is opaque here; the stubbed batched_in treats it AS the content hash
    # so we can drive cache hit vs invalidation by passing a different "conn".
    monkeypatch.setattr(cmd_dead, "batched_in", lambda conn, _sql, paths: [{"path": p, "hash": conn} for p in paths])

    # Run 1 — cold: cache miss → blame + populate.
    r1 = cmd_dead._blame_files_cached("hashA", tmp_path, ["a.py"])
    assert reblamed == [["a.py"]]
    assert len(r1["a.py"]) == 3
    assert r1["a.py"][0] == {"timestamp": 100, "author": "alice"}

    # Run 2 — same content hash: cache HIT, no git subprocess.
    reblamed.clear()
    r2 = cmd_dead._blame_files_cached("hashA", tmp_path, ["a.py"])
    assert reblamed == []  # served from cache
    assert r2["a.py"] == r1["a.py"]  # identical entries

    # Run 3 — content changed (different hash): cache MISS → reblame.
    reblamed.clear()
    cmd_dead._blame_files_cached("hashB", tmp_path, ["a.py"])
    assert reblamed == [["a.py"]]  # invalidated → reblamed


def test_blame_cache_skips_light_index_poison(tmp_path, monkeypatch):
    """The 'roam-light-pending' hash poison must NOT be cached against (it doesn't
    uniquely reflect content) — such files reblame every time."""
    (tmp_path / ".roam").mkdir()
    reblamed: list[list[str]] = []
    monkeypatch.setattr(
        cmd_dead, "_blame_uncached", lambda _r, paths: reblamed.append(list(paths)) or {p: [] for p in paths}
    )
    monkeypatch.setattr(
        cmd_dead, "batched_in", lambda _conn, _sql, paths: [{"path": p, "hash": "roam-light-pending"} for p in paths]
    )

    cmd_dead._blame_files_cached("x", tmp_path, ["a.py"])
    cmd_dead._blame_files_cached("x", tmp_path, ["a.py"])
    assert reblamed == [["a.py"], ["a.py"]]  # poison → never cached
