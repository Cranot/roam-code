"""Source contracts and executable examples for the public homepage.

These checks do not constitute browser, layout, or assistive-technology QA.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest

from tests._helpers.repo_root import repo_root

SITE = repo_root() / "templates/distribution/landing-page"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return " ".join(child.text() if isinstance(child, Element) else child for child in self.children)

    def find(self, tag):
        for child in self.children:
            if isinstance(child, Element):
                if child.tag == tag:
                    yield child
                yield from child.find(tag)


class HomepageParser(HTMLParser):
    """Validate explicit tag nesting and retain only source-level HTML data."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]
        self.elements = []
        self.feed(source)
        self.close()
        assert self.stack == [self.root], "Unclosed HTML elements"

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs))
        self.elements.append(node)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS:
            assert self.stack[-1].tag == tag, f"Closing {tag} inside {self.stack[-1].tag}"
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def normalized(text):
    return " ".join(text.split())


@pytest.fixture
def page():
    return HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))


def test_homepage_structure_accessible_names_and_legacy_anchors(page):
    assert len(list(page.root.find("main"))) == len(list(page.root.find("h1"))) == 1
    ids = [node.attrs["id"] for node in page.elements if "id" in node.attrs]
    assert len(ids) == len(set(ids)), "Duplicate IDs break navigation and accessible names"
    assert {
        "main",
        "install",
        "how-it-works",
        "compiler",
        "senses",
        "what-roam-catches",
        "compare",
        "audit",
        "audit-heading",
        "what-you-get",
        "fit",
    } <= set(ids)
    for node in page.elements:
        for attr in ("aria-labelledby", "aria-controls"):
            assert set(node.attrs.get(attr, "").split()) <= set(ids)
        if node.tag in {"a", "summary"}:
            assert normalized(node.text()) or node.attrs.get("aria-label"), "Unnamed interactive element"
        if node.tag == "input":
            assert node.attrs.get("aria-label")
    assert any(node.attrs.get("href") == "#main" for node in page.root.find("a"))


def test_homepage_keeps_assets_local_and_atlas_progressively_enhanced(page):
    for script in page.root.find("script"):
        if script.attrs.get("type") == "application/ld+json":
            assert isinstance(json.loads(script.text()), dict)
        else:
            assert script.attrs == {"type": "module", "src": "/atlas.mjs"}
            assert not script.text().strip()
    for node in page.elements:
        assert not any(key.startswith("on") for key in node.attrs), "Keep navigation native, without inline handlers"
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
            parsed = urlparse(resource)
            assert not parsed.scheme and not parsed.netloc, f"Remote asset: {resource}"
            assert (SITE / parsed.path.lstrip("/")).is_file(), f"Missing asset: {resource}"
    assert any(node.attrs.get("href") == "/home.css" for node in page.root.find("link"))
    for other in SITE.rglob("*.html"):
        if other != SITE / "index.html":
            assert 'href="/home.css"' not in other.read_text(encoding="utf-8"), "Homepage CSS leaked onto another page"


def test_faq_structured_data_matches_the_visible_answers(page):
    schemas = [
        json.loads(node.text()) for node in page.root.find("script") if node.attrs.get("type") == "application/ld+json"
    ]
    faq = next(schema for schema in schemas if schema["@type"] == "FAQPage")
    faq_section = next(node for node in page.root.find("section") if node.attrs.get("aria-labelledby") == "faq-heading")
    visible = {
        normalized(next(node.find("summary")).text()): normalized(next(node.find("p")).text())
        for node in faq_section.find("details")
    }
    questions = faq["mainEntity"]
    assert len(questions) == len(visible) == 7
    assert {question["name"] for question in questions} == set(visible)
    for question in questions:
        # Visible prose may append a navigation link after the full answer.
        assert visible[question["name"]].startswith(normalized(question["acceptedAnswer"]["text"]))


def test_homepage_example_and_limits_are_explicit(page):
    text = normalized(next(page.root.find("main")).text())
    assert "Illustrative checkout example, not a live report." in text
    assert "a suggested test list is not test coverage" in text
    assert "a health score is not permission to merge" in text
    assert "first parser download need network access" in text
    assert "opt-in MCP model summarization can include source snippets" in text
    assert 'pip install "roam-code[mcp]"' in text


def test_homepage_leads_with_agent_workflow_and_setup(page):
    hero = next(page.root.find("header"))
    hero_text = normalized(hero.text()).lower()
    assert "coding agents" in hero_text
    assert "free static checks" in hero_text
    assert "agent" in normalized(next(hero.find("h1")).text()).lower()
    primary_action = next(link for link in hero.find("a") if link.attrs.get("class") == "home-button")
    assert "agent" in primary_action.text().lower()
    assert primary_action.attrs["href"] == "#install"

    install = next(node for node in page.elements if node.attrs.get("id") == "install")
    steps = list(next(install.find("ol")).find("li"))
    assert len(steps) == 3
    assert 'pip install "roam-code[mcp]"' in normalized(steps[0].text())
    assert any(link.attrs.get("href") == "/setup#install-mcp" for link in steps[-1].find("a"))
    assert "instructions" in steps[-1].text()


def test_homepage_hero_shows_one_real_atlas_with_native_fallback_and_full_page_link(page):
    hero = next(page.root.find("header"))
    previews = [node for node in page.elements if "data-atlas" in node.attrs]
    assert len(previews) == 1, "Keep one atlas preview, in the hero rather than repeated below it"
    preview = previews[0]
    assert any(node is preview for node in hero.find("figure"))
    assert "data-compact" in preview.attrs
    image = next(preview.find("img"))
    assert image.attrs["src"] == "/atlas-map.svg"
    assert image.attrs["width"] == "960" and image.attrs["height"] == "620"
    assert image.attrs.get("loading") != "lazy", "The hero preview must not wait for scrolling"
    assert image.attrs["fetchpriority"] == "high"
    assert any(link.attrs.get("href") == "/explore" for link in preview.find("a"))
    assert len(list(preview.find("noscript"))) == 1
    assert not list(preview.find("iframe")), "Reuse the bounded atlas, not a second embedded page"
    # These limits remain visible after JS replaces the selected area's copy.
    scope = next(node for node in preview.find("p") if node.attrs.get("class") == "atlas-widget-scope")
    assert "Python imports, not runtime impact. Not live." in scope.text()
    assert not any(key.startswith("data-") for key in scope.attrs)


def test_homepage_preserves_the_illustrative_example_below_the_real_atlas(page):
    hero = next(page.root.find("header"))
    assert "four-function fixture" in normalized(hero.text())
    atlas = next(node for node in hero.find("figure") if "home-hero-atlas" in node.attrs.get("class", ""))
    assert "calculate_total" not in atlas.text(), "Keep the real checkout result distinct from the Roam import map"
    example = next(node for node in page.root.find("figure") if node.attrs.get("class") == "home-map")
    assert page.elements.index(example) > page.elements.index(hero)
    assert "Illustrative checkout example, not a live report." in example.text()
    assert "roam impact calculate_total" in example.text()


def test_homepage_metadata_keeps_agent_first_positioning(page):
    title = normalized(next(page.root.find("title")).text())
    assert "agent" in title.lower()
    metadata = {node.attrs.get("name", node.attrs.get("property")): node.attrs for node in page.root.find("meta")}
    for key in ("og:title", "twitter:title"):
        assert metadata[key]["content"] == title
    for key in ("description", "og:description", "twitter:description"):
        description = metadata[key]["content"].lower()
        assert all(term in description for term in ("agent", "free", "local", "static"))
    software = next(
        json.loads(script.text())
        for script in page.root.find("script")
        if script.attrs.get("type") == "application/ld+json"
        and json.loads(script.text())["@type"] == "SoftwareApplication"
    )
    assert all(term in software["description"].lower() for term in ("agent", "free", "local", "static"))
    assert software["offers"]["price"] == "0"


def test_homepage_opening_keeps_agent_purpose_mechanism_and_cost_boundary(page):
    # Guard the stated product scope, not a preferred slogan. Change impact is
    # one use; requiring that exact headline hid navigation and quality work.
    hero = next(page.root.find("header"))
    headline = normalized(next(hero.find("h1")).text()).lower()
    assert "agent" in headline
    opening = next(node for node in hero.find("div") if node.attrs.get("class") == "home-intro")
    text = normalized(opening.text()).lower()
    assert all(word in text for word in ("files", "functions", "connections", "query"))
    assert "free static checks" in text
    assert "model usage is separate" in text
    assert "could affect" in text, "Static connections are not a complete runtime prediction"
    assert "catch structural problems" not in text, "A finding is a lead, not validated bug-catching"


def test_homepage_shows_algorithm_alternatives_beyond_navigation(page):
    """Guard the different engineering job, not the preferred headline."""
    questions = next(node for node in page.elements if node.attrs.get("id") == "how-it-works")
    assert any(normalized(code.text()) == "roam algo" for code in questions.find("code"))
    example = next(node for node in page.elements if node.attrs.get("class") == "home-agent-note")
    text = normalized(example.text()).lower()
    assert "illustrative algorithm example" in text
    assert "set or lookup table" in text
    assert all(term in text for term in ("value types", "changes to the list", "order", "duplicates", "position"))
    assert "tests behavior and measures performance" in text
    assert "not an automatic rewrite or a guaranteed speedup" in text
    assert "review preset, not default core" in text
    assert any(link.attrs.get("href") == "/docs/command-reference#algorithm-choices" for link in example.find("a"))
    guide = HomepageParser((SITE / "docs/command-reference.html").read_text(encoding="utf-8"))
    algorithm = next(node for node in guide.elements if node.attrs.get("id") == "algorithm-choices")
    assert "restart the server" in normalized(algorithm.text())
    metadata = {node.attrs.get("name", node.attrs.get("property")): node.attrs for node in page.root.find("meta")}
    for key in ("description", "og:description", "twitter:description"):
        assert all(term in metadata[key]["content"] for term in ("patterns", "alternatives", "catalog"))


def test_homepage_algorithm_summary_names_the_source_of_alternatives(page):
    """Selected meaning boundaries, not a writing-quality score or fixed slogan."""
    hero = next(page.root.find("header"))
    lede = next(node for node in hero.find("p") if node.attrs.get("class") == "home-lede")
    software = next(
        json.loads(script.text())
        for script in page.root.find("script")
        if script.attrs.get("type") == "application/ld+json"
        and json.loads(script.text())["@type"] == "SoftwareApplication"
    )
    for text in (normalized(lede.text()), software["description"]):
        assert all(term in text for term in ("patterns", "alternatives", "catalog"))
    algorithm = next(node for node in page.elements if node.attrs.get("id") == "compiler")
    text = normalized(algorithm.text()).lower()
    assert "keep the existing code" in text, "Comparing alternatives need not result in a rewrite"


def test_homepage_same_page_direction_icons_follow_target_order(page):
    """Directional decoration must agree with the target, without entering its name."""
    checked = 0
    for link in page.root.find("a"):
        href = link.attrs.get("href", "")
        if not href.startswith("#"):
            continue
        icons = [span for span in link.find("span") if normalized(span.text()) in {"↑", "↓", "↗"}]
        if not icons:
            continue
        target = next(node for node in page.elements if node.attrs.get("id") == href[1:])
        expected = "↓" if page.elements.index(target) > page.elements.index(link) else "↑"
        for icon in icons:
            assert normalized(icon.text()) == expected
            assert icon.attrs.get("aria-hidden") == "true"
            checked += 1
    assert checked >= 3, "No directional same-page controls inspected"


def test_homepage_setup_links_to_an_existing_first_result_check(page):
    install = next(node for node in page.elements if node.attrs.get("id") == "install")
    assert any(
        link.attrs.get("href") == "/docs/integration-tutorials#validate-connection" for link in install.find("a")
    )
    guide = HomepageParser((SITE / "docs/integration-tutorials.html").read_text(encoding="utf-8"))
    assert any(node.attrs.get("id") == "validate-connection" for node in guide.elements)
    assert "roam_search_symbol" in guide.root.text() and "roam_uses" in guide.root.text()


def test_homepage_connect_step_lands_after_installation(page):
    install = next(node for node in page.elements if node.attrs.get("id") == "install")
    chooser = next(link for link in install.find("a") if "Choose your agent" in link.text())
    assert chooser.attrs["href"] == "/setup#install-mcp"
    setup = HomepageParser((SITE / "setup.html").read_text(encoding="utf-8"))
    target = next(node for node in setup.elements if node.attrs.get("id") == "install-mcp")
    assert any(link.attrs.get("href", "").startswith("/docs/integration-tutorials#") for link in target.find("a"))


def test_homepage_patch_card_does_not_promise_test_selection_from_critique(page):
    card = next(
        node
        for node in page.root.find("article")
        if any(link.attrs.get("href") == "/docs/command-reference#critique" for link in node.find("a"))
    )
    assert "related tests" not in normalized(card.text()).lower()
    # Discovery label routes to the scoped recipe; it must not demonstrate an
    # unstaged-only pipeline as a generic patch, nor inherit HEAD's intent.
    assert normalized(next(card.find("code")).text()) == "roam critique"


def test_about_qualifies_records_and_detector_leads():
    about = HomepageParser((SITE / "about.html").read_text(encoding="utf-8"))
    text = normalized(next(about.root.find("main")).text())
    assert "does not prove who acted" in text
    assert "findings are leads to investigate" in text
    assert "it catches the structural class those layers miss" not in text
    assert "who acted, what authority existed" not in text
    assert "Every analysis Roam runs writes" not in text


def test_about_separates_available_reports_from_planned_hosting():
    about = HomepageParser((SITE / "about.html").read_text(encoding="utf-8"))
    text = normalized(next(about.root.find("main")).text())
    assert "planned, not available to subscribe to" in text
    assert "HEAD~5..HEAD" in text
    assert "5 / 30 / 90 PRs" not in text, "The free sample selects a commit range, not PR identities"
    assert "as early-access products" not in text


def test_agent_readable_intro_does_not_invent_runtime_or_comparison_proof():
    text = (SITE / "llms.txt").read_text(encoding="utf-8")
    assert '"What could this change affect?"' in text
    assert "Imported runtime traces are separate observations" in text
    assert "does not authenticate who acted" in text
    assert "graph-aware change questions they don't" not in text
    assert "Evidence + compliance" not in text


def test_homepage_qualifies_cost_privacy_and_agent_use(page):
    answers = {
        normalized(next(node.find("summary")).text()): normalized(next(node.find("p")).text())
        for node in page.root.find("details")
    }
    assert (
        "Connecting tools alone does not guarantee your agent will use them"
        in answers["Is Roam for agents or for people?"]
    )
    assert "Static checks use local compute, not model calls" in answers["Is Roam free?"]
    assert "own model usage is separate" in answers["Is Roam free?"]
    assert "may send them to its model provider under its own settings" in answers["Does my code leave my machine?"]
    assert "You still set the goals and decide what ships" in answers["Does Roam replace tests or code review?"]


def test_linked_setup_installs_mcp_and_explains_agent_instructions():
    setup = HomepageParser((SITE / "setup.html").read_text(encoding="utf-8"))
    text = normalized(next(setup.root.find("main")).text())
    assert 'pip install "roam-code[mcp]"' in text
    assert "include Roam checks in its project instructions" in text
    assert "does not guarantee the agent uses them" in text
    assert "run tests and report missing or incomplete checks" in text
    assert any(link.attrs.get("href", "").startswith("/docs/integration-tutorials#") for link in setup.root.find("a"))


def test_homepage_walkthrough_and_connection_example_execute(tmp_path):
    """Exercise real indexing and graph commands, not hand-written output fixtures."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pricing.py").write_text("def calculate_total(items):\n    return sum(items)\n", encoding="utf-8")
    callers = {"orders.py": "place_order", "preview.py": "preview_order", "discounts.py": "apply_discount"}
    for filename, name in callers.items():
        (checkout / filename).write_text(
            f"from pricing import calculate_total\n\ndef {name}(items):\n    return calculate_total(items)\n",
            encoding="utf-8",
        )
    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for args in (
        ["init"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Example"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, timeout=30)

    def run(*args, stdin=None, expected_codes=(0,)):
        result = subprocess.run(
            [sys.executable, "-m", "roam", *args],
            cwd=tmp_path,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert result.returncode in expected_codes, result.stdout + result.stderr
        if result.returncode == 5:
            assert json.loads(result.stdout)["summary"]["high_severity"] > 0
        return result.stdout

    source = (SITE / "index.html").read_text(encoding="utf-8")
    for command in ("roam init", "roam understand", "roam impact calculate_total", "roam critique"):
        assert command in source
    run("init")
    assert (tmp_path / ".roam").is_dir()
    overview = json.loads(run("--json", "understand"))
    assert overview["summary"]["verdict"]
    impact = json.loads(run("--json", "impact", "calculate_total"))
    assert impact["summary"]["verdict"]
    for caller in callers.values():
        assert caller in json.dumps(impact), f"Missing illustrated caller: {caller}"
    # The homepage disclosure is selected fields, not invented JSON.
    # Compare both the captured full response and today's real fixture result.
    snapshot_path = SITE / "data/examples/checkout-impact-2026-09-13.json"
    captured = json.loads(snapshot_path.read_text(encoding="utf-8"))
    parsed_page = HomepageParser(source)
    disclosure = next(node for node in parsed_page.root.find("details") if node.attrs.get("class") == "home-output")
    excerpt = json.loads(next(disclosure.find("pre")).text())
    assert set(excerpt) == {"symbol", "direct_dependents", "cap_applied", "partial_success", "truncated"}
    assert len(excerpt["direct_dependents"]["call"]) == len(callers)
    disclosure_text = normalized(disclosure.text())
    assert "Selected fields from roam --json impact calculate_total" in disclosure_text
    assert "four-function example" in disclosure_text and "on 13 September 2026" in disclosure_text
    assert "Roam 14.1.0" in disclosure_text
    assert captured["_meta"]["timestamp"].startswith("2026-09-13T")
    assert any(
        link.attrs.get("href") == "/data/examples/checkout-impact-2026-09-13.json" for link in disclosure.find("a")
    )
    for key, value in excerpt.items():
        assert value == captured[key], f"Displayed field differs from captured result: {key}"
        assert value == impact[key], f"Current fixture result changed: {key}"
    assert captured["summary"]["risk_level_canonical"] == "high"
    assert "three of four functions" in normalized(disclosure.text())
    preflight = json.loads(run("--json", "preflight", "calculate_total"))
    assert preflight["summary"]["verdict"]
    (checkout / "pricing.py").write_text(
        "def calculate_total(items):\n    return round(sum(items), 2)\n", encoding="utf-8"
    )
    diff = subprocess.run(["git", "diff"], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30)
    assert diff.stdout, "The review example requires a real change"
    patch = tmp_path / "change.patch"
    patch.write_text(diff.stdout, encoding="utf-8")
    reference = (SITE / "docs/command-reference.html").read_text(encoding="utf-8")
    assert 'roam --json critique --input change.patch --intent "Round checkout totals"' in reference
    critique = json.loads(
        run("--json", "critique", "--input", "change.patch", "--intent", "Round checkout totals", expected_codes=(0, 5))
    )
    assert critique["summary"]["verdict"]
    assert critique["summary"]["review_source"] == "input_file"
    assert critique["summary"]["intent"] == "Round checkout totals"
    assert critique["summary"]["check_status"] == {
        "clones-not-edited": "skipped:no_clone_pairs (run `roam clones --persist`)",
        "impact": "ran",
        "intent": "ran",
    }
    assert critique["summary"]["partial_success"] is True
