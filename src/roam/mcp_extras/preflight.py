"""W296 - cold-start preflight guard for MCP tools that need a built index.

Per CLAUDE.md "JSON-parse-on-empty-input" anti-pattern (Pattern 1), every
MCP tool must return a structured envelope the client can act on. Silence
or hang is the worst failure mode because the LLM cannot tell whether to
wait, retry, run something first, or give up.

This module implements the "no index yet" short-circuit: when an MCP tool
that requires a built index is invoked on a project where
``.roam/index.db`` does not exist, instead of running the underlying CLI
command (which on a fresh project triggers a full index build that
typically exceeds the MCP call timeout, leaving the client with no
signal), we return an immediate, structured ``index_not_built`` envelope
that tells the agent EXACTLY what to do next:

    {
        "status": "index_not_built",
        "summary": {"verdict": "<imperative + concrete-noun-anchored>",
                    "level": "blocker"},
        "next_command": "roam init",
        "expected_duration_seconds": 60,
        "retry_after_seconds": 60,
        "agent_contract": {"facts": [...], "next_commands": [...]},
    }

Why a small set of tools skip this guard:

* ``roam_init`` / ``roam_reindex`` -- they CREATE the index. Blocking
  them on the index existing would be a chicken-and-egg deadlock.
* ``roam_doctor`` -- diagnoses install state, must work BEFORE init.
* ``roam_catalog`` / ``roam_expand_toolset`` / ``roam_session_metrics``
  -- pure server-metadata tools, do not touch the index.
* ``roam_evidence_doctor`` / ``roam_fetch_handle`` /
  ``roam_pr_comment_render`` -- operate on file paths handed in by the
  caller, never read the index.

The guard does NOT auto-trigger indexing. The user-facing model is
"run init explicitly, then MCP tools work". Forcing an auto-init hides
the time cost from the agent and can fail in surprising ways (no .git,
no perms, cloud-sync collisions, ...).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Closed enumeration of MCP tool names that DO NOT require a built index.
#
# Every other registered MCP tool is gated by this guard. Membership is
# O(1) via ``frozenset`` and is intentionally narrow -- a tool belongs
# here only when its implementation provably does not query the index
# database (e.g. it operates on caller-supplied file paths, or it
# returns pure server-side metadata).
#
# Extending this set is a deliberate source-code edit; the drift-guard
# test ``test_no_index_needed_set_is_explicit_closed_enum`` pins the
# expected membership.
# ---------------------------------------------------------------------------
_NO_INDEX_NEEDED: frozenset[str] = frozenset(
    {
        # Bootstrap: CREATE the index (chicken-and-egg).
        "roam_init",
        "roam_reindex",
        # Pre-init diagnostics: must work without an index.
        "roam_doctor",
        # Pure server-metadata tools (no DB access).
        "roam_catalog",
        "roam_expand_toolset",
        "roam_session_metrics",
        # Operate on caller-supplied file paths (envelope / handle / packet).
        "roam_evidence_doctor",
        "roam_fetch_handle",
        "roam_pr_comment_render",
    }
)


def needs_index(tool_name: str) -> bool:
    """Return ``True`` when *tool_name* requires a built index.

    The default is ``True`` (gate by default) so a newly-added MCP tool
    inherits the safety net without an explicit opt-in. Tools that
    genuinely do not need the index must be added to ``_NO_INDEX_NEEDED``.
    """
    return tool_name not in _NO_INDEX_NEEDED


def index_is_built(repo_root: Path | str = ".") -> bool:
    """Return ``True`` when ``<repo_root>/.roam/index.db`` exists and is non-empty.

    Uses :func:`roam.db.connection.db_exists` so the resolution chain
    (``ROAM_DB_DIR`` env > ``.roam/config.json`` ``db_dir`` >
    ``<root>/.roam/index.db``) is identical to what every other roam
    command sees. A pure function -- no side effects.

    Falls back to ``False`` on any resolution error (e.g. a stale
    ``db_dir`` pointing at a deleted external drive) so the cold-start
    envelope fires instead of the agent seeing an opaque OSError.
    """
    try:
        from roam.db.connection import db_exists, find_project_root

        root = Path(repo_root) if not isinstance(repo_root, Path) else repo_root
        project_root = find_project_root(str(root))
        return db_exists(project_root)
    except Exception:
        return False


def cold_start_envelope(tool_name: str) -> dict[str, Any]:
    """Return the structured "index not built" envelope.

    The envelope is a complete response by itself -- consumers reading
    ``summary.verdict`` get the answer; consumers reading
    ``next_command`` get the action. No silent fields, no parse-on-empty.

    Verdict terminal noun is ``tools`` -- in the LAW 4 concrete-noun
    anchor set -- so the lint at ``tests/test_law4_lint.py`` accepts it.
    """
    # Terminal token: "tools" (concrete-noun anchor per CLAUDE.md LAW 4).
    verdict = "Index not built. Run `roam init` in a terminal first, then retry MCP tools."
    return {
        "command": tool_name,
        "status": "index_not_built",
        "summary": {
            "verdict": verdict,
            "level": "blocker",
            "partial_success": False,
        },
        "next_command": "roam init",
        "expected_duration_seconds": 60,
        "retry_after_seconds": 60,
        "agent_contract": {
            "facts": [
                "0 of 8 evidence questions answered without indexed symbols",
                "1 prerequisite command unmet -- run roam init",
            ],
            "next_commands": [
                "roam init",
                "# then retry the MCP tool that returned this envelope",
            ],
        },
        "_meta": {
            "guard": "w296_cold_start",
            "tool": tool_name,
        },
    }


def maybe_cold_start_envelope(tool_name: str, root: str | Path = ".") -> dict[str, Any] | None:
    """Return the cold-start envelope when the index is missing, else ``None``.

    Single-call entry point used by the ``@_tool`` decorator wiring.
    Short-circuits to ``None`` for tools that don't need an index so the
    overhead on the high-volume path (every other tool call) is a single
    set membership check plus one ``Path.exists()``.
    """
    if not needs_index(tool_name):
        return None
    # Allow tests / one-off scripts to bypass the guard entirely.
    if os.environ.get("ROAM_MCP_DISABLE_COLD_START_GUARD"):
        return None
    if index_is_built(root):
        return None
    return cold_start_envelope(tool_name)


# ---------------------------------------------------------------------------
# Description hint -- appended to tool descriptions for index-gated tools.
# Imperative voice per CLAUDE.md LAW 2 ("Run X" not "This command does X").
# ---------------------------------------------------------------------------
INDEX_REQUIRED_HINT: str = "Requires a built index -- run `roam init` first if you haven't yet."


def maybe_decorate_description(tool_name: str, description: str) -> str:
    """Append the index-required hint to a tool's description when applicable.

    Idempotent: appending twice is a no-op. Preserves the original
    description verbatim when the tool does not need an index OR when
    the hint is already present.
    """
    if not needs_index(tool_name):
        return description
    if INDEX_REQUIRED_HINT in description:
        return description
    if not description:
        return INDEX_REQUIRED_HINT
    # Ensure a single space + period separator.
    sep = "" if description.rstrip().endswith(".") else "."
    return f"{description.rstrip()}{sep} {INDEX_REQUIRED_HINT}"
