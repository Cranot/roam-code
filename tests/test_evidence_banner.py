"""W259 — honest evidence-coverage banner tests.

The banner is the first signal a reviewer reads on a PR Replay
Markdown report. W259 implements the three-tier threshold table that
W254 measured:

    Tier           Threshold                              Banner
    -----------    -----------------------------------    ---------------------
    STRONG         complete >= 7                          "Strong evidence coverage"
    PARTIAL        complete + partial >= 5 AND            "Partial coverage"
                   missing <= 3
    INSUFFICIENT   else                                   "Insufficient evidence"

These tests assert:

1. ``test_banner_strong_tier`` — a packet that scores 7 complete / 0
   partial / 1 missing classifies as STRONG and produces a banner
   blockquote containing the strong-tier label.
2. ``test_banner_partial_tier`` — a (3, 2, 3) packet classifies as
   PARTIAL.
3. ``test_banner_insufficient_tier`` — a (1, 1, 6) packet classifies
   as INSUFFICIENT.
4. ``test_banner_appears_above_limitations`` — the Markdown render
   places the banner above the "## Evidence limitations" section.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from roam.commands.cmd_pr_replay import _render_evidence_markdown
from roam.evidence.banner import (
    TIER_INSUFFICIENT,
    TIER_LABELS,
    TIER_PARTIAL,
    TIER_STRONG,
    banner_envelope_block,
    classify_evidence_coverage,
    render_banner_markdown,
)

# ---------------------------------------------------------------------------
# Synthetic packet stub
# ---------------------------------------------------------------------------


@dataclass
class _FakePacket:
    """Minimal duck-typed stand-in for ChangeEvidence.

    ``classify_evidence_coverage`` calls ``evidence_completeness()``
    and reads no other attributes, so this stub is sufficient for the
    tier-classification tests. The Markdown renderer needs more
    attributes — those tests use a real ChangeEvidence instance.
    """

    complete: int
    partial: int
    missing: int
    not_applicable: int = 0

    def evidence_completeness(self) -> dict[str, Any]:
        # Counts mirror what ChangeEvidence.evidence_completeness()
        # returns; the per-Q entries are not consulted by the banner
        # helper so we omit them.
        return {
            "complete": self.complete,
            "partial": self.partial,
            "missing": self.missing,
            "not_applicable": self.not_applicable,
        }


# ---------------------------------------------------------------------------
# 1. Strong tier — complete >= 7
# ---------------------------------------------------------------------------


def test_banner_strong_tier() -> None:
    """A (7, 0, 1) packet classifies as STRONG."""
    packet = _FakePacket(complete=7, partial=0, missing=1)
    tier_id, label, rationale = classify_evidence_coverage(packet)

    assert tier_id == TIER_STRONG
    assert label == TIER_LABELS[TIER_STRONG]
    assert label == "Strong evidence coverage"
    # Rationale must name the counts so the banner is auditable on
    # its own line.
    assert "7 of 8" in rationale
    assert "1 missing" in rationale

    # Markdown render: blockquoted, contains the tier label.
    md = render_banner_markdown(packet)
    assert md.startswith("> ")
    assert label in md
    assert "7 of 8" in md

    # JSON envelope block carries the tier.
    block = banner_envelope_block(packet)
    assert block["tier"] == TIER_STRONG
    assert block["label"] == label
    assert block["counts"]["complete"] == 7
    assert block["counts"]["missing"] == 1


# ---------------------------------------------------------------------------
# 2. Partial tier — complete + partial >= 5 AND missing <= 3
# ---------------------------------------------------------------------------


def test_banner_partial_tier() -> None:
    """A (3, 2, 3) packet classifies as PARTIAL."""
    packet = _FakePacket(complete=3, partial=2, missing=3)
    tier_id, label, rationale = classify_evidence_coverage(packet)

    assert tier_id == TIER_PARTIAL
    assert label == TIER_LABELS[TIER_PARTIAL]
    assert label == "Partial coverage"
    # Rationale lumps complete + partial into the "answered fully or
    # partially" count.
    assert "5 of 8" in rationale
    assert "3 missing" in rationale

    md = render_banner_markdown(packet)
    assert md.startswith("> ")
    assert label in md

    block = banner_envelope_block(packet)
    assert block["tier"] == TIER_PARTIAL


# ---------------------------------------------------------------------------
# 3. Insufficient tier — anything that fails both thresholds
# ---------------------------------------------------------------------------


def test_banner_insufficient_tier() -> None:
    """A (1, 1, 6) packet classifies as INSUFFICIENT.

    Fails STRONG (complete < 7) and PARTIAL (missing > 3) so falls
    through to INSUFFICIENT.
    """
    packet = _FakePacket(complete=1, partial=1, missing=6)
    tier_id, label, rationale = classify_evidence_coverage(packet)

    assert tier_id == TIER_INSUFFICIENT
    assert label == TIER_LABELS[TIER_INSUFFICIENT]
    assert label == "Insufficient evidence"
    assert "1 of 8" in rationale
    # INSUFFICIENT explicitly warns against publishing.
    assert "do not publish" in rationale.lower()

    md = render_banner_markdown(packet)
    assert md.startswith("> ")
    assert label in md

    block = banner_envelope_block(packet)
    assert block["tier"] == TIER_INSUFFICIENT
    assert block["counts"]["missing"] == 6


# ---------------------------------------------------------------------------
# 4. Banner appears above limitations section in Markdown render
# ---------------------------------------------------------------------------


def test_banner_appears_above_limitations() -> None:
    """The banner is rendered above the "## Evidence limitations" section.

    Uses a real ChangeEvidence with minimal fields so the renderer
    can run end-to-end. The packet defaults to "all missing", so the
    banner should be INSUFFICIENT — and either way must appear
    before the limitations heading.
    """
    from roam.evidence import ChangeEvidence

    packet = ChangeEvidence(
        evidence_id="ev-W259-test",
        commit_sha="abc12345" + "0" * 32,
        git_range="HEAD~1..HEAD",
        verdict="no verdict",
    ).with_content_hash()

    md = _render_evidence_markdown(
        evidence=packet,
        commits=[],
        by_detector=[],
        review_suggestions=None,
    )

    # The banner blockquote must appear in the output.
    assert "> **Evidence coverage:" in md, "evidence-coverage banner missing from rendered Markdown"

    banner_pos = md.index("> **Evidence coverage:")
    limitations_pos = md.index("## Evidence limitations")
    assert banner_pos < limitations_pos, (
        "banner must render ABOVE the Evidence limitations section "
        f"(banner @ {banner_pos}, limitations @ {limitations_pos})"
    )

    # And the banner must appear before any of the populated sections
    # (Scope, Actors, Findings) — the banner is the first signal a
    # reviewer reads after the title.
    scope_pos = md.index("## Scope")
    assert banner_pos < scope_pos, f"banner must render before ## Scope (banner @ {banner_pos}, scope @ {scope_pos})"


# ---------------------------------------------------------------------------
# Defensive guard — non-packet input raises rather than fabricates
# ---------------------------------------------------------------------------


def test_banner_refuses_packet_without_completeness_method() -> None:
    """``classify_evidence_coverage`` raises if the input has no
    ``evidence_completeness`` method.

    The banner must not silently fabricate counts; the assurance
    discipline (CLAUDE.md: "Never N/A without running it") requires
    that absence of the method is a loud error.
    """

    class _Bogus:
        pass

    with pytest.raises(AttributeError, match="evidence_completeness"):
        classify_evidence_coverage(_Bogus())
