"""Product facts must survive changes of source, policy and site shape."""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import replace

import pytest

from scripts import build_site_product_facts as builder
from tests._helpers.repo_root import repo_root

ROOT = repo_root()


@pytest.fixture
def product_site(tmp_path, monkeypatch):
    # Real site bytes, disposable filesystem, no subprocesses/network/clocks.
    shutil.copytree(ROOT / builder.SITE, tmp_path / builder.SITE)
    (tmp_path / "dev").mkdir()
    shutil.copyfile(ROOT / builder.POLICY, tmp_path / builder.POLICY)
    (tmp_path / "templates/legal").mkdir(parents=True)
    shutil.copyfile(ROOT / "templates/legal/sow-pr-replay.md", tmp_path / "templates/legal/sow-pr-replay.md")
    counts = builder.collect_counts(ROOT)
    monkeypatch.setattr(builder, "collect_counts", lambda root: counts)
    return tmp_path


def test_current_product_facts_are_generated():
    assert builder.run(ROOT) == 0


@pytest.mark.parametrize("key", ["teamReplayPrice", "deepReplayPrice", "teamReplayPrCount", "deepReplayPrCount"])
def test_adopted_fact_change_requires_rebuild(product_site, key):
    assert builder.run(product_site) == 0
    path = product_site / builder.POLICY
    policy = json.loads(path.read_text(encoding="utf-8"))
    original = policy[key]
    policy[key] += 1
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="adopted SOW"):
        builder.run(product_site, write=True)
    terms = product_site / "templates/legal/sow-pr-replay.md"
    literal = f"${original:,}" if key.endswith("Price") else f"{original} "
    replacement = f"${policy[key]:,}" if key.endswith("Price") else f"{policy[key]} "
    terms.write_text(terms.read_text(encoding="utf-8").replace(literal, replacement), encoding="utf-8")
    if key.endswith("Price"):
        original_credit = f"${original / 2:,.0f} credits"
        new_credit = f"${policy[key] / 2:,.2f} credits"
        terms.write_text(terms.read_text(encoding="utf-8").replace(original_credit, new_credit), encoding="utf-8")
    before = {p: p.read_bytes() for p in (product_site / builder.SITE).rglob("*") if p.is_file()}
    assert builder.run(product_site) == 1
    assert all(p.read_bytes() == content for p, content in before.items())
    assert builder.run(product_site, write=True) == 0
    assert builder.run(product_site) == 0
    facts = json.loads((product_site / builder.SNAPSHOT).read_text(encoding="utf-8"))
    assert facts[key] == policy[key]
    for name in ("audit.html", "pricing.html", "press.html", "compare.html"):
        text = (product_site / builder.SITE / name).read_text(encoding="utf-8")
        matches = [value for marker, value in builder.FACT.findall(text) if marker == key]
        assert matches and set(matches) == {builder.fact_value(facts, key)}


@pytest.mark.parametrize("damage", ["missing", "unknown", "unclosed"])
def test_binding_damage_refuses_before_any_write(product_site, damage):
    path = product_site / builder.SITE / "pricing.html"
    text = path.read_text(encoding="utf-8")
    old = "<!-- product-fact:teamReplayPrice -->"
    if damage == "missing":
        text = builder.FACT.sub(lambda m: m[2] if m[1] == "teamReplayPrice" else m[0], text)
    elif damage == "unknown":
        text += "<!-- product-fact:invented -->0<!-- /product-fact -->"
    else:
        text = text.replace(old, "<!-- product-fact:teamReplayPrice", 1)
    path.write_text(text, encoding="utf-8")
    before = {p: p.read_bytes() for p in (product_site / builder.SITE).rglob("*") if p.is_file()}
    with pytest.raises((KeyError, ValueError)):
        builder.run(product_site, write=True)
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("value", ["LIVE", "", None, False, [], {}])
def test_unknown_availability_is_not_available(product_site, value):
    path = product_site / builder.POLICY
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["productAvailability"]["review"] = value
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError):
        builder.run(product_site, write=True)


def test_history_and_unbound_copy_are_not_rewritten():
    facts = builder.product_facts(ROOT)
    historical = "Measured v13.4: 245 tools, 30 PRs, $2,500; a 90-day plan."
    assert builder.render(historical, facts) == historical
    altered = copy.deepcopy(facts)
    altered["teamReplayPrCount"] = 31
    text = "<!-- product-fact:teamReplayPrCount -->30<!-- /product-fact --> PRs; 30-minute call"
    assert builder.render(text, altered).endswith("31<!-- /product-fact --> PRs; 30-minute call")


def test_empty_site_is_not_pass(product_site):
    with pytest.raises((ValueError, OSError)):
        builder.run(product_site / "missing")


def test_obvious_unbound_offer_cannot_silently_drift(product_site):
    path = product_site / builder.SITE / "press.html"
    path.write_text(path.read_text(encoding="utf-8") + "<p>Team $2,400</p>", encoding="utf-8")
    with pytest.raises(ValueError, match="unbound offer"):
        builder.run(product_site, write=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mcp_default_preset", 18),
        ("mcp_full", 247),
        ("command_names", 288),
        ("languages", 29),
        ("pyproject_version", "14.1.1"),
    ],
)
def test_registry_and_version_changes_reach_static_snapshot(product_site, monkeypatch, field, value):
    counts = replace(builder.collect_counts(ROOT), **{field: value})
    monkeypatch.setattr(builder, "collect_counts", lambda root: counts)
    assert builder.run(product_site) == 1
    assert builder.run(product_site, write=True) == 0
    assert builder.run(product_site) == 0
    facts = builder.product_facts(product_site)
    stored = json.loads((product_site / builder.SNAPSHOT).read_text(encoding="utf-8"))
    assert stored == facts
    llms = (product_site / builder.SITE / "llms.txt").read_text(encoding="utf-8")
    assert str(value) in builder.BLOCK.search(llms)[2]


def test_snapshot_corruption_and_deleted_required_page_are_not_green(product_site):
    (product_site / builder.SNAPSHOT).write_text("{}", encoding="utf-8")
    assert builder.run(product_site) == 1
    (product_site / builder.SITE / "press.html").unlink()
    with pytest.raises(ValueError, match="missing or empty"):
        builder.run(product_site, write=True)
    assert (product_site / builder.SNAPSHOT).read_text(encoding="utf-8") == "{}"


def test_stale_credit_in_written_terms_refuses_generation(product_site):
    terms = product_site / "templates/legal/sow-pr-replay.md"
    terms.write_text(terms.read_text(encoding="utf-8").replace("$1,250 credits", "$1,000 credits"), encoding="utf-8")
    with pytest.raises(ValueError, match="scope/price/credit"):
        builder.run(product_site, write=True)


def test_mcp_tutorial_is_required_without_commercial_bindings(product_site):
    assert builder.REQUIRED["docs/mcp-usage.html"] == set()
    assert builder.run(product_site) == 0
    tutorial = product_site / builder.SITE / "docs/mcp-usage.html"
    tutorial.unlink()
    with pytest.raises(ValueError, match="missing or empty required site scope"):
        builder.run(product_site)


def test_current_report_terms_and_status_are_required_bindings(product_site):
    facts = builder.product_facts(product_site)
    for name in ("examples/team-replay-report.md", "status.html"):
        path = product_site / builder.SITE / name
        text = path.read_text(encoding="utf-8")
        for key in builder.REQUIRED[name]:
            assert builder.fact_value(facts, key) in text
    report = product_site / builder.SITE / "examples/team-replay-report.md"
    original = report.read_text(encoding="utf-8")
    assert "Synthetic engagement" in original and "**0**" in original
    report.write_text(original.replace("$2,500", "$2,400"), encoding="utf-8")
    assert builder.run(product_site) == 1
    assert builder.run(product_site, write=True) == 0
    assert report.read_text(encoding="utf-8") == original
