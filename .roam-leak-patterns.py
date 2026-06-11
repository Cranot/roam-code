"""Repo-local leak catalogue for the verify ``secrets`` gate.

Thin shim: the single-source pattern catalogue lives in
``scripts/internal_language_patterns.py`` (shared with the CI gate and the
commit/push git hooks). This file is the discovery point the verify
``secrets`` check looks for (``.roam-leak-patterns.py`` at the project
root), so every `roam verify --auto` — including the Claude Code Stop hook
— scans changed files against the same catalogue at edit time, hours
before commit/push/CI would catch it.
"""

from __future__ import annotations

import importlib.util as _ilu
import pathlib as _pl

_spec = _ilu.spec_from_file_location(
    "internal_language_patterns",
    _pl.Path(__file__).parent / "scripts" / "internal_language_patterns.py",
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

FORBIDDEN_PATTERNS = _mod.FORBIDDEN_PATTERNS
should_scan = _mod.should_scan
