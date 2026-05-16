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

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because fitness outputs are invocation-scoped architectural
rule-evaluation aggregates (per-rule PASS / FAIL verdicts derived from
``.roam/fitness.yaml`` against the indexed graph) — not per-location
code violations. The detector-namespace siblings (``smells``, ``debt``,
``complexity``) DO ship SARIF for their per-location findings; fitness
sits a layer above as the policy-aggregate. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1224-audit memo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import WarningsOut, json_envelope, loc, to_json


# R22 — confidence classifier for fitness violations.
#
# Each fitness rule may carry an explicit severity:
#   high   — severity "error" (CI-blocking architecture violation)
#   medium — severity "warning" (regression / drift signal)
#   low    — severity "info" (advisory)
#
# Most rules don't currently set a severity field; we infer one from
# rule type + threshold direction so existing configs still produce
# reasonable confidence labels without requiring config changes:
#   - dependency-allow-false → error (an explicit forbidden edge fired)
#   - metric over a hard max (cycles > 0, complexity > 25) → error
#   - trend regressions → warning
#   - naming-convention drifts → info
def _fitness_classify(violation: dict) -> tuple[str, str]:
    """Map a fitness violation to a (confidence, reason) tuple."""
    severity = (violation.get("severity") or "").lower()
    vtype = (violation.get("type") or "").lower()
    if severity == "error":
        return "high", f"rule severity=error ({vtype} rule)"
    if severity == "warning":
        return "medium", f"rule severity=warning ({vtype} rule)"
    if severity == "info":
        return "low", f"rule severity=info ({vtype} rule)"
    # No explicit severity — infer from rule type. Hard architecture
    # gates default to high, drift / advisory rules to medium / low.
    if vtype == "dependency":
        return "high", "dependency-rule violation (no explicit severity; inferred)"
    if vtype == "metric":
        return "high", "metric-threshold violation (no explicit severity; inferred)"
    if vtype == "trend":
        return "medium", "trend regression (no explicit severity; inferred)"
    if vtype == "naming":
        return "low", "naming-convention violation (no explicit severity; inferred)"
    return "medium", "unknown rule type; defaulting to medium"


def _load_rules(
    project_root: Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load fitness rules from .roam/fitness.yaml.

    Walks ``.roam/fitness.yaml`` then ``.roam/fitness.yml`` and delegates
    file-read + parse + root-type check to
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings` (W1051,
    Phase 2 of the YAML-loader consolidation).

    W1051 (Pattern 2 — silent fallback, mirror of W706's
    ``_load_ignore_findings_file``): when *warnings_out* is supplied as
    a ``list[str]``, every silent-fallback path (file unreadable,
    malformed YAML, non-mapping root, missing ``rules`` key, non-list
    ``rules``, non-dict entries) appends an actionable warning naming
    the path, the failure shape, and the resolution. Pre-W1051 callers
    that don't supply ``warnings_out`` retain byte-identical
    silent-empty behaviour so the existing happy-path
    ``cmd_fitness.fitness`` envelope stays unchanged when
    ``.roam/fitness.yaml`` is well-formed.
    """
    config_path = project_root / ".roam" / "fitness.yaml"
    if not config_path.exists():
        # Try .yml extension
        config_path = project_root / ".roam" / "fitness.yml"
    if not config_path.exists():
        return []

    from roam.commands._yaml_loader import load_yaml_with_warnings

    path_str = str(config_path)
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        config_path,
        tiny_parser=_parse_simple_yaml_dict,
        config_label="fitness",
        warnings_out=warnings_out,
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # YAML / wrong root type / tiny-parser fallback). Propagate the
        # empty result without piling on a second "no `rules:` key"
        # warning that would just confuse the caller.
        return []
    assert isinstance(data, dict)
    if "rules" not in data:
        if warnings_out is not None:
            warnings_out.append(
                f"fitness: {path_str!r} has no `rules:` key. "
                f"Expected shape: `rules:` followed by a list of "
                f"`{{name, type, ...}}` entries."
            )
        return []
    rules = data.get("rules")
    if not isinstance(rules, list):
        if warnings_out is not None:
            warnings_out.append(
                f"fitness: {path_str!r} `rules` is {type(rules).__name__!r}, expected a list. Treating as empty rules."
            )
        return []
    out: list[dict] = []
    for idx, r in enumerate(rules):
        if not isinstance(r, dict):
            if warnings_out is not None:
                warnings_out.append(
                    f"fitness: {path_str!r} rules[{idx}] is "
                    f"{type(r).__name__!r}, expected a mapping with "
                    f"`name` / `type` / ... keys. Skipping entry."
                )
            continue
        out.append(r)
    return out


def _parse_simple_yaml_dict(text: str) -> dict:
    """Minimal YAML parser for fitness rules (no PyYAML dependency).

    Returns a ``{"rules": [...]}`` dict so the helper's root-type check
    + ``rules`` extraction in :func:`_load_rules` works on a uniform
    shape regardless of whether PyYAML parsed the file or this fallback
    did.

    W1058: rule-list parsing is now shared with ``cmd_budget`` via
    :func:`roam.commands._yaml_loader.parse_rule_list`.
    """
    from roam.commands._yaml_loader import parse_rule_list

    rules = parse_rule_list(text)
    return {"rules": rules} if rules else {}


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

    from roam.index.gitignore import matches_gitignore

    violations = []
    for r in rows:
        src_match = matches_gitignore(r["source_path"], from_pattern)
        tgt_match = matches_gitignore(r["target_path"], to_pattern)

        if src_match and tgt_match and not allow:
            violations.append(
                {
                    "rule": rule["name"],
                    "type": "dependency",
                    "message": f"{r['source_name']} -> {r['target_name']}",
                    "source": f"{r['source_path']}:{r['line'] or '?'}",
                    "target": r["target_path"],
                    "edge_kind": r["kind"],
                }
            )

    return violations


def _threshold_metric_violations(rule, metric: str, value, max_val, min_val) -> list[dict]:
    violations = []
    if max_val is not None and value > max_val:
        violations.append(_metric_violation(rule, metric, value, "max", max_val))
    if min_val is not None and value < min_val:
        violations.append(_metric_violation(rule, metric, value, "min", min_val))
    return violations


def _metric_violation(rule, metric: str, value, bound: str, threshold) -> dict:
    return {
        "rule": rule["name"],
        "type": "metric",
        "message": f"{metric}={value} ({bound}={threshold})",
        "metric": metric,
        "value": value,
        "threshold": threshold,
    }


def _symbol_graph_cycles(conn):
    from roam.graph.builder import build_symbol_graph
    from roam.graph.cycles import find_cycles

    graph = build_symbol_graph(conn)
    return graph, find_cycles(graph)


def _actionable_cycles(conn, cycles):
    """Return only architectural cycles, mirroring health's filter.

    Local-only and test-involved SCCs are excluded so fitness gates do not
    fail on intra-file refs (e.g. Vue ``<script setup>``) or duplicate-named
    test helpers — neither is a real cycle.
    """
    from roam.graph.cycles import format_cycles, mark_actionable_cycles

    formatted = format_cycles(cycles, conn) if cycles else []
    mark_actionable_cycles(formatted)
    actionable_ids = {tuple(scc) for scc, fc in zip(cycles, formatted) if fc.get("actionable")}
    return [scc for scc in cycles if tuple(scc) in actionable_ids]


def _check_cycles_metric(rule, conn) -> list[dict]:
    try:
        _, cycles = _symbol_graph_cycles(conn)
    except Exception:
        return []
    cycles = _actionable_cycles(conn, cycles)
    return _threshold_metric_violations(rule, "cycles", len(cycles), rule.get("max"), rule.get("min"))


def _check_health_score_metric(rule, conn) -> list[dict]:
    try:
        graph, cycles = _symbol_graph_cycles(conn)
    except Exception:
        return []
    total_syms = len(graph)
    if total_syms == 0:
        return []
    cycles = _actionable_cycles(conn, cycles)
    cycle_syms = sum(len(cycle) for cycle in cycles)
    cycle_pct = cycle_syms / total_syms * 100
    score = max(0, 100 - int(cycle_pct * 2))
    return _threshold_metric_violations(rule, "health_score", score, rule.get("max"), rule.get("min"))


def _check_cognitive_complexity_metric(rule, conn) -> list[dict]:
    threshold = rule.get("max") if rule.get("max") is not None else 999
    limit_clause = "" if rule.get("_all_violations") else "LIMIT 50"
    try:
        rows = conn.execute(
            f"""SELECT sm.cognitive_complexity, s.name, s.kind,
                      s.line_start, f.path
               FROM symbol_metrics sm
               JOIN symbols s ON sm.symbol_id = s.id
               JOIN files f ON s.file_id = f.id
               WHERE sm.cognitive_complexity > ?
               ORDER BY sm.cognitive_complexity DESC
               {limit_clause}""",
            (threshold,),
        ).fetchall()
    except Exception:
        return []

    return [
        {
            "rule": rule["name"],
            "type": "metric",
            "message": f"{row['name']} complexity={row['cognitive_complexity']:.0f} (max={threshold})",
            "source": loc(row["path"], row["line_start"]),
            "metric": "cognitive_complexity",
            "value": row["cognitive_complexity"],
            "threshold": threshold,
        }
        for row in rows
    ]


def _check_count_metric_rule(rule, conn) -> list[dict]:
    violations: list[dict] = []
    _check_count_metric(rule.get("metric", ""), rule, conn, violations)
    return violations


def _check_metric_rule(rule, conn) -> list[dict]:
    """Check a metric threshold rule."""
    metric = rule.get("metric", "")
    if metric == "cycles":
        return _check_cycles_metric(rule, conn)
    if metric == "health_score":
        return _check_health_score_metric(rule, conn)
    if metric == "cognitive_complexity":
        return _check_cognitive_complexity_metric(rule, conn)
    if metric in ("god_components", "bottlenecks", "dead_exports", "layer_violations"):
        return _check_count_metric_rule(rule, conn)
    return []


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
        rows = conn.execute("SELECT COUNT(*) FROM graph_metrics WHERE in_degree + out_degree > 20").fetchone()
        count = rows[0] if rows else 0
    elif metric == "bottlenecks":
        count = conn.execute("SELECT COUNT(*) FROM graph_metrics WHERE betweenness > 0.1").fetchone()[0]
    else:
        return

    if max_val is not None and count > max_val:
        violations.append(
            {
                "rule": rule["name"],
                "type": "metric",
                "message": f"{metric}={count} (max={max_val})",
                "metric": metric,
                "value": count,
                "threshold": max_val,
            }
        )
    if min_val is not None and count < min_val:
        violations.append(
            {
                "rule": rule["name"],
                "type": "metric",
                "message": f"{metric}={count} (min={min_val})",
                "metric": metric,
                "value": count,
                "threshold": min_val,
            }
        )


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
            violations.append(
                {
                    "rule": rule["name"],
                    "type": "naming",
                    "message": f"{name} does not match {pattern}",
                    "source": loc(r["path"], r["line_start"]),
                }
            )

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
        "health_score",
        "tangle_ratio",
        "avg_complexity",
        "brain_methods",
        "cycles",
        "god_components",
        "bottlenecks",
        "dead_exports",
        "layer_violations",
        "files",
        "symbols",
        "edges",
    }
    if metric not in _SNAPSHOT_METRICS:
        return [
            {
                "rule": rule.get("name", "unnamed"),
                "type": "trend",
                "message": f"Unknown snapshot metric '{metric}'. Valid: {', '.join(sorted(_SNAPSHOT_METRICS))}",
            }
        ]

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
        violations.append(
            {
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
            }
        )

    if max_increase is not None and delta > max_increase:
        violations.append(
            {
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
            }
        )

    return violations


_CHECKERS = {
    "dependency": _check_dependency_rule,
    "metric": _check_metric_rule,
    "naming": _check_naming_rule,
    "trend": _check_trend_rule,
}


def _default_baseline_path(root: Path) -> Path:
    return root / ".roam" / "fitness-baseline.json"


def _violation_key(violation: dict) -> str:
    metric = str(violation.get("metric", ""))
    if metric == "cycles":
        return "|".join(
            str(part)
            for part in (
                violation.get("rule", ""),
                violation.get("type", ""),
                metric,
            )
        )
    if metric == "cognitive_complexity":
        message = str(violation.get("message", ""))
        symbol_name = message.split(" complexity=", 1)[0]
        source_path = str(violation.get("source", "")).split(":", 1)[0]
        return "|".join(
            str(part)
            for part in (
                violation.get("rule", ""),
                violation.get("type", ""),
                metric,
                source_path,
                symbol_name,
            )
        )
    parts = [
        violation.get("rule", ""),
        violation.get("type", ""),
        violation.get("metric", ""),
        violation.get("source", ""),
        violation.get("message", ""),
    ]
    return "|".join(str(part) for part in parts)


def _baseline_payload(rule_results: list[dict], violations: list[dict]) -> dict:
    keys = sorted({_violation_key(violation) for violation in violations})
    return {
        "schema": "roam-fitness-baseline-v1",
        "summary": {
            "rules": len(rule_results),
            "violations": len(violations),
        },
        "violation_keys": keys,
        "violations": violations,
    }


def _load_baseline(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise click.ClickException(f"Cannot read fitness baseline: {path}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid fitness baseline JSON: {path}") from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"Invalid fitness baseline shape: {path}")
    return data


def _baseline_keys(data: dict) -> set[str]:
    keys = data.get("violation_keys")
    if isinstance(keys, list):
        return {str(key) for key in keys}
    violations = data.get("violations", [])
    if isinstance(violations, list):
        return {_violation_key(violation) for violation in violations if isinstance(violation, dict)}
    return set()


def _baseline_delta(violations: list[dict], baseline: dict) -> dict:
    old_keys = _baseline_keys(baseline)
    current_keys = {_violation_key(violation) for violation in violations}
    new_violations = [violation for violation in violations if _violation_key(violation) not in old_keys]
    return {
        "baseline_violations": len(old_keys),
        "current_violations": len(current_keys),
        "new_violations": len(new_violations),
        "resolved_violations": len(old_keys - current_keys),
        "new_violation_items": new_violations,
    }


def _write_baseline(path: Path, rule_results: list[dict], violations: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_baseline_payload(rule_results, violations), indent=2), encoding="utf-8")


def _rules_for_baseline_mode(rules: list[dict]) -> list[dict]:
    out = []
    for rule in rules:
        copy = dict(rule)
        if copy.get("type") == "metric" and copy.get("metric") == "cognitive_complexity":
            copy["_all_violations"] = True
        out.append(copy)
    return out


def _emit_no_rules(json_mode: bool, *, warnings_out: list[str] | None = None) -> None:
    if json_mode:
        summary_payload: dict = {
            "rules_checked": 0,
            "passed": 0,
            "failed": 0,
            "total_violations": 0,
            "verdict": "no rules configured",
        }
        if warnings_out:
            summary_payload["warnings_out"] = list(warnings_out)
            summary_payload["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "fitness",
                    summary=summary_payload,
                    rules=[],
                    violations=[],
                )
            )
        )
        return
    click.echo("No fitness rules found. Create .roam/fitness.yaml or run:\n  roam fitness --init")
    if warnings_out:
        click.echo()
        for w in warnings_out:
            click.echo(f"WARNING: {w}")


def _filter_rules(rules: list[dict], rule_filter: str | None) -> list[dict]:
    if not rule_filter:
        return rules
    return [rule for rule in rules if rule_filter.lower() in rule.get("name", "").lower()]


def _run_fitness_rules(conn, rules: list[dict]) -> tuple[list[dict], list[dict]]:
    all_violations = []
    rule_results = []
    for rule in rules:
        checker = _CHECKERS.get(rule.get("type", ""))
        if checker is None:
            continue
        violations = checker(rule, conn)
        result_entry = _rule_result_entry(rule, violations)
        rule_results.append(result_entry)
        all_violations.extend(violations)
    return rule_results, all_violations


def _rule_result_entry(rule: dict, violations: list[dict]) -> dict:
    result = {
        "name": rule.get("name", "unnamed"),
        "type": rule.get("type", ""),
        "status": "PASS" if not violations else "FAIL",
        "violations": len(violations),
    }
    if reason := rule.get("reason", ""):
        result["reason"] = reason
    if link := rule.get("link", ""):
        result["link"] = link
    return result


def _rule_counts(rule_results: list[dict]) -> tuple[int, int]:
    passed = sum(1 for result in rule_results if result["status"] == "PASS")
    failed = sum(1 for result in rule_results if result["status"] == "FAIL")
    return passed, failed


def _baseline_compare(all_violations: list[dict], baseline_path: Path | None) -> tuple[dict | None, list[dict]]:
    if baseline_path is None:
        return None, []
    baseline_data = _load_baseline(baseline_path)
    baseline_info = _baseline_delta(all_violations, baseline_data)
    baseline_info["path"] = str(baseline_path)
    return baseline_info, baseline_info["new_violation_items"]


def _maybe_write_baseline(
    root: Path, write_baseline: bool, rule_results: list[dict], all_violations: list[dict]
) -> str | None:
    if not write_baseline:
        return None
    written_path = _default_baseline_path(root)
    _write_baseline(written_path, rule_results, all_violations)
    return str(written_path)


def _fitness_summary(
    rule_results, passed: int, failed: int, all_violations, baseline_info, written_baseline_path
) -> dict:
    # Mirror the text-mode verdict so JSON consumers don't have to
    # re-derive the bottom line — and so :func:`verdict_with_high_count`
    # has something to append the high-count suffix to.
    if failed == 0:
        verdict = f"all {passed} fitness rule(s) pass"
    elif passed == 0:
        verdict = f"all {failed} fitness rule(s) fail ({len(all_violations)} violation(s))"
    else:
        verdict = f"{failed} of {passed + failed} fitness rule(s) fail ({len(all_violations)} violation(s))"
    summary = {
        "verdict": verdict,
        "rules_checked": len(rule_results),
        "passed": passed,
        "failed": failed,
        "total_violations": len(all_violations),
    }
    if baseline_info:
        summary["baseline"] = {k: v for k, v in baseline_info.items() if k != "new_violation_items"}
    if written_baseline_path:
        summary["baseline_written"] = written_baseline_path
    return summary


def _json_violations(violations: list[dict]) -> list[dict]:
    return [{key: value for key, value in violation.items()} for violation in violations[:100]]


def _emit_fitness_json(summary, rule_results, all_violations, new_violations) -> None:
    # R22: wrap each violation in {value, confidence, reason}.
    # Consumers that previously read violations[i]["rule"] must now
    # read violations[i]["value"]["rule"] plus
    # violations[i]["confidence"] / violations[i]["reason"].
    violation_values = _json_violations(all_violations)
    new_violation_values = _json_violations(new_violations)
    violation_triples = wrap_findings(violation_values, classifier=_fitness_classify)
    new_violation_triples = wrap_findings(new_violation_values, classifier=_fitness_classify)
    distribution = confidence_distribution(violation_triples)
    enriched_summary = dict(summary)
    enriched_summary["findings_confidence_distribution"] = distribution
    if "verdict" in enriched_summary:
        enriched_summary["verdict"] = verdict_with_high_count(enriched_summary["verdict"], distribution)
    click.echo(
        to_json(
            json_envelope(
                "fitness",
                summary=enriched_summary,
                rules=rule_results,
                violations=violation_triples,
                new_violations=new_violation_triples,
            )
        )
    )


def _emit_rule_line(rule_result: dict, explain: bool) -> None:
    icon = "PASS" if rule_result["status"] == "PASS" else "FAIL"
    detail = f" ({rule_result['violations']} violations)" if rule_result["violations"] else ""
    reason = rule_result.get("reason", "")
    link = rule_result.get("link", "")
    line = f"  [{icon}] {rule_result['name']}{detail}"
    if rule_result["status"] == "FAIL" and reason:
        line += f" -- Reason: {reason}"
    if rule_result["status"] == "FAIL" and link:
        line += f" (see: {link})"
    click.echo(line)
    if explain and (reason or link):
        if reason:
            click.echo(f"    Reason: {reason}")
        if link:
            click.echo(f"    Link:   {link}")


def _emit_violations_text(violations: list[dict], heading: str = "Violations") -> None:
    if not violations:
        return
    click.echo(f"\n{heading} ({len(violations)}):\n")
    for violation in violations[:30]:
        src = violation.get("source", "")
        click.echo(f"  {violation['rule']}: {violation['message']}")
        if src:
            click.echo(f"    at {src}")
    if len(violations) > 30:
        click.echo(f"\n  ... and {len(violations) - 30} more")


def _emit_baseline_delta_text(baseline_info: dict | None, new_violations: list[dict]) -> None:
    if not baseline_info:
        return
    click.echo(
        "\nBaseline delta: "
        f"{baseline_info['new_violations']} new, "
        f"{baseline_info['resolved_violations']} resolved "
        f"(baseline {baseline_info['baseline_violations']}, "
        f"current {baseline_info['current_violations']})"
    )
    _emit_violations_text(new_violations, heading="New violations")


def _emit_fitness_text(
    rule_results,
    all_violations,
    baseline_info,
    new_violations,
    written_baseline_path,
    passed,
    failed,
    explain,
    *,
    warnings_out: list[str] | None = None,
) -> None:
    # v12.12.6 — verdict-first output. Without this line a user
    # scanning the top of the output saw "Fitness check: 3 rules"
    # and had to count the [FAIL] / [PASS] markers themselves to
    # know the bottom line. Mirrors the convention every other
    # command in the surface follows.
    if failed == 0:
        verdict = f"all {passed} fitness rule(s) pass"
    elif passed == 0:
        verdict = f"all {failed} fitness rule(s) fail ({len(all_violations)} violation(s))"
    else:
        verdict = f"{failed} of {passed + failed} fitness rule(s) fail ({len(all_violations)} violation(s))"
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Fitness check: {len(rule_results)} rules\n")
    for rule_result in rule_results:
        _emit_rule_line(rule_result, explain)
    _emit_violations_text(all_violations)
    _emit_baseline_delta_text(baseline_info, new_violations)
    if written_baseline_path:
        click.echo(f"\nBaseline written: {written_baseline_path}")
    click.echo(f"\n{passed} passed, {failed} failed")
    if warnings_out:
        click.echo()
        for w in warnings_out:
            click.echo(f"WARNING: {w}")


def _finish_fitness(write_baseline: bool, baseline_info: dict | None, failed: int) -> None:
    if write_baseline:
        return
    if baseline_info is not None:
        if baseline_info["new_violations"] > 0:
            raise SystemExit(1)
        return
    if failed > 0:
        raise SystemExit(1)


# ── CLI command ──────────────────────────────────────────────────────


@roam_capability(
    name="fitness",
    category="health",
    summary="Run architectural fitness functions from .roam/fitness.yaml",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("fitness")
@click.option("--init", "do_init", is_flag=True, help="Create a starter fitness.yaml")
@click.option("--rule", "rule_filter", default=None, help="Run only rules matching this name")
@click.option("--explain", is_flag=True, help="Show full reason for each rule")
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Compare against a saved baseline; exit non-zero only for new violations.",
)
@click.option(
    "--write-baseline",
    is_flag=True,
    help="Write current violations to .roam/fitness-baseline.json and exit zero.",
)
@click.pass_context
def fitness(ctx, do_init, rule_filter, explain, baseline_path, write_baseline):
    """Run architectural fitness functions from .roam/fitness.yaml.

    Checks dependency constraints, metric thresholds, and naming rules.
    Returns exit code 1 if any rule is violated (for CI integration).
    Use --init to create a starter configuration.

    Unlike ``preflight`` (which includes fitness rules as one of 6 signal
    dimensions in a compound check), this command provides the full fitness
    interface: per-rule output, ``--init`` scaffold, ``--rule`` filter,
    ``--explain`` annotations, baseline/delta mode, and trend regression guards.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    if do_init:
        _init_config(root)
        return

    ensure_index()
    _fitness_warnings: list[str] = []
    rules = _load_rules(root, warnings_out=_fitness_warnings)

    if not rules:
        _emit_no_rules(json_mode, warnings_out=_fitness_warnings)
        return

    rules = _filter_rules(rules, rule_filter)
    if not rules:
        click.echo(f"No rules matching '{rule_filter}'.")
        return
    if baseline_path is not None or write_baseline:
        rules = _rules_for_baseline_mode(rules)

    with open_db(readonly=True) as conn:
        rule_results, all_violations = _run_fitness_rules(conn, rules)

    passed, failed = _rule_counts(rule_results)
    baseline_info, new_violations = _baseline_compare(all_violations, baseline_path)
    written_baseline_path = _maybe_write_baseline(root, write_baseline, rule_results, all_violations)
    summary = _fitness_summary(rule_results, passed, failed, all_violations, baseline_info, written_baseline_path)
    if _fitness_warnings:
        # W1051: surface loader warnings (malformed rules entries that
        # were skipped) so the agent doesn't see a green verdict that
        # silently dropped half its rules.
        summary["warnings_out"] = list(_fitness_warnings)
        summary["partial_success"] = True

    if json_mode:
        _emit_fitness_json(summary, rule_results, all_violations, new_violations)
    else:
        _emit_fitness_text(
            rule_results,
            all_violations,
            baseline_info,
            new_violations,
            written_baseline_path,
            passed,
            failed,
            explain,
            warnings_out=_fitness_warnings,
        )

    _finish_fitness(write_baseline, baseline_info, failed)


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
  # Run `roam trends --save` periodically to build history.
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
