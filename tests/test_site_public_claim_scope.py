"""Regression guards for public labels and evidence claims, not marketing scores."""

from __future__ import annotations

import pytest

from tests._helpers.repo_root import repo_root
from tests.test_homepage_contract import HomepageParser, normalized

SITE = repo_root() / "templates/distribution/landing-page"


def _page(name):
    return HomepageParser((SITE / name).read_text(encoding="utf-8"))


def _evidence_text():
    page = _page("trust.html")
    sections = [node for node in page.root.find("section") if node.attrs.get("id") == "evidence-layer"]
    assert len(sections) == 1
    return normalized(sections[0].text())


def test_refund_title_does_not_promote_a_planned_product_refund_to_all_services():
    page = _page("refund.html")
    title = normalized(next(page.root.find("title")).text()).lower()
    body = normalized(next(page.root.find("article")).text())
    assert "refund" in title and "roam" in title
    assert "30-day" not in title and "money-back" not in title
    # Preserve the actual conditional policy; removing its terms is not a fix.
    assert "Roam Cloud (when available)" in body
    assert "PR Replay" in body and "30-day first-charge refund" in body


def test_trust_record_scope_does_not_claim_every_command_was_collected():
    text = _evidence_text()
    assert "covering every command" not in text
    assert "supported commands" in text and "initialization" in text
    assert "missing" in text.lower()


def test_trust_preparation_is_separate_from_signing_and_agent_context():
    text = _evidence_text()
    assert "exactly what context the agent consumed" not in text
    assert "into one signed bundle" not in text
    assert "not automatically signed" in text
    assert "recorded" in text.lower() and "consumed" in text.lower()


def test_trust_modes_do_not_authenticate_human_approval():
    text = _evidence_text()
    assert "lease record of the human approver" not in text
    assert "MCP" in text
    assert "not human approval" in text


def test_trust_hmac_claim_keeps_local_key_and_integrity_boundary():
    text = _evidence_text()
    assert "identify any post-hoc edits" not in text
    assert "local key" in text
    assert "identity" in text and "complete" in text


@pytest.mark.parametrize("name", ["trust.html", "governance.html"])
def test_development_records_are_not_ai_system_operational_logs(name):
    text = normalized(_page(name).root.text())
    assert "generates the record-keeping evidence Article 12 requires" not in text
    assert "not the operational logs" in text
    assert "do not establish that Article 12 requirements are met" in text


def test_trust_reproduction_requires_retained_inputs_and_records():
    text = normalized(_page("trust.html").root.text())
    assert "rebuild every bundle from source" not in text
    assert "retained inputs and records" in text


def test_trust_remediation_windows_remain_targets_not_slas():
    text = normalized(_page("trust.html").root.text())
    assert "remediation SLAs" not in text
    assert "remediation targets" in text
    assert "high within 30 days, medium within 90" in text


def test_governance_replay_is_recorded_events_not_a_complete_transcript():
    text = normalized(_page("governance.html").root.text())
    assert "renders the full transcript" not in text
    assert "recorded run events as a human-readable timeline" in text


def test_governance_signing_is_optional_not_on_every_preparation_bundle():
    text = normalized(_page("governance.html").root.text())
    assert "attestation on each bundle" not in text
    assert "Markdown + PDF + signed JSON" not in text
    assert "Attestations and signatures are optional, separately configured outputs" in text
