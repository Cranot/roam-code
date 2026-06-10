"""Stable API boundary for the pr-bundle primitives.

Centralizes the small set of helpers other modules need:
  * bundle path resolution        ← cmd_pr_bundle._bundle_path
  * canonical empty-bundle shape  ← cmd_pr_bundle._empty_bundle
  * atomic write                  ← cmd_pr_bundle._atomic_write_bundle
  * load (tolerant)               ← cmd_pr_bundle._load_bundle
  * auto-collect responses        ← cmd_pr_bundle._auto_collect

Plus higher-level discovery helpers used by `roam guard-pr`, `roam
proof-bundle`, and `roam guard-history`:
  * `all_bundle_paths(root)` — every .roam/pr-bundles/*.json sorted by mtime desc
  * `discover_active_bundle(root, bundle_arg)` — auto-pick most-recent OR honor --bundle

These three commands each had their own slightly-different discovery
implementation before this consolidation — drift was an inevitable risk.
Now there's ONE helper.
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.cmd_pr_bundle import (
    _atomic_write_bundle as atomic_write_bundle,
)
from roam.commands.cmd_pr_bundle import (
    _auto_collect as auto_collect,
)
from roam.commands.cmd_pr_bundle import (
    _bundle_path as bundle_path,
)
from roam.commands.cmd_pr_bundle import (
    _empty_bundle as empty_bundle,
)
from roam.commands.cmd_pr_bundle import (
    _load_bundle as load_bundle,
)

__all__ = [
    "atomic_write_bundle",
    "auto_collect",
    "bundle_path",
    "empty_bundle",
    "load_bundle",
    "all_bundle_paths",
    "discover_active_bundle",
    "bundles_dir",
]


def bundles_dir(root: Path) -> Path:
    """Path to .roam/pr-bundles/ under the given repo root."""
    return root / ".roam" / "pr-bundles"


def all_bundle_paths(root: Path) -> list[Path]:
    """Return all bundle JSON files under root, sorted by mtime desc.

    Returns an empty list if the directory doesn't exist.
    """
    d = bundles_dir(root)
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def discover_active_bundle(
    root: Path | None,
    bundle_arg: str | None = None,
) -> Path | None:
    """Resolve the bundle path the user means.

    Resolution order:
      1. If `bundle_arg` is given AND points to an existing file → use it.
      2. If `root` is given, pick the most-recently-modified bundle from
         .roam/pr-bundles/ (the canonical auto-discovery — what `guard-pr`
         and `proof-bundle` both do for the current branch).
      3. None if no bundle found.

    Returns None if nothing usable; callers decide whether to init or fail.
    """
    if bundle_arg:
        p = Path(bundle_arg)
        return p if p.is_file() else None
    if root is None:
        return None
    bundles = all_bundle_paths(root)
    return bundles[0] if bundles else None
