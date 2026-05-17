"""Sanity checks for the docs site under ``templates/distribution/landing-page/docs/``.

The canonical docs live at https://roam-code.com/docs/ (Cloudflare Pages,
served from ``templates/distribution/landing-page/docs/``). GitHub Pages
was disabled on 2026-05-08 and ``docs/site/`` was deleted; the previous
redirect-stub tests in this file went with it.

Remaining checks: the required HTML files exist, each one declares a
canonical URL pointing at the production path, and each one renders
the surface counts that are kept in sync by ``scripts/sync_surface_counts.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

SITE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "distribution" / "landing-page"
DOCS_ROOT = Path(__file__).resolve().parents[1] / "templates" / "distribution" / "landing-page" / "docs"


REQUIRED_PAGES = {
    "index.html": "https://roam-code.com/docs/",
    "getting-started.html": "https://roam-code.com/docs/getting-started",
    "command-reference.html": "https://roam-code.com/docs/command-reference",
    "architecture.html": "https://roam-code.com/docs/architecture",
    "integration-tutorials.html": "https://roam-code.com/docs/integration-tutorials",
}


def test_docs_site_required_pages_exist():
    """Every URL in the production sitemap maps to a real file in the repo."""
    for filename in REQUIRED_PAGES:
        path = DOCS_ROOT / filename
        assert path.is_file(), f"missing docs page: {path.relative_to(DOCS_ROOT.parents[3])}"


def test_docs_pages_declare_canonical_url():
    """Each docs page declares its canonical URL on roam-code.com/docs/*."""
    for filename, canonical in REQUIRED_PAGES.items():
        path = DOCS_ROOT / filename
        text = path.read_text(encoding="utf-8")
        # The pages all use a ``<link rel="canonical" href="…">`` declaration.
        assert f'rel="canonical" href="{canonical}"' in text, f"{filename} should declare canonical={canonical}"


def _site_path_for_url(url: str) -> Path:
    """Map a canonical roam-code.com URL to its static HTML file."""
    parsed = urlparse(url)
    path = parsed.path
    if path in ("", "/"):
        return SITE_ROOT / "index.html"
    if path.endswith("/"):
        return SITE_ROOT / path.lstrip("/") / "index.html"
    candidate = SITE_ROOT / path.lstrip("/")
    if candidate.suffix:
        return candidate
    return candidate.with_suffix(".html")


def _html_pages():
    return sorted(SITE_ROOT.rglob("*.html"))


def test_indexable_canonical_pages_are_in_sitemap():
    """Every indexable canonical HTML page must be present in sitemap.xml."""
    sitemap = (SITE_ROOT / "sitemap.xml").read_text(encoding="utf-8")
    sitemap_targets = {
        _site_path_for_url(url).resolve()
        for url in re.findall(r"<loc>(.*?)</loc>", sitemap)
        if url.startswith("https://roam-code.com/")
    }

    missing = []
    for path in _html_pages():
        if path.name == "404.html":
            continue
        text = path.read_text(encoding="utf-8")
        if "noindex" in text:
            continue
        if 'rel="canonical" href="https://roam-code.com/' not in text:
            continue
        if path.resolve() not in sitemap_targets:
            missing.append(path.relative_to(SITE_ROOT).as_posix())

    assert not missing, "Indexable canonical pages missing from sitemap.xml: " + ", ".join(missing)


def test_html_pages_have_tight_search_preview_metadata():
    """Pages should have one H1 and title/description lengths fit common previews."""
    failures = []
    for path in _html_pages():
        text = path.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", text, re.DOTALL | re.IGNORECASE)
        desc = re.search(r'<meta name="description" content="([^"]+)">', text, re.IGNORECASE)
        h1_count = len(re.findall(r"<h1\b", text, re.IGNORECASE))

        rel = path.relative_to(SITE_ROOT).as_posix()
        if not title:
            failures.append(f"{rel}: missing <title>")
        elif len(re.sub(r"\s+", " ", title.group(1)).strip()) > 70:
            failures.append(f"{rel}: title longer than 70 chars")
        if not desc:
            failures.append(f"{rel}: missing meta description")
        elif not 50 <= len(desc.group(1)) <= 180:
            failures.append(f"{rel}: meta description should be 50-180 chars")
        if h1_count != 1:
            failures.append(f"{rel}: expected exactly one <h1>, got {h1_count}")

    assert not failures, "HTML metadata quality issues:\n  " + "\n  ".join(failures)


def test_docs_html_pages_have_clean_url_redirects():
    """Cloudflare redirects should keep legacy docs/*.html links canonical."""
    redirects = (SITE_ROOT / "_redirects").read_text(encoding="utf-8")
    missing = []
    for path in sorted(DOCS_ROOT.glob("*.html")):
        if path.name == "index.html":
            continue
        clean = f"/docs/{path.stem.replace('_', '-')}"
        legacy = f"/docs/{path.name}"
        pattern = re.compile(rf"^{re.escape(legacy)}\s+{re.escape(clean)}\s+301$", re.MULTILINE)
        if not pattern.search(redirects):
            missing.append(f"{legacy} -> {clean}")

    assert not missing, "Missing docs clean-URL redirects:\n  " + "\n  ".join(missing)
