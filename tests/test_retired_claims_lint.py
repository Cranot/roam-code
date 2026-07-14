"""Retired-claims lint — pre-audit benchmark claims must never reappear.

Produced by the 2026-07-14 public-claims audit (two independent audits
across roam-code + compile-code found the same drift class): the README
was corrected in c2adf10d, but the deployed landing page (``index.html``)
still carried the RETIRED claims — "10/10 fixed in both arms at −13%
cost" (no parity caveat), "91% of envelopes ship pre-executed answers"
(the corrected figure is 57% pre-executed + ~33% structured facts), a
``pip install compile-code`` one-liner for a package that is not on PyPI,
and the CHANGELOG's unannotated "same shape on Opus (−86% turns)".

Follows the pattern of ``test_w462_landing_page_tool_count_drift.py``:
scan the public text surfaces (landing-page tree + README.md +
CHANGELOG.md) for the retired literals and fail when one appears
WITHOUT its correction annotation on the same line(s). CHANGELOG
history is append-only, so a historical claim is acceptable exactly
when it carries a bracketed ``[corrected ...]`` note (or equivalent
parity/n=10 caveat) — silent reappearance is not.
"""

from __future__ import annotations

import re

from tests._helpers.repo_root import repo_root

ROOT = repo_root()

_LANDING_DIR = ROOT / "templates" / "distribution" / "landing-page"
_FILE_SUFFIXES = (".html", ".txt", ".md")

# Root-level public docs scanned in addition to the landing-page tree.
_ROOT_DOCS = ("README.md", "CHANGELOG.md")

# (name, compiled pattern, allow-markers). A match is acceptable iff any
# allow-marker (case-insensitive substring) appears on the line(s) the
# match spans — i.e. the claim is annotated as historical/corrected, not
# asserted as present-tense truth. An empty marker tuple = never allowed.
# ``\s+`` deliberately spans line wraps (HTML paragraphs wrap mid-claim).
_RETIRED_CLAIMS: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "91%-pre-executed (corrected to 57% L1 + ~33% facts)",
        re.compile(r"91%\s+of\s+envelopes\s+ship\s+pre-executed", re.IGNORECASE),
        ("corrected", "earlier", " was ", "was:"),
    ),
    (
        "10/10-both-arms parity phrasing (n=10 cannot establish parity)",
        re.compile(r"10/10\s+fixed\s+in\s+both\s+arms", re.IGNORECASE),
        ("n=10", "parity", "corrected"),
    ),
    (
        "-86%-turns Opus claim (corrected: -33% overall; -88% single cell)",
        re.compile(r"[−-]86%\s+turns", re.IGNORECASE),
        ("corrected",),
    ),
    (
        "pip-install-compile-code one-liner (not on PyPI; install is git-based)",
        re.compile(r"pip\s+install\s+compile-code\s*(?:&&|&amp;&amp;)", re.IGNORECASE),
        (),
    ),
)


def _iter_scanned_files():
    """Yield (rel_path, abs_path) for every public text surface in scope."""
    for name in _ROOT_DOCS:
        path = ROOT / name
        if path.is_file():
            yield name, path
    for path in sorted(_LANDING_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _FILE_SUFFIXES:
            continue
        yield path.relative_to(ROOT).as_posix(), path


def _find_violations(text: str) -> list[tuple[int, str, str]]:
    """Return (lineno, claim_name, span_text) for every unannotated match."""
    line_starts = [0]
    for m in re.finditer(r"\n", text):
        line_starts.append(m.end())
    line_starts.append(len(text) + 1)

    def span_lines(start: int, end: int) -> tuple[int, str]:
        first_line = next(
            (i for i in range(len(line_starts) - 1) if line_starts[i] <= start < line_starts[i + 1]),
            0,
        )
        last_line = next(
            (i for i in range(len(line_starts) - 1) if line_starts[i] <= end <= line_starts[i + 1]),
            first_line,
        )
        seg_start = line_starts[first_line]
        seg_end = line_starts[min(last_line + 1, len(line_starts) - 1)] - 1
        return first_line + 1, text[seg_start:seg_end]

    violations: list[tuple[int, str, str]] = []
    for name, pattern, allow_markers in _RETIRED_CLAIMS:
        for m in pattern.finditer(text):
            lineno, segment = span_lines(m.start(), m.end())
            lower = segment.lower()
            if any(marker in lower for marker in allow_markers):
                continue  # annotated historical reference — acceptable
            violations.append((lineno, name, m.group(0)))
    return violations


def test_retired_claims_do_not_reappear_uncorrected():
    """No public surface may re-assert a retired benchmark claim unannotated."""
    failures: list[str] = []
    scanned_files = 0

    for rel, path in _iter_scanned_files():
        scanned_files += 1
        text = path.read_text(encoding="utf-8")
        for lineno, name, raw in _find_violations(text):
            failures.append(f"{rel}:{lineno}: retired claim [{name}] via {raw!r}")

    # Sanity: a silent empty walk (wrong path after a rename) would let
    # drift slip through unnoticed — the W462 leak class itself.
    assert scanned_files > 2, (
        f"Only {scanned_files} public surfaces scanned; check _LANDING_DIR / _ROOT_DOCS paths."
    )

    assert not failures, (
        "Retired public claims reappeared uncorrected (2026-07-14 claims audit; "
        "annotate with the correction on the same line, or use the current "
        "README claim language):\n  " + "\n  ".join(failures)
    )


def test_retired_claims_lint_actually_catches_reappearance():
    """Sanity: prove each retired-claim rule fires on its literal, and that
    a correction annotation on the same line suppresses it."""
    dirty = (
        "<p>Replayed on 723 real prompts: 91% of envelopes\n"
        "ship pre-executed answers.</p>\n"
        "<p>10/10 fixed in both arms at -13% cost.</p>\n"
        "<p>same shape on Opus (−86% turns).</p>\n"
        "<p><code>pip install compile-code &amp;&amp; compile claude</code></p>\n"
    )
    hits = _find_violations(dirty)
    assert len(hits) == 4, f"expected all 4 retired-claim rules to fire, got: {hits}"

    annotated = (
        "10/10 fixed in both arms (n=10 - no parity claim).\n"
        "same shape on Opus (-86% turns) [corrected 2026-07-14: -33% overall].\n"
    )
    assert _find_violations(annotated) == [], (
        "correction annotations on the same line must suppress the lint"
    )
