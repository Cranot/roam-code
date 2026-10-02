"""Keep scenario guides directly navigable using native, named contents links.

These source contracts do not measure browser layout or screen-reader behavior.
"""

from __future__ import annotations

import re

import pytest

from tests.test_homepage_contract import SITE, HomepageParser, normalized

SCENARIOS = [
    (
        "demos.html",
        "example-contents-title",
        (
            "clone-repair",
            "hidden-callers",
            "repeated-lookups",
            "database-queries",
            "dependency-boundaries",
            "test-gaps",
            "refactor-move",
            "review-evidence",
        ),
    ),
    (
        "how-roam-thinks.html",
        "workflow-contents-title",
        (
            "repo-orientation",
            "task-context",
            "change-impact",
            "patch-review",
            "algorithmic-cost",
            "architecture-drift",
            "refactor-planning",
            "parallel-agents",
            "evidence-records",
        ),
    ),
]


@pytest.mark.parametrize("name,label_id,anchors", SCENARIOS, ids=[item[0] for item in SCENARIOS])
def test_every_scenario_has_a_stable_heading_target_and_named_native_contents(name, label_id, anchors):
    page = HomepageParser((SITE / "docs" / name).read_text(encoding="utf-8"))
    headings = [node for node in page.root.find("h2") if re.match(r"\d+\. ", normalized(node.text()))]
    assert len(headings) == len(anchors), "Each numbered scenario needs a contents destination"
    assert [node.attrs.get("id") for node in headings] == list(anchors), "Keep public scenario fragments stable"

    ids = [node.attrs["id"] for node in page.elements if "id" in node.attrs]
    assert len(ids) == len(set(ids)), "Fragments and accessible names require unique IDs"
    assert {"main", "nav-toggle", "site-navigation"} <= set(ids), "Retain existing navigation targets"
    contents = [node for node in page.root.find("nav") if node.attrs.get("aria-labelledby") == label_id]
    assert len(contents) == 1, "Expose one named native contents navigation"
    contents = contents[0]
    labels = [node for node in contents.find("p") if node.attrs.get("id") == label_id]
    assert len(labels) == 1 and normalized(labels[0].text()), "Name contents with a visible label"
    assert page.elements.index(contents) < page.elements.index(headings[0]), "Offer jumps before the scenarios"
    lists = list(contents.find("ol"))
    assert len(lists) == 1, "Use native numbering that follows the guide's scenario order"
    links = list(lists[0].find("a"))
    assert [node.attrs.get("href") for node in links] == [f"#{anchor}" for anchor in anchors]
    assert len(list(lists[0].find("li"))) == len(links), "Give each scenario its own list item"
    for link in links:
        assert normalized(link.text()), "Each destination needs a descriptive link label"
        assert not {"role", "tabindex", "onclick"} & link.attrs.keys(), "Keep links natively operable"
    for node in (contents, *contents.find("p"), *contents.find("ol"), *contents.find("li"), *links):
        assert "hidden" not in node.attrs and node.attrs.get("aria-hidden") != "true"
