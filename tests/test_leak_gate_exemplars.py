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

import hashlib
import importlib.util
import re

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
    # VPS absolute paths. The first two are the directories the rule used to
    # enumerate by name; the rest are the reason it no longer does. A rule
    # that lists the box directories somebody already thought of catches only
    # the past — a benchmark write-up naming a different top-level directory
    # passed the gate cleanly and published the box's layout.
    ("VPS absolute path", '"command": "/root/repos/roam-code/.venv/bin/roam"'),
    ("VPS absolute path", "results live at /root/apps/someproject/bench/cells.tsv"),
    ("VPS absolute path", "Cloned full-history into `/root/l1-measure/targets`."),
    ("VPS absolute path", "corpus staged under /root/some-directory-invented-today/out/"),
    ("VPS absolute path", "scratch clones at /root/1c-scratch/Textualize_rich"),
    # NOTE: exemplars for the platform name / day-job codename / Greek
    # domain terms used to live here as plaintext lines. Those patterns are
    # now hashed literal terms (see "Hashed literal terms" in
    # scripts/internal_language_patterns.py) with genericized labels
    # (private-platform-name, internal-project-codename,
    # internal-domain-term-*) — putting their real plaintext values in this
    # file, even as a whitelisted fixture, would re-ship exactly the values
    # hashing exists to stop shipping. Their "still detected" coverage lives
    # in tests/test_leak_gate_hashed_terms.py instead, which reads real
    # values from a gitignored file and SKIPS (never fabricates a pass)
    # when that file is absent.
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
        # Precision controls for the widened VPS-path rule. Widening a rule
        # until it matches everything is not hardening; each of these is a
        # real line from this repo that must stay clean.
        #
        # A dot-directory under the root account is a tool-managed, XDG-style
        # state location that exists identically wherever that tool ran as
        # root. It describes the tool, not the operator's service tree.
        'p.add_argument("--projects-dir", default="/root/.claude/projects")',
        "Synthetic-only — never touches /root/.claude/projects or /var/log/.",
        # Prose, not a filesystem path: the substring only looks like one.
        "# allowed only after policy/root/mode resolution succeeds; an unavailable",
        # No trailing segment, so not a directory reference.
        "the root account is /root and nothing else",
    ]
    for line in benign:
        hits = _m.scan_text("synthetic.py", line)
        assert not hits, f"benign line tripped {[h[0] for h in hits]}: {line!r}"


def test_whitelist_contains_this_file() -> None:
    assert "tests/test_leak_gate_exemplars.py" in _m.WHITELIST_FILES


# ---------------------------------------------------------------------------
# The exemption table is a ratchet, not a convenience.
# ---------------------------------------------------------------------------


def test_pattern_path_exemptions_are_pinned() -> None:
    """Silencing a rule for a file must be a reviewed, visible change.

    ``PATTERN_PATH_EXEMPTIONS`` is the narrow alternative to whitelisting a
    whole file, and narrow is exactly what makes it tempting to reach for. A
    gate that can be quieted by appending a path to a dict is a gate whose
    green tick means "nobody objected", so the table is pinned here: growing
    it fails this test and forces the addition to be argued for.
    """
    assert _m.PATTERN_PATH_EXEMPTIONS == {
        "VPS absolute path": frozenset({"tests/data/1c_frozen.json"}),
    }


def test_the_one_exemptions_stated_reason_is_actually_true() -> None:
    """The exemption claims a SHA-256 pin protects the file. Verify the pin.

    The rationale written next to that entry is that the file cannot acquire
    a new leak of any kind without breaking ``test_repair_intent_frozen.py``,
    which asserts its digest — a stronger guarantee than the scan the
    exemption drops. That argument is only sound while the pin exists and
    still matches. An exemption justified by a check nobody re-checks is the
    same defect class this catalogue exists to prevent, so the justification
    is measured here rather than trusted.
    """
    root = repo_root()
    exempt_rel = next(iter(_m.PATTERN_PATH_EXEMPTIONS["VPS absolute path"]))
    exempt_path = root / exempt_rel
    assert exempt_path.is_file(), f"exempted path no longer exists: {exempt_rel}"

    pin_source = (root / "tests" / "test_repair_intent_frozen.py").read_text(encoding="utf-8")
    match = re.search(r'EXPECTED_FROZEN_SHA256\s*=\s*"([0-9a-f]{64})"', pin_source)
    assert match, "tests/test_repair_intent_frozen.py no longer pins a SHA-256; the exemption's premise is gone"

    actual = hashlib.sha256(exempt_path.read_bytes()).hexdigest()
    assert actual == match.group(1), (
        f"{exempt_rel} no longer matches its pinned digest, so the exemption is unjustified: "
        f"pinned {match.group(1)}, actual {actual}"
    )
