"""W1437 — ``surface_counts`` helpers resolve ``cli.py`` / ``mcp_server.py``
in BOTH editable-install and wheel-installed layouts.

Why this regression test exists
--------------------------------

The W420 cascade migrated ``cmd_surface`` / ``cmd_compatibility`` /
``cmd_doctor`` / ``cmd_capabilities`` from runtime ``roam.cli._COMMANDS``
to AST-parsed ``roam.surface_counts.cli_commands()`` to make headline
counts plugin-loading-invariant. The original implementation of
``cli_commands()`` (and the related ``mcp_*`` helpers) walked
``Path(__file__).parents`` looking for ``src/roam/cli.py`` — a path that
DOES NOT EXIST in a wheel install (``pip install roam-code`` lands
``cli.py`` directly under ``site-packages/roam/`` with no ``src/``
prefix). Result: every PyPI user got

    Error: Could not locate repository root from surface_counts.py

on ``roam --json surface`` and ``roam --json capabilities``. The
W420 fix worked in editable installs (where ``src/roam/cli.py`` exists
on disk) and silently broke in production.

The fix routes lookups through :func:`roam.surface_counts._package_file`,
which uses ``importlib.resources.files("roam")`` — the same wheel-safe
pattern already used by W554 / W664 / W668 (``cmd_evidence_oscal``,
``cmd_taint``, ``roam.templates.audit_report``).

This test pins the contract: the AST helpers must resolve their source
files via the installed-package surface, not via a hard-coded ``src/``
walk. The smoke check is to drive the public ``cli_commands()`` and
``mcp_tool_names()`` paths and verify they (a) succeed and (b) produce
non-empty results that match the live source under any install layout.

Calibration evidence
--------------------

This test is one of two instruments per CP44/CP47 ("validate shape AND
executability"):

- Shape instrument (this file): proves ``_package_file()`` resolves to
  an extant file under any layout.
- Executability instrument (out of band): the post-fix wheel smoke
  invocation ``roam --json surface | jq .summary.command_count`` must
  return the canonical 241 from a fresh ``pip install`` into an
  isolated venv. That smoke is the user-visible verification; this
  file is the in-repo regression guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.surface_counts import (
    _package_file,
    cli_commands,
    mcp_preset_counts,
    mcp_surface_counts,
    mcp_tool_names,
)


def test_package_file_resolves_cli_py():
    """``_package_file('cli.py')`` returns an extant path."""
    p = _package_file("cli.py")
    assert isinstance(p, Path)
    assert p.exists(), f"_package_file('cli.py') resolved to {p!r} which does not exist"
    assert p.name == "cli.py"


def test_package_file_resolves_mcp_server_py():
    """``_package_file('mcp_server.py')`` returns an extant path."""
    p = _package_file("mcp_server.py")
    assert isinstance(p, Path)
    assert p.exists(), f"_package_file('mcp_server.py') resolved to {p!r} which does not exist"
    assert p.name == "mcp_server.py"


def test_package_file_does_not_require_src_prefix():
    """The resolution path MUST be importlib.resources-based, not a ``src/`` walk.

    Wheel installs land ``cli.py`` at ``site-packages/roam/cli.py`` — there is
    no ``src`` segment anywhere in the path. The resolver must succeed in
    that layout. We can't easily fake the wheel layout in-process, but we
    CAN assert the resolution doesn't go through an unconditional ``src``
    segment: the resolved path's parent should be the ``roam`` package
    directory directly. (In editable installs the parent chain happens to
    include ``src``; in wheel installs it does not. Either is fine — what
    matters is that the resolver uses the installed-package surface.)
    """
    cli_p = _package_file("cli.py").resolve()
    # The parent of the resolved file must be the ``roam`` package
    # directory itself — this holds for BOTH layouts.
    assert cli_p.parent.name == "roam", (
        f"_package_file('cli.py') resolved to {cli_p!r}; "
        f"expected parent dir to be 'roam' (the installed package). "
        f"If parent is something else, the resolver may have walked the "
        f"wrong way and the wheel install will break."
    )


def test_package_file_raises_on_unknown_file():
    """Unknown filenames raise loudly rather than returning a phantom path."""
    import pytest

    with pytest.raises(RuntimeError, match="Could not locate"):
        _package_file("nonexistent-sentinel-file-w1437.py")


def test_cli_commands_works_via_package_file():
    """``cli_commands()`` consumes ``_package_file`` and returns a non-empty dict.

    The headline regression: pre-fix this raised
    ``RuntimeError: Could not locate repository root`` in any wheel install.
    """
    commands = cli_commands()
    assert isinstance(commands, dict)
    assert len(commands) > 0
    # Sanity floor: roam ships well over 100 commands. If this drops we have
    # bigger problems than wheel layout.
    assert len(commands) >= 100


def test_mcp_tool_names_works_via_package_file():
    """``mcp_tool_names()`` consumes ``_package_file`` and returns a non-empty list."""
    tools = mcp_tool_names()
    assert isinstance(tools, list)
    assert len(tools) > 0
    # Sanity floor matches the existing surface_counts test.
    assert len(tools) >= 103


def test_mcp_surface_counts_works_via_package_file():
    """``mcp_surface_counts()`` consumes ``_package_file`` and computes counts.

    Drives both the decorator scan and ``_CORE_TOOLS`` extraction — two
    distinct AST-load paths through ``_package_file``.
    """
    counts = mcp_surface_counts()
    assert counts["registered_tools"] > 0
    assert counts["core_tools"] > 0


def test_mcp_preset_counts_works_via_package_file():
    """``mcp_preset_counts()`` consumes ``_package_file`` and emits a non-empty dict."""
    presets = mcp_preset_counts()
    assert isinstance(presets, dict)
    assert len(presets) > 0
    # ``full`` is the canonical "no filter" sentinel — must always be present.
    assert "full" in presets


def test_repo_root_is_dev_tree_only_helper():
    """``_repo_root`` is preserved for dev-tree-only callers.

    Production helpers (``cli_commands``, ``mcp_tool_names`` and friends)
    MUST route through ``_package_file``. ``_repo_root`` stays as the
    fallback inside ``_package_file`` and for source-tree dev scripts
    (``cmd_compatibility._default_baseline_path``, etc.) that genuinely
    need the project root.

    Pin: when called from inside the source tree, it still resolves to
    a directory containing ``src/roam/cli.py`` (the historical
    contract).
    """
    from roam.surface_counts import _repo_root

    root = _repo_root()
    assert (root / "src" / "roam" / "cli.py").exists()
