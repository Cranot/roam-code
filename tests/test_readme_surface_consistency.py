import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.surface_counts import canonical_cli_commands, mcp_tool_names


def _readme_text() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _readme_cli_commands(text: str) -> set[str]:
    rows = re.findall(r"\|\s*`roam\s+([^`]+)`\s*\|", text)
    cmds: set[str] = set()
    for row in rows:
        token = row.strip().split()[0]
        if token.startswith("-") or token.startswith("<"):
            continue
        cmds.add(token)
    return cmds


def _readme_mcp_tools(text: str) -> set[str]:
    return set(re.findall(r"\|\s*`(roam_[a-z0-9_-]+)`\s*\|", text))


def test_readme_covers_all_canonical_cli_commands():
    text = _readme_text()
    readme_cmds = _readme_cli_commands(text)
    canonical = set(canonical_cli_commands())
    missing = sorted(canonical - readme_cmds)
    assert not missing, f"README missing CLI commands: {missing}"


def test_readme_mcp_tool_list_matches_source():
    text = _readme_text()
    readme_tools = _readme_mcp_tools(text)
    source_tools = set(mcp_tool_names())
    missing = sorted(source_tools - readme_tools)
    extra = sorted(readme_tools - source_tools)
    assert not missing, f"README missing MCP tools: {missing}"
    assert not extra, f"README has unknown MCP tools: {extra}"
    assert "MCP tool list (all 101)" in text


def test_readme_has_v11_narrative_section():
    text = _readme_text()
    assert "## What's New in v11" in text
    assert "MCP v2" in text
    assert "92% reduction" in text
    assert "1000x" in text
    assert "O(changed)" in text
    assert "SARIF" in text


def test_readme_roadmap_refreshed_for_v11_state():
    text = _readme_text()
    assert "### Shipped" in text
    assert "### Next (v11 Closeout + immediate follow-up)" in text
    assert "MCP v2 agent surface" in text
    # Stale count-era roadmap line should not remain.
    assert "MCP server -- 19 tools, 2 resources" not in text
