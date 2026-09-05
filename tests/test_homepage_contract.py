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


def test_homepage_keeps_assets_local_and_needs_no_executable_script(page):
    for script in page.root.find("script"):
        assert script.attrs == {"type": "application/ld+json"}
        assert isinstance(json.loads(script.text()), dict)
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
    schemas = [json.loads(node.text()) for node in page.root.find("script")]
    faq = next(schema for schema in schemas if schema["@type"] == "FAQPage")
    visible = {
        normalized(next(node.find("summary")).text()): normalized(next(node.find("p")).text())
        for node in page.root.find("details")
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
    for command in ("roam init", "roam understand", "roam impact calculate_total", "git diff | roam critique"):
        assert command in source
    run("init")
    assert (tmp_path / ".roam").is_dir()
    overview = json.loads(run("--json", "understand"))
    assert overview["summary"]["verdict"]
    impact = json.loads(run("--json", "impact", "calculate_total"))
    assert impact["summary"]["verdict"]
    for caller in callers.values():
        assert caller in json.dumps(impact), f"Missing illustrated caller: {caller}"
    preflight = json.loads(run("--json", "preflight", "calculate_total"))
    assert preflight["summary"]["verdict"]
    (checkout / "pricing.py").write_text(
        "def calculate_total(items):\n    return round(sum(items), 2)\n", encoding="utf-8"
    )
    diff = subprocess.run(["git", "diff"], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30)
    assert diff.stdout, "The review example requires a real change"
    critique = json.loads(run("--json", "critique", stdin=diff.stdout, expected_codes=(0, 5)))
    assert critique["summary"]["verdict"]
