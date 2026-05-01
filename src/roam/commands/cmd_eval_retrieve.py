"""roam eval-retrieve — measure recall@K against a labeled task set.

Examples
--------

    roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl
    roam --json eval-retrieve --tasks bench/retrieve/roam_self.jsonl
    roam eval-retrieve --tasks ... --min-recall-at-20 0.6
    roam eval-retrieve --tasks ... --sweep

    # Emit retrieval output in a benchmark-portable shape (CodeRAG-Bench /
    # BEIR-style ctxs array) for public leaderboard submission.
    roam eval-retrieve --tasks <bench>.jsonl \\
        --emit-format coderag --emit-out runs/roam.jsonl
"""

from __future__ import annotations

import json as _json
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
from roam.retrieve.pipeline import run_retrieve


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
@click.option(
    "--emit-format",
    type=click.Choice(["roam", "coderag", "beir"], case_sensitive=False),
    default="roam",
    show_default=True,
    help=(
        "Output shape for the per-task results. "
        "``roam`` = our internal per_task envelope. "
        "``coderag`` = CodeRAG-Bench-compatible ctxs array (one JSON line "
        "per task: ``{task_id, query, ctxs:[{id, title, text, score}, ...]}``). "
        "``beir`` = BEIR-style trec_eval-friendly run file "
        "(one line per (task, retrieved) pair: "
        "``{query_id, doc_id, rank, score, run_name}``)."
    ),
)
@click.option(
    "--emit-out",
    "emit_out_path",
    type=click.Path(),
    default=None,
    help=(
        "When ``--emit-format`` is ``coderag`` or ``beir``, write the "
        "per-task retrieval output to this JSONL path. Required for those "
        "formats; the default ``roam`` format goes through the normal "
        "envelope/report channels."
    ),
)
@click.option(
    "--emit-k",
    type=int,
    default=20,
    show_default=True,
    help="Top-K candidates to include in each emitted record (CodeRAG/BEIR formats only).",
)
@click.pass_context
def eval_retrieve(
    ctx,
    tasks_path,
    rerank,
    sweep,
    min_recall_at_20,
    report_path,
    emit_format,
    emit_out_path,
    emit_k,
):
    """Run the retrieval eval harness over a labeled task set."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    path = Path(tasks_path) if tasks_path else _default_task_path()
    if not path.exists():
        raise click.UsageError(
            f"task file not found: {path}. Pass --tasks <path> or run from a repo with bench/retrieve/."
        )

    tasks = load_tasks(path)

    # Validate emit-format / emit-out combination up front.
    fmt = (emit_format or "roam").lower()
    if fmt in ("coderag", "beir") and not emit_out_path:
        raise click.UsageError(f"--emit-format {fmt} requires --emit-out <path> (the JSONL run file).")

    ensure_index()
    with open_db(readonly=True) as conn:
        # Bench-portable emit path runs *alongside* the normal harness when
        # requested. Each task drives a fresh `run_retrieve` call; the
        # emitter formats the candidates per the chosen schema.
        if fmt in ("coderag", "beir"):
            written = _emit_bench_run(conn, tasks, fmt, Path(emit_out_path), top_k=emit_k)
            click.echo(f"Wrote {written} records to {emit_out_path} ({fmt} format).")

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


# ---------------------------------------------------------------------------
# Benchmark-portable emit
# ---------------------------------------------------------------------------


def _emit_bench_run(conn, tasks, fmt: str, out_path: Path, *, top_k: int) -> int:
    """Run retrieve over each task and write a benchmark-portable JSONL.

    Two output formats:

    * ``coderag`` — one JSON object per task with a ``ctxs`` array, the
      shape used by CodeRAG-Bench / BEIR-style evaluation harnesses::

          {"task_id": "...", "query": "...",
           "ctxs": [
             {"id": "src/auth.py:12-45", "title": "src/auth.py",
              "text": "<task plus span metadata>", "score": 0.87},
             ...
           ]}

    * ``beir`` — one JSON object per (task, retrieved_doc) pair, the
      trec_eval-friendly shape used by the BEIR benchmark suite::

          {"query_id": "...", "doc_id": "src/auth.py:12-45",
           "rank": 1, "score": 0.87, "run_name": "roam-code-v12"}

    Both formats are stable across roam versions — only the ``run_name``
    field changes per release. ``out_path`` is overwritten if it exists.
    Returns the number of *task records* written (one line per task for
    coderag, one line per retrieved doc for beir).
    """
    fmt = fmt.lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    run_name = "roam-code-v12"
    with out_path.open("w", encoding="utf-8") as fh:
        for task in tasks:
            # Run a real retrieve. K is the binding limit (no budget cap)
            # because benchmark submissions need stable top-K results.
            result = run_retrieve(conn, task.task, budget=10_000, k=top_k, rerank="fast")
            candidates = result.get("candidates", [])[:top_k]

            if fmt == "coderag":
                ctxs = []
                for c in candidates:
                    fp = c.get("file_path") or c.get("file") or ""
                    line_start = c.get("line_start") or 0
                    line_end = c.get("line_end") or line_start
                    span_id = f"{fp}:{line_start}-{line_end}"
                    text = (f"{c.get('qualified_name') or c.get('name') or ''} ({c.get('kind') or 'symbol'})").strip()
                    ctxs.append(
                        {
                            "id": span_id,
                            "title": fp,
                            "text": text,
                            "score": float(c.get("score", 0.0)),
                        }
                    )
                rec = {
                    "task_id": task.task_id,
                    "query": task.task,
                    "ctxs": ctxs,
                }
                fh.write(_json.dumps(rec, ensure_ascii=False))
                fh.write("\n")
                written += 1
            elif fmt == "beir":
                for rank, c in enumerate(candidates, start=1):
                    fp = c.get("file_path") or c.get("file") or ""
                    line_start = c.get("line_start") or 0
                    line_end = c.get("line_end") or line_start
                    rec = {
                        "query_id": task.task_id,
                        "doc_id": f"{fp}:{line_start}-{line_end}",
                        "rank": rank,
                        "score": float(c.get("score", 0.0)),
                        "run_name": run_name,
                    }
                    fh.write(_json.dumps(rec, ensure_ascii=False))
                    fh.write("\n")
                    written += 1
            else:
                # Unknown format — should be unreachable thanks to Click's
                # choice validation, but guard anyway.
                raise click.UsageError(f"unknown emit format: {fmt}")
    return written
