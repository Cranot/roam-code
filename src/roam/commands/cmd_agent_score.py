"""``roam agent-score`` -- aggregate scoring across runs in ``.roam/runs/``.

Reads every run on disk (optionally filtered by ``--agent`` /
``--since``), buckets events by agent, and emits a composite score in
0..100 per agent plus useful diagnostic counts.

The score is intentionally a 3-component composite, not a single
black-box number -- consumers can opt into any individual signal:

  - completion_rate    fraction of runs that hit a non-``in_progress`` status
  - clean_signal_rate  ``1 - partial_success_rate``
  - breadth_factor     ``min(unique_actions / 5, 1.0)``

Then::

    overall_score = round(
        completion_rate   * 70 +   # 70 pts for finishing runs
        clean_signal_rate * 20 +   # 20 pts for clean envelopes
        breadth_factor    * 10,    # 10 pts for >=5 distinct commands
        1,
    )

Why this shape:
  - The 70/20/10 split makes "did the agent finish at all" the dominant
    signal (you cannot earn breadth or signal points on runs you never
    closed). This mirrors the dogfood observation that *abandoned* runs
    are the single strongest predictor of an agent under load.
  - ``partial_success`` is the dogfood-validated proxy for "the agent
    proceeded despite a fuzzy result". Weighting it at 20 stops a
    careful agent from being penalised heavily for one truncated impact
    output.
  - Breadth caps at 5 actions on purpose: we want to reward agents that
    use the gate suite (preflight, diff, critique, ...) without
    rewarding "use 50 commands once each" theatrics.

Low-confidence flag: agents with <2 runs get ``confidence: "low"`` and
the verdict mentions it explicitly so consumers do not over-index on a
single data point.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because agent-score findings are invocation-scoped aggregates
(a composite 0-100 score) tied to a set of runs at invocation time --
not per-location violations. Multi-location expansion would distort
SARIF semantics ("score 75 across 10 runs" is not a per-file violation).
See action.yml line 401 _SUPPORTED_SARIF allowlist and W1149 audit memo.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Optional

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.ledger import list_runs, read_run_events, runs_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _duration_ms(started_at: str, ended_at: Optional[str]) -> int:
    """Run duration in ms, or 0 when either side is missing/malformed."""
    if not started_at or not ended_at:
        return 0
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    delta = e - s
    return int(delta.total_seconds() * 1000)


def _median(values: list[int]) -> int:
    """Plain median; returns 0 on empty input."""
    if not values:
        return 0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) // 2


def _score_one(agg: dict) -> dict:
    """Compute the composite score for a single agent's aggregate dict.

    Returns the same dict, mutated, with ``score`` + the three score
    components added. Kept tiny + side-effecting on purpose -- the
    caller iterates over the agents map.
    """
    total = agg["runs_total"]
    completed = agg["runs_completed"]
    failed = agg["runs_failed"]
    abandoned = agg["runs_abandoned"]
    # Completion: a run "completed" in our sense means it was ended with
    # a non-in_progress status (completed / failed / abandoned). For the
    # score, failure is still *finishing the run* so we credit it, but
    # abandoned (never ended) is not.
    completion_rate = (completed + failed) / total if total else 0.0
    clean_rate = 1.0 - agg["partial_success_rate"]
    breadth_factor = min(len(agg["unique_actions"]) / 5.0, 1.0)

    score = round(
        completion_rate * 70.0 + clean_rate * 20.0 + breadth_factor * 10.0,
        1,
    )
    agg["score"] = score
    agg["score_components"] = {
        "completion_rate": round(completion_rate, 3),
        "clean_signal_rate": round(clean_rate, 3),
        "breadth_factor": round(breadth_factor, 3),
        "weights": {"completion": 70, "clean_signal": 20, "breadth": 10},
    }
    agg["confidence"] = "low" if total < 2 else "ok"
    _ = (failed, abandoned)  # documented above; left in agg for inspection
    return agg


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="agent-score",
    category="agent-os",
    summary="Aggregate scoring across agent runs: completion, clean-signal, breadth -> 0..100.",
    inputs=["--agent", "--since", "--top"],
    outputs=["agents", "score", "stats"],
    examples=[
        "roam agent-score",
        "roam agent-score --agent claude-code",
        "roam agent-score --since 2026-05-01 --top 5",
    ],
    tags=["runs", "agent-os", "scoring", "r20"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("agent-score")
@click.option("--agent", default=None, help="Filter to runs by this agent (default: score all agents).")
@click.option("--since", default=None, help="Filter to runs started at >= <SINCE> (ISO-8601).")  # W1117-followup
@click.option(
    "--top", "--limit", "top", default=0, type=int, help="Cap agents reported to <N> highest scores (0 = all)."
)  # W1142: --limit alias; W1117-followup
@click.pass_context
def agent_score_cmd(ctx, agent, since, top):
    """Aggregate runs and score each agent on a 0..100 composite.

    Defaults to scoring ALL agents; pass ``--agent`` to restrict to one.
    Empty state (no runs / no matching runs) returns a clean envelope
    with ``state: "no_data"`` -- never empty stdout, never a crash.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    rroot = runs_root(root)

    # ---- Empty state: no runs directory yet ---------------------------
    if not rroot.exists():
        verdict = "no runs yet -- run 'roam runs start --agent NAME' to open one"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "agent-score",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "state": "no_data",
                            "agents_scored": 0,
                            "next_commands": ["roam runs start --agent <name>"],
                        },
                        budget=token_budget,
                        agents=[],
                        facts_extra=["no .roam/runs/ directory exists yet"],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    # ---- Bucket runs by agent ----------------------------------------
    metas = list(list_runs(root, agent=agent, since=since))
    if not metas:
        verdict = "no runs match the given filters"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "agent-score",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "state": "no_data",
                            "agents_scored": 0,
                            "next_commands": ["roam runs list"],
                        },
                        budget=token_budget,
                        agents=[],
                        facts_extra=["no runs match the filters"],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    by_agent: dict[str, dict] = {}
    for m in metas:
        a = m.agent or "(unknown)"
        agg = by_agent.setdefault(
            a,
            {
                "agent": a,
                "runs_total": 0,
                "runs_completed": 0,
                "runs_failed": 0,
                "runs_abandoned": 0,
                "_in_progress": 0,
                "_durations_ms": [],
                "_event_count": 0,
                "_partial_count": 0,
                "_actions": Counter(),
                "_partial_actions": Counter(),
                "_run_ids": [],
                "_highest_partial_run_id": None,
                "_highest_partial_count": 0,
            },
        )
        agg["runs_total"] += 1
        agg["_run_ids"].append(m.run_id)
        if m.status == "completed":
            agg["runs_completed"] += 1
        elif m.status == "failed":
            agg["runs_failed"] += 1
        elif m.status == "abandoned":
            agg["runs_abandoned"] += 1
        elif m.status == "in_progress":
            # An in-progress run on disk that's stale (no recent activity)
            # gets surfaced as "in_progress" rather than reclassified --
            # the score will tag it as not-completed in completion_rate.
            agg["_in_progress"] += 1

        if m.ended_at:
            agg["_durations_ms"].append(_duration_ms(m.started_at, m.ended_at))

        # Walk events for this run; cheap pass, ledger is JSONL.
        per_run_partial = 0
        for ev in read_run_events(root, m.run_id):
            agg["_event_count"] += 1
            act = ev.get("action") or ""
            if act:
                agg["_actions"][act] += 1
            if bool(ev.get("partial_success")):
                agg["_partial_count"] += 1
                per_run_partial += 1
                if act:
                    agg["_partial_actions"][act] += 1
        if per_run_partial > agg["_highest_partial_count"]:
            agg["_highest_partial_count"] = per_run_partial
            agg["_highest_partial_run_id"] = m.run_id

    # ---- Finalise each agent's aggregate -----------------------------
    scored: list[dict] = []
    for a, agg in by_agent.items():
        events_total = agg["_event_count"]
        partial_rate = agg["_partial_count"] / events_total if events_total else 0.0
        # Treat in-progress runs as ABANDONED for scoring purposes: an
        # agent that opens runs and never closes them shouldn't earn the
        # 70 completion points for those runs. We keep the raw counters
        # accessible (in_progress is not silently dropped).
        agg["runs_abandoned"] += agg["_in_progress"]

        result = {
            "agent": agg["agent"],
            "runs_total": agg["runs_total"],
            "runs_completed": agg["runs_completed"],
            "runs_failed": agg["runs_failed"],
            "runs_abandoned": agg["runs_abandoned"],
            "runs_in_progress": agg["_in_progress"],
            "partial_success_rate": round(partial_rate, 3),
            "median_run_duration_ms": _median(agg["_durations_ms"]),
            "unique_actions": sorted(agg["_actions"].keys()),
            "most_common_actions": [{"action": a, "count": c} for a, c in agg["_actions"].most_common(5)],
            "most_common_partial_failures": [
                {"action": a, "count": c} for a, c in agg["_partial_actions"].most_common(5)
            ],
            "highest_partial_success_run_id": agg["_highest_partial_run_id"],
            "event_count": events_total,
            "run_ids": agg["_run_ids"],
        }
        # Carry partial_success_rate into the score helper.
        result["partial_success_rate"] = round(partial_rate, 3)
        # _score_one mutates in place, but it reads partial_success_rate +
        # unique_actions + the run counts, so build a tiny dict and merge.
        tmp = {
            "runs_total": result["runs_total"],
            "runs_completed": result["runs_completed"],
            "runs_failed": result["runs_failed"],
            "runs_abandoned": result["runs_abandoned"],
            "partial_success_rate": result["partial_success_rate"],
            "unique_actions": result["unique_actions"],
        }
        _score_one(tmp)
        result["score"] = tmp["score"]
        result["score_components"] = tmp["score_components"]
        result["confidence"] = tmp["confidence"]
        scored.append(result)

    # Sort: highest score first; tie-break on more runs (more confident).
    scored.sort(key=lambda r: (-r["score"], -r["runs_total"], r["agent"]))
    # W1142-followup-B: cap-hit disclosure. Record the full pre-slice
    # count so the envelope can disclose when ``--limit`` collapsed
    # the agent list.
    total_scored_full = len(scored)
    if top > 0:
        scored = scored[:top]
    scored_truncated = total_scored_full > len(scored)

    # ---- Verdict + facts ---------------------------------------------
    agents_scored = len(scored)
    if agents_scored == 1:
        r = scored[0]
        if r["confidence"] == "low":
            verdict = (
                f"Agent {r['agent']}: {r['score']}/100 over {r['runs_total']} run "
                f"(low confidence: only {r['runs_total']} run(s))"
            )
        else:
            verdict = f"Agent {r['agent']}: {r['score']}/100 over {r['runs_total']} runs"
    else:
        top_agent = scored[0]
        verdict = (
            f"Scored {agents_scored} agents; top: {top_agent['agent']} "
            f"{top_agent['score']}/100 over {top_agent['runs_total']} runs"
        )

    facts = []
    for r in scored:
        facts.append(
            f"agent {r['agent']} scored {r['score']}/100 "
            f"(runs_total={r['runs_total']}, "
            f"completed={r['runs_completed']}, "
            f"partial_success_rate={r['partial_success_rate']})"
        )
        if r["confidence"] == "low":
            facts.append(f"agent {r['agent']}: low confidence ({r['runs_total']} run(s))")

    next_commands: list[str] = []
    # Steer the agent at its worst run first: highest-partial-success
    # run id is the most likely to be worth replaying.
    for r in scored:
        if r["highest_partial_success_run_id"]:
            next_commands.append(f"roam replay {r['highest_partial_success_run_id']}")
            break
    next_commands.append("roam runs list")
    if any(r["confidence"] == "low" for r in scored):
        next_commands.append("roam runs start --agent <name>")

    partial_success_any = any(r["partial_success_rate"] > 0.0 for r in scored)

    if json_mode:
        # W1142-followup-B: cap-hit disclosure. Surface whether the
        # agent's --limit collapsed the scored agent list.
        #
        # ``count`` / ``total_count`` go into ``summary`` so the
        # formatter's auto-derive emits sensible LAW-4 facts
        # (``"16 agents scored"`` / ``"16 total count"``). The
        # CLI ``--limit`` value is a query parameter — humanizing
        # it produces semantically-broken ``"0 limit findings"`` /
        # ``"10 limit findings"`` strings, so keep it out of summary
        # and stamp it as a top-level meta field for consumers that
        # need to reason about the cap.
        _cap_summary = {
            "count": len(scored),
            "total_count": total_scored_full,
            "truncated": scored_truncated,
        }
        _warnings_out: list[str] = []
        if scored_truncated:
            _warnings_out.append(f"truncated to {len(scored)} of {total_scored_full} — pass --limit larger to see more")
        _summary = {
            "verdict": verdict,
            "partial_success": partial_success_any or scored_truncated,
            "state": "ok",
            "agents_scored": agents_scored,
            "next_commands": next_commands,
            **_cap_summary,
        }
        if _warnings_out:
            _summary["warnings_out"] = _warnings_out
        # Route next_commands through summary so the formatter
        # auto-derives ``agent_contract`` from ``summary.next_commands``.
        click.echo(
            to_json(
                json_envelope(
                    "agent-score",
                    summary=_summary,
                    budget=token_budget,
                    agents=scored,
                    score_formula={
                        "components": ["completion_rate", "clean_signal_rate", "breadth_factor"],
                        "weights": {"completion": 70, "clean_signal": 20, "breadth": 10},
                        "formula": "round(completion_rate*70 + (1-partial_success_rate)*20 + min(unique_actions/5,1.0)*10, 1)",
                    },
                    facts_extra=facts,
                    cap_limit=top,
                )
            )
        )
        return

    # ---- Text output --------------------------------------------------
    click.echo(f"VERDICT: {verdict}")
    rows = []
    for r in scored:
        rows.append(
            [
                r["agent"],
                str(r["score"]),
                str(r["runs_total"]),
                str(r["runs_completed"]),
                str(r["runs_failed"]),
                str(r["runs_abandoned"]),
                f"{r['partial_success_rate']:.2f}",
                r["confidence"],
            ]
        )
    click.echo(
        format_table(
            ["Agent", "Score", "Runs", "Done", "Fail", "Abandon", "Partial%", "Conf"],
            rows,
        )
    )
