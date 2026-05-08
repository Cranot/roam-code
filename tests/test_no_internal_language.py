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

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Session-pass numbering ("Pass 79 — deprecated commands")
    ("Pass NN session marker", re.compile(r"\bPass \d+ — ")),
    # Letter-coded session markers ("R5 (2026-05-07) — ", "X14 (2026-05-06):")
    ("Letter-coded session marker", re.compile(r"\b[A-Z]{1,2}\d+ \(\d{4}-\d{2}-\d{2}\)")),
    # "(round 4 #15)" / "(round 3 #2 noted that)"
    ("Round-numbered session marker", re.compile(r"\(round \d+ #\d+")),
    # "Phase 0/1 of v2 monetization plan"
    ("v2 monetization plan reference", re.compile(r"Phase \d+(?:\.\d+)? of (?:the )?v2 monetization plan")),
    # "(per build_priorities.md)" / "(per internal backlog)"
    (
        "Internal-doc cross-reference",
        re.compile(r"\(per (?:build_priorities\.md|dev/CODE-BACKLOG\.md|the v\d+ plan)\)"),
    ),
    # "monetization_v2_subscription_pivot.md" filename references
    ("Monetization v2 strategy filename", re.compile(r"\bmonetization_v2_subscription_pivot\.md\b")),
    # "dogfood notes 2026-05-XX" / "dogfood R17 2026-05-01"
    (
        "Dogfood-notes session marker",
        re.compile(r"\bdogfood notes \d{4}-\d{2}-\d{2}\b|\bdogfood R\d+ \d{4}-\d{2}-\d{2}"),
    ),
    # "(2026-05-XX dogfood)" parentheticals
    ("Dated dogfood parenthetical", re.compile(r"\(\d{4}-\d{2}-\d{2} dogfood\)")),
    # Personal local-machine paths
    ("Windows personal path", re.compile(r"C:\\Users\\Dimitris|D:\\OneDrive - CosmoHac")),
    # Real customer name (the user's day-job employer)
    ("Day-job customer name", re.compile(r"\bunion[- ]web\b|\bSecond-Repo\b", re.IGNORECASE)),
    # Internal session reports
    (
        "Internal session report filename",
        re.compile(
            r"\bOVERNIGHT-\d{4}-\d{2}-\d{2}\.md\b|\bDOGFOOD-RESULTS-\d{4}-\d{2}-\d{2}\.md\b|"
            r"\bREPORT-\d{4}-\d{2}-\d{2}(?:-round\d+)?\.md\b|\bRELEASE-CHECKLIST\.md\b"
        ),
    ),
    # Internal claude-memory paths
    ("Claude-memory path", re.compile(r"~/\.claude/projects/D--OneDrive---CosmoHac-")),
    # Old GitHub Pages docs URL (we migrated to roam-code.com/docs/)
    ("Old GH Pages docs URL", re.compile(r"https?://cranot\.github\.io/roam-code/")),
    # CFO-objection sales-pitch script
    (
        "CFO-objection script",
        re.compile(
            r"signed PO by Friday|highest-conversion buyer-meeting|Article-12-curious leads|"
            r"Hosted-product Phase 0 helper"
        ),
    ),
    # Monetization-v2 phrasing leftovers
    ("Monetization-v2 leftover", re.compile(r"\bv2-monetization\b|\bv2 monetization layer\b")),
    # Greek-vendor exclusion clause (Union conflict-of-interest list)
    ("Greek-vendor exclusion clause", re.compile(r"Greek B2B accounting/ERP/POS|Union exclusion list|Union conflict")),
    # Stripe Atlas / Greek IKE corporate-structure decisions in the wrong place
    (
        "Corporate-structure decision leak",
        re.compile(r"Stripe Atlas Delaware C-corp / Greek freelancer|Greek IKE vs Atlas"),
    ),
]


# Files where these patterns are intentional product behaviour or test
# fixtures FOR the patterns themselves — not real leaks.
WHITELIST_FILES = {
    # This file itself owns the pattern catalogue.
    "tests/test_no_internal_language.py",
    # AI-authorship detector + test fixtures around it.
    "src/roam/security/aibom_extension.py",
    "tests/test_ai_ratio.py",
    "tests/test_v12_2.py",
}


# Glob-style allowlist for paths excluded from the sweep.
EXCLUDED_DIRS = (
    "internal/",
    "reports/",
    "bench-repos/",
    ".roam/",
    "__pycache__",
    ".egg-info",
    "venv/",
    ".venv/",
    "node_modules/",
    "dist/",
    "build/",
    ".git/",
)

# Only check these file extensions.
SCAN_EXTENSIONS = (".py", ".md", ".html", ".yml", ".yaml", ".json", ".txt", ".tmpl", ".css", ".js")


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
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel in WHITELIST_FILES:
        return False
    if not rel.endswith(SCAN_EXTENSIONS):
        return False
    for excluded in EXCLUDED_DIRS:
        if excluded in rel:
            return False
    return True


def _scan_for_leaks() -> list[tuple[str, str, int, str]]:
    """Return [(rel_path, pattern_name, line_no, line_text)] for every hit."""
    findings: list[tuple[str, str, int, str]] = []
    for path in _git_tracked_files():
        if not _should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    findings.append((rel, name, line_no, line.strip()[:200]))
                    break  # one pattern per line is enough; stop checking others
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
