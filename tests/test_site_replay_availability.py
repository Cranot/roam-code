"""PR Replay tier states in the site policy decide what every served file offers.

A paused tier must not appear as available: no structured offer, request email,
request button, bound price or scope, or paid-report call to action. Pages
that govern existing orders and dated history keep their terms.
"""

from __future__ import annotations

import json
import re
import shutil

import pytest

from scripts import build_site_product_facts as builder
from tests._helpers.repo_root import repo_root

ROOT = repo_root()
STATE_CASES = {
    "both-paused": {"team": "PAUSED", "deep": "PAUSED"},
    "team-open": {"team": "OPEN", "deep": "PAUSED"},
    "deep-open": {"team": "PAUSED", "deep": "OPEN"},
    "both-open": {"team": "OPEN", "deep": "OPEN"},
}


def served(root):
    return [(relative, path.read_text(encoding="utf-8")) for path, relative, _ in builder.served_files(root)]


def findings(root, facts):
    found = {relative: builder.paused_offer_findings(relative, text, facts) for relative, text in served(root)}
    return {relative: rows for relative, rows in found.items() if rows}


def service_offers(root):
    text = (root / builder.SITE / "audit.html").read_text(encoding="utf-8")
    record = re.search(r'<script type="application/ld\+json" data-product-offers>(.*?)</script>', text, re.S)[1]
    return [offer["name"] for offer in json.loads(record)["offers"]]


def request_subjects(root):
    text = (root / builder.SITE / "audit.html").read_text(encoding="utf-8")
    return set(re.findall(r"subject=PR%20Replay%20(\w+)%20request", text))


@pytest.fixture
def product_site(tmp_path, monkeypatch):
    # Real site bytes and stored variants in a disposable tree; no network or subprocesses.
    shutil.copytree(ROOT / builder.SITE, tmp_path / builder.SITE)
    shutil.copytree(ROOT / builder.OFFERS, tmp_path / builder.OFFERS)
    shutil.copyfile(ROOT / builder.POLICY, tmp_path / builder.POLICY)
    shutil.copyfile(ROOT / builder.README, tmp_path / builder.README)
    (tmp_path / "templates/legal").mkdir(parents=True)
    shutil.copyfile(ROOT / "templates/legal/sow-pr-replay.md", tmp_path / "templates/legal/sow-pr-replay.md")
    counts = builder.collect_counts(ROOT)
    monkeypatch.setattr(builder, "collect_counts", lambda root: counts)
    return tmp_path


def set_tiers(root, states):
    path = root / builder.POLICY
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["replayTiers"].update(states)
    path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")


def test_served_files_present_no_paused_tier_as_available():
    facts = builder.product_facts(ROOT)
    assert builder.paused_paid_tiers(facts), "Run the state cases below when every paid tier is open"
    assert findings(ROOT, facts) == {}


def test_published_tier_states_are_the_policy_states():
    facts = builder.product_facts(ROOT)
    stored = json.loads((ROOT / builder.SNAPSHOT).read_text(encoding="utf-8"))
    assert stored["replayTiers"] == facts["replayTiers"]
    llms = (ROOT / builder.SITE / "llms.txt").read_text(encoding="utf-8")
    line = "PR Replay tiers: " + "; ".join(f"{tier}: {state}" for tier, state in facts["replayTiers"].items()) + "."
    assert line in builder.BLOCK.search(llms)[2]


def test_structured_offers_list_only_open_tiers():
    facts = builder.product_facts(ROOT)
    names = service_offers(ROOT)
    assert len(names) == sum(builder.tier_open(facts, tier) for tier in builder.REPLAY_TIERS)
    for tier in builder.REPLAY_TIERS:
        assert any(tier.title() in name for name in names) == builder.tier_open(facts, tier)


def test_open_sample_tier_stays_offered_everywhere_it_was():
    # Control: the check removes paused tiers only; the open Sample remains.
    facts = builder.product_facts(ROOT)
    assert facts["replayTiers"]["sample"] == "OPEN"
    assert "PR Replay — Sample (DIY)" in service_offers(ROOT)
    audit = (ROOT / builder.SITE / "audit.html").read_text(encoding="utf-8")
    assert 'id="sample"' in audit and "roam pr-replay --tier sample" in audit
    for name in ("index.html", "pricing.html", "llms.txt"):
        text = (ROOT / builder.SITE / name).read_text(encoding="utf-8")
        assert "/audit#sample" in text or "roam pr-replay --tier sample" in text
    sample_offer = '<a href="mailto:x@example.com?subject=PR%20Replay%20Sample%20request">Request Sample</a>'
    assert builder.paused_offer_findings("index.html", sample_offer, facts) == []


@pytest.mark.parametrize("case", STATE_CASES)
def test_every_tier_state_builds_a_site_that_offers_only_open_tiers(product_site, case):
    set_tiers(product_site, STATE_CASES[case])
    assert builder.run(product_site, write=True) == 0
    assert builder.run(product_site) == 0
    facts = builder.product_facts(product_site)
    assert findings(product_site, facts) == {}
    open_paid = {tier.title() for tier in builder.PAID_TIERS if builder.tier_open(facts, tier)}
    assert request_subjects(product_site) == open_paid
    assert len(service_offers(product_site)) == 1 + len(open_paid)
    notice = "reports are paused for new requests"
    audit = " ".join((product_site / builder.SITE / "audit.html").read_text(encoding="utf-8").split())
    assert (notice in audit) == (len(open_paid) < len(builder.PAID_TIERS))
    if builder.paused_paid_tiers(facts):
        assert f"Paid {builder.tier_titles(builder.paused_paid_tiers(facts))} {notice}" in audit
    # Reopening and pausing again returns the committed bytes.
    set_tiers(product_site, json.loads((ROOT / builder.POLICY).read_text(encoding="utf-8"))["replayTiers"])
    assert builder.run(product_site, write=True) == 0
    for path, relative, _ in builder.served_files(product_site):
        assert path.read_bytes() == (ROOT / path.relative_to(product_site)).read_bytes(), relative


def test_pausing_a_tier_without_regenerating_is_refused(product_site):
    # Falsifier: open copy left in place after the policy pauses the tier is found and not written.
    set_tiers(product_site, {"team": "OPEN"})
    assert builder.run(product_site, write=True) == 0
    set_tiers(product_site, {"team": "PAUSED"})
    facts = builder.product_facts(product_site)
    stale = findings(product_site, facts)
    assert {"audit.html", "pricing.html", "press.html", "llms.txt"} <= set(stale)
    assert any("structured offer" in row for row in stale["audit.html"])
    assert any("request email" in row for row in stale["audit.html"])


@pytest.mark.parametrize(
    "planted",
    [
        '<a href="mailto:hello@roam-code.com?subject=PR%20Replay%20Deep%20request">Ask us</a>',
        "<p>Request Team today.</p>",
        "<p>Deep reports cost $6,000.</p>",
        "<p>Ask about a paid report.</p>",
        '<script type="application/ld+json">{"@type": "Offer", "name": "PR Replay — Team"}</script>',
    ],
)
def test_planted_paused_offer_is_refused_before_any_write(product_site, planted):
    path = product_site / builder.SITE / "pricing.html"
    path.write_text(path.read_text(encoding="utf-8").replace("</main>", planted + "</main>", 1), encoding="utf-8")
    before = {p: p.read_bytes() for p in (product_site / builder.SITE).rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="presents a paused PR Replay tier"):
        builder.run(product_site, write=True)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_existing_order_pages_keep_their_terms():
    facts = builder.product_facts(ROOT)
    refund = (ROOT / builder.SITE / "refund.html").read_text(encoding="utf-8")
    assert "Team" in refund and "Deep" in refund
    assert builder.paused_offer_findings("refund.html", "<p>Request Team — $2,500</p>", facts) == []


@pytest.mark.parametrize("damage", [{"team": "LIVE"}, {"team": ""}, {"extra": "OPEN"}])
def test_unknown_tier_state_is_refused(product_site, damage):
    set_tiers(product_site, damage)
    with pytest.raises(ValueError, match="tier states"):
        builder.run(product_site, write=True)


def test_stored_variant_without_a_served_marker_is_refused(product_site):
    store = product_site / builder.OFFERS / "site" / "audit.html"
    store.write_text(store.read_text(encoding="utf-8") + "<!-- variant:orphan:open -->x<!-- /variant -->\n")
    with pytest.raises(ValueError, match="no served marker"):
        builder.run(product_site)


MARKDOWN = [relative for _, relative, _ in builder.served_files(ROOT) if relative.endswith(".md")]
MARKER = re.compile(r"<!-- /?replay-offer[\w:-]* -->")


def block_comment_findings(text):
    """CommonMark HTML block type 2: a line opening with ``<!--`` makes the whole line raw HTML.

    So a replay-offer marker that starts a line must stand alone, with blank lines around it,
    or the Markdown next to it renders as literal text or changes block structure.
    """
    lines = text.split("\n")
    found = []
    for index, line in enumerate(lines):
        if not re.match(r" {0,3}<!--", line) or not MARKER.search(line):
            continue
        if re.sub(r"<!--.*?-->", "", line).strip():
            found.append(f"{index + 1}: text shares a line with a block-level marker: {line.strip()[:80]}")
        neighbours = [lines[i] for i in (index - 1, index + 1) if 0 <= i < len(lines)]
        if any(neighbour.strip() and not MARKER.fullmatch(neighbour.strip()) for neighbour in neighbours):
            found.append(f"{index + 1}: block-level marker without a blank line around it")
    return found


@pytest.mark.parametrize("case", STATE_CASES)
@pytest.mark.parametrize("name", MARKDOWN)
def test_markdown_markers_change_nothing_in_the_rendered_document(name, case):
    from tests._helpers.replay_tiers import rendered

    text = rendered(name, STATE_CASES[case])
    assert block_comment_findings(text) == []
    # markdown-it-py is locked through the dev and ci dependencies (rich), not declared directly.
    markdown_it = pytest.importorskip("markdown_it")
    md = markdown_it.MarkdownIt("commonmark")

    def html(source):
        return " ".join(MARKER.sub("", md.render(source)).split())

    assert html(text) == html(MARKER.sub("", text))
