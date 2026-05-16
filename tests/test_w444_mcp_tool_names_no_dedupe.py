"""W444 — `mcp_tool_names()` must NEVER silently dedupe.

Defense-in-depth against W432 duplicate-registration regressions:

* W432 sealed an actual duplicate-registration bug at the
  ``@_tool(name=...)`` site in ``src/roam/mcp_server.py``.
* W444 audited every caller of the AST-only ``mcp_tool_names()`` helper
  (in ``src/roam/surface_counts.py``) and found that the helper itself
  silently collapsed dupes via ``sorted(set(names))``, hiding any
  future regression of the same shape from README count checks, the
  wrapper-coverage test, and the canonical surface count.
* The helper now raises on duplicates. This test pins both that
  behavior and the no-duplicate runtime invariant.

Pairs with W445's import-time fail-loud at the ``@_tool`` registration
site for full defense-in-depth across the AST surface and the live
runtime registry.

Implementation note: the helper-raises test loads
``src/roam/surface_counts.py`` from THIS worktree by absolute path so
the test exercises the worktree edit even when ``roam`` is
editable-installed from a different sibling worktree / the parent
checkout.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_WORKTREE_ROOT = Path(__file__).resolve().parents[1]
_SURFACE_COUNTS_PATH = _WORKTREE_ROOT / "src" / "roam" / "surface_counts.py"


def _load_worktree_surface_counts():
    """Import this worktree's ``surface_counts.py`` directly by path.

    Bypasses any ``pip install -e .`` editable install pointing at the
    parent checkout. Returns the loaded module.
    """
    spec = importlib.util.spec_from_file_location("_worktree_surface_counts_w444", _SURFACE_COUNTS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mcp_tool_names_has_no_duplicates() -> None:
    """``mcp_tool_names()`` must return distinct entries.

    The helper now raises ``ValueError`` on duplicates rather than
    silently collapsing them via ``set(...)``. The post-condition we
    pin here is the contract every downstream caller now relies on:
    ``len(names) == len(set(names))`` — no hidden collapse.
    """
    sc = _load_worktree_surface_counts()

    names = sc.mcp_tool_names()
    assert len(names) == len(set(names)), (
        f"mcp_tool_names() returned duplicate entries: {[n for n in names if names.count(n) > 1]}"
    )


def test_mcp_tool_names_helper_raises_on_duplicates(monkeypatch, tmp_path) -> None:
    """If the source file ever regrows a duplicate ``@_tool(name=...)``
    decoration, the helper must raise — not silently collapse.

    Synthesises a mini ``mcp_server.py`` with a deliberate duplicate
    then aims ``_repo_root()`` at it. The raised message must name the
    duplicate entries so the failure points an engineer at the offending
    line.
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "src" / "roam").mkdir(parents=True)
    (fake_repo / "src" / "roam" / "cli.py").write_text("_COMMANDS = {}\n", encoding="utf-8")
    (fake_repo / "src" / "roam" / "mcp_server.py").write_text(
        "from typing import Any\n"
        "def _tool(*args: Any, **kwargs: Any):\n"
        "    def deco(fn): return fn\n"
        "    return deco\n"
        '@_tool(name="roam_dupe")\n'
        "def a() -> None: ...\n"
        '@_tool(name="roam_dupe")\n'
        "def b() -> None: ...\n",
        encoding="utf-8",
    )

    sc = _load_worktree_surface_counts()

    monkeypatch.setattr(sc, "_repo_root", lambda: fake_repo)
    with pytest.raises(ValueError, match=r"roam_dupe"):
        sc.mcp_tool_names()


def test_registered_tools_runtime_has_no_duplicates() -> None:
    """Runtime registry ``_REGISTERED_TOOLS`` must contain distinct entries.

    Companion runtime check to the AST-only ``mcp_tool_names()`` check
    above. Skips cleanly when FastMCP isn't installed (the registry
    is empty without the optional extra) so the test still passes
    in minimal environments.
    """
    try:
        from roam.mcp_server import _REGISTERED_TOOLS
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"mcp_server import failed (likely no fastmcp): {exc}")

    if not _REGISTERED_TOOLS:
        pytest.skip("_REGISTERED_TOOLS empty (FastMCP not installed)")

    duplicates = [n for n in _REGISTERED_TOOLS if _REGISTERED_TOOLS.count(n) > 1]
    assert not duplicates, f"_REGISTERED_TOOLS contains duplicate tool names: {sorted(set(duplicates))}"
