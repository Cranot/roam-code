"""roam fleet — graph-aware planner for multi-agent code work (C.1).

Phase-6 research finding: the agent-fleet *orchestration* layer was
fully claimed in April 2026 (Copilot ``/fleet``, Cursor multitask,
Windsurf Wave 13, Gemini CLI subagents, Claude Agent Teams) — but
none of those runtimes compute a *graph-aware partition* with conflict
prediction. They all rely on an LLM scoping or human file-list
curation. roam-code already has Louvain partitioning, dark-matter
co-change, and personalised PageRank; ``roam fleet plan`` packages
those into a `.roam-fleet.json` envelope that the runtimes consume.

Public surface:

* :mod:`fleet.manifest` — wrap a partition into a fleet plan.
* :mod:`fleet.adapters` — render the plan in formats consumable by
  external orchestrators (Composio, Copilot CLI, Cursor BG, raw JSON).
"""

from __future__ import annotations
