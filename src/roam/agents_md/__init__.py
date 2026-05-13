"""AGENTS.md generator (R15).

Synthesizes a human-readable ``AGENTS.md`` from indexed-repo state so an
AI agent joining a codebase has a single doc that tells it:

* the stack (language mix),
* naming conventions (from
  :mod:`roam.commands.conventions_helper` -- the canonical detector),
* danger zones (top files by ``churn x complexity x max_fan_in``,
  matching the cheap approximation in :mod:`roam.commands.cmd_dashboard`),
* pre-edit / after-edit / pre-PR gates (from
  :mod:`roam.constitution.loader`),
* test conventions (from indexed test files),
* high-confidence architectural invariants (from
  :mod:`roam.laws.miner`),
* graph-aware policy rule files (from ``.roam/rules/``),
* capability roster summary (from :data:`roam.capability.REGISTRY`).

The generator is intentionally COMPOSITIONAL: every section is sourced
from an existing subsystem rather than re-derived. The module is also
strictly READ-ONLY -- no files are touched during generation.

Public API
----------
* :func:`generate_agents_md` -- build an :class:`AgentsMd` from a
  repo root + open SQLite connection.
* :func:`render_markdown` -- render an :class:`AgentsMd` to GitHub-
  flavored Markdown.
* :class:`AgentsMd` -- structured-data view of the rendered output;
  matches the JSON envelope shape returned by ``roam agents-md --json``.
"""

from __future__ import annotations

from roam.agents_md.generator import (
    AgentsMd,
    generate_agents_md,
    render_markdown,
    section_danger_zones,
    section_laws,
    section_stack,
)

__all__ = [
    "AgentsMd",
    "generate_agents_md",
    "render_markdown",
    # W15.2 followup — public-API helpers promoted out of the
    # ``_section_*`` namespace because ``cmd_brief`` reuses them.
    "section_stack",
    "section_danger_zones",
    "section_laws",
]
