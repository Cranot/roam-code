"""Buyer-facing source contracts; not conversion or browser-UX evidence."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_homepage_contract import SITE, HomepageParser, normalized


def page(name):
    return HomepageParser((SITE / name).read_text(encoding="utf-8"))


def prose(text):
    # The source parser inserts a separator around inline links. Ignore only
    # that punctuation spacing, not changed words, amounts, or qualifications.
    return re.sub(r"\s+([.,;:!?])", r"\1", normalized(text))


def test_prose_normalization_preserves_material_claim_differences():
    assert prose("Read the DPA .") == prose("Read the DPA.")
    assert prose("not available") != prose("available")
    assert prose("$2,500") != prose("$6,000")
    assert prose("after report delivery") != prose("after launch")


@pytest.mark.parametrize("name", ["pricing.html", "audit.html"])
def test_existing_selling_pages_keep_accessible_static_structure(name):
    document = page(name)
    assert len(list(document.root.find("main"))) == len(list(document.root.find("h1"))) == 1
    ids = [node.attrs["id"] for node in document.elements if "id" in node.attrs]
    assert len(ids) == len(set(ids))
    for node in document.elements:
        assert set(node.attrs.get("aria-labelledby", "").split()) <= set(ids)
        if node.tag in {"a", "summary"}:
            assert normalized(node.text()) or node.attrs.get("aria-label")
        assert not any(attr.startswith("on") for attr in node.attrs)
        resource = node.attrs.get("src")
        if node.tag == "link" and node.attrs.get("rel") in {
            "stylesheet",
            "preload",
            "icon",
            "manifest",
            "apple-touch-icon",
        }:
            resource = node.attrs["href"]
        if resource:
            target = urlparse(resource)
            assert not target.scheme and not target.netloc
            assert (SITE / target.path.lstrip("/")).is_file()
    for script in document.root.find("script"):
        assert script.attrs.get("type") == "application/ld+json"
        assert set(script.attrs) <= {"type", "data-product-offers"}
        if "data-product-offers" in script.attrs:
            assert name == "audit.html"
        assert isinstance(json.loads(script.text()), dict)
    assert not list(document.root.find("form")), "No new lead collection or checkout"


def test_pricing_leads_with_available_free_and_paid_paths():
    document = page("pricing.html")
    hero = next(document.root.find("header"))
    destinations = {node.attrs.get("href") for node in hero.find("a")}
    assert {"/setup", "/audit#tiers"} <= destinations
    text = normalized(hero.text()).lower()
    assert "agents" in text and "free" in text
    assert "not available" in text and "review" in text and "cloud" in text


@pytest.mark.parametrize("name", ["pricing.html", "audit.html", "compare.html", "governance.html"])
def test_wide_tables_have_named_keyboard_scroll_regions(name):
    document = page(name)
    wrappers = [node for node in document.elements if "compare-table-wrap" in node.attrs.get("class", "").split()]
    assert wrappers, "Keep coverage of the actual wide tables"
    for wrapper in wrappers:
        assert wrapper.attrs.get("tabindex") == "0"
        assert wrapper.attrs.get("role") == "region"
        caption = next(wrapper.find("caption"))
        assert caption.attrs.get("id")
        assert wrapper.attrs.get("aria-labelledby") == caption.attrs["id"]
        assert normalized(caption.text())
        for table in wrapper.find("table"):
            assert all(header.attrs.get("scope") in {"col", "row"} for header in table.find("th"))
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    assert "[tabindex]:focus-visible" in css


@pytest.mark.parametrize("anchor", ["review", "cloud", "self-hosted"])
def test_future_product_cards_do_not_look_like_available_subscriptions(anchor):
    document = page("pricing.html")
    card = next(node for node in document.elements if node.attrs.get("id") == anchor)
    text = normalized(card.text()).lower()
    assert "not available" in text if anchor != "self-hosted" else "scoped" in text
    assert "most teams" not in text
    assert all(not link.attrs.get("href", "").startswith("https://buy.stripe.com/") for link in card.find("a"))


@pytest.mark.parametrize("name", ["pricing.html", "audit.html"])
@pytest.mark.parametrize(
    "unsupported",
    ["most teams", "customer pipelines", "close to free", "checkout launches soon", "continuous roam review does"],
)
def test_selling_copy_does_not_reintroduce_observed_overclaims(name, unsupported):
    assert unsupported not in normalized(next(page(name).root.find("main")).text()).lower()


def test_replay_limits_are_visible_before_the_request_and_linkable_from_the_sow():
    document = page("audit.html")
    limits = next(
        (node for node in document.elements if node.attrs.get("id") == "what-this-report-does-not-cover"), None
    )
    assert limits is not None, "The SOW's public exclusion anchor must resolve"
    text = normalized(limits.text()).lower()
    assert all(term in text for term in ("tests", "security audit", "incident", "current"))
    first_request = next(node for node in document.elements if node.attrs.get("href", "").startswith("mailto:"))
    assert document.elements.index(limits) < document.elements.index(first_request)
    assert "last incident" not in normalized(next(document.root.find("h1")).text()).lower()


def test_replay_sample_explains_actual_git_window_and_portable_commands():
    sample = next(node for node in page("audit.html").elements if node.attrs.get("id") == "sample")
    text = normalized(sample.text())
    assert "HEAD~5..HEAD" in text and "six commits" in text and "not necessarily five pull requests" in text
    assert "shallow" in text.lower()
    codes = [normalized(node.text()) for node in sample.find("code")]
    assert "pip install roam-code" in codes and "roam pr-replay --tier sample" in codes
    assert not any("&&" in command for command in codes)


def test_audit_requests_are_drafts_and_do_not_require_private_code_in_email():
    document = page("audit.html")
    for tier in ("Team", "Deep"):
        target = next(
            node.attrs["href"]
            for node in document.root.find("a")
            if node.attrs.get("href", "").startswith("mailto:")
            and f"PR Replay {tier} request" in parse_qs(urlparse(node.attrs["href"]).query).get("subject", [])
        )
        assert urlparse(target).path == "hello@roam-code.com"
        body = parse_qs(urlparse(target).query)["body"][0]
        assert "share privately" in body
    text = normalized(next(document.root.find("main")).text())
    assert "opens an email draft" in text
    assert "Do not email source code, credentials, or private reports" in text
    assert "Response time and availability are confirmed by email" in text


def test_audit_credit_preserves_amounts_without_hypothetical_subscription_costs():
    document = page("audit.html")
    closing = next(node for node in document.elements if node.attrs.get("class") == "page-cta-strip")
    assert any(node.attrs.get("href") == "#sample" for node in closing.find("a"))
    credit = next(node for node in document.elements if node.attrs.get("id") == "credit")
    assert "fee is still payable in full" in normalized(credit.text())
    assert "$299" not in normalized(credit.text())
    assert "remaining subscription cost" not in normalized(credit.text())
    assert len(list(next(credit.find("thead")).find("th"))) == 3
    assert "launch date" in normalized(credit.text())
    rows = list(credit.find("tr"))[1:]
    assert len(rows) == 2
    for row in rows:
        amounts = [int(re.search(r"\$([\d,]+)", cell.text())[1].replace(",", "")) for cell in row.find("td")]
        fee, discount = amounts
        assert discount == fee // 2


def test_audit_faq_json_matches_all_visible_answers():
    document = page("audit.html")
    visible = {
        normalized(next(node.find("summary")).text()): normalized(next(node.find("p")).text())
        for node in document.root.find("details")
    }
    faq = next(json.loads(node.text()) for node in document.root.find("script") if '"FAQPage"' in node.text())
    assert {entry["name"] for entry in faq["mainEntity"]} == set(visible)
    for entry in faq["mainEntity"]:
        assert prose(entry["acceptedAnswer"]["text"]) == prose(visible[entry["name"]])


def test_existing_offer_prices_and_future_price_visibility_are_preserved():
    audit = page("audit.html")
    service = next(json.loads(node.text()) for node in audit.root.find("script") if '"Service"' in node.text())
    offers = service["offers"]
    assert [(offer["price"], offer["priceCurrency"]) for offer in offers] == [
        ("0", "USD"),
        ("2500", "USD"),
        ("6000", "USD"),
    ]
    # Commissioned reports require scope confirmation, not a product preorder.
    assert all("availability" not in offer for offer in offers[1:])
    pricing = page("pricing.html")
    plans = next(node for node in pricing.root.find("details") if node.attrs.get("class") == "selling-plans")
    assert "open" not in plans.attrs, "Keep unavailable products secondary, with native disclosure"
    # The source parser separates data on either side of a generation marker;
    # browsers concatenate these inline price/month fragments.
    text = re.sub(r"\s+(?=/mo\b)", "", normalized(plans.text()))
    assert all(amount in text for amount in ("$99/mo", "$299/mo", "$799/mo", "$1,499/mo"))
    assert "proposed" in text.lower()


def test_report_choices_precede_the_detailed_methodology():
    document = page("audit.html")
    sections = [node.attrs.get("id") for node in document.root.find("section")]
    assert sections.index("tiers") < sections.index("deliverable") < sections.index("evidence")
    hero = next(document.root.find("header"))
    assert {"#sample", "#tiers"} <= {link.attrs.get("href") for link in hero.find("a")}


@pytest.mark.parametrize("tier", ["team", "deep"])
def test_paid_cards_explain_deliverable_and_kickoff_without_reading_fine_print(tier):
    document = page("audit.html")
    card = next(node for node in document.root.find("article") if f"replay-tier--{tier}" in node.attrs.get("class", ""))
    assert len(list(card.find("li"))) >= 3
    text = normalized(card.text()).lower()
    assert "markdown + pdf" in text and "from the agreed kickoff" in text
    assert ("5 business days" if tier == "team" else "10 business days") in text


@pytest.mark.parametrize("tier", ["Team", "Deep"])
def test_email_requests_capture_the_question_without_assuming_calendar_scope(tier):
    document = page("audit.html")
    query = next(
        parse_qs(urlparse(node.attrs["href"]).query)
        for node in document.root.find("a")
        if node.attrs.get("href", "").startswith("mailto:")
        and f"PR Replay {tier} request" in parse_qs(urlparse(node.attrs["href"]).query).get("subject", [])
    )
    body = query["body"][0]
    assert "Question we want answered:" in body
    assert "Languages / framework:" in body
    assert "90 days" not in body
    assert "no source code or credentials" in body
