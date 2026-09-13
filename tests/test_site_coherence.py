"""Cross-route source contracts, not rendered layout or accessibility certification."""

from __future__ import annotations

import re

import pytest

from scripts.build_changelog_html import _inline
from scripts.build_site_navigation import navigation, updated
from tests.test_homepage_contract import SITE, HomepageParser, normalized


@pytest.mark.parametrize("path", sorted(SITE.rglob("*.html")), ids=lambda path: path.relative_to(SITE).as_posix())
def test_every_page_has_the_shared_navigation_and_valid_nesting(path):
    page = HomepageParser(path.read_text(encoding="utf-8"))
    source = path.read_text(encoding="utf-8")
    assert updated(source, path.relative_to(SITE).as_posix()) == source
    ids = [node.attrs["id"] for node in page.elements if "id" in node.attrs]
    assert len(ids) == len(set(ids))
    nav = next(page.root.find("nav"))
    assert nav.attrs.get("class") == "site-nav"
    links = next(nav.find("ul"))
    assert [(a.attrs["href"], normalized(a.text())) for a in links.find("a")] == [
        ("/explore", "Explore the map"),
        ("/docs/", "Docs"),
        ("/compare", "Compare"),
        ("/pricing", "Pricing"),
        ("https://github.com/Cranot/roam-code", "GitHub"),
        ("/setup", "Set up your agent"),
    ]
    toggle = next(nav.find("input"))
    assert toggle.attrs["aria-controls"] == links.attrs["id"]
    assert toggle.attrs["type"] == "checkbox"  # Native, no script dependency.
    assert next(nav.find("label")).attrs["for"] == toggle.attrs["id"]
    assert next(nav.find("a")).attrs["aria-label"] == "Roam — home"
    for node in page.elements:
        assert not re.search(r"font(?:-\w+)?\s*:", node.attrs.get("style", "")), "Type belongs in shared CSS"


def test_navigation_toggle_only_receives_focus_when_its_label_is_visible():
    """Source guard for desktop Tab order; rendered focus needs browser checks."""
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    base_toggle = re.search(r"\.nav-toggle-checkbox\s*\{([^}]+)\}", css).group(1)
    assert re.search(r"display:\s*none\s*;", base_toggle), "Desktop must not expose a clipped Tab stop"
    mobile = re.search(r"@media\s*\(max-width:\s*56rem\)\s*\{(.*?)\n\}", css, re.S).group(1)
    mobile_toggle = re.search(r"\.nav-toggle-checkbox\s*\{([^}]+)\}", mobile)
    assert mobile_toggle and re.search(r"display:\s*block\s*;", mobile_toggle.group(1))
    # Preserve the native checkbox, visible focus proxy, and no-JS drawer.
    assert re.search(r"\.nav-toggle-label\s*\{\s*display:\s*flex\s*;", mobile)
    assert re.search(r"\.nav-toggle-checkbox:checked\s*~\s*\.nav-links\s*\{\s*display:\s*flex\s*;", mobile)
    focus = re.search(r"\.nav-toggle-checkbox:focus-visible\s*\+\s*\.nav-toggle-label\s*\{([^}]+)\}", css)
    assert focus and "outline:" in focus.group(1)


def test_shared_type_uses_relative_tokens_and_has_no_tiny_navigation():
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    assert "--t-base: 1rem;" in css
    assert "--t-sm: 0.875rem;" in css
    assert "--font-sans:" in css and "--font-mono:" in css
    assert "font-family: var(--font-sans);" in css
    assert "font-size: var(--t-base);" in css
    assert not re.search(r"font-size:\s*(?:\d|clamp\()[^;}]*px", css)
    # A fixed sticky offset collides when the primary nav wraps or text grows.
    subnav = re.search(r"\.docs-subnav\s*\{([^}]+)\}", css).group(1)
    assert "position: sticky" not in subnav


def test_reference_tables_share_a_grid_inside_named_keyboard_scrollers():
    scanned = 0
    for path in sorted((SITE / "docs").glob("*.html")):
        page = HomepageParser(path.read_text(encoding="utf-8"))
        for table in page.root.find("table"):
            parent = next(node for node in page.elements if any(child is table for child in node.children))
            assert "table-wrap" in parent.attrs.get("class", "").split(), path.name
            assert parent.attrs.get("tabindex") == "0", path.name
            assert parent.attrs.get("role") == "group", path.name
            assert parent.attrs.get("aria-label"), path.name
            scanned += 1
    assert scanned >= 30, "An empty or reduced table corpus is not a pass"
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    assert not re.search(r"table tbody\s*\{[^}]*display:\s*table", css)
    assert ".docs-page .table-wrap:focus-visible" in css


def test_atlas_keeps_readable_map_extent_and_html_selection_on_both_pages():
    for name in ("explore.html", "index.html"):
        page = HomepageParser((SITE / name).read_text(encoding="utf-8"))
        select = next(node for node in page.root.find("select") if "data-node-select" in node.attrs)
        assert select.attrs.get("aria-label") == "Choose a code area"
    page = HomepageParser((SITE / "explore.html").read_text(encoding="utf-8"))
    graph = next(node for node in page.elements if "data-graph" in node.attrs)
    assert graph.attrs.get("tabindex") == "0"
    assert graph.attrs.get("aria-describedby") == "map-navigation-hint"
    assert "Scroll sideways" in page.root.text()
    css = (SITE / "atlas.css").read_text(encoding="utf-8")
    assert "min-width:60rem" in css
    legend = next(node for node in page.elements if node.attrs.get("class") == "atlas-legend")
    assert "data-controls" in legend.attrs and "hidden" in legend.attrs
    assert "Static source snapshot" in page.root.text()


def test_setup_install_command_can_receive_keyboard_scroll_focus():
    page = HomepageParser((SITE / "setup.html").read_text(encoding="utf-8"))
    command = next(node for node in page.elements if node.attrs.get("class") == "install-cmd")
    assert command.attrs.get("tabindex") == "0"
    assert command.attrs.get("aria-label") == "Installation command; scroll sideways if needed"


def test_enlarged_text_can_wrap_long_prose_tokens_and_shrink_the_atlas_grid():
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    assert "main { overflow-wrap: anywhere; }" in css
    atlas_css = (SITE / "atlas.css").read_text(encoding="utf-8")
    assert ".atlas-body { grid-template-columns:minmax(0,1fr); }" in atlas_css
    assert ".trust-strip-inner { grid-template-columns: minmax(0, 1fr); }" in css


def test_manual_status_page_does_not_claim_a_live_health_measurement():
    source = (SITE / "status.html").read_text(encoding="utf-8")
    assert "not a live status feed" in source
    assert "Operational —" not in source
    assert "status-up" not in source
    assert "None to date" not in source


def test_mutable_assets_revalidate_without_conflicting_cache_directives():
    """Stable filenames must not reuse yesterday's stylesheet after new markup."""
    import fnmatch

    rules = []
    for line in (SITE / "_headers").read_text(encoding="utf-8").splitlines():
        if line.startswith("/"):
            rules.append((line.strip(), []))
        elif line.strip().startswith("Cache-Control:"):
            rules[-1][1].append(line.strip().split(":", 1)[1].strip())
    mutable = [
        "/",
        "/explore",
        "/docs/",
        "/landing.css",
        "/home.css",
        "/atlas.css",
        "/atlas.mjs",
        "/atlas-model.mjs",
        "/atlas-data.json",
        "/atlas-map.svg",
    ]
    for path in mutable:
        matches = [value for pattern, values in rules if fnmatch.fnmatchcase(path, pattern) for value in values]
        assert matches == ["public, max-age=0, must-revalidate"], (path, matches)
    # Pages combines repeated values, so a global policy plus a font override
    # is ambiguous. Leave non-overridden files to the provider's ETag default.
    for path in ("/fonts/space-grotesk-latin.woff2", "/og-code-analysis.png", "/favicon.svg"):
        matches = [value for pattern, values in rules if fnmatch.fnmatchcase(path, pattern) for value in values]
        assert len(matches) == 1, (path, matches)


def test_skip_link_target_accepts_focus_without_adding_a_tab_stop():
    paths = sorted(SITE.rglob("*.html"))
    assert len(paths) == 31
    for path in paths:
        page = HomepageParser(path.read_text(encoding="utf-8"))
        main = next(page.root.find("main"))
        assert main.attrs.get("id") == "main"
        assert main.attrs.get("tabindex") == "-1", path.name


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("`**kwargs` and **bold**", "<code>**kwargs</code> and <strong>bold</strong>"),
        ("`a*b` with `c*d`", "<code>a*b</code> with <code>c*d</code>"),
        ("``a`b`` and *emphasis*", "<code>a`b</code> and <em>emphasis</em>"),
        ("`[text](url)`", "<code>[text](url)</code>"),
        ("**`<value>`**", "<strong><code>&lt;value&gt;</code></strong>"),
        ("[Read **this**](https://example.com)", '<a href="https://example.com">Read <strong>this</strong></a>'),
    ],
)
def test_changelog_code_spans_preserve_literal_characters(source, expected):
    rendered = _inline(source)
    assert rendered == expected
    HomepageParser(rendered)


def test_navigation_generator_refuses_missing_or_ambiguous_markup():
    for source in ("<main>No nav</main>", navigation("index.html") * 2):
        with pytest.raises(ValueError, match="exactly one"):
            updated(source, "index.html")
    assert 'aria-current="page"' in navigation("explore.html")
    assert 'aria-current="true"' in navigation("docs/architecture.html")
    assert 'aria-current="page"' in navigation("docs/index.html")
    assert "aria-current" not in navigation("about.html")


def test_changelog_sentinel_cannot_replace_literal_input():
    rendered = _inline("\x00code0\x00 and `**kwargs`")
    assert rendered == "\x00code0\x00 and <code>**kwargs</code>"


def test_press_palette_matches_shared_color_tokens():
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    tokens = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6});", css))
    page = HomepageParser((SITE / "press.html").read_text(encoding="utf-8"))
    checked = 0
    for item in page.root.find("li"):
        codes = [code.text() for code in item.find("code")]
        if len(codes) == 2 and codes[1] in tokens:
            assert codes[0] == tokens[codes[1]]
            checked += 1
    assert checked >= 10, "An empty palette is not a consistency check"


def test_setup_action_preserves_contrast_in_current_and_hover_states():
    """Guard known specificity collisions; browser state testing is separate."""
    shared = (SITE / "landing.css").read_text(encoding="utf-8")
    atlas = (SITE / "atlas.css").read_text(encoding="utf-8")
    current = re.search(r"\.nav-links \.nav-start\[aria-current\]\s*\{([^}]+)\}", shared)
    assert current and "color: #fff" in current.group(1)
    hover = re.search(r"\.nav-links \.nav-start:hover\s*\{([^}]+)\}", shared)
    assert hover and "color: #fff" in hover.group(1)
    assert not re.search(r"\.atlas-page \.nav-links", atlas), "Use the shared navigation colors on the light atlas"


def test_shared_styles_have_no_unresolved_custom_properties():
    css = "\n".join((SITE / name).read_text(encoding="utf-8") for name in ("landing.css", "home.css", "atlas.css"))
    definitions = set(re.findall(r"(--[\w-]+)\s*:", css))
    uses = set(re.findall(r"var\((--[\w-]+)\)", css))
    assert uses and definitions
    assert not uses - definitions


def test_receipt_page_does_not_claim_a_payment_from_a_url_visit():
    page = HomepageParser((SITE / "thank-you.html").read_text(encoding="utf-8"))
    text = normalized(next(page.root.find("main")).text())
    assert "does not confirm a payment, booking, or repository access" in text
    assert "paid and confirmed" not in text


@pytest.mark.parametrize("name", ["docs/index.html", "compare.html", "governance.html", "docs/canonical-demo.html"])
def test_entry_page_introductions_are_short_and_do_not_claim_universal_proof(name):
    page = HomepageParser((SITE / name).read_text(encoding="utf-8"))
    intro = next(node for node in page.root.find("p") if node.attrs.get("class") in {"hero-subhead", "subtitle"})
    words = normalized(intro.text()).split()
    assert 10 <= len(words) <= 65
    assert "proof for every" not in " ".join(words).lower()
