"""Editorial invariants; not proof of legal compliance or service delivery."""

from __future__ import annotations

import re

import pytest

from tests.test_homepage_contract import SITE, HomepageParser, normalized


def page_text(name):
    return normalized(HomepageParser((SITE / name).read_text(encoding="utf-8")).root.text())


def test_audit_clone_retention_uses_the_existing_calendar_window():
    text = page_text("audit.html")
    assert "delete on completion" not in text
    assert "deleted on completion" not in text
    assert "After delivery: temporary clone is deleted," not in text
    assert text.count("within 7 calendar days of report delivery") == 3


def test_audit_does_not_predict_delivery_speed_from_repository_size():
    # Include JSON-LD as well as visible prose.
    source = normalized((SITE / "audit.html").read_text(encoding="utf-8"))
    assert "Smaller repos usually finish faster" not in source
    assert source.count("Any different schedule is agreed in writing before work begins.") == 2


@pytest.mark.parametrize("name", ["audit.html", "trust.html"])
def test_provider_description_does_not_use_an_undefined_compliance_label(name):
    text = page_text(name)
    assert "GDPR-native" not in text
    assert "EU-based provider" in text


def test_integration_tutorial_keeps_offer_conditions_at_the_canonical_destination():
    source = (SITE / "docs/integration-tutorials.html").read_text(encoding="utf-8")
    text = page_text("docs/integration-tutorials.html")
    assert "HEAD~5..HEAD" in text
    assert "recent commits, not five identified PRs" in text
    assert 'href="/audit#sample"' in source
    assert 'href="/audit"' in source
    for phrase in ("Review credit", "50%", "launch-based terms"):
        assert phrase not in text


def test_short_credit_table_opts_into_compact_layout_without_shrinking_other_tables():
    document = HomepageParser((SITE / "audit.html").read_text(encoding="utf-8"))
    credit = next(node for node in document.elements if node.attrs.get("id") == "credit")
    table = next(credit.find("table"))
    assert "compare-table--compact" in table.attrs.get("class", "").split()
    assert len(list(next(table.find("thead")).find("th"))) == 3
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    compact = re.search(r"\.compare-table--compact\s*\{([^}]+)\}", css)[1]
    assert "min-width: 0" in compact
    first_header = re.search(r"\.compare-table--compact thead th:first-child\s*\{([^}]+)\}", css)[1]
    assert "min-width: 0" in first_header
    prices = re.search(r"\.compare-table--compact tbody td\s*\{([^}]+)\}", css)[1]
    assert "white-space: nowrap" in prices
    assert any("min-width: 760px" in rule for rule in re.findall(r"\.compare-table\s*\{([^}]+)\}", css))
    for name in ("compare.html", "pricing.html"):
        assert "compare-table--compact" not in (SITE / name).read_text(encoding="utf-8")
