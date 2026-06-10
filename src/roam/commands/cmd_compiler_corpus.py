"""``roam compiler-corpus`` — analyze a SAVED prompt corpus through the compiler.

SARIF is deliberately NOT emitted: returns aggregate corpus routing metrics, not a line-level findings stream.

Companion to ``roam compiler-health``. Where ``compiler-health`` reads live
on-disk telemetry, ``compiler-corpus`` *runs* the compile pipeline against a
file of newline-separated prompts and aggregates the routing distribution,
L1 fire rate, envelope sizes, and compile latency over that fixed corpus.

Input file format:
  * One prompt per line.
  * Blank lines and lines starting with ``#`` are skipped.

Output: a single JSON envelope (or human verdict text) with:
  * ``corpus_path`` — file analyzed
  * ``prompts_processed`` — int (after blank/comment skip + ``--limit``)
  * ``artifact_distribution`` — counts of ``l1_probe`` / ``facts`` / ``lean`` / ``full`` / …
  * ``procedure_distribution`` — top 10 procedures by frequency
  * ``l1_route_rate_pct`` — int 0..100
  * ``envelope_bytes`` — ``{p50, p95, max}``
  * ``compile_latency_ms`` — ``{p50, p95, max}``
  * ``top_misses`` — prompts where the L1 probe surfaced no procedure-specific facts
  * ``agent_contract.facts`` — LAW-4 anchored

LAW-4 anchors used: ``prompts``, ``bytes``, ``entries``, ``findings``,
``files``.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_DEFAULT_LIMIT = 50
_DEFAULT_CORPUS = "internal/benchmarks/envelope-baselines/CORPUS.txt"


# ----------------------------------------------------------------------
# Corpus loading
# ----------------------------------------------------------------------


def _load_corpus(path: Path, limit: int) -> list[str]:
    """Read prompts from ``path``; skip blanks + ``#`` comments; cap at ``limit``.

    Missing file returns ``[]`` — caller renders ``state: "not_initialized"``.
    """
    if not path.exists() or not path.is_file():
        return []
    prompts: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                prompts.append(line)
                if len(prompts) >= limit:
                    break
    except OSError:
        return []
    return prompts


# ----------------------------------------------------------------------
# Per-prompt compile + record
# ----------------------------------------------------------------------


def _compile_one(prompt: str, cwd: str | None) -> dict:
    """Compile a single prompt; capture routing + size + latency.

    Returns a dict with: ``prompt``, ``procedure``, ``artifact_label``,
    ``envelope_bytes``, ``compile_ms``, ``probe_empty`` (bool),
    ``error`` (str or None).
    """
    record: dict = {
        "prompt": prompt,
        "procedure": None,
        "artifact_label": None,
        "envelope_bytes": 0,
        "compile_ms": 0.0,
        "probe_empty": True,
        "error": None,
    }
    t0 = time.perf_counter()
    try:
        from roam.plan.compiler import compile_for_artifact, compile_plan

        plan = compile_plan(prompt, cwd)
        envelope, label = compile_for_artifact(plan, cwd)
    except Exception as exc:  # noqa: BLE001 — capture so one bad prompt doesn't kill the run
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["compile_ms"] = (time.perf_counter() - t0) * 1000
        return record

    record["compile_ms"] = (time.perf_counter() - t0) * 1000
    record["procedure"] = getattr(plan, "procedure", None) or "unknown"
    record["artifact_label"] = label or "unknown"
    try:
        body = json.dumps(envelope, default=str)
        record["envelope_bytes"] = len(body.encode("utf-8"))
    except (TypeError, ValueError):
        record["envelope_bytes"] = 0

    # "Probe empty" heuristic — delegated to the canonical introspector so
    # this command can never re-drift into the agent_contract.facts vs
    # prefetched_facts bug (2026-06-02 dogfood). An L1 envelope is empty when
    # it carries no SUBSTANTIVE probe families; non-L1 labels are never
    # misses (lower-signal by design).
    from roam.plan.envelope_introspect import introspect as _introspect

    record["probe_empty"] = _introspect(envelope).get("probe_empty", False)
    return record


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Empty input → 0."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def _aggregate(records: list[dict]) -> dict:
    """Roll per-prompt records up into the envelope sections.

    Empty input returns ``{"state": "not_initialized"}`` so the caller can
    render the no-data path without crashing.
    """
    if not records:
        return {"state": "not_initialized"}

    art_counts: Counter = Counter()
    proc_counts: Counter = Counter()
    env_sizes: list[float] = []
    latencies: list[float] = []
    misses: list[str] = []
    errors: list[dict] = []

    for r in records:
        if r.get("error"):
            errors.append({"prompt": r["prompt"], "error": r["error"]})
            continue
        art = r.get("artifact_label") or "unknown"
        proc = r.get("procedure") or "unknown"
        art_counts[art] += 1
        proc_counts[proc] += 1
        env_sizes.append(float(r.get("envelope_bytes", 0)))
        latencies.append(float(r.get("compile_ms", 0.0)))
        if r.get("probe_empty"):
            misses.append(r["prompt"])

    total = sum(art_counts.values())
    l1_n = art_counts.get("l1_probe", 0)
    l1_pct = int(round(l1_n * 100 / total)) if total else 0

    def _rounded(values: list[float]) -> dict:
        return {
            "p50": int(round(_percentile(values, 50))),
            "p95": int(round(_percentile(values, 95))),
            "max": int(round(max(values) if values else 0)),
        }

    return {
        "state": "ok",
        "artifact_distribution": dict(art_counts),
        "procedure_distribution": dict(proc_counts.most_common(10)),
        "l1_route_rate_pct": l1_pct,
        "envelope_bytes": _rounded(env_sizes),
        "compile_latency_ms": _rounded(latencies),
        "top_misses": misses[:10],
        "compile_errors": errors[:10],
    }


# ----------------------------------------------------------------------
# Score
# ----------------------------------------------------------------------


def _compute_score(agg: dict) -> int:
    """0..100 composite over L1 fire rate + latency budget.

    Weights:
      * L1 fire rate ........ 60 (l1_pct/100 * 60)
      * Latency budget ...... 40 (1.0 at p50<=500ms, 0.0 at p50>=5000ms)
    """
    if agg.get("state") != "ok":
        return 0
    l1_pct = agg.get("l1_route_rate_pct", 0) or 0
    p50 = agg.get("compile_latency_ms", {}).get("p50", 0) or 0
    if p50 <= 500:
        lat = 1.0
    elif p50 >= 5000:
        lat = 0.0
    else:
        lat = max(0.0, 1.0 - (p50 - 500) / 4500.0)
    return int(round((l1_pct / 100.0) * 60.0 + lat * 40.0))


# ----------------------------------------------------------------------
# CLI command
# ----------------------------------------------------------------------


@click.command(name="compiler-corpus")
@click.option(
    "--corpus",
    "corpus_path",
    default=_DEFAULT_CORPUS,
    show_default=True,
    type=click.Path(file_okay=True, dir_okay=False),
    help="Newline-separated prompt corpus file",
)
@click.option(
    "--limit", default=_DEFAULT_LIMIT, show_default=True, type=int, help="Max prompts to compile (bounds wall time)"
)
@click.option(
    "--root",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False, dir_okay=True),
    help="Project root passed to the compiler as cwd",
)
@click.pass_context
@roam_capability(
    name="compiler-corpus",
    category="planning",
    summary="Compile a saved prompt corpus and aggregate routing + L1 + envelope-size + latency",
    inputs=("--corpus", "--limit", "--root"),
    outputs=("summary_envelope",),
    examples=(
        "roam compiler-corpus",
        "roam compiler-corpus --corpus internal/benchmarks/envelope-baselines/CORPUS.txt",
        "roam --json compiler-corpus --corpus /tmp/my-corpus.txt --limit 20",
    ),
    tags=("planning", "compiler", "corpus"),
    requires_index=False,
    ai_safe=True,
    side_effect=False,
)
def compiler_corpus(ctx: click.Context, corpus_path: str, limit: int, root: str) -> None:
    """Compile every prompt in a corpus file and aggregate the pipeline metrics.

    Unlike ``roam compiler-health`` (which reads live ``.roam/compile-runs.jsonl``
    telemetry), this command *runs* ``compile_plan`` + ``compile_for_artifact``
    in-process for each prompt and reports the routing distribution, L1 fire
    rate, envelope-size percentiles, and compile-latency percentiles over the
    fixed input set. Use it to compare two compiler revisions against the same
    prompt set without shipping a benchmark harness.

    Bounds wall time via ``--limit`` (default 50). Tolerant of bad prompts:
    per-prompt exceptions are captured into ``compile_errors``.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root_p = Path(root).resolve()
    corpus_p = Path(corpus_path)
    if not corpus_p.is_absolute():
        corpus_p = (root_p / corpus_p).resolve()

    prompts = _load_corpus(corpus_p, limit=max(0, int(limit)))

    # Missing/empty corpus → render not_initialized envelope and exit cleanly.
    if not prompts:
        verdict = f"Compiler-corpus: 0 prompts ({corpus_p.name}) — corpus not initialized"
        envelope = json_envelope(
            "compiler-corpus",
            summary={
                "verdict": verdict,
                "score": 0,
                "partial_success": True,
                "state": "not_initialized",
            },
            corpus_path=str(corpus_p),
            prompts_processed=0,
            artifact_distribution={},
            procedure_distribution={},
            l1_route_rate_pct=0,
            envelope_bytes={"p50": 0, "p95": 0, "max": 0},
            compile_latency_ms={"p50": 0, "p95": 0, "max": 0},
            top_misses=[],
            compile_errors=[],
            agent_contract={
                "facts": [
                    "0 corpus prompts",
                    "0 corpus entries",
                    "0 envelope bytes",
                    "0 compile errors",
                ],
                "next_commands": [
                    f"echo 'roam compile-stats' > {corpus_p}  # seed a corpus file",
                    "roam compiler-health  # inspect live telemetry instead",
                ],
                "risks": [],
                "confidence": None,
            },
            score_definition="weighted: l1=60 latency=40; 0 when corpus is empty",
        )
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo(f"VERDICT: {verdict}")
        return

    # Compile every prompt; one bad prompt does not abort the run.
    records = [_compile_one(p, str(root_p)) for p in prompts]
    agg = _aggregate(records)
    score = _compute_score(agg)

    art_dist = agg.get("artifact_distribution", {})
    proc_dist = agg.get("procedure_distribution", {})
    l1_pct = agg.get("l1_route_rate_pct", 0)
    env_sizes = agg.get("envelope_bytes", {"p50": 0, "p95": 0, "max": 0})
    latencies = agg.get("compile_latency_ms", {"p50": 0, "p95": 0, "max": 0})
    misses = agg.get("top_misses", [])
    errors = agg.get("compile_errors", [])

    # ``freeform`` shorthand covers ``facts`` + ``contract`` + ``full`` —
    # anything not L1. Surface it in the verdict for the at-a-glance read.
    freeform_n = sum(v for k, v in art_dist.items() if k != "l1_probe")
    total = sum(art_dist.values()) or 1
    freeform_pct = int(round(freeform_n * 100 / total))

    verdict = (
        f"Compiler-corpus: {len(prompts)} prompts, "
        f"{l1_pct}% L1, {freeform_pct}% freeform, "
        f"p50={latencies.get('p50', 0)}ms"
    )

    partial = bool(errors) or any(r.get("error") for r in records)

    # LAW-4 anchored facts (terminals: prompts, entries, bytes, findings,
    # errors, ms, files).
    facts = [
        f"{len(prompts)} corpus prompts",
        f"{len(proc_dist)} procedure entries",
        f"{env_sizes.get('p50', 0)} envelope bytes",
        f"{len(misses)} empty-probe findings",
        f"{len(errors)} compile errors",
    ]

    summary = {
        "verdict": verdict,
        "score": score,
        "partial_success": partial,
        "state": "ok",
    }

    envelope = json_envelope(
        "compiler-corpus",
        summary=summary,
        corpus_path=str(corpus_p),
        prompts_processed=len(prompts),
        artifact_distribution=art_dist,
        procedure_distribution=proc_dist,
        l1_route_rate_pct=l1_pct,
        envelope_bytes=env_sizes,
        compile_latency_ms=latencies,
        top_misses=misses,
        compile_errors=errors,
        agent_contract={
            "facts": facts,
            "next_commands": [
                "roam compiler-health           # live-telemetry counterpart",
                "roam compile-stats --by-mode   # per-mode breakdown of live runs",
                f"roam compiler-corpus --corpus {corpus_p.name} --limit 200  # widen the sample",
            ],
            "risks": [],
            "confidence": None,
        },
        score_definition="weighted: l1=60 latency=40 (p50 budget: 500ms full credit, 5000ms zero)",
        l1_route_rate_pct_definition="100 * count(art_label == 'l1_probe') / count(all successful compiles)",
    )

    if json_mode:
        click.echo(to_json(envelope))
        return

    # Text rendering — verdict-first.
    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(f"Score: {score}/100")
    click.echo(f"Corpus: {corpus_p}")
    click.echo(f"Prompts: {len(prompts)}")
    click.echo("")
    click.echo("Artifact distribution:")
    for art, n in sorted(art_dist.items(), key=lambda kv: -kv[1]):
        click.echo(f"  {art:<14s} {n:>4d}")
    click.echo("")
    click.echo("Top procedures:")
    for proc, n in proc_dist.items():
        click.echo(f"  {proc:<26s} {n:>4d}")
    click.echo("")
    click.echo(
        f"Envelope bytes: p50={env_sizes.get('p50', 0)} p95={env_sizes.get('p95', 0)} max={env_sizes.get('max', 0)}"
    )
    click.echo(
        f"Compile ms:     p50={latencies.get('p50', 0)} p95={latencies.get('p95', 0)} max={latencies.get('max', 0)}"
    )
    if misses:
        click.echo("")
        click.echo(f"Top empty-probe prompts ({len(misses)}):")
        for p in misses[:5]:
            click.echo(f"  - {p[:80]}")
    if errors:
        click.echo("")
        click.echo(f"Compile errors ({len(errors)}):")
        for e in errors[:5]:
            click.echo(f"  - {e['error']}: {e['prompt'][:60]}")
