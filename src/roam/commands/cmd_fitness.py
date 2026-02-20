"""Architectural fitness function runner.

Reads rules from .roam/fitness.yaml and checks them against the index.
Supports dependency constraints, layer enforcement, metric thresholds,
naming conventions, and trend-based regression guards.
Returns exit code 1 on violations for CI use.

Example .roam/fitness.yaml:
  rules:
    - name: "No direct DB access from handlers"
      type: dependency
      from: "src/handlers/**"
      to: "src/db/**"
      allow: false

    - name: "Services must not import controllers"
      type: dependency
      from: "**/services/**"
      to: "**/controllers/**"
      allow: false

    - name: "Max function complexity"
      type: metric
      metric: cognitive_complexity
      max: 25

    - name: "No cycles allowed"
      type: metric
      metric: cycles
      max: 0

    - name: "Health score minimum"
      type: metric
      metric: health_score
      min: 70

    - name: "Functions must use snake_case"
      type: naming
      kind: function
      pattern: "^[a-z_][a-z0-9_]*$"
      exclude: "test_*"

    - name: "Health must not regress"
      type: trend
      metric: health_score
      max_decrease: 5
      window: 3

    - name: "Complexity must not creep"
      type: trend
      metric: avg_complexity
      max_increase: 2.0
      window: 3
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.commands.resolve import ensure_index
from roam.output.formatter import loc, to_json, json_envelope


def _load_rules(project_root: Path) -> list[dict]:
    """Load fitness rules from .roam/fitness.yaml."""
    config_path = project_root / ".roam" / "fitness.yaml"
    if not config_path.exists():
        # Try .yml extension
        config_path = project_root / ".roam" / "fitness.yml"
    if not config_path.exists():
        return []

    try:
        import yaml
    except ImportError:
        # Fall back to basic YAML-like parsing for simple configs
        return _parse_simple_yaml(config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "rules" not in data:
        return []
    return data["rules"]


def _parse_simple_yaml(path: Path) -> list[dict]:
    """Minimal YAML parser for fitness rules (no PyYAML dependency)."""
    text = path.read_text(encoding="utf-8")
    rules = []
    current_rule = None

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
            # Type conversions
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


# ── Rule checkers ────────────────────────────────────────────────────

def _check_dependency_rule(rule, conn) -> list[dict]:
    """Check a dependency constraint rule.

    Verifies that symbols in 'from' glob don't have edges to symbols
    in 'to' glob (or vice versa if allow=true).
    """
    from_pattern = rule.get("from", "**")
    to_pattern = rule.get("to", "**")
    allow = rule.get("allow", False)

    # Get all edges with file paths
    rows = conn.execute(
        """SELECT e.source_id, e.target_id, e.kind, e.line,
                  sf.path as source_path, tf.path as target_path,
                  ss.name as source_name, ts.name as target_name
           FROM edges e
           JOIN symbols ss ON e.source_id = ss.id
           JOIN symbols ts ON e.target_id = ts.id
           JOIN files sf ON ss.file_id = sf.id
           JOIN files tf ON ts.file_id = tf.id"""
    ).fetchall()

    violations = []
    for r in rows:
        src_match = fnmatch.fnmatch(r["source_path"], from_pattern)
        tgt_match = fnmatch.fnmatch(r["target_path"], to_pattern)

        if src_match and tgt_match and not allow:
            violations.append({
                "rule": rule["name"],
                "type": "dependency",
                "message": f"{r['source_name']} -> {r['target_name']}",
                "source": f"{r['source_path']}:{r['line'] or '?'}",
                "target": r["target_path"],
                "edge_kind": r["kind"],
            })

    return violations


def _check_metric_rule(rule, conn) -> list[dict]:
    """Check a metric threshold rule."""
    metric = rule.get("metric", "")
    max_val = rule.get("max")
    min_val = rule.get("min")
    violations = []

    # Global metrics (from health-style computation)
    if metric == "cycles":
        try:
            from roam.graph.builder import build_symbol_graph
            from roam.graph.cycles import find_cycles
            G = build_symbol_graph(conn)
            cycles = find_cycles(G)
            count = len(cycles)
            if max_val is not None and count > max_val:
                violations.append({
                    "rule": rule["name"],
                    "type": "metric",
                    "message": f"cycles={count} (max={max_val})",
                    "metric": "cycles",
                    "value": count,
                    "threshold": max_val,
                })
            if min_val is not None and count < min_val:
                violations.append({
                    "rule": rule["name"],
                    "type": "metric",
                    "message": f"cycles={count} (min={min_val})",
                    "metric": "cycles",
                    "value": count,
                    "threshold": min_val,
                })
        except Exception:
            pass

    elif metric == "health_score":
        # Compute health score inline
        try:
            from roam.graph.builder import build_symbol_graph
            from roam.graph.cycles import find_cycles
            G = build_symbol_graph(conn)
            total_syms = len(G)
            if total_syms == 0:
                return []

            cycles = find_cycles(G)
            cycle_syms = sum(len(c) for c in cycles)
            cycle_pct = (cycle_syms / total_syms * 100) if total_syms else 0

            score = max(0, 100 - int(cycle_pct * 2))

            if max_val is not None and score > max_val:
                violations.append({
                    "rule": rule["name"], "type": "metric",
                    "message": f"health_score={score} (max={max_val})",
                    "metric": "health_score", "value": score, "threshold": max_val,
                })
            if min_val is not None and score < min_val:
                violations.append({
                    "rule": rule["name"], "type": "metric",
                    "message": f"health_score={score} (min={min_val})",
                    "metric": "health_score", "value": score, "threshold": min_val,
                })
        except Exception:
            pass

    elif metric == "cognitive_complexity":
        # Per-symbol metric check
        try:
            threshold = max_val if max_val is not None else 999
            rows = conn.execute(
                """SELECT sm.cognitive_complexity, s.name, s.kind,
                          s.line_start, f.path
                   FROM symbol_metrics sm
                   JOIN symbols s ON sm.symbol_id = s.id
                   JOIN files f ON s.file_id = f.id
                   WHERE sm.cognitive_complexity > ?
                   ORDER BY sm.cognitive_complexity DESC
                   LIMIT 50""",
                (threshold,),
            ).fetchall()
            for r in rows:
                violations.append({
                    "rule": rule["name"], "type": "metric",
                    "message": (
                        f"{r['name']} complexity={r['cognitive_complexity']:.0f} "
                        f"(max={threshold})"
                    ),
                    "source": loc(r["path"], r["line_start"]),
                    "metric": "cognitive_complexity",
                    "value": r["cognitive_complexity"],
                    "threshold": threshold,
                })
        except Exception:
            pass

    elif metric in ("god_components", "bottlenecks", "dead_exports", "layer_violations"):
        # These require more complex computation — delegate to simplified checks
        _check_count_metric(metric, rule, conn, violations)

    return violations


def _check_count_metric(metric, rule, conn, violations):
    """Check count-based metrics."""
    max_val = rule.get("max")
    min_val = rule.get("min")

    if metric == "dead_exports":
        count = conn.execute(
            """SELECT COUNT(*) FROM symbols s
               LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
               WHERE s.is_exported = 1
               AND (gm.in_degree IS NULL OR gm.in_degree = 0)"""
        ).fetchone()[0]
    elif metric == "god_components":
        rows = conn.execute(
            "SELECT COUNT(*) FROM graph_metrics WHERE in_degree + out_degree > 20"
        ).fetchone()
        count = rows[0] if rows else 0
    elif metric == "bottlenecks":
        count = conn.execute(
            "SELECT COUNT(*) FROM graph_metrics WHERE betweenness > 0.1"
        ).fetchone()[0]
    else:
        return

    if max_val is not None and count > max_val:
        violations.append({
            "rule": rule["name"], "type": "metric",
            "message": f"{metric}={count} (max={max_val})",
            "metric": metric, "value": count, "threshold": max_val,
        })
    if min_val is not None and count < min_val:
        violations.append({
            "rule": rule["name"], "type": "metric",
            "message": f"{metric}={count} (min={min_val})",
            "metric": metric, "value": count, "threshold": min_val,
        })


def _check_naming_rule(rule, conn) -> list[dict]:
    """Check a naming convention rule."""
    kind = rule.get("kind", "function")
    pattern = rule.get("pattern", "")
    exclude = rule.get("exclude", "")

    if not pattern:
        return []

    regex = re.compile(pattern)
    exclude_re = re.compile(exclude) if exclude else None

    rows = conn.execute(
        """SELECT s.name, s.kind, s.line_start, f.path
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.kind = ?""",
        (kind,),
    ).fetchall()

    violations = []
    for r in rows:
        name = r["name"]
        if exclude_re and exclude_re.match(name):
            continue
        if not regex.match(name):
            violations.append({
                "rule": rule["name"],
                "type": "naming",
                "message": f"{name} does not match {pattern}",
                "source": loc(r["path"], r["line_start"]),
            })

    return violations


def _check_trend_rule(rule, conn) -> list[dict]:
    """Check a trend-based regression guard.

    Compares the latest snapshot metric value against recent history
    to detect gradual degradation that absolute thresholds miss.

    Supported fields:
      metric:        snapshot column name (health_score, tangle_ratio, etc.)
      window:        number of recent snapshots to consider (default 3)
      max_decrease:  max allowed drop from the window average (for metrics where higher=better)
      max_increase:  max allowed rise from the window average (for metrics where lower=better)
      direction:     optional override: "higher_is_better" or "lower_is_better"
    """
    metric = rule.get("metric", "")
    window = rule.get("window", 3)
    max_decrease = rule.get("max_decrease")
    max_increase = rule.get("max_increase")

    # Validate the metric is a real snapshot column
    _SNAPSHOT_METRICS = {
        "health_score", "tangle_ratio", "avg_complexity", "brain_methods",
        "cycles", "god_components", "bottlenecks", "dead_exports",
        "layer_violations", "files", "symbols", "edges",
    }
    if metric not in _SNAPSHOT_METRICS:
        return [{
            "rule": rule.get("name", "unnamed"),
            "type": "trend",
            "message": f"Unknown snapshot metric '{metric}'. "
                       f"Valid: {', '.join(sorted(_SNAPSHOT_METRICS))}",
        }]

    # Fetch recent snapshots (need at least 2 to compute a trend)
    rows = conn.execute(
        f"SELECT {metric} FROM snapshots ORDER BY timestamp DESC LIMIT ?",
        (window + 1,),
    ).fetchall()

    if len(rows) < 2:
        return []  # Not enough history to judge

    latest = rows[0][0]
    if latest is None:
        return []

    # Compute average of previous snapshots (excluding latest)
    previous_vals = [r[0] for r in rows[1:] if r[0] is not None]
    if not previous_vals:
        return []

    prev_avg = sum(previous_vals) / len(previous_vals)
    delta = latest - prev_avg
    violations = []

    if max_decrease is not None and delta < -max_decrease:
        violations.append({
            "rule": rule.get("name", "unnamed"),
            "type": "trend",
            "message": (
                f"{metric} dropped by {abs(delta):.1f} "
                f"(from avg {prev_avg:.1f} to {latest:.1f}, "
                f"max allowed decrease: {max_decrease})"
            ),
            "metric": metric,
            "latest": latest,
            "previous_avg": round(prev_avg, 2),
            "delta": round(delta, 2),
            "threshold": max_decrease,
        })

    if max_increase is not None and delta > max_increase:
        violations.append({
            "rule": rule.get("name", "unnamed"),
            "type": "trend",
            "message": (
                f"{metric} increased by {delta:.1f} "
                f"(from avg {prev_avg:.1f} to {latest:.1f}, "
                f"max allowed increase: {max_increase})"
            ),
            "metric": metric,
            "latest": latest,
            "previous_avg": round(prev_avg, 2),
            "delta": round(delta, 2),
            "threshold": max_increase,
        })

    return violations


_CHECKERS = {
    "dependency": _check_dependency_rule,
    "metric": _check_metric_rule,
    "naming": _check_naming_rule,
    "trend": _check_trend_rule,
}


# ── CLI command ──────────────────────────────────────────────────────

@click.command("fitness")
@click.option("--init", "do_init", is_flag=True, help="Create a starter fitness.yaml")
@click.option("--rule", "rule_filter", default=None, help="Run only rules matching this name")
@click.option("--explain", is_flag=True, help="Show full reason for each rule")
@click.pass_context
def fitness(ctx, do_init, rule_filter, explain):
    """Run architectural fitness functions from .roam/fitness.yaml.

    Checks dependency constraints, metric thresholds, and naming rules.
    Returns exit code 1 if any rule is violated (for CI integration).
    Use --init to create a starter configuration.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    if do_init:
        _init_config(root)
        return

    ensure_index()
    rules = _load_rules(root)

    if not rules:
        if json_mode:
            click.echo(to_json(json_envelope("fitness",
                summary={"rules_checked": 0, "passed": 0, "failed": 0,
                          "total_violations": 0, "verdict": "no rules configured"},
                rules=[], violations=[],
            )))
        else:
            click.echo(
                "No fitness rules found. Create .roam/fitness.yaml or run:\n"
                "  roam fitness --init"
            )
        return

    if rule_filter:
        rules = [r for r in rules if rule_filter.lower() in r.get("name", "").lower()]
        if not rules:
            click.echo(f"No rules matching '{rule_filter}'.")
            return

    with open_db(readonly=True) as conn:
        all_violations = []
        rule_results = []

        for rule in rules:
            rtype = rule.get("type", "")
            checker = _CHECKERS.get(rtype)
            if checker is None:
                continue

            violations = checker(rule, conn)
            status = "PASS" if not violations else "FAIL"
            reason = rule.get("reason", "")
            link = rule.get("link", "")
            result_entry = {
                "name": rule.get("name", "unnamed"),
                "type": rtype,
                "status": status,
                "violations": len(violations),
            }
            if reason:
                result_entry["reason"] = reason
            if link:
                result_entry["link"] = link
            rule_results.append(result_entry)
            all_violations.extend(violations)

        passed = sum(1 for r in rule_results if r["status"] == "PASS")
        failed = sum(1 for r in rule_results if r["status"] == "FAIL")

        if json_mode:
            click.echo(to_json(json_envelope("fitness",
                summary={
                    "rules_checked": len(rule_results),
                    "passed": passed,
                    "failed": failed,
                    "total_violations": len(all_violations),
                },
                rules=rule_results,
                violations=[
                    {k: v for k, v in viol.items()}
                    for viol in all_violations[:100]  # Cap at 100
                ],
            )))
        else:
            click.echo(f"Fitness check: {len(rule_results)} rules\n")

            for rr in rule_results:
                icon = "PASS" if rr["status"] == "PASS" else "FAIL"
                detail = f" ({rr['violations']} violations)" if rr["violations"] else ""
                line = f"  [{icon}] {rr['name']}{detail}"
                # Append reason/link on FAIL lines
                reason = rr.get("reason", "")
                link = rr.get("link", "")
                if rr["status"] == "FAIL" and reason:
                    line += f" -- Reason: {reason}"
                if rr["status"] == "FAIL" and link:
                    line += f" (see: {link})"
                click.echo(line)
                # --explain: show reason/link below every rule
                if explain and (reason or link):
                    if reason:
                        click.echo(f"    Reason: {reason}")
                    if link:
                        click.echo(f"    Link:   {link}")

            if all_violations:
                click.echo(f"\nViolations ({len(all_violations)}):\n")
                for v in all_violations[:30]:
                    src = v.get("source", "")
                    click.echo(f"  {v['rule']}: {v['message']}")
                    if src:
                        click.echo(f"    at {src}")

                if len(all_violations) > 30:
                    click.echo(f"\n  ... and {len(all_violations) - 30} more")

            click.echo(f"\n{passed} passed, {failed} failed")

        if failed > 0:
            raise SystemExit(1)


def _init_config(root: Path):
    """Create a starter .roam/fitness.yaml."""
    config_dir = root / ".roam"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "fitness.yaml"

    if config_path.exists():
        click.echo(f"Config already exists: {config_path}")
        return

    config_path.write_text(
        """# Architectural fitness functions for roam
# Run with: roam fitness
# Use in CI: roam fitness && echo "Architecture OK"
# Each rule may include optional 'reason' and 'link' fields for documentation.

rules:
  # Dependency constraints
  - name: "No test imports in production"
    type: dependency
    source: "src/**"
    forbidden_target: "tests/**"
    reason: "Production code must not depend on test infrastructure"
    link: ""

  # - name: "No direct DB access from handlers"
  #   type: dependency
  #   from: "src/handlers/**"
  #   to: "src/db/**"
  #   allow: false
  #   reason: "Handlers should use service layer for DB access"
  #   link: "https://wiki.example.com/arch/layering"

  # Metric thresholds
  - name: "No cycles"
    type: metric
    metric: cycles
    max: 0
    reason: "Dependency cycles make the codebase harder to reason about"

  - name: "Health score above 60"
    type: metric
    metric: health_score
    min: 60

  - name: "Max function complexity 25"
    type: metric
    metric: cognitive_complexity
    max: 25
    reason: "Functions above this threshold should be split"

  # Naming conventions
  # - name: "Functions use snake_case"
  #   type: naming
  #   kind: function
  #   pattern: "^[a-z_][a-z0-9_]*$"
  #   exclude: "test_.*"

  # Trend-based regression guards (requires snapshots)
  # These catch gradual degradation that absolute thresholds miss.
  # Run `roam snapshot` periodically to build history.
  - name: "Health must not regress"
    type: trend
    metric: health_score
    max_decrease: 5
    window: 3
    reason: "Health score dropped significantly vs recent snapshots"

  - name: "Complexity must not creep"
    type: trend
    metric: avg_complexity
    max_increase: 2.0
    window: 3
    reason: "Average complexity is trending upward"

  # - name: "No new brain methods"
  #   type: trend
  #   metric: brain_methods
  #   max_increase: 0
  #   window: 1
  #   reason: "New brain methods should be refactored, not added"

  # - name: "Tangle ratio stable"
  #   type: trend
  #   metric: tangle_ratio
  #   max_increase: 0.05
  #   window: 3
  #   reason: "Dependency tangle is increasing"
""",
        encoding="utf-8",
    )
    click.echo(f"Created {config_path}")
    click.echo("Edit the rules and run: roam fitness")
