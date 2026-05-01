"""roam retrieve eval harness — recall@K runner."""

from __future__ import annotations

import itertools
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roam.retrieve.pipeline import run_retrieve


@dataclass
class EvalTask:
    task_id: str
    task: str
    expected_files: tuple[str, ...]
    notes: str = ""


@dataclass
class TaskResult:
    task_id: str
    task: str
    expected_files: tuple[str, ...]
    retrieved_files: tuple[str, ...]
    recall_at: dict[int, float] = field(default_factory=dict)
    miss_count: int = 0


def load_tasks(path: Path | str) -> list[EvalTask]:
    """Read a JSONL file of eval tasks.

    Each line is a JSON object with ``task`` and ``expected_files`` keys.
    ``task_id`` is auto-derived from the task text if absent. Blank
    lines and ``//`` comment lines are tolerated.
    """
    p = Path(path)
    out: list[EvalTask] = []
    text = p.read_text(encoding="utf-8")
    for ln, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        try:
            doc = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p}:{ln} not valid JSON: {exc}") from exc
        task_text = (doc.get("task") or "").strip()
        if not task_text:
            raise ValueError(f"{p}:{ln} missing 'task' field")
        expected = tuple(doc.get("expected_files") or ())
        if not expected:
            raise ValueError(f"{p}:{ln} 'expected_files' must be non-empty")
        task_id = doc.get("task_id") or _slugify(task_text)
        notes = doc.get("notes", "")
        out.append(
            EvalTask(
                task_id=task_id,
                task=task_text,
                expected_files=tuple(_normalise_path(p) for p in expected),
                notes=notes,
            )
        )
    return out


def evaluate_task(
    conn: sqlite3.Connection,
    task: EvalTask,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    rerank: str = "fast",
    weights: dict[str, float] | None = None,
) -> TaskResult:
    """Run retrieve once and compute recall@K for each K in *ks*.

    Pass ``weights`` to override the config-driven α/β/γ/δ/ε vector —
    used by the sweep mode to rotate weights without touching config.
    """
    biggest_k = max(ks)
    result = run_retrieve(
        conn,
        task.task,
        budget=10_000,  # large enough that K is the binding limit, not budget
        k=biggest_k,
        rerank=rerank,
        weights=weights,
    )
    candidates = result["candidates"]
    retrieved_files: list[str] = []
    for c in candidates:
        path = c.get("file_path") or c.get("file") or ""
        if path:
            retrieved_files.append(_normalise_path(path))

    expected_set = set(task.expected_files)
    recall_at: dict[int, float] = {}
    for k in ks:
        top = retrieved_files[:k]
        hit = sum(1 for f in expected_set if f in top)
        recall_at[k] = hit / len(expected_set) if expected_set else 0.0

    misses = expected_set - set(retrieved_files)

    return TaskResult(
        task_id=task.task_id,
        task=task.task,
        expected_files=task.expected_files,
        retrieved_files=tuple(retrieved_files),
        recall_at=recall_at,
        miss_count=len(misses),
    )


def aggregate_results(
    per_task: list[TaskResult],
    ks: tuple[int, ...] = (5, 10, 20),
) -> dict[str, Any]:
    """Compute mean recall@K across the task set."""
    if not per_task:
        return {f"recall_at_{k}": 0.0 for k in ks} | {"task_count": 0}
    out: dict[str, Any] = {"task_count": len(per_task)}
    for k in ks:
        out[f"recall_at_{k}"] = round(
            sum(r.recall_at.get(k, 0.0) for r in per_task) / len(per_task),
            4,
        )
    return out


def run_eval(
    conn: sqlite3.Connection,
    tasks: list[EvalTask],
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    rerank: str = "fast",
    weights: dict[str, float] | None = None,
) -> tuple[list[TaskResult], dict[str, Any]]:
    """Run the harness once. Returns ``(per_task, aggregate)``."""
    per_task = [evaluate_task(conn, t, ks=ks, rerank=rerank, weights=weights) for t in tasks]
    return per_task, aggregate_results(per_task, ks=ks)


# ---------------------------------------------------------------------------
# Weight sweep
# ---------------------------------------------------------------------------

#: Default mini-sweep grid — small enough to finish in seconds on a
#: 14k-symbol repo, big enough to surface obvious miscalibrations.
DEFAULT_SWEEP_GRID: dict[str, tuple[float, ...]] = {
    "alpha": (0.3, 0.4, 0.5),
    "beta": (0.15, 0.25, 0.35),
    "gamma": (0.15,),
    "delta": (0.10, 0.15),
    "epsilon": (0.05,),
}


def sweep_weights(
    conn: sqlite3.Connection,
    tasks: list[EvalTask],
    grid: dict[str, tuple[float, ...]] | None = None,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    target_k: int = 20,
) -> list[dict[str, Any]]:
    """Run the harness across the cartesian product of *grid*.

    Each weight vector is plumbed through ``run_retrieve(weights=...)``
    so the rerank score actually rotates instead of repeating the
    baseline. Returns one dict per weight vector with the mean
    recall@target_k plus the full aggregate. Sorted descending by
    recall@target_k.
    """
    grid = grid or DEFAULT_SWEEP_GRID
    keys = sorted(grid.keys())
    combos = list(itertools.product(*(grid[k] for k in keys)))

    out: list[dict[str, Any]] = []
    for combo in combos:
        weights = dict(zip(keys, combo))
        per_task = [evaluate_task(conn, t, ks=ks, weights=weights) for t in tasks]
        agg = aggregate_results(per_task, ks=ks)
        out.append(
            {
                "weights": weights,
                "aggregate": agg,
                f"recall_at_{target_k}": agg.get(f"recall_at_{target_k}", 0.0),
            }
        )

    out.sort(key=lambda r: -r[f"recall_at_{target_k}"])
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_path(p: str) -> str:
    return (p or "").replace("\\", "/").lstrip("./")


def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:60] or "task"


def render_markdown_report(
    per_task: list[TaskResult],
    aggregate: dict[str, Any],
) -> str:
    """Render the eval result as a Markdown report for human review."""
    lines = ["# roam retrieve eval report", ""]
    lines.append(f"Tasks: **{aggregate['task_count']}**")
    for k in (5, 10, 20):
        key = f"recall_at_{k}"
        if key in aggregate:
            lines.append(f"- mean recall@{k}: **{aggregate[key]:.3f}**")
    lines.append("")
    lines.append("## Per-task")
    lines.append("")
    lines.append("| task | recall@5 | recall@10 | recall@20 | misses |")
    lines.append("|---|---|---|---|---|")
    for r in per_task:
        lines.append(
            f"| {r.task_id} | "
            f"{r.recall_at.get(5, 0):.2f} | "
            f"{r.recall_at.get(10, 0):.2f} | "
            f"{r.recall_at.get(20, 0):.2f} | "
            f"{r.miss_count} |"
        )
    return "\n".join(lines)
