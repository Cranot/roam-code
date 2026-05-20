"""Anti-leak CI gate.

Walks every tracked source file (src/, tests/, docs/, templates/, root
configs) and fails the suite if any forbidden internal-language pattern
appears. Catches regression on the patterns scrubbed during the
2026-05-07/08 stealth-launch sweeps.

Whitelisted contexts (intentional uses):
- This file itself (the test owns the patterns it forbids).
- ``src/roam/security/aibom_extension.py`` and the ``test_ai_ratio.py`` /
  ``test_v12_2.py`` test fixtures: they describe and detect AI-authorship
  trailers as a product feature, not as session signature.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()


# ---------------------------------------------------------------------------
# Pattern definitions — single source of truth in scripts/.
# ---------------------------------------------------------------------------
#
# The forbidden-pattern catalogue + scan helpers live in the stdlib-only
# ``scripts/internal_language_patterns.py`` module so the commit/push-time
# git hooks (``scripts/scan_internal_language.py``) and this CI gate share
# ONE definition. ``scripts/`` is not an importable package (no __init__.py
# so it stays out of the wheel), so load it by path the same way this suite
# loads ``.github/scripts/*`` modules.


def _load_patterns_module():
    """Load ``scripts/internal_language_patterns.py`` by path (no package)."""
    script = REPO_ROOT / "scripts" / "internal_language_patterns.py"
    spec = importlib.util.spec_from_file_location("internal_language_patterns", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_patterns = _load_patterns_module()

FORBIDDEN_PATTERNS = _patterns.FORBIDDEN_PATTERNS
WHITELIST_FILES = _patterns.WHITELIST_FILES
EXCLUDED_DIRS = _patterns.EXCLUDED_DIRS
SCAN_EXTENSIONS = _patterns.SCAN_EXTENSIONS
should_scan = _patterns.should_scan
scan_text = _patterns.scan_text


def _git_tracked_files() -> list[Path]:
    """Return every file tracked by git, relative to the repo root."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        p = REPO_ROOT / line.strip()
        if not p.exists() or p.is_dir():
            continue
        paths.append(p)
    return paths


def _should_scan(path: Path) -> bool:
    """Delegate to the canonical ``should_scan`` (posix-relpath signature)."""
    return should_scan(path.relative_to(REPO_ROOT).as_posix())


def _scan_for_leaks() -> list[tuple[str, str, int, str]]:
    """Return [(rel_path, pattern_name, line_no, line_text)] for every hit."""
    findings: list[tuple[str, str, int, str]] = []
    for path in _git_tracked_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not should_scan(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, line_no, text_snippet in scan_text(rel, text):
            findings.append((rel, name, line_no, text_snippet))
    return findings


@pytest.mark.smoke
def test_no_internal_language_in_tracked_files() -> None:
    """Fail if any forbidden internal-language pattern lands in a tracked file.

    Run locally before pushing:
        pytest tests/test_no_internal_language.py -q

    If a hit is intentional (e.g. a new AI-detection test fixture), add the
    file to WHITELIST_FILES with a comment explaining why.
    """
    findings = _scan_for_leaks()
    if not findings:
        return

    # Build a human-readable failure message.
    lines = [f"\n{len(findings)} forbidden-pattern hit(s) found in tracked files:\n"]
    by_pattern: dict[str, list[tuple[str, int, str]]] = {}
    for rel, name, line_no, text in findings:
        by_pattern.setdefault(name, []).append((rel, line_no, text))
    for name in sorted(by_pattern):
        hits = by_pattern[name]
        lines.append(f"\n  [{name}] — {len(hits)} hit(s):")
        for rel, line_no, text in hits[:8]:
            lines.append(f"    {rel}:{line_no}  {text}")
        if len(hits) > 8:
            lines.append(f"    ... and {len(hits) - 8} more")
    lines.append("")
    lines.append("Each pattern was deliberately removed during the 2026-05 stealth sweeps.")
    lines.append("If a hit is intentional, edit tests/test_no_internal_language.py:")
    lines.append("  - add the file to WHITELIST_FILES (with a comment explaining why), or")
    lines.append("  - tighten the regex to exclude the legitimate case.")

    pytest.fail("\n".join(lines))
