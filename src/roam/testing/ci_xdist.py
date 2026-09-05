"""CI auto-parallelism pytest plugin: inject ``-n N --dist loadgroup``.

Why this exists: the CI matrix invokes ``pytest tests/ -x -q -m "not slow"``
without explicit worker arguments. Historically that ran sequentially. The
3.10 lane (slowest interpreter: no stdlib tomllib, legacy
pathlib) has outgrown its job timeout three times — 20 -> 30 -> 45 minutes,
killed at ~95% progress on 84343dc4, fdd2d3be, and twice on 70993e9 — while
runners have 4 idle cores and the dev extras already install pytest-xdist.
Parallelism is the durable fix; another timeout bump is the treadmill.

Why a ``-p``-loaded plugin and not the alternatives:

- Historically workflow updates were unavailable without a token with the
  ``workflow`` scope. The workflow now selects four workers through
  ``ROAM_XDIST_WORKERS``; the plugin's environment-independent default is two.
- ``addopts = "-n auto"`` directly in pyproject crashes any environment
  that has pytest but not pytest-xdist ("unrecognized arguments").
- ``pytest_load_initial_conftests`` in a conftest is never called — pytest
  honors that hook only for early-loaded plugins, which a ``-p`` module is.

Activation guards (all must hold):

- ``CI`` env var truthy (GitHub Actions sets ``CI=true``); local runs are
  never touched.
- ``ROAM_AUTO_XDIST`` is not ``"0"`` (explicit opt-out).
- pytest-xdist is importable.
- No explicit ``-n`` / ``--numprocesses`` / ``--dist`` / ``-p no:xdist``
  already on the command line — user intent always wins.

``--dist loadgroup`` (not plain ``load``) so ``xdist_group`` markers keep
serializing their groups (timing-sensitive perf tests rely on it).
"""

from __future__ import annotations

import os


def xdist_args_to_inject(args, env, xdist_available):
    """Return the extra pytest args to prepend, or [] when injection must
    not happen. Pure function so tests can pin the whole guard matrix."""
    if not xdist_available:
        return []
    if not env.get("CI"):
        return []
    if env.get("ROAM_AUTO_XDIST", "1") == "0":
        return []
    for i, a in enumerate(args):
        if a == "-n" or (a.startswith("-n") and len(a) > 2 and a[2:].strip().isalnum()):
            return []
        if a == "--numprocesses" or a.startswith("--numprocesses="):
            return []
        if a == "--dist" or a.startswith("--dist="):
            return []
        if a == "-p" and i + 1 < len(args) and args[i + 1] == "no:xdist":
            return []
        if a == "-pno:xdist":
            return []
    # Default 2, overridable via ``ROAM_XDIST_WORKERS``.
    #
    # This default was originally a SIGBUS mitigation: ``-n auto`` spawns one
    # worker per core and the aggregate memory pressure was blamed for
    # "Fatal Python error: Bus error" during parallel test-module import. Both
    # actual root causes were later found and fixed structurally (see
    # ``_suppress_bytecode_writes_under_ci_xdist``), and a 2/3/4-worker
    # comparison on CI produced no Bus error at any count, so the number is no
    # longer load-bearing for SIGBUS.
    #
    # It stays conservative because of a different failure mode that generalises
    # to any suite like this one: tests that shell out to a CLI which lazily
    # builds a shared on-disk artifact (an index, a cache, a compiled bundle)
    # will each race to build it and collide on its lock, and more workers only
    # widen that window. Build the artifact once, up front, before raising this.
    workers = env.get("ROAM_XDIST_WORKERS", "2")
    return ["-n", workers, "--dist", "loadgroup"]


def _suppress_bytecode_writes_under_ci_xdist(env) -> bool:
    """Under xdist on CI, the pytest assertion-rewrite ``.pyc`` cache is shared
    across workers. Concurrent write + mmap of the SAME rewritten pyc races: one
    worker truncates/rewrites the file while another has it mmapped, so the
    mapped pages vanish and accessing them raises SIGBUS ("Fatal Python error:
    Bus error") inside ``_pytest/assertion/rewrite.py`` ``exec_module`` — on a
    different test module each run, crashing a worker and reddening the lane.

    Suppressing bytecode writes makes every worker rewrite in-memory (assertion
    introspection is fully preserved — only the on-disk cache is skipped), so
    there is no shared file to race on. Applied to controller AND workers: it is
    keyed on the environment (CI + xdist), not on whether THIS process injects
    ``-n`` (workers inherit ``-n`` from the controller and skip injection).
    """
    xdist_available = _xdist_importable()
    if not xdist_available:
        return False
    if not env.get("CI"):
        return False
    if env.get("ROAM_AUTO_XDIST", "1") == "0":
        return False
    import sys

    sys.dont_write_bytecode = True
    # THE primary SIGBUS fix: roam sets ``PRAGMA mmap_size=1 GB`` per SQLite
    # connection. Under xdist each worker mmaps that per connection, and N
    # workers x 1 GB x several index DBs blows past a memory-limited runner ->
    # "Fatal Python error: Bus error" mid-test (concentrated on index-heavy
    # tests like test_math). Disable SQLite mmap on CI+xdist (plain read/write
    # I/O — slightly slower, no giant mapping to exhaust). setdefault so an
    # explicit override still wins.
    env.setdefault("ROAM_SQLITE_MMAP_SIZE", "0")
    # Residual pressure after the mmap fix (crashes 4-5 -> 0-2 but not zero):
    # bound the OTHER per-connection memory PRAGMAs the same way. 64 MB page
    # cache x many open connections x N workers, plus temp_store=MEMORY
    # sort/b-tree spill, still spike a memory-limited runner. 8 MB cache +
    # FILE temp spill are correctness-neutral (slower, bounded).
    env.setdefault("ROAM_SQLITE_CACHE_KB", "8000")
    env.setdefault("ROAM_SQLITE_TEMP_STORE", "FILE")
    return True


def _xdist_importable() -> bool:
    try:
        import xdist  # noqa: F401

        return True
    except ImportError:
        return False


def pytest_load_initial_conftests(early_config, parser, args):
    xdist_available = _xdist_importable()
    _suppress_bytecode_writes_under_ci_xdist(os.environ)
    args[:] = xdist_args_to_inject(args, os.environ, xdist_available) + args
