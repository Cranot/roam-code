"""Pin the invariant that every fragment in ``_DOC_LINKS`` points to an
existing ``id="..."`` anchor on the troubleshooting docs page.

R7+ G5 wired the existing ``_DOC_LINKS`` table to real anchors on
``templates/distribution/landing-page/docs/troubleshooting.html``.
Without this test, anyone editing the docs page can rename / delete an
``id="..."`` and the agent-facing ``doc_link`` URLs will silently 404
to the right page but scroll nowhere.

The test reads both the source-of-truth dict and the static HTML, so
it fails fast (in CI, before deploy) on either side drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TROUBLESHOOTING_HTML = (
    PROJECT_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "troubleshooting.html"
)


@pytest.fixture(scope="module")
def page_anchors() -> set[str]:
    """All ``id="..."`` values in the troubleshooting page."""
    if not TROUBLESHOOTING_HTML.is_file():
        pytest.skip(f"docs page not present at {TROUBLESHOOTING_HTML}")
    text = TROUBLESHOOTING_HTML.read_text(encoding="utf-8")
    return set(re.findall(r'id="([^"]+)"', text))


@pytest.fixture(scope="module")
def doc_links() -> dict[str, str]:
    """The error_code → doc-URL map exposed by the MCP server."""
    try:
        from roam.mcp_server import _DOC_LINKS
    except ImportError:
        pytest.skip("FastMCP not installed; mcp_server module not importable")
    return dict(_DOC_LINKS)


def test_every_fragment_has_a_matching_anchor(page_anchors, doc_links):
    """For every URL with a ``#fragment``, the fragment must exist as
    an ``id`` on the docs page. URLs without a fragment are page-level
    fallbacks and don't need anchor matching."""
    missing: list[tuple[str, str]] = []
    for code, url in doc_links.items():
        if "#" not in url:
            continue
        fragment = url.split("#", 1)[1]
        if fragment not in page_anchors:
            missing.append((code, fragment))
    assert not missing, (
        "_DOC_LINKS references docs anchors that don't exist:\n"
        + "\n".join(f"  {c} -> #{f}" for c, f in missing)
        + "\n\n"
        + f"Page has these anchors: {sorted(page_anchors)}"
    )


def test_known_error_codes_have_doc_links(doc_links):
    """Tripwire: if a future change drops a code from the map, fail
    fast so we don't ship an MCP error envelope without a doc_link."""
    required = {
        "INDEX_NOT_FOUND",
        "INDEX_STALE",
        "DB_LOCKED",
        "PERMISSION_DENIED",
        "UNKNOWN",
    }
    missing = required - doc_links.keys()
    assert not missing, f"_DOC_LINKS missing required error codes: {sorted(missing)}"


def test_all_doc_links_are_https_to_canonical_domain(doc_links):
    """Every doc_link must be ``https://roam-code.com/...``. Catches
    accidental ``localhost:`` / staging / ``http://`` regressions."""
    bad: list[tuple[str, str]] = []
    for code, url in doc_links.items():
        if not url.startswith("https://roam-code.com/"):
            bad.append((code, url))
    assert not bad, (
        "Some _DOC_LINKS values are not https://roam-code.com URLs:\n"
        + "\n".join(f"  {c} -> {u}" for c, u in bad)
    )
