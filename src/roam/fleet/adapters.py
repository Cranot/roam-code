"""Render `.roam-fleet.json` for external orchestrators.

Each adapter takes the canonical fleet envelope (see
:func:`fleet.manifest.build_fleet_manifest`) and shapes it for one
runtime. We start with three: raw, Composio Agent Orchestrator,
Copilot CLI ``/fleet``. Cursor Background Agents lands in v12.1.
"""

from __future__ import annotations

from typing import Any


def to_raw(envelope: dict[str, Any]) -> dict[str, Any]:
    """Pass-through with shape stability — explicit no-op."""
    return dict(envelope)


def to_composio(envelope: dict[str, Any]) -> dict[str, Any]:
    """Composio Agent Orchestrator manifest.

    Spec (April 2026, MIT, ``ComposioHQ/agent-orchestrator``): a list
    of ``agents`` each with ``name``, ``goal``, ``allowed_paths``, and
    optional ``depends_on`` for sequencing.
    """
    agents = []
    by_id = {t["task_id"]: t for t in envelope.get("tasks", [])}
    merge_order = envelope.get("merge_order", [])
    # Build dependency edges: an agent depends on every agent that should
    # merge before it.
    if merge_order:
        seen: list[int] = []
        for pid in merge_order:
            tid = f"task-{pid}"
            if tid not in by_id:
                continue
            agents.append(
                {
                    "name": by_id[tid]["task_id"],
                    "goal": by_id[tid]["description"],
                    "allowed_paths": by_id[tid]["file_scope"],
                    "depends_on": [f"task-{p}" for p in seen],
                    "constraints": {
                        "conflict_risk": by_id[tid]["conflict_risk"],
                        "estimated_complexity": by_id[tid]["estimated_complexity"],
                    },
                }
            )
            seen.append(pid)
    else:
        for t in envelope.get("tasks", []):
            agents.append(
                {
                    "name": t["task_id"],
                    "goal": t["description"],
                    "allowed_paths": t["file_scope"],
                    "depends_on": [],
                    "constraints": {
                        "conflict_risk": t["conflict_risk"],
                        "estimated_complexity": t["estimated_complexity"],
                    },
                }
            )

    return {
        "version": "composio.v1",
        "workspace_goal": envelope.get("goal"),
        "agents": agents,
        "shared": {
            "conflict_hotspots": envelope.get("conflict_hotspots", []),
            "overall_conflict_probability": envelope.get("overall_conflict_probability", 0.0),
        },
    }


def to_copilot_cli(envelope: dict[str, Any]) -> dict[str, Any]:
    """GitHub Copilot CLI ``/fleet`` worktree manifest.

    `/fleet` consumes a list of ``{description, worktree_branch, files}``;
    we pass the suggested branch from the planner so each Copilot worker
    starts on a clean branch named ``fleet/<id>-<role-slug>``.
    """
    worktrees = []
    for t in envelope.get("tasks", []):
        worktrees.append(
            {
                "description": t["description"],
                "worktree_branch": t["suggested_branch"] or f"copilot/{t['task_id']}",
                "files": t["file_scope"],
                "labels": [
                    f"role:{t['role']}",
                    f"risk:{t['conflict_risk'].lower()}",
                ],
            }
        )
    return {
        "version": "copilot-cli.fleet.v1",
        "goal": envelope.get("goal"),
        "worktrees": worktrees,
    }


ADAPTERS = {
    "raw": to_raw,
    "composio": to_composio,
    "copilot": to_copilot_cli,
}
