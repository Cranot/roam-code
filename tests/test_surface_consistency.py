"""Surface-consistency tests — stop the split-brain bleed.

Same conceptual data ("what is this command?") lives across 8+ separate
dicts in 3 files today:

* ``cli.py``:        ``_COMMANDS``, ``_CATEGORIES``, ``_DEPRECATED_COMMANDS``
* ``cmd_surface``:   ``_MATURITY``
* ``cmd_explain_command``: ``_STALE_SENSITIVE``
* ``mcp_server.py``: ``_CORE_TOOLS``, ``_NON_READ_ONLY_TOOLS``,
  ``_DESTRUCTIVE_TOOLS``, ``_TASK_REQUIRED_TOOLS``, ``_TASK_OPTIONAL_TOOLS``,
  plus the 6 preset sets

Adding a command requires touching ~5 of these. Skipping any produces a
silently-degraded surface that no test catches today (uncategorised
command falls into "More Commands"; un-registered tool ID in
_CORE_TOOLS gets ignored at preset-resolve; deprecation replacement
pointing at a missing command surfaces a confusing error to the user
who follows the upgrade hint).

These five assertions are the stop-the-bleed before the bigger
Capability Registry rework lands. They lock the current contract and
fail loudly if drift slips in.

Allowlists capture intentional exceptions — not "TODO" placeholders.
When you genuinely need to skip categorisation or tool registration,
add the name to the allowlist with a comment explaining why.

Discovery note: ``_REGISTERED_TOOLS`` only contains tools that survived
preset filtering at import time (default ``core``), so checking metadata
sets against it would falsely flag many tools. Instead we parse
``mcp_server.py`` for ``@_tool(name="...")`` declarations — that's the
canonical "what tools EXIST" set, independent of which preset is active.
"""

from __future__ import annotations

import re
from pathlib import Path

from roam.cli import _CATEGORIES, _COMMANDS, _DEPRECATED_COMMANDS

# ---------------------------------------------------------------------------
# Allowlists for intentional exceptions
# ---------------------------------------------------------------------------

# Commands that are deliberately not in any _CATEGORIES bucket. Each entry
# must have a documented reason. New entries require justification in PR
# review — don't paper over a forgotten category dict update by extending
# the allowlist.
_INTENTIONALLY_UNCATEGORISED: dict[str, str] = {
    # Aliases pointing at canonical commands — we list the canonical name,
    # not the alias, in _CATEGORIES.
    "digest": "alias for 'trends --compare'",
    "math": "alias for 'algo'",
    "refs": "alias for 'uses'",
    "snapshot": "alias for 'trends --save'",
    "trend": "alias for 'trends'",
    "onboard": "alias for 'understand'",
    "churn": "alias for 'weather'",
}


# ---------------------------------------------------------------------------
# 1. Every _COMMANDS entry has a _CATEGORIES entry OR is on the allowlist
# ---------------------------------------------------------------------------


def test_every_command_is_categorised_or_allowlisted():
    """A command added to ``_COMMANDS`` without a ``_CATEGORIES`` entry
    falls into the dumping-ground "More Commands" section of
    ``roam --help``. That happens silently today.

    Either categorise the new command, or add it to the allowlist
    above with a documented reason.
    """
    categorised: set[str] = set()
    for bucket in _CATEGORIES.values():
        categorised.update(bucket)

    uncategorised = [name for name in _COMMANDS if name not in categorised and name not in _INTENTIONALLY_UNCATEGORISED]
    assert not uncategorised, (
        f"{len(uncategorised)} command(s) in _COMMANDS have no _CATEGORIES entry "
        f"and are not on the intentional allowlist:\n  {sorted(uncategorised)}\n\n"
        f"Either:\n"
        f"  1. Add them to a category in src/roam/cli.py:_CATEGORIES, or\n"
        f"  2. Add them to _INTENTIONALLY_UNCATEGORISED in this file with "
        f"a documented reason."
    )


# ---------------------------------------------------------------------------
# 2. Every _CATEGORIES entry resolves to a real _COMMANDS entry
# ---------------------------------------------------------------------------


def test_every_categorised_name_resolves_to_a_command():
    """Reverse direction: a typo in ``_CATEGORIES`` (or a renamed command
    that wasn't updated everywhere) leaves a dangling entry. ``--help``
    quietly drops it; the user sees a missing command in the category.
    """
    dangling: list[tuple[str, str]] = []
    for category, names in _CATEGORIES.items():
        for n in names:
            if n not in _COMMANDS:
                dangling.append((category, n))

    assert not dangling, "_CATEGORIES references commands that don't exist in _COMMANDS:\n  " + "\n  ".join(
        f"{cat}: {name!r}" for cat, name in dangling
    )


# ---------------------------------------------------------------------------
# 3. Every _DEPRECATED_COMMANDS replacement resolves
# ---------------------------------------------------------------------------


def test_every_deprecation_replacement_resolves():
    """When a deprecated command is removed, its ``replacement`` is shown
    to the user as the upgrade hint. If the replacement itself doesn't
    exist, the user types it and gets ``Error: No such command``.
    """
    bad_replacements: list[tuple[str, str]] = []
    for old_name, record in _DEPRECATED_COMMANDS.items():
        # Bare-string form is accepted (per cli.py:23).
        if isinstance(record, str):
            replacement = record
        elif isinstance(record, dict):
            replacement = record.get("replacement", "")
        else:
            replacement = ""
        if replacement and replacement not in _COMMANDS:
            bad_replacements.append((old_name, replacement))

    assert not bad_replacements, "_DEPRECATED_COMMANDS has replacements that don't resolve:\n  " + "\n  ".join(
        f"{old!r} -> {new!r} (missing)" for old, new in bad_replacements
    )


# ---------------------------------------------------------------------------
# Discovery: parse mcp_server.py for declared @_tool names
# ---------------------------------------------------------------------------


def _declared_mcp_tools() -> set[str]:
    """Parse ``mcp_server.py`` for ``@_tool(name="...")`` declarations.

    This is the canonical "what tools exist in the source" set — the
    full 137-tool surface independent of which preset is active at
    import time. ``_REGISTERED_TOOLS`` would only show the subset that
    passed preset filtering.
    """
    src = (Path(__file__).resolve().parents[1] / "src" / "roam" / "mcp_server.py").read_text(encoding="utf-8")
    # Match `@_tool(...)` decorations and pull the `name=` arg out of the
    # arglist. Regex spans newlines because the decorator is multi-line.
    pattern = re.compile(r'@_tool\((?:[^()]|\([^()]*\))*?name\s*=\s*"([^"]+)"', re.DOTALL)
    return set(pattern.findall(src))


# ---------------------------------------------------------------------------
# 4. Every _CORE_TOOLS member is a real declared @_tool
# ---------------------------------------------------------------------------


def test_core_tools_are_all_declared():
    """``_CORE_TOOLS`` is a hand-curated set of tool names exposed under
    the default ``core`` MCP preset. A typo or rename that isn't propagated
    silently drops the tool from the agent's surface — the agent calls
    it and gets ``tool not found``.

    Source-of-truth: declared ``@_tool(name=...)`` decorations in
    ``mcp_server.py``.
    """
    import roam.mcp_server as mcp

    declared = _declared_mcp_tools()
    assert declared, "could not parse any @_tool declarations from mcp_server.py"

    undeclared = sorted(set(mcp._CORE_TOOLS) - declared)
    assert not undeclared, (
        f"{len(undeclared)} tool(s) in _CORE_TOOLS are not declared as @_tool in "
        f"mcp_server.py:\n  {undeclared}\n\n"
        f"Either:\n"
        f"  1. Remove them from _CORE_TOOLS, or\n"
        f"  2. Add the @_tool(name=...) decoration so the agent surface matches."
    )


# ---------------------------------------------------------------------------
# 5. Every side-effect / task-marker tool is declared
# ---------------------------------------------------------------------------


def test_side_effect_and_task_tools_are_declared():
    """Tools tagged as non-read-only / destructive / task-required /
    task-optional must actually exist as ``@_tool`` declarations in
    ``mcp_server.py``. A name in one of these sets that doesn't resolve
    to a real declaration is dead metadata — annotation that annotates
    nothing.
    """
    import roam.mcp_server as mcp

    declared = _declared_mcp_tools()
    assert declared, "could not parse any @_tool declarations from mcp_server.py"

    failures: list[str] = []
    for set_name, members in (
        ("_NON_READ_ONLY_TOOLS", mcp._NON_READ_ONLY_TOOLS),
        ("_DESTRUCTIVE_TOOLS", mcp._DESTRUCTIVE_TOOLS),
        ("_TASK_REQUIRED_TOOLS", mcp._TASK_REQUIRED_TOOLS),
        ("_TASK_OPTIONAL_TOOLS", mcp._TASK_OPTIONAL_TOOLS),
    ):
        missing = sorted(set(members) - declared)
        if missing:
            failures.append(f"  {set_name}: {missing}")

    assert not failures, "Side-effect / task metadata refers to tools without a @_tool declaration:\n" + "\n".join(
        failures
    )
