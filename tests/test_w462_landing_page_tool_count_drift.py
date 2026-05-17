"""W462 — drift-guard for MCP tool-count integers on landing-page HTML.

W458 audited the landing-page HTML and found two count integers
(``57`` core preset / ``224`` full registry) repeated across multiple
pages. This test pins those numbers against the canonical AST-derived
counts in ``src/roam/mcp_server.py`` (``_CORE_TOOLS`` + the
``@_tool(name=...)`` decorator scan) so any future preset/registry
change flips a single, clear failure here.

Lightweight by design: no auto-wire substrate (W460 deferred).
"""

from __future__ import annotations

import re

from tests._helpers.repo_root import repo_root

ROOT = repo_root()

_LANDING_PAGES = (
    "templates/distribution/landing-page/index.html",
    "templates/distribution/landing-page/docs/command-reference.html",
    "templates/distribution/landing-page/docs/mcp-usage.html",
    "templates/distribution/landing-page/press.html",
    "templates/distribution/landing-page/pricing.html",
)

# MCP-context phrasings only — excludes unrelated "5 core verbs" (CLI verbs).
_MCP_NUM = re.compile(
    r"""(?ix)\b(\d+)(?:[\s\-/]+)(?:
        core\s+(?:agent\s+)?tools?
      | core\s+structured\s+questions
      | core\s*/?\s*\d+
      | tools?\s+plus
      | (?:total\s+)?MCP\s+tools?
      | full\s+preset\s+tools?
      | in\s+<code>full</code>
      | tool\s+core\s+preset
      | in\s+the\s+default\s+core\s+preset
    )""",
)
_MCP_HTML = re.compile(r"<strong>(\d+)</strong>\s+MCP\s+tools?", re.IGNORECASE)


def test_landing_page_mcp_tool_counts_match_canonical():
    """Every MCP-tool-count claim on the landing-page must match canonical."""
    from roam.surface_counts import mcp_surface_counts

    counts = mcp_surface_counts()
    core, full = counts["core_tools"], counts["registered_tools"]
    canonical = {core, full}
    failures = []
    for rel in _LANDING_PAGES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for m in (*_MCP_NUM.finditer(text), *_MCP_HTML.finditer(text)):
            n = int(m.group(1))
            if n not in canonical:
                failures.append(f"{rel}: scraped {n} not in {{core={core}, full={full}}} via {m.group(0)!r}")
    assert not failures, "Landing-page MCP-tool-count drift:\n  " + "\n  ".join(failures)
