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
    return ["-n", "auto", "--dist", "loadgroup"]


def pytest_load_initial_conftests(early_config, parser, args):
    try:
        import xdist  # noqa: F401

        xdist_available = True
    except ImportError:
        xdist_available = False
    args[:] = xdist_args_to_inject(args, os.environ, xdist_available) + args
