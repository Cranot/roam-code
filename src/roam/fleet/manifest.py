"""Build the canonical `.roam-fleet.json` envelope from a partition manifest.

The shape is intentionally adapter-friendly: every agent runtime we've
seen wants ``(task_id, description, files, dependencies)``. Beyond
that, our envelope adds ``conflict_risk``, ``suggested_merge_order``,
and ``evidence`` (PageRank pivots, co-change ridges) so the runtime
can choose execution order without re-deriving the graph itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "roam-fleet/v1"


@dataclass
class FleetTask:
    """One unit of fleet work — typically one partition."""

    task_id: str
    title: str
    description: str
    file_scope: list[str] = field(default_factory=list)
    role: str = ""
    conflict_risk: str = "LOW"
    estimated_complexity: float = 0.0
    test_coverage: float = 0.0
    pagerank_anchors: list[str] = field(default_factory=list)
    cross_repo_dependencies: list[str] = field(default_factory=list)
    suggested_branch: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "file_scope": list(self.file_scope),
            "role": self.role,
            "conflict_risk": self.conflict_risk,
            "estimated_complexity": round(self.estimated_complexity, 2),
            "test_coverage": round(self.test_coverage, 4),
            "pagerank_anchors": list(self.pagerank_anchors),
            "cross_repo_dependencies": list(self.cross_repo_dependencies),
            "suggested_branch": self.suggested_branch,
        }


def build_fleet_manifest(
    partition_manifest: dict[str, Any],
    *,
    goal: str = "",
    branch_prefix: str = "fleet",
) -> dict[str, Any]:
    """Wrap a ``compute_partition_manifest`` result in the fleet envelope.

    Parameters
    ----------
    partition_manifest:
        The output of :func:`roam.commands.cmd_partition.compute_partition_manifest`.
    goal:
        Free-form description of the work the fleet is being dispatched
        for (e.g. "split the auth refactor into parallel subtasks").
        Threaded into each task's ``description`` and into the envelope's
        ``goal`` field.
    branch_prefix:
        Prefix for the suggested per-task branch name. Default ``"fleet"``
        gives ``fleet/0-roleslug``.

    Returns
    -------
    dict
        The full fleet envelope with ``schema``, ``goal``, ``tasks``,
        ``merge_order``, ``conflict_hotspots``, ``overall_conflict_probability``,
        and pass-through ``evidence``.
    """
    tasks: list[dict[str, Any]] = []
    parts = partition_manifest.get("partitions", [])
    for p in parts:
        role = p.get("role") or "Worker"
        slug = _slugify(role)
        task_id = f"task-{p['id']}"
        title = f"{role} — {p['file_count']} file(s), conflict {p.get('conflict_risk', 'LOW')}"
        description = _build_task_description(role, p, goal)
        anchors = [
            f"{ks.get('name', '?')} ({ks.get('kind', '?')}) at {ks.get('file', '?')}"
            for ks in p.get("key_symbols", [])[:5]
        ]
        tasks.append(
            FleetTask(
                task_id=task_id,
                title=title,
                description=description,
                file_scope=list(p.get("files", [])),
                role=role,
                conflict_risk=p.get("conflict_risk", "LOW"),
                estimated_complexity=float(p.get("complexity", 0.0)),
                test_coverage=float(p.get("test_coverage", 0.0)),
                pagerank_anchors=anchors,
                suggested_branch=f"{branch_prefix}/{p['id']}-{slug}",
            ).to_dict()
        )

    return {
        "schema": SCHEMA_VERSION,
        "goal": goal or "(no goal supplied)",
        "verdict": partition_manifest.get("verdict", ""),
        "tasks": tasks,
        "merge_order": [int(x) for x in partition_manifest.get("merge_order", [])],
        "conflict_hotspots": [
            {"file": h.get("file"), "score": h.get("conflict_score", 0)}
            for h in partition_manifest.get("conflict_hotspots", [])
        ],
        "overall_conflict_probability": float(partition_manifest.get("overall_conflict_probability", 0.0)),
        "agent_count": len(tasks),
    }


def _build_task_description(role: str, partition: dict[str, Any], goal: str) -> str:
    risk = partition.get("conflict_risk", "LOW")
    complexity = partition.get("complexity", 0.0)
    files = partition.get("files", [])
    file_preview = ", ".join(files[:3]) + (f" + {len(files) - 3} more" if len(files) > 3 else "")
    parts = [
        f"Owner role: {role}.",
        f"Conflict risk: {risk} (lower is more parallel-safe).",
        f"Estimated cognitive load: {complexity:.0f}.",
        f"Files: {file_preview or '(none)'}.",
    ]
    if goal:
        parts.insert(
            0,
            f"In service of the fleet goal: {goal}.",
        )
    parts.append(
        "Edit only the files listed in `file_scope`. Read-only access to "
        "shared dependencies as listed by the orchestrator."
    )
    return "  ".join(parts)


def _slugify(text: str) -> str:
    """Lowercase + alnum + dash. Used for branch names."""
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "worker"
