"""Render `.roam-fleet.json` for external orchestrators.

Each adapter takes the canonical fleet envelope (see
:func:`fleet.manifest.build_fleet_manifest`) and shapes it for one
runtime. We start with three: raw, Composio Agent Orchestrator,
Copilot CLI ``/fleet``. Cursor Background Agents adapter is planned.
"""

from __future__ import annotations

from typing import Any


def to_raw(envelope: dict[str, Any]) -> dict[str, Any]:
    """Pass-through with shape stability — explicit no-op."""
    return dict(envelope)


def _composio_agent(task: dict[str, Any], depends_on: list[str]) -> dict[str, Any]:
    """Build one Composio agent record from a roam fleet task."""
    return {
        "name": task["task_id"],
        "goal": task["description"],
        "allowed_paths": task["file_scope"],
        "depends_on": depends_on,
        "constraints": {
            "conflict_risk": task["conflict_risk"],
            "estimated_complexity": task["estimated_complexity"],
        },
    }


def _ordered_agents(by_id: dict[str, dict[str, Any]], merge_order: list[int]) -> list[dict[str, Any]]:
    """Build agents in merge order with accumulated dependency edges.

    Unknown task IDs are skipped so the manifest stays runnable even when
    the planner referenced tasks that did not survive validation.
    """
    agents: list[dict[str, Any]] = []
    seen: list[int] = []
    for pid in merge_order:
        tid = f"task-{pid}"
        task = by_id.get(tid)
        if task is None:
            continue
        agents.append(_composio_agent(task, depends_on=[f"task-{p}" for p in seen]))
        seen.append(pid)
    return agents


def to_composio(envelope: dict[str, Any]) -> dict[str, Any]:
    """Composio Agent Orchestrator manifest.

    Spec (April 2026, MIT, ``ComposioHQ/agent-orchestrator``): a list
    of ``agents`` each with ``name``, ``goal``, ``allowed_paths``, and
    optional ``depends_on`` for sequencing.
    """
    tasks = envelope.get("tasks", [])
    by_id = {t["task_id"]: t for t in tasks}
    merge_order = envelope.get("merge_order", [])

    if merge_order:
        agents = _ordered_agents(by_id, merge_order)
    else:
        agents = [_composio_agent(t, depends_on=[]) for t in tasks]

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
