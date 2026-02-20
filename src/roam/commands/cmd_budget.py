"""Enforce architectural trajectory via per-PR delta limits."""

from __future__ import annotations

import os
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Default budget rules (used when no config file exists)
# ---------------------------------------------------------------------------

_DEFAULT_BUDGETS = [
    {"name": "Health score floor", "metric": "health_score", "max_decrease": 5},
    {"name": "No new cycles", "metric": "cycles", "max_increase": 0},
    {"name": "No new god components", "metric": "god_components", "max_increase": 0},
    {"name": "Layer discipline", "metric": "layer_violations", "max_increase": 0},
    {"name": "Complexity budget", "metric": "avg_complexity", "max_increase_pct": 10},
    {"name": "No new brain methods", "metric": "brain_methods", "max_increase": 0},
]

_DEFAULT_YAML = """\
# roam budget configuration
# Each rule defines a threshold for how much a metric may change per PR.
# Rules: max_increase, max_decrease (absolute), max_increase_pct (percentage).
# Run 'roam budget' to check. Exit code 1 if any rule fails (CI gate).
version: "1"
budgets:
  - name: "Health score floor"
    metric: health_score
    max_decrease: 5

  - name: "No new cycles"
    metric: cycles
    max_increase: 0

  - name: "No new god components"
    metric: god_components
    max_increase: 0

  - name: "Layer discipline"
    metric: layer_violations
    max_increase: 0

  - name: "Complexity budget"
    metric: avg_complexity
    max_increase_pct: 10

  - name: "No new brain methods"
    metric: brain_methods
    max_increase: 0
"""


# ---------------------------------------------------------------------------
# YAML loading with fallback
# ---------------------------------------------------------------------------


def _load_budgets(config_path: Path | None) -> list[dict]:
    """Load budget rules from a YAML file.

    Falls back to a simple line parser if PyYAML is not installed.
    """
    if config_path is None or not config_path.exists():
        return []

    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml(config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "budgets" not in data:
        return []
    return data["budgets"]


def _parse_simple_yaml(path: Path) -> list[dict]:
    """Minimal YAML parser for budget rules (no PyYAML dependency)."""
    text = path.read_text(encoding="utf-8")
    rules: list[dict] = []
    current_rule: dict | None = None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- name:"):
            if current_rule:
                rules.append(current_rule)
            current_rule = {"name": stripped.split(":", 1)[1].strip().strip('"').strip("'")}
        elif current_rule and ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            else:
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
            current_rule[key] = val

    if current_rule:
        rules.append(current_rule)

    return rules


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def _evaluate_rule(rule: dict, before: dict, after: dict) -> dict:
    """Evaluate a single budget rule.

    Returns {name, metric, status, before, after, delta, budget, reason}.
    """
    name = rule.get("name", "unnamed")
    metric = rule.get("metric", "")
    reason = rule.get("reason", "")

    b_val = before.get(metric)
    a_val = after.get(metric)

    if b_val is None or a_val is None:
        return {
            "name": name,
            "metric": metric,
            "status": "SKIP",
            "before": b_val,
            "after": a_val,
            "delta": None,
            "budget": _budget_str(rule),
            "reason": reason or f"metric '{metric}' not found",
        }

    b_val = float(b_val)
    a_val = float(a_val)
    delta = a_val - b_val

    status = "PASS"

    if "max_increase" in rule:
        threshold = float(rule["max_increase"])
        if delta > threshold:
            status = "FAIL"
    elif "max_decrease" in rule:
        threshold = float(rule["max_decrease"])
        if (b_val - a_val) > threshold:
            status = "FAIL"
    elif "max_increase_pct" in rule:
        threshold = float(rule["max_increase_pct"])
        if b_val != 0:
            pct = (delta / abs(b_val)) * 100
        else:
            pct = 0.0 if delta == 0 else 100.0
        if pct > threshold:
            status = "FAIL"

    return {
        "name": name,
        "metric": metric,
        "status": status,
        "before": b_val if b_val != int(b_val) else int(b_val),
        "after": a_val if a_val != int(a_val) else int(a_val),
        "delta": delta if delta != int(delta) else int(delta),
        "budget": _budget_str(rule),
        "reason": reason,
    }


def _budget_str(rule: dict) -> str:
    """Format the budget threshold for display."""
    if "max_increase" in rule:
        return f"max +{rule['max_increase']}"
    if "max_decrease" in rule:
        return f"max -{rule['max_decrease']}"
    if "max_increase_pct" in rule:
        return f"max +{rule['max_increase_pct']}%"
    return "?"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("budget")
@click.option("--init", "do_init", is_flag=True,
              help="Generate default .roam/budget.yaml.")
@click.option("--staged", is_flag=True, help="Analyse staged changes only.")
@click.option("--range", "commit_range", default=None,
              help="Git range, e.g. main..HEAD.")
@click.option("--explain", is_flag=True,
              help="Show reasoning per rule.")
@click.option("--config", "config_path", default=None,
              help="Custom budget config path.")
@click.pass_context
def budget(ctx, do_init, staged, commit_range, explain, config_path):
    """Check pending changes against architectural budgets.

    Evaluates metric deltas against budget rules defined in
    .roam/budget.yaml (or defaults). Exit code 1 if any budget
    is exceeded, making it suitable as a CI gate.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    # --init: create default config
    if do_init:
        _handle_init(root, json_mode)
        return

    ensure_index()

    # Load budget rules
    if config_path:
        cfg = Path(config_path)
    else:
        cfg = root / ".roam" / "budget.yaml"
        if not cfg.exists():
            cfg = root / ".roam" / "budget.yml"

    budgets = _load_budgets(cfg) if cfg.exists() else []
    if not budgets:
        budgets = list(_DEFAULT_BUDGETS)

    from roam.graph.diff import find_before_snapshot, metric_delta
    from roam.commands.metrics_history import collect_metrics

    with open_db(readonly=True) as conn:
        current = collect_metrics(conn)

        base_ref = None
        if commit_range and ".." in commit_range:
            base_ref = commit_range.split("..")[0]

        before_snap = find_before_snapshot(conn, root, base_ref)

    has_before = before_snap is not None

    # Evaluate rules
    results = []
    if has_before:
        for rule in budgets:
            results.append(_evaluate_rule(rule, before_snap, current))
    else:
        for rule in budgets:
            results.append({
                "name": rule.get("name", "unnamed"),
                "metric": rule.get("metric", ""),
                "status": "SKIP",
                "before": None,
                "after": None,
                "delta": None,
                "budget": _budget_str(rule),
                "reason": "no snapshot available",
            })

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    if failed > 0:
        verdict = f"{failed} of {len(results)} budgets exceeded"
    elif skipped == len(results):
        verdict = "all rules skipped (no snapshot available)"
    else:
        verdict = f"all {passed} budgets within limits"

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "budget",
            summary={
                "verdict": verdict,
                "rules_checked": len(results),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
            },
            rules=results,
            has_before_snapshot=has_before,
        )))
        if failed > 0:
            ctx.exit(1)
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    for r in results:
        status_tag = f"[{r['status']}]"
        name = r["name"]

        if r["status"] == "SKIP":
            click.echo(f"  {status_tag:6s} {name}: skipped ({r['reason'] or 'no data'})")
        else:
            before_val = r["before"]
            after_val = r["after"]
            delta_val = r["delta"]

            # Format delta
            if isinstance(delta_val, float) and delta_val != int(delta_val):
                delta_str = f"{delta_val:+.1f}"
            else:
                delta_str = f"{delta_val:+d}" if isinstance(delta_val, (int, float)) else str(delta_val)

            # Check if percentage
            pct_str = ""
            if "max_increase_pct" in _find_rule(budgets, r["name"]):
                if r["before"] and r["before"] != 0:
                    pct = (r["delta"] / abs(r["before"])) * 100
                    pct_str = f" ({pct:+.1f}%)"

            exceeded = "  << EXCEEDED" if r["status"] == "FAIL" else ""
            click.echo(
                f"  {status_tag:6s} {name}: {before_val} -> {after_val}  "
                f"(delta: {delta_str}{pct_str}, budget: {r['budget']}){exceeded}"
            )

            if explain and r.get("reason"):
                click.echo(f"         reason: {r['reason']}")

    if not has_before:
        click.echo()
        click.echo("Note: No snapshot found. Run 'roam snapshot' to create a baseline.")

    if failed > 0:
        ctx.exit(1)


def _find_rule(budgets: list[dict], name: str) -> dict:
    """Find a budget rule by name."""
    for b in budgets:
        if b.get("name") == name:
            return b
    return {}


def _handle_init(root: Path, json_mode: bool):
    """Create default .roam/budget.yaml."""
    roam_dir = root / ".roam"
    roam_dir.mkdir(parents=True, exist_ok=True)
    config_path = roam_dir / "budget.yaml"

    if config_path.exists():
        if json_mode:
            click.echo(to_json(json_envelope(
                "budget",
                summary={"verdict": "budget.yaml already exists", "rules_checked": 0,
                         "passed": 0, "failed": 0, "skipped": 0},
                rules=[],
                has_before_snapshot=False,
            )))
        else:
            click.echo(f"Budget config already exists: {config_path}")
            click.echo("Edit it manually or delete it and re-run --init.")
        return

    config_path.write_text(_DEFAULT_YAML, encoding="utf-8")
    if json_mode:
        click.echo(to_json(json_envelope(
            "budget",
            summary={"verdict": f"created {config_path}", "rules_checked": 0,
                     "passed": 0, "failed": 0, "skipped": 0},
            rules=[],
            has_before_snapshot=False,
        )))
    else:
        click.echo(f"Created {config_path} with default budgets.")
        click.echo("Edit thresholds to match your project's needs.")
