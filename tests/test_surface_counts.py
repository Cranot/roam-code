from __future__ import annotations

import re
import sys
from pathlib import Path

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
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
    # Alias lineup. Round 4 #16 added trend/digest/snapshot as aliases
    # of the consolidated `roam trends` so docs keep resolving.
    # (v12.12.7) added ``refs`` as an alias for ``uses`` to
    # give agents a grep-familiar entry point for "find references".
    assert counts["alias_groups"] == [
        ["algo", "math"],
        ["churn", "weather"],
        ["digest", "snapshot", "trend", "trends"],
        ["onboard", "understand"],
        ["refs", "uses"],
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
    v11 (138 vs 140 in different files). We accept either ``command_names``
    (counts aliases) or ``canonical_commands`` (deduped) since both are
    defensible public counts.
    """
    counts = cli_surface_counts()
    valid = {counts["command_names"], counts["canonical_commands"]}
    for doc in ("README.md", "CLAUDE.md", "llms-install.md"):
        if not Path(doc).exists():
            continue
        text = _read(doc)
        n = _docs_command_count(text)
        assert n is not None, f"{doc} has no '<N> commands' phrase"
        assert n in valid, f"{doc} says '{n} commands' — expected one of {sorted(valid)}"


def test_readme_specialised_command_count_matches_five_verb_model():
    counts = cli_surface_counts()
    valid = {counts["command_names"] - 5, counts["canonical_commands"] - 5}
    text = _read("README.md")
    # Allow either "other N specialised commands" or "remaining ~N commands" phrasing
    match = re.search(r"\b(?:other|remaining)\s+~?(\d+)\s+(?:specialised|commands)\b", text)
    assert match, "README missing 'other N specialised commands' / 'remaining ~N commands' phrase"
    n = int(match.group(1))
    assert n in valid, f"README says {n} — expected one of {sorted(valid)} (counts.5verb)"


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
