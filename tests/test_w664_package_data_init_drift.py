"""W664 drift-guard: every package referenced by ``[tool.setuptools.package-data]``
in ``pyproject.toml`` MUST be a real Python package -- i.e., its source
directory MUST contain an ``__init__.py`` file.

Why this lint exists -- the W643 incident.

``src/roam/security/taint_rules/`` shipped without ``__init__.py``. That
silently turned it into a namespace subpackage: Python created an
*implicit* package object whose ``__path__`` was a list rather than a
single concrete filesystem entry. The W643-affected loader resolved
rule YAML files with

    files("roam.security.taint_rules") / "..."

which on a namespace subpackage returns a ``MultiplexedPath``. Wrapping
that in ``as_file()`` extracts the resource into a *temporary*
directory, and the ``with`` block's ``__exit__`` cleans the temp dir up
-- so any caller that captured the path and used it outside the
``with`` block was left pointing at a deleted file. The visible failure
was tests in ``tests/test_taint.py::test_deserialization_pack_loads``
flaking with "file not found" on the resource path the loader had just
returned.

W643 fixed the symptom by adding the missing ``__init__.py`` to
``src/roam/security/taint_rules/``. This wave (W664) adds the
*structural* drift-guard: every directory referenced by
``package-data`` in ``pyproject.toml`` must have an ``__init__.py``,
otherwise the namespace-subpackage / ``MultiplexedPath`` trap can
recur silently for any future contributor adding a new package-data
glob.

Companion test: ``tests/test_package_data_wheel_drift.py`` pins the
*reachability* of specific files via ``importlib.resources`` (W570 /
W610). This W664 test pins the *package-shape* prerequisite that
those reachability assertions silently depend on.
"""

from __future__ import annotations

try:
    import tomllib  # Python 3.11+ stdlib
except ModuleNotFoundError:  # Python 3.10
    # tomli is a stdlib-shaped backport but is NOT a dev-dep of roam-code.
    # Rather than pull in a transitive dep just for one test that runs on
    # 3.11+, skip cleanly on 3.10. The drift-guard still runs on the three
    # newer Python versions in the matrix, so coverage is preserved.
    import pytest

    pytest.skip(
        "tomllib not available on Python <3.11 and tomli is not a dev-dep; drift-guard still runs on 3.11/3.12/3.13",
        allow_module_level=True,
    )
from pathlib import Path

from tests._helpers.repo_root import repo_root


def _load_package_data() -> dict[str, list[str]]:
    """Return ``[tool.setuptools.package-data]`` from ``pyproject.toml``."""
    with open(repo_root() / "pyproject.toml", "rb") as handle:
        config = tomllib.load(handle)
    return config.get("tool", {}).get("setuptools", {}).get("package-data", {})


def _package_dir(root: Path, package_name: str) -> Path:
    """Resolve a dotted package name to its directory under ``src/``."""
    return root / "src" / Path(*package_name.split("."))


def test_all_package_data_dirs_have_init_py() -> None:
    """Every directory in ``package-data`` must contain ``__init__.py``.

    A missing ``__init__.py`` turns the directory into a namespace
    subpackage; ``importlib.resources.files(...)`` then returns a
    ``MultiplexedPath`` whose ``as_file()`` materialises a *temporary*
    extraction directory that is cleaned up on ``with`` exit. Any caller
    that uses the path outside the ``with`` block (or that re-uses a
    captured ``Path`` after the resource manager has closed) dereferences
    a deleted file. This was the W643 incident on
    ``src/roam/security/taint_rules/``.
    """
    root = repo_root()
    package_data = _load_package_data()

    assert package_data, (
        "pyproject.toml [tool.setuptools.package-data] is empty or missing. "
        "If this is intentional, delete or relax this drift-guard; otherwise "
        "the wheel-visibility contract is broken (see W570 / W610)."
    )

    offenders: list[tuple[str, str]] = []
    for package_name in package_data:
        directory = _package_dir(root, package_name)
        init_py = directory / "__init__.py"
        if not init_py.is_file():
            offenders.append((package_name, str(directory)))

    assert offenders == [], (
        "W664: package-data entries without __init__.py -- this turns the "
        "directory into a namespace subpackage and "
        "importlib.resources.as_file() returns a tempdir path that gets "
        "cleaned up on `with` exit (re W643 incident with "
        "security/taint_rules/). Offenders:\n  "
        + "\n  ".join(f"{name} -> {path}" for name, path in offenders)
        + '\nFix: add a one-line `"""Marker module so importlib.resources '
        'resolves a concrete package path."""` __init__.py to each '
        "offending directory."
    )


def test_all_package_data_dirs_exist() -> None:
    """Every directory in ``package-data`` must actually exist on disk.

    Catches the silent-rot case where a refactor moves or deletes the
    directory but leaves the ``package-data`` glob in ``pyproject.toml``.
    The wheel-reachability tests (W570 / W610) would catch this for the
    specific files they pin, but a fresh package-data entry added before
    its reachability guard lands would slip through.
    """
    root = repo_root()
    package_data = _load_package_data()

    missing: list[tuple[str, str]] = []
    for package_name in package_data:
        directory = _package_dir(root, package_name)
        if not directory.is_dir():
            missing.append((package_name, str(directory)))

    assert missing == [], (
        "W664: package-data entries that point at non-existent directories. "
        "Either the directory was renamed/deleted and pyproject.toml was "
        "not updated, or the package-data entry was added before the "
        "directory landed. Offenders:\n  " + "\n  ".join(f"{name} -> {path}" for name, path in missing)
    )
