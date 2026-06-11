"""Exemplar corpus for the anti-leak gate — patterns must KEEP catching these.

Every entry is a synthetic-but-realistic line modeled on a leak class that
actually reached (or nearly reached) the public repo. The catalogue lives in
``scripts/internal_language_patterns.py``; this suite is the ratchet that
stops a future "tidy-up" of a regex from silently weakening it. When adding
a new forbidden pattern, add at least one exemplar here.

This file is whitelisted in the catalogue (it owns leak-shaped strings by
design), same as the CI gate test itself.
"""

from __future__ import annotations

import importlib.util

import pytest

from tests._helpers.repo_root import repo_root


def _patterns():
    script = repo_root() / "scripts" / "internal_language_patterns.py"
    spec = importlib.util.spec_from_file_location("internal_language_patterns", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_m = _patterns()

# (expected_pattern_name, exemplar_line)
EXEMPLARS = [
    # Dated dogfood markers — every adjacency variant seen in real comments.
    ("Dated dogfood parenthetical", "# prefetched_facts bug (2026-06-02 dogfood). An L1 envelope is empty"),
    ("Dated dogfood parenthetical", "# Concept-search guard (2026-06-05 dogfood: PSR-12 repo re-flagged)"),
    ("Dated dogfood parenthetical", "# R.6 (dogfood 2026-05-01) — rule-YAML demotion"),
    ("Dated dogfood parenthetical", "# dogfood 2026-05-04 — test-file demotion"),
    ("Dated dogfood parenthetical", "noise is pure. 2026-06-07 dogfood: django/pytest packs"),
    # Letter-coded session markers.
    ("Letter-coded session marker", "# W31 (2026-05-30): Phase A --explain smoke discovered cycles"),
    # Dated ALLCAPS memo filenames, bare or path-prefixed, with slug tails.
    ("Dated internal memo filename", "documented in SYNTHESIS-2026-05-12.md Pattern 4"),
    ("Dated internal memo filename", "see SESSION-2026-06-09-classifier-waves-stdout-race.md for the log"),
    ("Dated internal memo filename", "per ARCHITECTURE-EVIDENCE-COMPILER-2026-05-13.md the bundle"),
    # Claude-memory slug references.
    ("Claude-memory slug reference", "Per the pivot memo (`project_pivot_to_roam_guard`), this is the gap"),
    ("Claude-memory slug reference", "exhausted. See [[project_v04_envelope_regression]]."),
    ("Claude-memory slug reference", "anchor: [[feedback_measurement_variance_protocol]]"),
    # Host-platform name.
    ("Host-platform name", "wired the compiler into Stoa via the verify hook"),
    # VPS absolute paths.
    ("VPS absolute path", '"command": "/root/repos/roam-code/.venv/bin/roam"'),
    ("VPS absolute path", "results live at /root/apps/someproject/bench/cells.tsv"),
    # Day-job customer name.
    ("Day-job customer name", "reproduced on the union-web frontend"),
    # Greek domain terms.
    ("Greek domain term — kiniseis", '"find where useKiniseisBalance is",'),
    # Internal planning cross-reference.
    (
        "Internal/ folder revenue-ops or planning cross-reference",
        "Workstream #5 in internal/planning/NEXT-PRIORITIES.md asks for a",
    ),
]


@pytest.mark.parametrize("expected,line", EXEMPLARS, ids=[f"{n}:{i}" for i, (n, _) in enumerate(EXEMPLARS)])
def test_exemplar_is_caught(expected: str, line: str) -> None:
    hits = _m.scan_text("synthetic.py", line)
    assert hits, f"exemplar no longer caught by any pattern: {line!r}"
    # First-matching-pattern-wins mirrors the scanner; the expected class
    # must be the one that fires (or at least fire among the candidates).
    names = {h[0] for h in hits}
    assert expected in names, f"caught by {names}, expected {expected!r}: {line!r}"


def test_benign_lines_pass() -> None:
    """Plain dates, the word dogfood alone, and code identifiers never trip."""
    benign = [
        "Released 2026-06-10 with attestations.",
        "The dogfood corpus drives command quality.",
        "internal/dogfood/README.md is the entry point",
        "project_root = repo_root()",
        "raise ProjectRootNotFound(project_root_lookup_failed)",
        "CHANGELOG.md follows Keep a Changelog.",
        "stoarrr is not a word but stoas (lowercase plural) is fine",
    ]
    for line in benign:
        hits = _m.scan_text("synthetic.py", line)
        assert not hits, f"benign line tripped {[h[0] for h in hits]}: {line!r}"


def test_whitelist_contains_this_file() -> None:
    assert "tests/test_leak_gate_exemplars.py" in _m.WHITELIST_FILES
