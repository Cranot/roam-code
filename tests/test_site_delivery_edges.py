"""Source guards for remapped error pages and narrowly scoped static headers.

URL resolution and simple path rules are checked locally. These controls do
not emulate Pages routing or establish the headers a deployed host returns.
"""

from __future__ import annotations

import fnmatch
from collections import Counter
from urllib.parse import urljoin

import pytest

from tests.test_homepage_contract import SITE, HomepageParser

CARD_PATH = "/.well-known/mcp-server-card"
REPORT_PATH = "/examples/team-replay-report.md"
CONTROL_PATHS = [
    "/",
    "/docs/",
    "/docs/missing",
    "/landing.css",
    "/og-code-analysis.png",
    "/.well-known/security.txt",
    "/.well-known/mcp-server-card.json",
    "/.well-known/mcp/server-card.json",
    "/.well-known/mcp-server-card-extra",
    "/.well-known/mcp-server-card/missing",
]
SECURITY_HEADERS = {
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "reporting-endpoints",
    "content-security-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
}


@pytest.mark.parametrize("origin", ["https://roam-code.com", "https://example.roam-code.pages.dev"])
@pytest.mark.parametrize("path", ["/missing", "/docs/missing", "/docs/missing/", "/nested/deep/missing", "/404"])
def test_error_page_styles_resolve_to_the_root_asset_at_every_url_depth(origin, path):
    page = HomepageParser((SITE / "404.html").read_text(encoding="utf-8"))
    assert not list(page.root.find("base")), "A broad base URL would also alter recovery links and fragments"
    links = list(page.root.find("link"))
    stylesheets = [node for node in links if node.attrs.get("rel") == "stylesheet"]
    preloads = [node for node in links if node.attrs.get("rel") == "preload" and node.attrs.get("as") == "style"]
    assert len(stylesheets) == len(preloads) == 1
    assert (SITE / "landing.css").is_file()
    for node in [*stylesheets, *preloads]:
        assert urljoin(origin + path, node.attrs["href"]) == origin + "/landing.css", (
            "The root error document is also served at missing nested URLs"
        )


def test_error_page_keeps_noindex_and_native_recovery_destinations():
    page = HomepageParser((SITE / "404.html").read_text(encoding="utf-8"))
    robots = [node.attrs.get("content") for node in page.root.find("meta") if node.attrs.get("name") == "robots"]
    assert robots == ["noindex,follow"]
    canonicals = [node.attrs.get("href") for node in page.root.find("link") if node.attrs.get("rel") == "canonical"]
    assert canonicals == ["https://roam-code.com/404"]
    destinations = {node.attrs.get("href") for node in page.root.find("a")}
    assert {"#main", "/", "/explore", "/setup", "/docs/"} <= destinations


def _header_rules(source):
    """Read this site's plain path/splat header blocks, retaining duplicates."""
    rules = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if raw == raw.lstrip():
            assert line.startswith("/"), "Extend this scoped parser if host-specific rules are introduced"
            rules.append((line, []))
        else:
            name, separator, value = line.partition(":")
            assert rules and separator and value.strip(), "Malformed source header declaration"
            rules[-1][1].append((name.lower(), value.strip()))
    return rules


def _assert_scoped_card_type(source):
    rules = _header_rules(source)
    owners = [(path, value) for path, headers in rules for name, value in headers if name == "content-type"]
    expected_types = {CARD_PATH: "application/json", REPORT_PATH: "text/plain; charset=utf-8"}
    assert owners == list(expected_types.items()), "Use exact-path MIME owners, not broad overrides"
    for path in [CARD_PATH, REPORT_PATH, "/examples/other.md", *CONTROL_PATHS]:
        # Only this file's plain paths and '*' splat are modeled. Provider
        # default headers and actual response behavior need live verification.
        headers = [
            (name, value) for pattern, values in rules if fnmatch.fnmatchcase(path, pattern) for name, value in values
        ]
        assert all(count == 1 for count in Counter(name for name, _ in headers).values()), (
            "Pages joins duplicate custom headers rather than replacing an earlier rule"
        )
        values = dict(headers)
        assert SECURITY_HEADERS <= values.keys()
        assert values["x-content-type-options"] == "nosniff"
        assert values["cache-control"] == "public, max-age=0, must-revalidate"
        assert values.get("content-type") == expected_types.get(path)


def test_extensionless_card_has_one_exact_json_mime_rule_without_changing_other_routes():
    _assert_scoped_card_type((SITE / "_headers").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mutation", ["broad-type", "duplicate-type", "wrong-type", "duplicate-cache"])
def test_mime_guard_rejects_broad_wrong_or_duplicate_custom_headers(mutation):
    # Start from a small valid header-control fixture so each negative case
    # proves its intended refuter even before the production rule is fixed.
    source = "/*\n" + "".join(
        f"  {name}: controlled\n" for name in sorted(SECURITY_HEADERS - {"x-content-type-options"})
    )
    source += "  X-Content-Type-Options: nosniff\n  Cache-Control: public, max-age=0, must-revalidate\n"
    source += CARD_PATH + "\n  Content-Type: application/json\n"
    source += REPORT_PATH + "\n  Content-Type: text/plain; charset=utf-8\n"
    _assert_scoped_card_type(source)
    if mutation == "broad-type":
        source = source.replace(CARD_PATH + "\n", "/.well-known/*\n")
    elif mutation == "duplicate-type":
        source += CARD_PATH + "\n  content-type: application/json\n"
    elif mutation == "wrong-type":
        source = source.replace("application/json", "application/octet-stream")
    else:
        source += CARD_PATH + "\n  cache-control: public, max-age=0, must-revalidate\n"
    with pytest.raises(AssertionError):
        _assert_scoped_card_type(source)
