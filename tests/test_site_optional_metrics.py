"""Keep optional numeric-metric recovery discoverable without claiming full health."""

from __future__ import annotations

from tests.test_homepage_contract import SITE, HomepageParser, normalized


def test_troubleshooting_explains_optional_metrics_and_rechecks_evidence():
    page = (SITE / "docs/troubleshooting.html").read_text(encoding="utf-8")
    text = normalized(HomepageParser(page).root.text())
    assert 'python -m pip install "roam-code[metrics]"' in text
    assert "same Python environment" in text
    assert "algebraic connectivity" in text.lower()
    assert "roam --json health --explain" in text
    assert "partial_success" in text
    assert "not a test-suite result" in text
