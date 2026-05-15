"""Shared wording-lint helper for compliance-overclaim detection.

W184 introduced the discipline: Roam ``maps to`` and ``supports
evidence for`` controls; it never ``certifies``, ``guarantees``, or
``makes compliant``. The lint enforces this across every public
surface (README, landing page, control-mapping YAML, OSCAL emission).

Until W536, the constants + scan loop were duplicated across three
test files (``test_doc_consistency.py``, ``test_evidence_oscal.py``,
``test_evidence_oscal_ar.py``) — three copies invited drift the way
W506 surfaced when ``iso_42001`` was renamed across two duplicate
allowlists. W518 consolidated framework slugs into
``roam.evidence.control_mapping_vocab``; this module mirrors that
solution for the wording lint.

Public API (three names; pinned by a drift guard in
``tests/test_evidence_oscal.py::test_wording_lint_single_source_of_truth``):

- ``FORBIDDEN_WORDS`` — the compliance-overclaim word stems
- ``NEGATION_MARKERS`` — phrases that bracket an overclaim into a
  negation / disclaimer context (``does not certify``, ``never
  guarantees``)
- ``scan_for_overclaims(text)`` — return the list of (forbidden-word,
  window) tuples found in ``text`` outside any negation window. An
  empty list means the text is wording-compliant.

The negation window is the 30 chars before the match start through
10 chars after the match end (matches the W184/W203 lint discipline
in the original test_doc_consistency.py implementation).
"""

from __future__ import annotations

# Closed enumeration of compliance-overclaim word stems. Case-
# insensitive substring matches (``certif`` catches ``certify`` /
# ``certifies`` / ``certified`` / ``certification``).
FORBIDDEN_WORDS: tuple[str, ...] = ("certif", "compliant", "guarantee")

# Closed enumeration of negation-window markers. A forbidden word is
# permitted only when one of these phrases sits within 30 chars
# before the match or 10 chars after.
NEGATION_MARKERS: tuple[str, ...] = (
    "not ",
    "no ",
    "never ",
    "doesn't ",
    "does not ",
)

# Window sizes mirror the W184/W203 lint discipline.
_WINDOW_PRE = 30
_WINDOW_POST = 10


def scan_for_overclaims(text: str) -> list[tuple[str, str]]:
    """Return forbidden-word matches in ``text`` outside any negation window.

    Each element is a ``(word_stem, window)`` tuple where ``word_stem``
    is the entry from ``FORBIDDEN_WORDS`` that matched, and ``window``
    is the surrounding lowercased text (for diagnostic messages).

    An empty list means the text passes the wording lint.
    """
    lowered = text.lower()
    violations: list[tuple[str, str]] = []
    for word in FORBIDDEN_WORDS:
        start = 0
        while True:
            idx = lowered.find(word, start)
            if idx == -1:
                break
            window_start = max(0, idx - _WINDOW_PRE)
            window = lowered[window_start : idx + len(word) + _WINDOW_POST]
            if not any(neg in window for neg in NEGATION_MARKERS):
                violations.append((word, window))
            start = idx + len(word)
    return violations
