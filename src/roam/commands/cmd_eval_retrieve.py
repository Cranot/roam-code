"""roam eval-retrieve — measure recall@K against a labeled task set.

Examples
--------

    roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl
    roam --json eval-retrieve --tasks bench/retrieve/roam_self.jsonl
    roam eval-retrieve --tasks ... --min-recall-at-20 0.6
    roam eval-retrieve --tasks ... --sweep
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.eval.harness import (
    load_tasks,
    render_markdown_report,
    run_eval,
    sweep_weights,
)
from roam.output.formatter import json_envelope, to_json


def _default_task_path() -> Path:
    """Project-relative default task file."""
    return Path("bench/retrieve/roam_self.jsonl")


@click.command("eval-retrieve")
@click.option(
    "--tasks",
    "tasks_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=("JSONL file of `(task, expected_files)` pairs. Default: ``bench/retrieve/roam_self.jsonl``."),
)
@click.option(
    "--rerank",
    type=click.Choice(["fast", "off"], case_sensitive=False),
    default="fast",
    show_default=True,
    help="Forwarded to `roam retrieve`.",
)
@click.option(
    "--sweep",
    is_flag=True,
    help=(
        "Run the harness across a small grid of weight vectors. "
        "Output the best-scoring vector. v12.0 ships a baseline-only "
        "sweep (full weight injection arrives in v12.1)."
    ),
)
@click.option(
    "--min-recall-at-20",
    type=float,
    default=None,
    help=("CI gate: exit 5 if the mean recall@20 is below this threshold."),
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(),
    default=None,
    help="Write a Markdown report to this path.",
)
@click.pass_context
def eval_retrieve(
    ctx,
    tasks_path,
    rerank,
    sweep,
    min_recall_at_20,
    report_path,
):
    """Run the retrieval eval harness over a labeled task set."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    path = Path(tasks_path) if tasks_path else _default_task_path()
    if not path.exists():
        raise click.UsageError(
            f"task file not found: {path}. Pass --tasks <path> or run from a repo with bench/retrieve/."
        )

    tasks = load_tasks(path)

    ensure_index()
    with open_db(readonly=True) as conn:
        if sweep:
            sweeps = sweep_weights(conn, tasks)
            best = sweeps[0] if sweeps else None
            verdict = f"Best recall@20: {best['recall_at_20']:.3f} with {best['weights']}" if best else "Empty sweep"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "eval-retrieve",
                            summary={
                                "verdict": verdict,
                                "task_count": len(tasks),
                                "best_recall_at_20": best["recall_at_20"] if best else 0.0,
                            },
                            tasks_path=str(path),
                            sweep=sweeps[:10],
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            for s in sweeps[:10]:
                click.echo(f"  recall@20={s['recall_at_20']:.3f}  weights={s['weights']}")
            return

        per_task, aggregate = run_eval(conn, tasks, rerank=rerank)

    verdict = (
        f"recall@5={aggregate.get('recall_at_5', 0):.3f}  "
        f"recall@10={aggregate.get('recall_at_10', 0):.3f}  "
        f"recall@20={aggregate.get('recall_at_20', 0):.3f}"
        f" across {aggregate['task_count']} task(s)"
    )

    if report_path:
        Path(report_path).write_text(
            render_markdown_report(per_task, aggregate),
            encoding="utf-8",
        )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "eval-retrieve",
                    summary={
                        "verdict": verdict,
                        **aggregate,
                    },
                    tasks_path=str(path),
                    per_task=[
                        {
                            "task_id": r.task_id,
                            "task": r.task,
                            "expected_files": list(r.expected_files),
                            "retrieved_files": list(r.retrieved_files),
                            "recall_at": r.recall_at,
                            "miss_count": r.miss_count,
                        }
                        for r in per_task
                    ],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo()
        click.echo(f"{'task':<40s} R@5    R@10   R@20   misses")
        for r in per_task:
            click.echo(
                f"{r.task_id[:40]:<40s} "
                f"{r.recall_at.get(5, 0):>5.2f}  "
                f"{r.recall_at.get(10, 0):>5.2f}  "
                f"{r.recall_at.get(20, 0):>5.2f}  "
                f"{r.miss_count:>4d}"
            )
        if report_path:
            click.echo()
            click.echo(f"Wrote Markdown report to: {report_path}")

    if min_recall_at_20 is not None and aggregate.get("recall_at_20", 0) < min_recall_at_20:
        ctx.exit(5)
