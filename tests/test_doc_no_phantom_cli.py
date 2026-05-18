"""Drift-guard: tracked doc files must NOT contain phantom-CLI invocations.

Why this drift-guard exists
---------------------------

Three tracked, hand-curated docs at the repo root ship to agents and
contributors as onboarding context:

* ``AGENTS.md`` — 500+ line hand-curated developer guide. NOT regenerated
  by ``roam agents-md --refresh`` (the generator emits a ~121-line
  auto-summary; running ``--refresh`` would destroy ~487 lines of
  hand-curated content).
* ``README.md`` — the public landing surface on PyPI/GitHub.
* ``CONTRIBUTING.md`` — contributor onboarding for the repo.

Without a mechanical gate, future edits to any of these could quietly
introduce CLI invocations that look plausible but don't actually exist
in the live CLI surface — phantom commands. Agents and contributors
consuming these files would then run those phantom invocations and
fail.

``CLAUDE.md`` is intentionally EXCLUDED from this gate. It is gitignored
(per W1076) and contains intentional documentary references to sealed
W-patterns (e.g. its Pattern-5 narrative on the historical ``roam vuln``
typo).

The 9 phantom CLI patterns
--------------------------

A sibling-verifier audit (2026-05-18) identified 9 known phantom-CLI
shapes that have appeared in the auto-generated agents-md output or in
near-miss copy elsewhere in the corpus. A second sibling audit confirmed
that AGENTS.md, README.md, and CONTRIBUTING.md are all currently CLEAN
of the same patterns; this drift-guard pins that state.

1. ``roam rules check`` — ``rules`` is a flat command, no ``check``
   subcommand exists. Canonical: ``roam rules --ci``.
2. ``roam rules new`` — same; ``rules`` has no ``new`` subcommand.
3. ``roam rules list`` — same; ``rules`` has no ``list`` subcommand.
4. ``roam constitution add-rule`` — ``constitution`` does not have an
   ``add-rule`` subcommand.
5. ``roam constitution status`` — same; no ``status`` subcommand.
6. ``roam constitution validate`` — same; no ``validate`` subcommand.
7. ``roam secrets --active`` — the ``--active`` flag does not exist on
   ``roam secrets``.
8. ``roam bisect <symbol>`` — ``bisect`` takes ``--metric``, not a
   positional symbol argument.
9. ``roam vuln ingest`` — should be ``roam vulns --import-file`` or
   ``roam vuln-map``; ``vuln ingest`` was never a real subcommand.

Exempt regions
--------------

Some narrative passages legitimately reference these phantom forms to
discuss the W-pattern bugs themselves (e.g. the AGENTS.md Pattern 5
audit text: ``for_security_review calls internal `roam vuln` (should be
`vulns`)``). Lines whose narrative explicitly anchors on the pattern
audit are exempt.

Detection rule: if the same line that contains a phantom invocation
ALSO contains the literal word ``Pattern`` or a ``W-`` prefix
(``W123``, ``W-pattern``, etc.), treat the match as a documentary
reference, not a live phantom emission. This is the same exemption
discipline used by ``test_changelog_phantoms.py`` for PHANTOM
annotations — the marker must appear in the same neighborhood as the
phantom mention.

Sibling test
------------

This test follows the structural-guard model of
:mod:`tests.test_changelog_phantoms` — a small, mechanical regex sweep
of tracked markdown files with a closed enumeration of known-bad
patterns and an exempt-region discipline. Both tests share the same
``repo_root()`` helper for fragile-path-free resolution (W588).
"""

from __future__ import annotations

import re

import pytest

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_ROOT = repo_root()

# Closed list of tracked, hand-curated docs the gate covers. CLAUDE.md is
# intentionally NOT in this list — it's gitignored (W1076) and carries
# intentional documentary references to sealed W-patterns. Extending the
# list (e.g. to dev/HANDOVER-*.md should those become tracked) is a
# deliberate source-code edit.
_PHANTOM_DOC_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
)


# ---------------------------------------------------------------------------
# Phantom-CLI pattern table
# ---------------------------------------------------------------------------

# Canonical 9-pattern enumeration. Extending this tuple is a deliberate
# source-code edit — the count-regression test below pins the size so a
# future drift cannot quietly grow the list and dilute the guard.
_PHANTOM_PATTERNS: tuple[str, ...] = (
    r"\broam\s+rules\s+check\b",  # rules is flat, no check subcommand
    r"\broam\s+rules\s+new\b",  # rules is flat, no new subcommand
    r"\broam\s+rules\s+list\b",  # rules is flat, no list subcommand
    r"\broam\s+constitution\s+add-rule\b",
    r"\broam\s+constitution\s+status\b",
    r"\broam\s+constitution\s+validate\b",
    r"\broam\s+secrets\s+--active\b",  # --active flag does not exist
    r"\broam\s+bisect\s+[a-zA-Z_]",  # bisect takes --metric, not positional symbol
    r"\broam\s+vuln\s+ingest\b",  # should be vulns --import-file or vuln-map
)

# Pre-compile once at module load for speed.
_COMPILED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p) for p in _PHANTOM_PATTERNS)


# ---------------------------------------------------------------------------
# Exempt-region detection
# ---------------------------------------------------------------------------


def _is_documentary_reference(line: str) -> bool:
    """True iff *line* is a documentary reference to a W-pattern, not a
    live phantom emission.

    A line is exempt iff it contains EITHER:

    * The literal word ``Pattern`` (case-sensitive — matches "Pattern 5",
      "Pattern-1 family", etc., but not the lowercase "pattern" prose
      mention).
    * A ``W-`` prefix or a ``W<digits>`` token (e.g. ``W123``, ``W-pattern``,
      ``W-Pattern``). This covers the historical-incident annotation style
      used throughout the codebase.

    The exemption is deliberately narrow: an arbitrary line mentioning
    "pattern" in lowercase does NOT qualify, and a phantom invocation
    without any audit-anchor marker is treated as a live emission.
    """
    if "Pattern" in line:
        return True
    if re.search(r"\bW-?[0-9]", line):
        return True
    return False


# ---------------------------------------------------------------------------
# Violation collection
# ---------------------------------------------------------------------------


def _collect_phantom_violations(doc_filename: str, lines: list[str]) -> list[str]:
    """Return one human-readable violation string per unannotated phantom.

    The message names the file:line, the matched text, and points the
    author at the two valid resolutions: rewrite to the canonical
    equivalent, or anchor the line with a ``Pattern`` / ``W-`` marker
    to make the documentary intent explicit.
    """
    violations: list[str] = []
    for idx, line in enumerate(lines):
        line_no = idx + 1
        if _is_documentary_reference(line):
            continue
        for pattern in _COMPILED_PATTERNS:
            for match in pattern.finditer(line):
                matched_text = match.group(0)
                violations.append(
                    f"{doc_filename}:{line_no}: phantom CLI invocation "
                    f"'{matched_text}'. Either use canonical equivalent "
                    f"(e.g. 'roam rules --ci' for 'rules check') OR add as "
                    f"documentary reference with 'Pattern' or 'W-' prefix "
                    f"in the same line."
                )
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_filename", _PHANTOM_DOC_FILES)
def test_doc_has_no_phantom_cli(doc_filename: str) -> None:
    """Each tracked doc in ``_PHANTOM_DOC_FILES`` must NOT contain any of
    the 9 known phantom-CLI shapes outside exempt documentary regions.

    Why: agents and contributors consume these docs as onboarding
    context. A phantom invocation trains the reader to run a command
    that doesn't exist on the live CLI surface and fail silently. The
    sibling-verifier audit (2026-05-18) confirmed all three files are
    currently clean (0 hits); this drift-guard pins that state so
    future edits cannot quietly reintroduce a phantom.

    To resolve a violation:

    1. Rewrite the invocation to its canonical equivalent (e.g.
       ``roam rules --ci`` instead of ``roam rules check``).
    2. OR — if the mention is intentional audit narrative discussing
       the phantom itself — anchor the line with a ``Pattern`` or
       ``W-`` marker so the exempt-region detector treats it as
       documentary, not live.
    """
    doc_path = REPO_ROOT / doc_filename
    assert doc_path.exists(), f"{doc_filename} not found at {doc_path}"
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    violations = _collect_phantom_violations(doc_filename, lines)
    assert not violations, (
        f"{doc_filename} contains phantom CLI invocation(s) outside exempt "
        "documentary regions — readers (agents and contributors) consuming "
        f"{doc_filename} would run these against the live CLI and fail. "
        "Offenders:\n  " + "\n  ".join(violations)
    )


def test_phantom_pattern_list_matches_documented_canon() -> None:
    """The ``_PHANTOM_PATTERNS`` tuple must contain exactly 9 entries.

    Regression-pin: the sibling-verifier audit established the canonical
    9-pattern enumeration. Growing the tuple silently would dilute the
    audit trail (a future reader could not tell which patterns came
    from the original audit vs later drift). Shrinking it would weaken
    the gate. Either direction requires a deliberate source-code edit
    that updates this count alongside the tuple.
    """
    assert len(_PHANTOM_PATTERNS) == 9, (
        f"_PHANTOM_PATTERNS must contain exactly 9 entries (matches the "
        f"sibling-verifier audit canon); got {len(_PHANTOM_PATTERNS)}. "
        f"If you intentionally grew the audit, update this assertion "
        f"with rationale linking to the new audit memo."
    )
