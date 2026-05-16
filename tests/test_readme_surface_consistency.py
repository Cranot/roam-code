import ast
import re
import sys

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.surface_counts import canonical_cli_commands, cli_commands, mcp_tool_names


def _deprecated_alias_names() -> set[str]:
    """Return the set of deprecated CLI alias names from ``cli._DEPRECATED_COMMANDS``.

    Loaded via AST so the allowlist stays in lockstep with the cli.py
    source of truth (matches the AST-only discipline of ``surface_counts``).
    """
    cli_path = ROOT / "src" / "roam" / "cli.py"
    module = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    for node in module.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            value = node.value
        if target == "_DEPRECATED_COMMANDS" and value is not None:
            obj = ast.literal_eval(value)
            if isinstance(obj, dict):
                return {name for name in obj if isinstance(name, str)}
    return set()


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
    """Gate both directions on the README's CLI tables.

    - missing: canonical command absent from README -> agents lose discoverability.
    - extras: README references a name that is no longer canonical (deleted /
      renamed / typo) -> agents copy-paste a command that no longer exists.

    Deprecated aliases listed in ``cli._DEPRECATED_COMMANDS`` are tolerated as
    extras because they remain user-typeable until removal (W697; mirrors the
    missing+extra dual gate already enforced on the MCP tool list).
    """
    text = _readme_text()
    readme_cmds = _readme_cli_commands(text)
    canonical = set(canonical_cli_commands())
    all_registered = set(cli_commands().keys())
    allowed_aliases = _deprecated_alias_names()

    missing = sorted(canonical - readme_cmds)
    assert not missing, f"README missing CLI commands: {missing}"

    extras = readme_cmds - canonical - allowed_aliases
    # Split for a sharper error: alias-but-not-allowlisted vs entirely unknown.
    alias_extras = sorted(extras & all_registered)
    unknown_extras = sorted(extras - all_registered)
    assert not alias_extras, (
        f"README references CLI aliases not in cli._DEPRECATED_COMMANDS: {alias_extras}. "
        f"Either promote them to canonical, drop them from the README, or add them "
        f"to _DEPRECATED_COMMANDS with a replacement."
    )
    assert not unknown_extras, (
        f"README references unknown CLI commands (deleted / renamed / typo): "
        f"{unknown_extras}. Update or remove these rows."
    )


def test_readme_mcp_tool_list_matches_source():
    text = _readme_text()
    readme_tools = _readme_mcp_tools(text)
    source_tools = set(mcp_tool_names())
    missing = sorted(source_tools - readme_tools)
    extra = sorted(readme_tools - source_tools)
    assert not missing, f"README missing MCP tools: {missing}"
    assert not extra, f"README has unknown MCP tools: {extra}"
    # The collapsed-section header must quote the same integer as the
    # tool list itself. Pre-v12 this drifted (header said "all 101" while
    # there were 102 entries). Extract the literal and compare.
    match = re.search(r"MCP tool list \(all (\d+)\)", text)
    assert match, "README must contain a 'MCP tool list (all N)' header"
    quoted = int(match.group(1))
    assert quoted == len(source_tools) == len(readme_tools), (
        f"README header says 'all {quoted}', source has {len(source_tools)}, "
        f"README table has {len(readme_tools)} — these must agree"
    )


def test_readme_cli_command_count_matches_source():
    """Symmetric counterpart to ``test_readme_mcp_tool_list_matches_source``.

    The MCP table has a ``(all N)`` header pinned to ``len(mcp_tool_names())``
    so deleting one row while adding another (cancelling deltas) still fails
    the count gate. The CLI tables had no equivalent: a silently DELETED
    canonical command would only fail
    ``test_readme_covers_all_canonical_cli_commands`` if the deletion did
    not cancel out an addition elsewhere. W685.

    Source of truth: ``canonical_cli_commands()`` (aliases collapsed) —
    matches what the canonical-coverage test above already enforces.
    """
    text = _readme_text()
    match = re.search(
        r"Full command reference — canonical command list \(all (\d+)\)",
        text,
    )
    assert match, (
        "README must contain a 'Full command reference — canonical command "
        "list (all N)' header (symmetric to the MCP tool list pin)"
    )
    quoted = int(match.group(1))
    canonical_count = len(canonical_cli_commands())
    assert quoted == canonical_count, (
        f"README header says 'all {quoted}', source has {canonical_count} "
        f"canonical CLI commands — these must agree. Regenerate via "
        f"`python dev/build_readme_counts.py --apply`."
    )


def test_readme_has_v11_narrative_section():
    text = _readme_text()
    assert "## What's New in v11" in text
    assert "MCP v2" in text
    assert "92% reduction" in text
    # v12.2: the "1000x" speedup claim was softened to a measured-cohort
    # statement during the adversarial-review pass (the original number
    # was unsourced and a competitor would screenshot it). The contract
    # this test now enforces: the FTS5/BM25 perf narrative is still in
    # the README, just no longer with a fragile multiplier.
    assert "FTS5/BM25" in text
    assert "milliseconds" in text
    assert "O(changed)" in text
    assert "SARIF" in text


def test_cli_deprecated_commands_is_ast_literal():
    """W702 contract: ``_DEPRECATED_COMMANDS`` must be a literal dict so the
    W697 AST-based auto-allowlist (``_deprecated_alias_names``) stays sound.

    A future refactor to a computed form (``dict(**...)``, comprehension,
    Call, etc.) would silently make ``ast.literal_eval`` raise — the W697
    allowlist would collapse to ``set()`` and the README extras gate would
    degrade to "no aliases tolerated" without any explicit failure here.
    """
    cli_path = ROOT / "src" / "roam" / "cli.py"
    module = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    for node in module.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            value = node.value
        if target != "_DEPRECATED_COMMANDS" or value is None:
            continue
        try:
            obj = ast.literal_eval(value)
        except (ValueError, SyntaxError) as exc:
            pytest.fail(
                "_DEPRECATED_COMMANDS contains a non-literal expression "
                "(W702): the W697 auto-allowlist relies on ast.literal_eval "
                f"and will silently collapse. Underlying error: {exc!r}"
            )
        assert isinstance(obj, dict), "_DEPRECATED_COMMANDS must be a dict"
        assert all(isinstance(k, str) for k in obj), (
            "_DEPRECATED_COMMANDS alias names must be str (the W697 allowlist set is keyed on str)"
        )
        return
    pytest.fail("_DEPRECATED_COMMANDS assignment not found in src/roam/cli.py")


def test_readme_roadmap_refreshed_for_v11_state():
    """W1289: pinning v11-era headings is stale — README evolved to v13.2.

    The original test asserted the README's Roadmap section had the
    v11 ``### Shipped`` / ``### Next`` headings plus a ``MCP v2 agent
    surface`` line. v13.2's README replaced that section entirely with
    a narrative geared to the agentic-assurance frame. The test still
    catches the only stale-era signal worth pinning: that the old
    "19 tools" count line is gone.
    """
    text = _readme_text()
    # Stale count-era roadmap line must not remain. (Load-bearing.)
    assert "MCP server -- 19 tools, 2 resources" not in text
