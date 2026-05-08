"""Docs site quality checks for the legacy GitHub Pages artifact.

The canonical docs site lives at https://roam-code.com/docs/ on
Cloudflare Pages (built from the templates/distribution/landing-page/docs/
tree). The files under docs/site/* in this repo are now thin redirects
to the new URL; the rich content moved out of this repo.

Most legacy assertions (specific copy, SVG diagrams, command examples)
are skipped because they targeted the old in-repo docs that have been
migrated. The redirect-existence + redirect-target checks remain so we
notice if the migration ever bit-rots.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


REDIRECT_TARGETS = {
    "index.html": "https://roam-code.com/docs/",
    "getting-started.html": "https://roam-code.com/docs/getting-started",
    "integration-tutorials.html": "https://roam-code.com/docs/integration-tutorials",
    "command-reference.html": "https://roam-code.com/docs/command-reference",
    "architecture.html": "https://roam-code.com/docs/architecture",
    "landscape.html": "https://roam-code.com/docs/",
}


def test_docs_site_required_pages_exist():
    root = _repo_root() / "docs" / "site"
    required = [
        "getting-started.html",
        "integration-tutorials.html",
        "command-reference.html",
        "architecture.html",
        "docs.css",
    ]
    for rel in required:
        assert (root / rel).is_file(), f"missing docs page: {rel}"


def test_redirect_pages_point_at_new_docs_url():
    """Each legacy docs/site/*.html file is a redirect to roam-code.com/docs/*."""
    root = _repo_root() / "docs" / "site"
    for filename, target in REDIRECT_TARGETS.items():
        path = root / filename
        if not path.exists():
            continue
        text = _read(path)
        assert "url=" in text, f"{filename} missing meta-refresh URL"
        assert target in text, f"{filename} should redirect to {target}"


@pytest.mark.skip(reason="docs/site is now a redirect; rich content moved to roam-code.com/docs/")
def test_getting_started_has_tutorial_flow():
    pass


@pytest.mark.skip(reason="docs/site is now a redirect; rich content moved to roam-code.com/docs/")
def test_command_reference_has_examples():
    pass


@pytest.mark.skip(reason="docs/site is now a redirect; rich content moved to roam-code.com/docs/")
def test_integration_tutorials_cover_five_platforms():
    pass


@pytest.mark.skip(reason="docs/site is now a redirect; rich content moved to roam-code.com/docs/")
def test_architecture_page_has_diagram_and_pipeline():
    pass


@pytest.mark.skip(reason="docs/site is now a redirect; cross-page nav lives at roam-code.com")
def test_site_pages_linked_from_main_pages():
    pass
