import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.surface_counts import (
    cli_surface_counts,
    collect_surface_counts,
    mcp_surface_counts,
    mcp_tool_names,
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_cli_surface_counts_self_consistent():
    """Counts derived from `_COMMANDS` must add up internally.

    Replaces the v11.x hard-coded "143/140/3" assertions. The shape stays
    fixed (canonical + aliases = command_names) so adding a command bumps
    the integers without bumping this test.
    """
    counts = cli_surface_counts()
    assert counts["command_names"] == counts["canonical_commands"] + counts["alias_names"]
    # We currently ship exactly three alias pairs. If the lineup changes,
    # the test deserves a deliberate update.
    assert counts["alias_groups"] == [
        ["algo", "math"],
        ["churn", "weather"],
        ["onboard", "understand"],
    ]
    # Sanity floor — never silently regress to v11-era counts.
    assert counts["canonical_commands"] >= 141


def test_mcp_surface_counts_self_consistent():
    counts = mcp_surface_counts()
    # `mcp_tool_names()` and `registered_tools` must agree — drift here is
    # the v11 bug we caught in v12 (README said "all 101", source had 102).
    assert counts["registered_tools"] == len(mcp_tool_names())
    assert counts["duplicate_tool_names"] == []
    # Core preset is intentionally a small curated subset; floor only.
    assert counts["core_tools"] >= 23
    assert counts["registered_tools"] >= counts["core_tools"]
    # Sanity floor — never silently regress.
    assert counts["registered_tools"] >= 103


def _docs_command_count(text: str) -> int | None:
    """Pull the first `<int> commands` integer from a doc, or None."""
    match = re.search(r"\b(\d+)\s+commands\b", text)
    return int(match.group(1)) if match else None


def test_docs_command_count_matches_source():
    """README, CLAUDE.md, and llms-install.md must quote the same integer
    that ``cli_surface_counts()`` reports — drift here is what bit us in
    v11 (138 vs 140 in different files).
    """
    canonical = cli_surface_counts()["canonical_commands"]
    for doc in ("README.md", "CLAUDE.md", "llms-install.md"):
        if not Path(doc).exists():
            continue
        text = _read(doc)
        n = _docs_command_count(text)
        assert n is not None, f"{doc} has no '<N> commands' phrase"
        assert n == canonical, f"{doc} says '{n} commands' but `_COMMANDS` has {canonical} canonical commands"


def test_collect_surface_counts_shape():
    payload = collect_surface_counts()
    assert set(payload.keys()) == {"cli", "mcp"}
    # Tighten the schema: every nested section must have positive integers
    # for the headline counts.
    cli = payload["cli"]
    assert cli["canonical_commands"] > 0
    assert cli["command_names"] >= cli["canonical_commands"]
    mcp = payload["mcp"]
    assert mcp["registered_tools"] > 0
    assert mcp["core_tools"] > 0
    assert mcp["registered_tools"] >= mcp["core_tools"]
