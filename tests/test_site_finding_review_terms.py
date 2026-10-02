"""Cross-surface guardrails for the prospective September 20 offer revision.

These checks pin the adopted text, not legal enforceability or service delivery.
"""

from __future__ import annotations

import pytest

from tests.test_homepage_contract import SITE, HomepageParser, normalized


def _text(name):
    return normalized(HomepageParser((SITE / name).read_text(encoding="utf-8")).root.text())


@pytest.mark.parametrize("name", ["refund.html", "sow-pr-replay.md"])
def test_finding_review_has_evidence_deadlines_and_no_provider_veto(name):
    text = (
        _text(name)
        if name.endswith(".html")
        else normalized((SITE.parents[1] / "legal" / name).read_text(encoding="utf-8"))
    )
    for phrase in (
        "new engagements expressly adopting",
        "September 20, 2026 terms",
        "Already-agreed customer rights",
        "earlier refund guarantees, remain unchanged",
        "agreed repository snapshot, scope and methodology",
        "5 business days after the walk-through",
        "10 business days",
        "30 calendar days after",
        "do not restart",
        "disagreement cannot postpone",
        "all findings in the original delivered report",
        "fewer than half the original high/medium findings",
        "zero material findings",
        "100% of the fee (not 50%)",
        "adding, splitting or reclassifying findings",
        "capped at the fee paid",
        "There is no review fee",
    ):
        assert phrase in text, (name, phrase)
    assert "calls a false positive" not in text
    assert "objections are reasonable" not in text


def test_mcp_reference_links_to_offer_without_repeating_its_commercial_terms():
    text = _text("docs/mcp-usage.html")
    assert "HEAD~5..HEAD" in text
    assert "not five identified PRs" in text
    for phrase in ("30-PR", "90-PR", "Review credit", "50%", "$2,500", "$6,000"):
        assert phrase not in text
    source = (SITE / "docs/mcp-usage.html").read_text(encoding="utf-8")
    assert 'href="/audit"' in source


def test_privacy_edge_description_names_metadata_without_denial():
    text = _text("privacy.html")
    assert "No personal data is processed by the static site itself" not in text
    assert "Cloudflare processes standard request metadata" in text
    assert "Clarified: 2026-09-20" in text
    assert "Policy effective: 2026-05-18" in text
