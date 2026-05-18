"""Drift-guard: prevent NEW phantom-memo references from landing in CHANGELOG.md.

The failure class this prevents
-------------------------------

CHANGELOG.md declares ``dev/*.md`` memo files SHIPPED in the version
history, but ~32 of those memo paths do not exist on disk. Manual
annotation campaigns (3 batches; 16 phantoms sealed) close the historical
drift, but nothing prevents a NEW phantom reference from being added in
a future CHANGELOG edit. This drift-guard closes that failure class
permanently: every backtick-fenced (or markdown-linked) ``dev/*.md`` path
in CHANGELOG.md must EITHER exist on disk OR be explicitly acknowledged
by an inline HTML ``<!-- PHANTOM YYYY-MM-DD: ... -->`` annotation on the
same line or the immediately-following line.

The annotation format itself is also locked in by a sibling drift test
(``test_phantom_annotation_format_is_consistent``) so future batches
cannot quietly drift the marker string and bypass the lint. The radius
constant (``_ANNOTATION_RADIUS``) is the single tuning knob — change it
deliberately and update the synthetic-fixture tests below.

Why the 3-line radius
----------------------------------

The annotation campaign places the marker either at the END of the line
containing the phantom path (the common case for single-path bullets)
or, on prose-quote runs whose line breaks fall mid-sentence, on one of
the next 3 lines below the path. Real CHANGELOG narrative regularly
has 1-2 lines of prose between a path mention and the next blank line
where authors naturally place HTML comments, so a 3-line radius
respects authoring patterns without losing precision. The radius
deliberately stops at 3 to prevent "annotation drift" (one PHANTOM
marker at the top of a section sweeping up unrelated paths several
screens away).

The sibling test
``test_phantom_annotation_format_is_consistent`` keeps the marker
shape locked so the radius check stays mechanical.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_ROOT = repo_root()
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Candidate path: dev/<filename>.md. Filename is restricted to the alphabet
# that the existing annotation campaign uses for memo filenames
# (letters / digits / underscore / dash / dot). The leading ``\b`` excludes
# accidental embedding inside a longer identifier; the path itself is
# captured.
_PATH_RE = re.compile(r"\bdev/[A-Za-z0-9._-]+\.md\b")

# Phantom annotation: opens with ``<!-- PHANTOM YYYY-MM-DD:`` exactly.
# Date pattern is checked in the dedicated drift-format test below.
_PHANTOM_INLINE = "<!-- PHANTOM 2026-"

# Canonical annotation shape — the format-consistency test pins this so
# the campaign cannot quietly switch to e.g. ``<!-- phantom 26-05:`` and
# bypass the radius check above.
_PHANTOM_FORMAT_RE = re.compile(r"<!-- PHANTOM \d{4}-\d{2}-\d{2}:")

# Markdown-fence prefix used to filter candidate paths to those that appear
# in a recognised reference shape: backtick-fenced ( `dev/X.md` ) or
# inside parentheses / brackets ( (dev/X.md), [dev/X.md] ).
_FENCING_CHARS = ("`", "(", "[")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_fenced(line: str, match_start: int) -> bool:
    """True iff the character immediately preceding *match_start* is a fence.

    The path must be backtick-fenced ( ``\\`dev/X.md\\``` ) or appear
    inside a markdown link / image reference ( ``[dev/X.md](...)``,
    ``(dev/X.md)`` ). Bare prose mentions are intentionally ignored — a
    sentence like "see dev/FOO.md for context" might or might not refer
    to a real file, and the original campaign only annotated the
    fenced-reference shape.
    """
    if match_start == 0:
        return False
    return line[match_start - 1] in _FENCING_CHARS


# Annotation radius: same line + next 3 lines. Real CHANGELOG narrative
# has 1-2 lines of prose between a path mention and the next blank line
# where authors place HTML comments; a 3-line radius respects that without
# letting annotations sweep up unrelated paths several screens away.
_ANNOTATION_RADIUS = 3


def _collect_phantom_violations(lines: list[str]) -> list[str]:
    """Return one human-readable violation string per unannotated phantom."""
    violations: list[str] = []
    for idx, line in enumerate(lines):
        line_no = idx + 1
        for match in _PATH_RE.finditer(line):
            if not _is_fenced(line, match.start()):
                continue
            rel_path = match.group(0)
            on_disk = (REPO_ROOT / rel_path).exists()
            if on_disk:
                continue
            # Path is absent on disk — require a PHANTOM annotation in radius.
            annotated = False
            for offset in range(_ANNOTATION_RADIUS + 1):
                probe_idx = idx + offset
                if probe_idx >= len(lines):
                    break
                if _PHANTOM_INLINE in lines[probe_idx]:
                    annotated = True
                    break
            if annotated:
                continue
            violations.append(
                f"CHANGELOG.md:{line_no}: phantom reference to {rel_path} "
                f"(file does not exist and has no PHANTOM annotation). "
                f"Either create the memo OR annotate with: "
                f"<!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent "
                f"from disk. Regenerate from BACKLOG/test-fixture breadcrumbs "
                f"before next release. -->"
            )
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_changelog_phantoms_are_annotated() -> None:
    """Every backtick-fenced ``dev/*.md`` path in CHANGELOG.md must EITHER
    point at a real file on disk OR carry a
    ``<!-- PHANTOM 2026-... -->`` annotation within the 3-line radius
    (same line, or one of the next 3 lines).

    The annotation is a deliberate acknowledgement that the version
    history declared a memo SHIPPED that does not currently exist on
    disk — the campaign's lineage marker. A path that fails both
    branches is a NEW phantom that slipped past the annotation
    campaign and needs to either be created or annotated before merge.
    """
    assert CHANGELOG_PATH.exists(), f"CHANGELOG.md not found at {CHANGELOG_PATH}"
    lines = CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()
    violations = _collect_phantom_violations(lines)
    assert not violations, (
        "CHANGELOG.md contains unannotated phantom dev/*.md references "
        "(declared SHIPPED but memo absent from disk). Either create the "
        "memo file or add the canonical PHANTOM annotation on the same "
        "line (or within the next 3 lines). Offenders:\n  " + "\n  ".join(violations)
    )


def test_phantom_annotation_format_is_consistent() -> None:
    """Every ``<!-- PHANTOM`` marker in CHANGELOG.md must match the canonical
    ``<!-- PHANTOM YYYY-MM-DD:`` shape.

    Drift-guard. The radius check in
    ``test_changelog_phantoms_are_annotated`` keys on the literal
    substring ``<!-- PHANTOM 2026-``; a future batch that abbreviates
    or restyles the marker (``<!-- phantom 26-05:``, ``<!-- PHANTOM:``,
    ``<!--PHANTOM 2026-05-18:``) would silently bypass that check.
    Pinning the canonical shape here means a vocabulary drift in the
    annotation must be deliberate (and the diff will surface it).
    """
    assert CHANGELOG_PATH.exists(), f"CHANGELOG.md not found at {CHANGELOG_PATH}"
    lines = CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()
    drift: list[str] = []
    for idx, line in enumerate(lines):
        if "<!-- PHANTOM" not in line:
            continue
        # Every occurrence on this line must satisfy the canonical regex.
        # ``re.findall`` over a permissive opener catches restyled
        # markers; the canonical regex matches the well-formed shape.
        opener_count = line.count("<!-- PHANTOM")
        canonical_count = len(_PHANTOM_FORMAT_RE.findall(line))
        if opener_count != canonical_count:
            drift.append(
                f"CHANGELOG.md:{idx + 1}: malformed PHANTOM annotation "
                f"(expected ``<!-- PHANTOM YYYY-MM-DD:``). Line: {line!r}"
            )
    assert not drift, (
        "CHANGELOG.md contains PHANTOM annotations that do not match the "
        "canonical ``<!-- PHANTOM YYYY-MM-DD:`` shape — the radius check "
        "in test_changelog_phantoms_are_annotated keys on that literal "
        "form, so a restyled marker would silently bypass the lint. "
        "Drift:\n  " + "\n  ".join(drift)
    )


# ---------------------------------------------------------------------------
# Detector sanity tests — synthetic fixtures verify the matcher itself
# ---------------------------------------------------------------------------


def test_detector_flags_unannotated_fenced_path(tmp_path: Path) -> None:
    """Synthetic: a backtick-fenced phantom WITHOUT a PHANTOM annotation
    is reported as a violation.
    """
    lines = ["- Some bullet referencing `(internal memo)`."]
    # Reuse the matcher with a synthetic in-memory line list and the real
    # repo root (the synthetic path will not exist on disk under it).
    violations = _collect_phantom_violations(lines)
    assert len(violations) == 1, f"expected 1 violation; got {violations}"
    assert "NONEXISTENT-2026-05-18.md" in violations[0]


def test_detector_exempts_same_line_annotation() -> None:
    """Synthetic: a phantom with PHANTOM annotation on the SAME line is
    exempt.
    """
    lines = [
        "- `dev/NONEXISTENT-A.md` shipped. <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. -->",
    ]
    assert _collect_phantom_violations(lines) == []


def test_detector_exempts_next_line_annotation() -> None:
    """Synthetic: a phantom with PHANTOM annotation on the NEXT line is
    exempt (the prose-quote runs use this shape).
    """
    lines = [
        "  `dev/NONEXISTENT-B.md` (the W123 memo) covers the missing case",
        "  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. -->",
    ]
    assert _collect_phantom_violations(lines) == []


def test_detector_ignores_bare_prose_mention() -> None:
    """Synthetic: a bare (unfenced) mention is intentionally ignored.

    The campaign only annotates fenced references; bare prose mentions
    are ambiguous (could be a filename, could be example text) and are
    out of scope.
    """
    lines = ["- See dev/NONEXISTENT-C.md for context (bare prose, unfenced)."]
    assert _collect_phantom_violations(lines) == []


def test_detector_exempts_annotation_within_3_line_radius() -> None:
    """Synthetic: a phantom with PHANTOM annotation 3 lines below the
    path is exempt — the radius covers narrative-interleaved annotations
    (e.g. the SLSA-V12 case where prose sits between the path mention
    and the natural annotation slot).
    """
    lines = [
        "  `dev/NONEXISTENT-D.md` (the W400 memo) covers the missing case",
        "  with detail on the W401 follow-up wave",
        "  and the W402 propagation arc",
        "  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. -->",
    ]
    assert _collect_phantom_violations(lines) == []


def test_detector_flags_annotation_beyond_radius() -> None:
    """Synthetic: a phantom with PHANTOM annotation 4 lines below the
    path is NOT exempt — the radius stops at 3 to prevent annotations
    at the top of a section sweeping up unrelated paths several
    screens away.
    """
    lines = [
        "  `dev/NONEXISTENT-E.md` covers the missing case",
        "  with line 1 of intervening prose",
        "  with line 2 of intervening prose",
        "  with line 3 of intervening prose",
        "  <!-- PHANTOM 2026-05-18: declared SHIPPED but memo absent from disk. -->",
    ]
    violations = _collect_phantom_violations(lines)
    assert len(violations) == 1, f"expected 1 violation; got {violations}"
    assert "NONEXISTENT-E.md" in violations[0]
