"""The one namespace both halves of the import-law pipeline speak.

Law **mining** reads *file paths* out of the index
(``src/roam/db/connection.py``). Law **checking** reads *module paths*
out of a diff (``from roam.db.connection import open_db``). Those are
two different namespaces, and under the dominant Python ``src/`` layout
they never intersect — which made every mined import law fire backwards
(W1439): the law said ``tests -> src/roam`` while every conforming
import resolved to ``roam/db``, so the checker flagged all 47 of the
conventional internal imports in the trailing 29 commits of this repo
and cleared ``from src.roam...`` — the one spelling that cannot resolve
at runtime under a src layout.

This module holds the single normalisation both halves call, so the two
namespaces cannot drift apart again. The canonical namespace is the
**import namespace** — the one an ``import`` statement actually names —
because it is the only one observable from a diff, and a diff is all
the checker ever gets.

Source roots
------------
A *source root* is a directory that holds packages but is not itself
importable: the namespace of the files under it starts at its children.
It is detected from repo layout, never hardcoded, so ``src/``,
``lib/``, ``python/`` and a monorepo's ``packages/foo/src`` all fall out
of one rule::

    D is a source root  <=>  some ``D/<child>/__init__.py`` exists
                             AND ``D/__init__.py`` does not exist
                             AND D holds no loose ``*.py`` modules

The third clause is what keeps this repo's ``tests/`` (1418 loose
``test_*.py`` modules sitting alongside a ``_helpers`` package) from
being mistaken for a source root.

Non-Python trees yield no source roots at all — no ``__init__.py``
markers, no evidence — so bucketing there is left exactly as it was.
That is deliberate: silence is the honest answer when the layout
carries no signal, and it keeps this fix from perturbing repos whose
laws were never mis-bucketed.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Optional

__all__ = [
    "bucket_for_file",
    "detect_source_roots",
    "detect_source_roots_from_paths",
    "namespace_contains",
    "normalize_path",
    "strip_source_root",
]

# Directories that never carry a repo's own source layout. Skipped by the
# filesystem probe so a vendored ``node_modules`` tree can't invent source
# roots (and so the probe stays fast on a big checkout).
_SKIP_DIR_NAMES = frozenset(
    {
        ".bzr",
        ".eggs",
        ".git",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".roam",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "bower_components",
        "build",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "target",
        "venv",
        "vendor",
    }
)

# Bounds on the filesystem probe. A source root is a *layout* fact, so it
# lives near the top of a tree; scanning deeper buys nothing and costs a
# stat storm on a monorepo.
_MAX_SCAN_DEPTH = 3
_MAX_SCAN_DIRS = 400


def detect_source_roots_from_paths(paths: Iterable[str]) -> frozenset[str]:
    """Return the source roots implied by a list of repo-relative paths.

    This is the index-side detector: the miner already has every indexed
    path in hand, so it needs no filesystem access. See the module
    docstring for the rule.
    """
    packages: set[str] = set()
    module_dirs: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        norm = raw.replace("\\", "/")
        directory, _, base = norm.rpartition("/")
        if base == "__init__.py":
            packages.add(directory)
        if base.endswith(".py"):
            module_dirs.add(directory)
    return _roots_from_package_layout(packages, module_dirs)


def _roots_from_package_layout(packages: set[str], module_dirs: set[str]) -> frozenset[str]:
    """Apply the source-root rule to a (packages, module_dirs) layout.

    Shared by both detectors so the index-side and filesystem-side
    answers cannot disagree about the same repo.
    """
    roots: set[str] = set()
    for package in packages:
        parent = package.rpartition("/")[0] if "/" in package else ""
        if not parent:
            # The repo root itself. Stripping it is a no-op, and naming it
            # would make ``strip_source_root`` ambiguous.
            continue
        if parent in packages:
            continue  # the parent is itself importable -> not a root
        if parent in module_dirs:
            continue  # loose modules live here -> it is a package dir, not a root
        roots.add(parent)
    return frozenset(roots)


def detect_source_roots(repo_root: Optional[Path]) -> frozenset[str]:
    """Return the source roots of the repo checked out at *repo_root*.

    This is the checker-side detector: ``roam laws check`` runs in CI
    against a plain checkout that may carry no index at all, so the
    layout has to be read off the filesystem. Bounded to
    ``_MAX_SCAN_DEPTH`` levels and ``_MAX_SCAN_DIRS`` directories.

    Returns an empty set — meaning "no normalisation" — when *repo_root*
    is missing, unreadable, or carries no Python package markers.
    """
    if repo_root is None:
        return frozenset()
    try:
        resolved = str(Path(repo_root).resolve())
    except OSError:
        return frozenset()
    return _detect_source_roots_cached(resolved)


@lru_cache(maxsize=64)
def _detect_source_roots_cached(repo_root: str) -> frozenset[str]:
    """Memoised filesystem probe.

    Keyed on the resolved path only: a repo's source layout does not
    change inside one process run, and re-walking it per law per file
    would turn an advisory gate into a stat storm.
    """
    packages: set[str] = set()
    module_dirs: set[str] = set()
    scanned = 0
    queue: list[tuple[str, str, int]] = [(repo_root, "", 0)]
    while queue:
        abs_dir, rel_dir, depth = queue.pop()
        if scanned >= _MAX_SCAN_DIRS:
            break
        scanned += 1
        try:
            entries = list(os.scandir(abs_dir))
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            child_rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            if is_dir:
                if depth + 1 < _MAX_SCAN_DEPTH and entry.name not in _SKIP_DIR_NAMES and not entry.name.startswith("."):
                    queue.append((entry.path, child_rel, depth + 1))
                continue
            if entry.name == "__init__.py":
                packages.add(rel_dir)
            if entry.name.endswith(".py"):
                module_dirs.add(rel_dir)
    return _roots_from_package_layout(packages, module_dirs)


def normalize_path(path: str) -> str:
    """Canonicalise separators and drop a leading ``./`` — nothing else.

    Deliberately not ``lstrip("./")``, which strips *characters* and so
    rewrote ``.github/scripts/x.py`` into ``github/scripts/x.py``. The
    miner bucketed the mangled form while the checker matched the real
    one, so the mined ``.github/scripts`` law could never match the file
    it was mined from — the same two-namespaces defect as W1439, one
    directory over.
    """
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.strip("/")


def strip_source_root(path: str, source_roots: Iterable[str]) -> str:
    """Re-express a repo path in the import namespace.

    ``src/roam/db`` -> ``roam/db`` when ``src`` is a source root, and
    ``roam/db`` -> ``roam/db`` when it is not. A path that *is* a source
    root collapses to ``""`` — it names no importable namespace at all.
    """
    if not path:
        return ""
    norm = normalize_path(path)
    # Longest root first so a nested root (``packages/app/src``) wins over
    # any shorter prefix of it.
    for root in sorted(source_roots, key=len, reverse=True):
        if not root:
            continue
        if norm == root:
            return ""
        if norm.startswith(root + "/"):
            return norm[len(root) + 1 :]
    return norm


def bucket_for_file(path: str, source_roots: Iterable[str] = (), *, depth: int = 2) -> str:
    """Return the import-namespace bucket that owns *path*.

    The bucket is the file's top *depth* directory segments — the
    basename is dropped so every file in a directory shares one bucket —
    re-expressed in the import namespace::

        src/roam/db/connection.py   -> roam        (src is a source root)
        roam/db/connection.py       -> roam/db     (flat layout, unchanged)
        tests/test_foo.py           -> tests
        .github/scripts/gate.py     -> .github/scripts
        app.py                      -> ""          (no directory)

    Bucketing *before* stripping is deliberate: it keeps the bucket
    granularity a repo already had (this repo's ``tests -> src/roam`` law
    stays one law, it just starts naming ``roam``) instead of splitting
    every src-layout repo's laws into per-subpackage shards that no
    longer clear the conformance threshold.
    """
    if not path:
        return ""
    norm = normalize_path(path)
    dirs = norm.split("/")[:-1]
    if not dirs:
        return ""
    return strip_source_root("/".join(dirs[:depth]), source_roots)


def namespace_contains(allowed: str, target: str) -> bool:
    """Return True when *target* names something inside *allowed*.

    Containment rather than equality, because the two sides arrive at
    different depths: a law's bucket is a directory (``roam``) while an
    import names a module (``roam/db/connection``). Segment-aligned, so
    ``roam`` does not swallow ``roamer/x``.
    """
    if not allowed or not target:
        return False
    return target == allowed or target.startswith(allowed + "/")
