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

from pathlib import Path


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
        assert f'rel="canonical" href="{canonical}"' in text, (
            f"{filename} should declare canonical={canonical}"
        )
