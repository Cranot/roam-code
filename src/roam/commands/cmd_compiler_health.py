"""``roam compiler-health`` — daily-dashboard view of compiler quality.

SARIF is deliberately NOT emitted: returns a composed health-dashboard envelope, not a line-level findings stream.

Composes 4 cheap data sources into one envelope. **No live compile calls.**

Data sources (each best-effort; failures render as ``state``, not crash):

1. **envelope-baselines drift** — counts JSON files under
   ``internal/benchmarks/envelope-baselines/`` and surfaces the count as a
   drift baseline. If the directory does not exist, the section reports
   ``state: "no_baseline"``.
2. **dispatch-trace aggregate** — reads the last 100 rows of
   ``.roam/compile-runs.jsonl`` and computes a per-procedure histogram.
3. **compile-stats KPIs** — calls
   :func:`roam.commands.cmd_compile_stats._summarize` on the same rows;
   extracts ``compile_latency_ms.p50``, ``l1_probe_pct``, and a by-mode
   breakdown.
4. **magic-numbers self-scan** — invokes
   :func:`roam.commands.cmd_magic_numbers._scan_file` on
   ``src/roam/plan/compiler.py`` with ``threshold=3`` and surfaces the
   top 5 most-occurring values.

Score weights (heuristic, 0..100):
  * L1 fire-rate ........ 40 pts (l1_pct / 100 * 40)
  * Latency budget ..... 25 pts (1.0 if p50<=500ms, 0 at p50>=5000ms)
  * Drift cleanliness .. 15 pts (drifted_count==0 ? 15 : 0; skipped if no baseline)
  * Magic-number debt .. 20 pts (max(0, 20 - findings_count * 2))

When a section is ``not_initialized`` its weight is dropped from the
denominator (score is normalized over the active weights).

LAW-4 anchors used: ``findings``, ``files``, ``rows``, ``alerts``,
``modes``.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_TELEMETRY_TAIL = 100
_COMPILER_TARGET = "src/roam/plan/compiler.py"
_MAGIC_THRESHOLD = 3
_BASELINE_DIR = "internal/benchmarks/envelope-baselines"
_DASHBOARD_L1_REQUIRED_PROCEDURES = frozenset(
    {
        "structural_coupling",
        "structural_callers",
        "structural_dead",
        "structural_blast",
        "structural_complexity",
        "structural_cycle",
        "trace_query",
        "describe_file",
        "stack_trace_fix",
        "refactor_move",
        "symbol_defined_where",
        "top_n_ranking",
        "cli_verb_why_slow",
        "compare_x_vs_y",
    }
)


# ----------------------------------------------------------------------
# Section 1 — envelope baseline drift
# ----------------------------------------------------------------------


def _section_env_drift(root: Path) -> dict:
    """Return drift summary or ``{"state": "no_baseline"}``.

    Counts ``*.json`` baselines under ``internal/benchmarks/envelope-baselines/``.
    Drift detection here is a placeholder: without a recompile we cannot
    know which baselines changed, so ``drifted_count`` is always 0 (caller
    runs ``roam envelope-diff`` for the real comparison). The presence of
    the directory and the baseline_count is itself a useful health signal.
    """
    bdir = root / _BASELINE_DIR
    if not bdir.exists() or not bdir.is_dir():
        return {"state": "no_baseline"}
    try:
        baselines = sorted(bdir.rglob("*.json"))
    except OSError:
        return {"state": "no_baseline"}
    return {
        "state": "ok",
        "baseline_count": len(baselines),
        "drifted_count": 0,
        "baseline_dir": str(bdir.relative_to(root)) if root in bdir.parents else str(bdir),
    }


# ----------------------------------------------------------------------
# Section 2 — dispatch-trace aggregate
# ----------------------------------------------------------------------


def _load_recent_telemetry(root: Path, tail: int = _TELEMETRY_TAIL) -> list[dict]:
    """Read the last ``tail`` rows of ``.roam/compile-runs.jsonl``.

    Tolerates corrupted JSON lines. Returns ``[]`` if the file is missing.
    """
    log_path = root / ".roam" / "compile-runs.jsonl"
    if not log_path.exists():
        return []
    rows: deque[dict] = deque(maxlen=max(1, tail))
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(rows)


def _section_routing(rows: list[dict]) -> dict:
    """Per-procedure routing histogram (top 10)."""
    if not rows:
        return {"state": "not_initialized"}
    counts: Counter = Counter()
    for r in rows:
        proc = r.get("procedure") or "unknown"
        counts[proc] += 1
    top = dict(counts.most_common(10))
    dominant_proc, _ = counts.most_common(1)[0]
    return {
        "state": "ok",
        "row_count": len(rows),
        "distribution": top,
        "dominant_procedure": dominant_proc,
    }


# ----------------------------------------------------------------------
# Section 3 — compile-stats per-mode KPIs
# ----------------------------------------------------------------------


def _section_per_mode_kpis(rows: list[dict]) -> dict:
    """Delegate to ``cmd_compile_stats._summarize`` + compute by-mode rollup.

    Returns ``{"state": "not_initialized"}`` on empty input.
    """
    if not rows:
        return {"state": "not_initialized"}
    try:
        from roam.commands.cmd_compile_stats import _summarize
    except ImportError:
        return {"state": "not_initialized"}
    try:
        summ = _summarize(rows)
    except (AttributeError, TypeError):
        return {"state": "not_initialized"}

    # Per-mode rollup (mirrors --by-mode flag on the CLI).
    per_mode: dict[str, dict] = {}
    bucket: dict[str, dict] = {}
    eligible_rows = 0
    eligible_l1_rows = 0
    for r in rows:
        mode = r.get("agent_mode") or "unknown"
        d = bucket.setdefault(
            mode,
            {
                "n": 0,
                "l1": 0,
                "ms_sum": 0.0,
                "cache_hits": 0,
                "eligible": 0,
                "eligible_l1": 0,
                "freeform": 0,
                "low_conf": 0,
                "task_hashes": Counter(),
            },
        )
        d["n"] += 1
        is_l1 = r.get("art_label") == "l1_probe"
        if is_l1:
            d["l1"] += 1
        proc = r.get("procedure") or "unknown"
        is_required = proc in _DASHBOARD_L1_REQUIRED_PROCEDURES
        if is_required:
            d["eligible"] += 1
            eligible_rows += 1
            if is_l1:
                d["eligible_l1"] += 1
                eligible_l1_rows += 1
        if proc == "freeform_explore":
            d["freeform"] += 1
        if isinstance(r.get("classifier_conf"), (int, float)) and r["classifier_conf"] < 0.6:
            d["low_conf"] += 1
        if r.get("cache_hit") is True:
            d["cache_hits"] += 1
        task_hash = r.get("task_hash")
        if task_hash:
            d["task_hashes"][task_hash] += 1
        ms = r.get("compile_ms")
        if isinstance(ms, (int, float)):
            d["ms_sum"] += ms
    for mode, d in bucket.items():
        repeated_rows = sum(c for c in d["task_hashes"].values() if c > 1)
        per_mode[mode] = {
            "n": d["n"],
            "l1_pct": d["l1"] * 100 // d["n"] if d["n"] else 0,
            "mean_compile_ms": round(d["ms_sum"] / d["n"], 1) if d["n"] else 0,
            "cache_hit_pct": d["cache_hits"] * 100 // d["n"] if d["n"] else 0,
            "l1_eligible_count": d["eligible"],
            "l1_eligible_probe_pct": (d["eligible_l1"] * 100 // d["eligible"] if d["eligible"] else None),
            "freeform_pct": d["freeform"] * 100 // d["n"] if d["n"] else 0,
            "low_conf_pct": d["low_conf"] * 100 // d["n"] if d["n"] else 0,
            "unique_tasks": len(d["task_hashes"]),
            "repeat_task_pct": repeated_rows * 100 // d["n"] if d["n"] else 0,
        }

    return {
        "state": "ok",
        "median_compile_ms": summ.get("compile_latency_ms", {}).get("p50"),
        "l1_probe_pct": summ.get("l1_probe_pct", 0),
        "l1_eligible_count": eligible_rows,
        "l1_eligible_probe_pct": (eligible_l1_rows * 100 // eligible_rows if eligible_rows else None),
        "cache_hit_pct": summ.get("cache_hit_pct", 0),
        "row_count": summ.get("row_count", 0),
        "per_mode": per_mode,
    }


# ----------------------------------------------------------------------
# Section 4 — self magic-numbers scan
# ----------------------------------------------------------------------


def _section_self_magic(root: Path, threshold: int = _MAGIC_THRESHOLD) -> dict:
    """AST-scan ``src/roam/plan/compiler.py`` and surface the top 5 values.

    Returns ``{"state": "not_initialized"}`` if the file is missing.
    """
    target = root / _COMPILER_TARGET
    if not target.exists() or not target.is_file():
        return {"state": "not_initialized"}
    try:
        from roam.commands.cmd_magic_numbers import _scan_file
    except ImportError:
        return {"state": "not_initialized"}
    try:
        occurrences = _scan_file(target, threshold=threshold, include_trivial=False)
    except Exception:
        return {"state": "not_initialized"}

    by_value: dict[int | float, list[tuple[int, str]]] = {}
    for value, lineno, snippet in occurrences:
        by_value.setdefault(value, []).append((lineno, snippet))

    findings = []
    for value, sites in by_value.items():
        if len(sites) < threshold:
            continue
        sites_sorted = sorted(sites, key=lambda s: s[0])
        findings.append(
            {
                "value": value,
                "occurrences": len(sites),
                "sites_top3": [{"line": ln, "context_snippet": sn} for ln, sn in sites_sorted[:3]],
            }
        )
    findings.sort(key=lambda f: (-f["occurrences"], str(f["value"])))
    return {
        "state": "ok",
        "target_file": _COMPILER_TARGET,
        "threshold": threshold,
        "findings_count": len(findings),
        "top": findings[:5],
    }


# ----------------------------------------------------------------------
# Score + alerts
# ----------------------------------------------------------------------


def _compute_score(
    env_drift: dict,
    per_mode: dict,
    self_magic: dict,
) -> tuple[int, dict[str, float]]:
    """Composite 0..100 health score with documented weights.

    Weights:
      * L1 fire-rate ......... 40
      * Latency budget ....... 25
      * Drift cleanliness .... 15 (skipped if no_baseline)
      * Magic-number debt .... 20
    Active weights renormalize the denominator when a section is absent.
    """
    weights: dict[str, float] = {}
    contributions: dict[str, float] = {}

    if per_mode.get("state") == "ok":
        eligible_n = per_mode.get("l1_eligible_count")
        eligible_pct = per_mode.get("l1_eligible_probe_pct")
        if isinstance(eligible_n, int) and eligible_n >= 5 and isinstance(eligible_pct, int):
            l1_pct = eligible_pct
        else:
            l1_pct = per_mode.get("l1_probe_pct", 0) or 0
        contributions["l1_fire_rate"] = (l1_pct / 100.0) * 40.0
        weights["l1_fire_rate"] = 40.0

        p50 = per_mode.get("median_compile_ms")
        if isinstance(p50, (int, float)):
            # 1.0 at <=500ms; 0.0 at >=5000ms; linear in between.
            if p50 <= 500:
                lat = 1.0
            elif p50 >= 5000:
                lat = 0.0
            else:
                lat = max(0.0, 1.0 - (p50 - 500) / 4500.0)
            contributions["latency_budget"] = lat * 25.0
            weights["latency_budget"] = 25.0

    if env_drift.get("state") == "ok":
        drifted = env_drift.get("drifted_count", 0) or 0
        contributions["drift_clean"] = 15.0 if drifted == 0 else 0.0
        weights["drift_clean"] = 15.0

    if self_magic.get("state") == "ok":
        n_findings = self_magic.get("findings_count", 0) or 0
        contributions["magic_debt"] = max(0.0, 20.0 - n_findings * 2.0)
        weights["magic_debt"] = 20.0

    total_w = sum(weights.values())
    if total_w <= 0:
        return 0, contributions
    score = sum(contributions.values()) / total_w * 100.0
    return int(round(score)), contributions


def _alert(severity: str, message: str) -> dict:
    return {"severity": severity, "message": message}


def _global_l1_alert(per_mode: dict) -> dict | None:
    l1_pct = per_mode.get("l1_probe_pct", 0) or 0
    eligible_n = per_mode.get("l1_eligible_count")
    eligible_pct = per_mode.get("l1_eligible_probe_pct")
    if isinstance(eligible_n, int) and eligible_n >= 5 and isinstance(eligible_pct, int):
        if eligible_pct < 60:
            return _alert(
                "warn",
                f"l1 eligible probe rate {eligible_pct}% below 60% target across {eligible_n} rows",
            )
        return None
    if eligible_n is None and l1_pct < 60:
        return _alert("warn", f"l1 fire rate {l1_pct}% below 60% target")
    return None


def _mode_l1_alert(mode: str, stats: dict) -> dict | None:
    eligible = stats.get("l1_eligible_count", 0) or 0
    pct = stats.get("l1_eligible_probe_pct")
    if eligible >= 5 and isinstance(pct, int) and pct < 60:
        return _alert(
            "warn",
            f"{mode} l1 eligible probe rate {pct}% below 60% target across {eligible} rows",
        )
    return None


def _mode_cache_alert(mode: str, stats: dict) -> dict | None:
    cache_pct = stats.get("cache_hit_pct", 0) or 0
    repeat_pct = stats.get("repeat_task_pct", 0) or 0
    if stats.get("n", 0) >= 10 and repeat_pct >= 30 and cache_pct < 30:
        return _alert(
            "info",
            f"{mode} repeated-task cache hit rate {cache_pct}% below 30% despite {repeat_pct}% repeated rows",
        )
    return None


def _per_mode_alerts(per_mode: dict) -> list[dict]:
    alerts: list[dict] = []
    first = _global_l1_alert(per_mode)
    if first:
        alerts.append(first)
    p50 = per_mode.get("median_compile_ms")
    if isinstance(p50, (int, float)) and p50 > 2000:
        alerts.append(_alert("warn", f"compile p50 {p50}ms above 2000ms budget"))
    for mode, stats in sorted((per_mode.get("per_mode") or {}).items()):
        for item in (_mode_l1_alert(mode, stats), _mode_cache_alert(mode, stats)):
            if item:
                alerts.append(item)
    return alerts


def _build_alerts(
    env_drift: dict,
    routing: dict,
    per_mode: dict,
    self_magic: dict,
) -> list[dict]:
    """Best-effort alert list. Empty when nothing is wrong AND telemetry exists."""
    alerts: list[dict] = []
    if per_mode.get("state") == "ok":
        alerts.extend(_per_mode_alerts(per_mode))
    if self_magic.get("state") == "ok":
        n = self_magic.get("findings_count", 0) or 0
        if n >= 10:
            alerts.append(_alert("info", f"{n} magic numbers in compiler.py — refactor candidates"))
    if env_drift.get("state") == "no_baseline":
        alerts.append(_alert("info", "no envelope-baselines directory — drift gating disabled"))
    if routing.get("state") == "not_initialized":
        alerts.append(_alert("info", "no compile telemetry yet — run `roam compile` to populate"))
    return alerts


# ----------------------------------------------------------------------
# CLI command
# ----------------------------------------------------------------------


def _blank_evidence() -> dict:
    return {"section": None, "metric": None, "value": None, "threshold": None}


def _first_percent(msg: str) -> int | None:
    import re as _re

    m = _re.search(r"(\d+)%", msg)
    return int(m.group(1)) if m else None


def _first_ms(msg: str) -> float | None:
    import re as _re

    m = _re.search(r"(\d+(?:\.\d+)?)ms", msg)
    return float(m.group(1)) if m else None


def _first_magic_count(msg: str) -> int | None:
    import re as _re

    m = _re.search(r"(\d+) magic", msg)
    return int(m.group(1)) if m else None


def _guard_l1_spec(msg: str) -> tuple[str, dict, str] | None:
    if "l1 fire rate" not in msg and "l1 eligible probe rate" not in msg:
        return None
    metric = "l1_eligible_probe_pct" if "eligible" in msg else "l1_probe_pct"
    evidence = {"section": "per_mode_kpis", "metric": metric, "value": _first_percent(msg), "threshold": 60}
    fix = "Add probes for under-covered eligible procedures; rerun `roam compile-stats --by-mode`."
    return "l1_fire_rate_below_target", evidence, fix


def _guard_cache_spec(msg: str) -> tuple[str, dict, str] | None:
    if "repeated-task cache hit rate" not in msg:
        return None
    evidence = {"section": "per_mode_kpis", "metric": "cache_hit_pct", "value": _first_percent(msg), "threshold": 30}
    fix = "Run `roam compile-cache build --top-misses` to warm repeated miss tasks."
    return "compile_cache_repeat_miss", evidence, fix


def _guard_latency_spec(msg: str) -> tuple[str, dict, str] | None:
    if "compile p50" not in msg:
        return None
    evidence = {"section": "per_mode_kpis", "metric": "median_compile_ms", "value": _first_ms(msg), "threshold": 2000}
    fix = "Profile the dominant procedure; consider envelope-cache priming."
    return "compile_latency_above_budget", evidence, fix


def _guard_magic_spec(msg: str) -> tuple[str, dict, str] | None:
    if "magic numbers" not in msg:
        return None
    evidence = {
        "section": "self_magic_numbers",
        "metric": "findings_count",
        "value": _first_magic_count(msg),
        "threshold": 10,
    }
    fix = "Lift repeated literals into named constants in `src/roam/plan/compiler.py`."
    return "compiler_magic_number_debt", evidence, fix


def _guard_baseline_spec(msg: str) -> tuple[str, dict, str] | None:
    if "envelope-baselines" not in msg:
        return None
    evidence = {"section": "env_drift", "metric": "state", "value": "no_baseline", "threshold": None}
    fix = "Run `roam envelope-diff --persist` once to seed baselines."
    return "no_envelope_baseline", evidence, fix


def _guard_telemetry_spec(msg: str) -> tuple[str, dict, str] | None:
    if "no compile telemetry" not in msg:
        return None
    evidence = {"section": "routing_distribution", "metric": "state", "value": "not_initialized", "threshold": None}
    fix = "Run `roam compile <task>` to populate `.roam/compile-runs.jsonl`."
    return "no_compile_telemetry", evidence, fix


_GUARD_SPEC_BUILDERS = (
    _guard_l1_spec,
    _guard_cache_spec,
    _guard_latency_spec,
    _guard_magic_spec,
    _guard_baseline_spec,
    _guard_telemetry_spec,
)


def _guard_spec_for_alert(msg: str) -> tuple[str, dict, str]:
    default = (
        "compiler_health_alert",
        _blank_evidence(),
        "Inspect `roam compiler-health` and act on the named metric.",
    )
    return next((spec for build in _GUARD_SPEC_BUILDERS if (spec := build(msg))), default)


def _alert_to_guard_finding(alert: dict) -> dict:
    """Convert a compiler-health alert into a Roam Guard finding dict.

    Shape (closed; matches the verdict engine's optimizer_findings consumer):

      {
        "rule": "<short slug derived from message>",
        "severity": "warn" | "critical" | "info",
        "category": "compiler-health",
        "message": "<original alert message>",
        "evidence": {section, metric, value, threshold},
        "suggested_fix": "<imperative one-liner>",
      }
    """
    msg = alert.get("message", "")
    sev = alert.get("severity", "info")
    rule, evidence, fix = _guard_spec_for_alert(msg)
    return {
        "rule": rule,
        "severity": sev,
        "category": "compiler-health",
        "message": msg,
        "evidence": evidence,
        "suggested_fix": fix,
    }


def _write_guard_findings(path: Path, alerts: list[dict]) -> None:
    """Write alerts (translated to guard-finding shape) to ``path``.

    Append-friendly: if ``path`` exists and parses to a JSON list, the new
    findings are concatenated. Anything unparseable is overwritten.
    """
    findings = [_alert_to_guard_finding(a) for a in alerts]
    existing: list[dict] = []
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prior, list):
                existing = prior
        except (json.JSONDecodeError, OSError):
            existing = []
    payload = existing + findings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@click.command(name="compiler-health")
@click.option(
    "--root",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False, dir_okay=True),
    help="Project root (contains .roam/ and src/roam/plan/compiler.py)",
)
@click.option(
    "--emit-guard-findings",
    default=None,
    type=click.Path(file_okay=True, dir_okay=False),
    help="Also write alerts as Roam Guard findings JSON to this path (appends to existing JSON list).",
)
@click.pass_context
@roam_capability(
    name="compiler-health",
    category="health",
    summary="Daily dashboard composing envelope-drift + routing + KPIs + self-magic-numbers",
    inputs=("--root",),
    outputs=("summary_envelope",),
    examples=(
        "roam compiler-health",
        "roam --json compiler-health --root /path/to/project",
    ),
    tags=("health", "compiler", "dashboard"),
    requires_index=False,
    ai_safe=True,
    side_effect=False,
)
def compiler_health(ctx: click.Context, root: str, emit_guard_findings: str | None) -> None:
    """One envelope per compiler health snapshot.

    Composes 4 cheap data sources (envelope-drift baseline count, dispatch
    routing histogram, compile-stats KPIs, magic-numbers self-scan) into a
    single 0..100 score + alert list. Does NOT run ``roam compile`` — all
    data comes from on-disk telemetry / artifacts.

    This command displaces the workflow ``roam compile-stats && roam
    magic-numbers src/roam/plan/compiler.py && ls
    internal/benchmarks/envelope-baselines/`` — three Bash/Read invocations
    collapsed into one envelope.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root_p = Path(root).resolve()

    env_drift = _section_env_drift(root_p)
    rows = _load_recent_telemetry(root_p)
    routing = _section_routing(rows)
    per_mode = _section_per_mode_kpis(rows)
    self_magic = _section_self_magic(root_p)

    score, contributions = _compute_score(env_drift, per_mode, self_magic)
    alerts = _build_alerts(env_drift, routing, per_mode, self_magic)

    # Verdict components
    median_ms = per_mode.get("median_compile_ms") if per_mode.get("state") == "ok" else None
    median_str = f"{median_ms}ms" if isinstance(median_ms, (int, float)) else "n/a"
    drift_n = env_drift.get("drifted_count", 0) if env_drift.get("state") == "ok" else 0
    top_proc = routing.get("dominant_procedure") if routing.get("state") == "ok" else "n/a"

    verdict = f"Compiler health: {score}/100 — {drift_n} drift findings, {median_str} p50, {top_proc} dominant"

    # Partial-success: ANY section degraded means partial.
    sections = {
        "env_drift": env_drift,
        "routing_distribution": routing,
        "per_mode_kpis": per_mode,
        "self_magic_numbers": self_magic,
    }
    partial = any(s.get("state") in ("not_initialized", "no_baseline") for s in sections.values())

    # Facts — LAW-4 anchored (terminals in concrete_plural_terminals).
    facts = [
        f"compiler health score over {len(contributions)} weighted dimensions",
        f"{routing.get('row_count', 0) if routing.get('state') == 'ok' else 0} telemetry rows",
        (
            f"{self_magic.get('findings_count', 0)} magic-number findings"
            if self_magic.get("state") == "ok"
            else "0 magic-number findings"
        ),
        f"{len(alerts)} alerts",
        (f"{len(per_mode.get('per_mode', {}))} agent modes" if per_mode.get("state") == "ok" else "0 agent modes"),
    ]

    summary = {
        "verdict": verdict,
        "score": score,
        "score_components": {k: round(v, 2) for k, v in contributions.items()},
        "partial_success": partial,
    }

    envelope = json_envelope(
        "compiler-health",
        summary=summary,
        env_drift=env_drift,
        routing_distribution=routing,
        per_mode_kpis=per_mode,
        self_magic_numbers=self_magic,
        alerts=alerts,
        agent_contract={
            "facts": facts,
            "next_commands": [
                "roam compile <task>            # generate more telemetry",
                "roam compile-stats --by-mode   # full KPI breakdown",
                "roam compile-stats --top-misses # cache prebuild candidates",
                f"roam magic-numbers {_COMPILER_TARGET}  # full findings",
            ],
            "risks": [],
            "confidence": None,
        },
        score_definition="weighted: l1=40 latency=25 drift=15 magic=20; renormalized over active sections",
    )

    if emit_guard_findings:
        try:
            _write_guard_findings(Path(emit_guard_findings), alerts)
        except OSError:
            # Best-effort: never crash the dashboard for a sidecar-write failure.
            pass

    if json_mode:
        click.echo(to_json(envelope))
        return

    # Text rendering — verdict-first.
    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(f"Score: {score}/100")
    for comp, val in contributions.items():
        click.echo(f"  {comp:<20s} {val:>6.2f}")
    click.echo("")
    click.echo("Sections:")
    for label, sect in sections.items():
        state = sect.get("state", "?")
        click.echo(f"  {label:<22s} state={state}")
    if alerts:
        click.echo("")
        click.echo("Alerts:")
        for a in alerts:
            click.echo(f"  [{a['severity']}] {a['message']}")
