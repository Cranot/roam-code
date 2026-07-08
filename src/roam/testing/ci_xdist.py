"""CI auto-parallelism pytest plugin: inject ``-n auto --dist loadgroup``.

Why this exists: the CI matrix job runs ``pytest tests/ -x -q -m "not slow"``
sequentially. The 3.10 lane (slowest interpreter: no stdlib tomllib, legacy
pathlib) has outgrown its job timeout three times — 20 -> 30 -> 45 minutes,
killed at ~95% progress on 84343dc4, fdd2d3be, and twice on 70993e9 — while
runners have 4 idle cores and the dev extras already install pytest-xdist.
Parallelism is the durable fix; another timeout bump is the treadmill.

Why a ``-p``-loaded plugin and not the alternatives:

- The workflow file cannot carry the change here: pushes touching
  ``.github/workflows/`` need a token with the ``workflow`` scope.
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
    # ``-n auto`` spawns one worker per core; on CI runners each worker loads
    # the tree-sitter native grammars (28 languages) + per-worker SQLite temp
    # files, and the aggregate mmap / ``/dev/shm`` pressure triggers SIGBUS
    # ("Fatal Python error: Bus error") during parallel test-module import,
    # crashing workers and reddening the whole test lane. Cap the worker count
    # to keep memory bounded (override via ``ROAM_XDIST_WORKERS``).
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
