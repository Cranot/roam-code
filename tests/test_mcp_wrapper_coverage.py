"""W22.4 MCP-wrapper coverage audit — advisory.

CLAUDE.md's "Adding a new CLI command" checklist names eight steps. Steps
2/3/5/6/7 are mechanically gated (the CLI registry, ``_DEPRECATED_COMMANDS``,
``@roam_capability``, and the LAW 4 lint all fail loudly on drift). Step 4
— "Add MCP tool wrapper in ``mcp_server.py`` if useful for agents" — was
the lone judgement-shaped step, and the only one whose absence silently
shipped: a new command could land with no MCP wrapper and no test would
catch it.

This module closes that audit gap by **comparing the CLI command surface
against the MCP tool surface and reporting any command that isn't wrapped
and isn't explicitly allowlisted as "doesn't need a wrapper".**

Why advisory, not hard-gating
-----------------------------

The sprint that introduced this audit had a long-standing no-touch rule
on ``src/roam/mcp_server.py`` — agents in flight (W22.2, W22.3, W22.5,
W23.3, W24.1) were already editing surrounding files and a hard registry
gate would force concurrent edits to that file. So the audit ships in
**advisory mode**: it computes the unwrapped set, subtracts the four
skip-taxonomy allowlists below, and ``pytest.xfail``s when the residual
("real gap") set is non-empty. A future wave can flip ``XFAIL_ON_GAP`` to
``False`` after the gap is wired up.

Detection strategy
------------------

We re-use the AST-only helper ``roam.surface_counts.mcp_tool_names()``
(introduced in W6.1) so the test does not import ``mcp_server.py``. We
then ask, for every CLI command name in ``_COMMANDS``, whether *any* of a
small set of plausible MCP tool names exists in the registered set:

* ``roam_<command-with-underscores>`` (trivial mapping)
* ``roam_<command>_<suffix>`` for a known list of suffix patterns
  (``_check``, ``_report``, ``_export``, ``_verify``, ``_emit``,
  ``_validate``, ``_changes``, ``_code``, ``_info``, ``_blame``)
* An explicit entry in ``_KNOWN_TOOL_ALIASES``

The allowlists capture the four categories from the W22.4 skip-taxonomy
in CLAUDE.md (setup/bootstrap, local-state, daemon, REPL helper). Names
not present in any allowlist and not wrapped are the "real gap" — the
list a future wave needs to wire up.
"""

from __future__ import annotations

import pytest

# Flip this to ``False`` once the real-gap list is empty (or trimmed to
# zero by adding the missing wrappers to ``mcp_server.py``). Until then,
# the test fails softly via ``pytest.xfail`` so CI surfaces drift without
# blocking unrelated PRs.
XFAIL_ON_GAP = True


# ---------------------------------------------------------------------------
# Skip-taxonomy allowlists (kept in sync with CLAUDE.md "Adding a new CLI
# command" step 4 — the four "doesn't need an MCP wrapper" categories).
# ---------------------------------------------------------------------------

# 1) Setup / bootstrap — one-time human-driven, no value through MCP.
_KNOWN_SETUP_COMMANDS: set[str] = {
    "init",            # repo-local index bootstrap
    "index",           # reindex bootstrap (covered by roam_reindex)
    "index-export",    # bundle ops
    "index-import",
    "index-stats",
    "graph-export",    # writes to disk; MCP would have to relay a file path
    "ci-setup",        # generates GH Actions / GitLab CI YAML on disk
    "mcp-setup",       # generates MCP server config files on disk
    "mcp-status",      # connectivity diagnostic from outside the MCP layer
    "mcp",             # the MCP server itself; can't expose-via-MCP
    "hooks",           # installs git hooks on disk
    "pre-commit",      # installs git pre-commit hook on disk
    "plugins",         # introspect plugin manifests on disk
    "db-check",        # integrity sweep over the local SQLite DB
    "schema",          # JSON envelope schema reference
    "config",          # writes .roam/config.json on disk
    "version",         # static version string
    "exit-codes",      # static exit-code reference
    "help-search",     # introspects Click help strings (CLI-only utility)
    "explain-command", # describes a command's contract (CLI-only utility)
    "recipes",         # lists ask recipes (CLI-only utility)
    "skill-generate",  # writes a skill manifest YAML on disk
    "agents-md",       # writes AGENTS.md on disk
    "laws",            # self-installing constitution; writes on disk
    "constitution",    # repo-local constitution CLI; writes on disk
    "capabilities",    # emits capability registry YAML/JSON
    "surface",         # canonical capability registry inventory
    "telemetry",       # local telemetry ring buffer
    "lsp",             # Language Server Protocol stdio server (not MCP)
}

# 2) Local-state only — the value lives on disk in ``.roam/``; running it
#    through a stateless MCP call would not surface anything an agent
#    couldn't get from the file directly.
_KNOWN_LOCAL_STATE_COMMANDS: set[str] = {
    "memory",         # repo-local agent memory store
    "annotate",       # persistent symbol/file annotations
    "annotations",    # alias of annotate
    "runs",           # per-agent-run event ledger
    "mode",           # agent-mode policy
    "lease",          # multi-agent lease registry
    "replay",         # re-narrate a past run from the ledger
    "pr-replay",      # generate buyer-facing replay report
    "suppress",       # record audit-trail-friendly suppression
    "permit",         # structural-permission verdict facade
    "intent-check",   # pre-flight on intended command (uses on-disk mode)
    "ws",             # workspace state (multi-repo grouping)
}

# 3) Daemon / long-running — incompatible with stateless MCP invocations.
_KNOWN_DAEMON_COMMANDS: set[str] = {
    "watch",  # poll-for-changes auto-reindex daemon
}

# 4) REPL / interactive helpers — N/A in MCP context.
_KNOWN_REPL_HELPERS: set[str] = set()  # currently empty; reserved.

_ALL_SKIPPED = (
    _KNOWN_SETUP_COMMANDS
    | _KNOWN_LOCAL_STATE_COMMANDS
    | _KNOWN_DAEMON_COMMANDS
    | _KNOWN_REPL_HELPERS
)


# ---------------------------------------------------------------------------
# Tool-name aliases — CLI command -> set of plausible MCP tool names where
# the underscore-form rule alone would miss the wrapper. Cross-checked by
# grepping ``@_tool(name="roam_...")`` in ``mcp_server.py``.
# ---------------------------------------------------------------------------

_KNOWN_TOOL_ALIASES: dict[str, set[str]] = {
    "dead":            {"roam_dead_code"},
    "complexity":      {"roam_complexity_report"},
    "bisect":          {"roam_bisect_blame"},
    "breaking":        {"roam_breaking_changes"},
    "budget":          {"roam_budget_check"},
    "capsule":         {"roam_capsule_export"},
    "cga":             {"roam_cga_emit", "roam_cga_verify"},
    "rules":           {"roam_check_rules", "roam_rules_check", "roam_rules_validate"},
    "trends":          {"roam_trends"},
    "trend":           {"roam_trends"},
    "snapshot":        {"roam_trends"},
    "digest":          {"roam_trends"},
    "churn":           {"roam_weather"},
    "weather":         {"roam_weather"},
    "uses":            {"roam_uses"},
    "refs":            {"roam_uses"},
    "vulns":           {"roam_vuln_map", "roam_vuln_reach"},
    "search":          {"roam_search_symbol", "roam_search_semantic"},
    "context":         {"roam_context", "roam_ws_context"},
    "understand":      {"roam_understand", "roam_ws_understand"},
    "file":            {"roam_file_info"},
    "review":          {"roam_review_change"},
    "annotate-symbol": {"roam_annotate_symbol"},
}

# A small set of suffixes that show up repeatedly in MCP tool names but
# are not part of the CLI command name (e.g., ``roam_dead`` is registered
# as ``roam_dead_code``; ``roam_complexity`` as ``roam_complexity_report``).
_KNOWN_SUFFIXES: tuple[str, ...] = (
    "check", "report", "export", "verify", "emit", "validate",
    "changes", "code", "info", "blame",
)


def _candidate_tool_names(cli_cmd: str) -> set[str]:
    """Return plausible ``roam_<X>`` names for a CLI command."""
    base_under = cli_cmd.replace("-", "_")
    out: set[str] = {f"roam_{cli_cmd}", f"roam_{base_under}"}
    for sfx in _KNOWN_SUFFIXES:
        out.add(f"roam_{base_under}_{sfx}")
    out.update(_KNOWN_TOOL_ALIASES.get(cli_cmd, set()))
    return out


def _unwrapped_commands() -> list[str]:
    """Return CLI command names with no plausibly-matching MCP tool."""
    from roam.surface_counts import cli_commands, mcp_tool_names

    mcp_tools = set(mcp_tool_names())
    cmds = cli_commands()
    return sorted(c for c in cmds if not (_candidate_tool_names(c) & mcp_tools))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_allowlists_reference_real_commands() -> None:
    """Every skip-taxonomy entry must name a real ``_COMMANDS`` key.

    A typo in an allowlist would silently mask a real gap, so we enforce
    that every name in every allowlist is a registered CLI command. This
    is the same sanity-check pattern used by ``test_capability_decoration``.
    """
    from roam.surface_counts import cli_commands

    real_cmds = set(cli_commands().keys())

    for label, allowlist in (
        ("_KNOWN_SETUP_COMMANDS", _KNOWN_SETUP_COMMANDS),
        ("_KNOWN_LOCAL_STATE_COMMANDS", _KNOWN_LOCAL_STATE_COMMANDS),
        ("_KNOWN_DAEMON_COMMANDS", _KNOWN_DAEMON_COMMANDS),
        ("_KNOWN_REPL_HELPERS", _KNOWN_REPL_HELPERS),
    ):
        stale = allowlist - real_cmds
        assert not stale, (
            f"{label} lists names that are not in cli._COMMANDS: "
            f"{sorted(stale)}. Either the command was renamed/removed or "
            f"the allowlist entry is a typo."
        )


def test_mcp_wrapper_coverage_advisory() -> None:
    """Advisory audit: surface CLI commands missing an MCP wrapper.

    A "real gap" is any unwrapped command that does NOT appear in one of
    the four skip-taxonomy allowlists (setup/bootstrap, local-state,
    daemon, REPL helper). When ``XFAIL_ON_GAP`` is ``True`` (default),
    a non-empty gap raises ``pytest.xfail`` with the offending list so
    CI surfaces drift without blocking unrelated PRs. Flip it to
    ``False`` once the gap is closed to convert this to a hard gate.
    """
    unwrapped = _unwrapped_commands()
    real_gap = sorted(c for c in unwrapped if c not in _ALL_SKIPPED)

    if not real_gap:
        # No drift — test passes cleanly. New commands either landed with
        # a wrapper or were explicitly allowlisted into the skip taxonomy.
        return

    message = (
        f"{len(real_gap)} CLI commands lack an MCP wrapper and are not "
        f"in any skip-taxonomy allowlist. Either:\n"
        f"  (a) add a wrapper in src/roam/mcp_server.py via @_tool(name=...),\n"
        f"  (b) extend _KNOWN_TOOL_ALIASES if the wrapper exists under a\n"
        f"      non-obvious name (e.g., dead -> roam_dead_code), OR\n"
        f"  (c) add to one of the skip-taxonomy allowlists if the command\n"
        f"      legitimately doesn't need MCP exposure (with rationale).\n"
        f"Real-gap commands: {real_gap}"
    )

    if XFAIL_ON_GAP:
        pytest.xfail(message)
    else:
        pytest.fail(message)


def test_skip_taxonomy_categories_are_disjoint() -> None:
    """A command must fit in at most one skip-taxonomy category.

    Overlapping allowlists make rationale ambiguous — if a command is
    both "local-state" and "daemon", which is it? Force the four sets
    to be mutually disjoint so each allowlist entry has one clear reason.
    """
    pairs = [
        ("setup", _KNOWN_SETUP_COMMANDS),
        ("local-state", _KNOWN_LOCAL_STATE_COMMANDS),
        ("daemon", _KNOWN_DAEMON_COMMANDS),
        ("repl-helper", _KNOWN_REPL_HELPERS),
    ]
    for i, (lbl_a, set_a) in enumerate(pairs):
        for lbl_b, set_b in pairs[i + 1:]:
            overlap = set_a & set_b
            assert not overlap, (
                f"commands appear in both {lbl_a} and {lbl_b} allowlists "
                f"({sorted(overlap)}) — pick one rationale per command."
            )
