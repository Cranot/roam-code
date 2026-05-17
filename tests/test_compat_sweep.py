"""Meta-tests pinning the compat sweep that followed the memory-commands sprint.

The sprint added ``roam memory add/list/relevant`` (215 commands total) and
brushed up against three classes of test-compat debt:

1. **Surface-count snapshots in README / CLAUDE.md.** The README's count
   sub-headline (``N commands · M MCP tools · 28 languages``) and CLAUDE.md's
   ``command_count: N`` block must track the live count from
   ``roam.surface_counts``. This file asserts they agree so future sprints
   can't accidentally let them drift. (W844-drive-by-2: the headline-IS-
   the-count framing was dropped in v13.2; the README hero now leads with
   credential-free + zero-egress + tamper-evident positioning, with counts
   relegated to a sub-line. The assertions below still pin the sub-line.)

2. **Click 8.3 stdout/stderr split.** ``parse_json_output`` in conftest now
   prefers ``result.stdout`` over the merged ``result.output``. Tests that
   parse JSON should funnel through that helper; tests that only substring-
   check ``result.output`` continue to work because Click 8.3 still exposes
   ``output`` as the merged stream.

3. **FastMCP 2.14 ``FunctionTool`` wrap.** The MCP test suite already uses
   ``_unwrap()`` helpers (see ``test_mcp_handle_off.py`` /
   ``test_response_volume_handles.py``) — no xfail cluster is required here.

The single goal of this file is the snapshot pin in section 1.
"""

from __future__ import annotations

import re
import sys

from tests._helpers.repo_root import repo_root

# Resolve via git's canonical toplevel so nested-worktree dispatch
# (``.claude/worktrees/.../.claude/worktrees/...``) still finds the
# project root that owns ``CLAUDE.md`` / ``README.md`` (W572).
ROOT = repo_root()
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.surface_counts import cli_surface_counts, mcp_surface_counts


def _readme_text() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _claude_md_text() -> str | None:
    # CLAUDE.md is intentionally untracked (removed from the public repo in
    # commit 89a338d9). Returning None lets callers skip CLAUDE.md-specific
    # assertions on CI / fresh clones without faking a hit.
    path = ROOT / "CLAUDE.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Section 1 — surface-count snapshots
# ---------------------------------------------------------------------------


def test_surface_command_count_matches_actual():
    """README + CLAUDE.md command counts must equal the live ``_COMMANDS``.

    Both files quote ``command_names`` (counts aliases) because that is the
    integer the user sees from ``roam --help-all``. ``canonical_commands``
    is the alias-collapsed view; we accept either form as long as it agrees
    with ``surface_counts``.
    """
    counts = cli_surface_counts()
    valid = {counts["command_names"], counts["canonical_commands"]}

    # README headline.
    readme = _readme_text()
    m = re.search(r"\b(\d+)\s+commands\b", readme)
    assert m, "README missing 'N commands' headline phrase"
    n = int(m.group(1))
    assert n in valid, (
        f"README says '{n} commands' but live counts are "
        f"command_names={counts['command_names']} canonical_commands="
        f"{counts['canonical_commands']}"
    )

    # CLAUDE.md headline + the ``Authoritative counts:`` line.
    # CLAUDE.md is intentionally untracked on public clones / CI — skip the
    # CLAUDE.md-specific assertions when absent. The README check above is
    # the primary defence; CLAUDE.md is defence-in-depth on local dev only.
    claude = _claude_md_text()
    if claude is not None:
        m_claude = re.search(r"\*\*(\d+)\s+commands", claude)
        assert m_claude, "CLAUDE.md missing '**N commands' headline phrase"
        n_claude = int(m_claude.group(1))
        assert n_claude in valid, (
            f"CLAUDE.md headline says '{n_claude} commands' but live counts are "
            f"command_names={counts['command_names']} canonical_commands="
            f"{counts['canonical_commands']}"
        )

        m_auth = re.search(r"command_count:\s*(\d+)", claude)
        assert m_auth, "CLAUDE.md missing 'command_count: N' authoritative-counts line"
        n_auth = int(m_auth.group(1))
        assert n_auth in valid, (
            f"CLAUDE.md authoritative-counts line says 'command_count: {n_auth}' "
            f"but live counts are command_names={counts['command_names']} "
            f"canonical_commands={counts['canonical_commands']}"
        )


def test_mcp_tool_count_matches_actual():
    """README's ``N MCP tools`` must equal the live registered-tool count.

    The live count comes from the AST-derived ``registered_tools`` total
    (every ``@_tool(name=...)`` decorator in ``mcp_server.py``). The README
    line refers to the ``full`` preset, which is what ``registered_tools``
    enumerates — the ``core`` preset is the smaller curated subset.
    """
    counts = mcp_surface_counts()
    full_total = counts["registered_tools"]

    readme = _readme_text()
    m = re.search(r"\b(\d+)\s+MCP\s+tools\b", readme)
    assert m, "README missing 'N MCP tools' headline phrase"
    n = int(m.group(1))
    assert n == full_total, f"README says '{n} MCP tools' but live registered_tools is {full_total}"
