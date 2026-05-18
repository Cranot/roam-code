"""Enforce architectural trajectory via per-PR delta limits.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because budget outputs are invocation-scoped budget gate
verdicts — not per-code-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1197-audit memo.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import WarningsOut, json_envelope, to_json

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


def _load_budgets(
    config_path: Path | None,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load budget rules from a YAML file.

    Falls back to a simple line parser if PyYAML is not installed.

    W1019c (Pattern 2 — silent fallback, mirror of W706's
    ``_load_ignore_findings_file``): when *warnings_out* is supplied as a
    ``list[str]``, every silent-fallback path (file unreadable / OSError,
    malformed YAML, non-mapping root, missing ``budgets`` key, non-list
    ``budgets``, non-dict entries) appends an actionable warning naming
    the path, the failure shape, and the resolution. Pre-W1019c callers
    that don't supply ``warnings_out`` retain byte-identical silent-empty
    behaviour so existing happy-path consumers (cmd_budget.budget,
    cmd_attest._collect_budget_evidence) keep emitting byte-identical
    envelopes when ``.roam/budget.yaml`` is well-formed.

    W1019c also migrates the file-read + YAML parse + no-PyYAML fallback
    + root-type check to
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings` — the
    helper owns the I/O + parser-fallback shape; the per-callsite
    ``budgets``-extraction and per-entry validation stays here.

    W1030-followup-A: legacy single-value return preserved for the existing
    callers. Callers that need the on-disk state for envelope
    disambiguation use :func:`_load_budgets_with_status` instead.
    """
    budgets, _status = _load_budgets_with_status(config_path, warnings_out=warnings_out)
    return budgets


def _load_budgets_with_status(
    config_path: Path | None,
    *,
    warnings_out: WarningsOut = None,
) -> tuple[list[dict], str]:
    """W1030-followup-A: load budget rules and return ``(budgets, status)``.

    ``status`` is a closed-enum string drawn from
    :data:`roam.commands._yaml_loader.LOAD_STATUSES`
    (``"ok"`` / ``"missing"`` / ``"empty_file"`` / ``"empty_yaml"`` /
    ``"read_error"`` / ``"parse_error"`` / ``"wrong_root_type"`` /
    ``"schema_invalid"``). Lets the budget command envelope disambiguate
    "no budget.yaml configured yet" (``missing`` -> use defaults silently)
    from "budget.yaml exists but is empty" (``empty_file`` -> use defaults
    + flag the empty stub) from "budget.yaml is broken" (``parse_error`` /
    ``wrong_root_type`` -> partial_success, warnings already populated by
    the canonical loader).

    ``config_path is None`` returns ``([], "missing")`` -- the budget
    command short-circuits this branch when no config arg is supplied.
    """
    if config_path is None:
        return [], "missing"

    from roam.commands._yaml_loader import load_yaml_with_warnings

    path_str = str(config_path)
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data, status = load_yaml_with_warnings(
        config_path,
        tiny_parser=_parse_simple_yaml_dict,
        config_label="budget",
        warnings_out=warnings_out,
        return_status=True,
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return [], status
    if status in ("empty_file", "empty_yaml"):
        # W1030-followup-A: zero-byte / comments-only file is a distinct
        # on-disk state from "non-empty file missing the ``budgets:``
        # key" -- the user created a stub but did not write any rules.
        # Suppress the "no `budgets:` key" warning that the legacy
        # missing-key branch would emit so the empty-stub state surfaces
        # cleanly as ``config_state=empty_file`` (or ``empty_yaml``) on
        # the envelope, with no warning. Pattern 2 is preserved for the
        # malformed cases — ``parse_error`` / ``wrong_root_type`` still
        # emit the canonical loader's warning above.
        return [], status
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # YAML / wrong root type / tiny-parser fallback). Propagate the
        # empty result without piling on a second "no `budgets:` key"
        # warning that would just confuse the caller.
        return [], status
    # ``data`` is a Mapping when ``allow_list_root`` is left at False
    # (the default we want for budget.yaml). The helper's root-type check
    # guarantees this; the assert keeps the type checker happy on the
    # post-helper budgets-extraction logic.
    assert isinstance(data, dict)
    if "budgets" not in data:
        if warnings_out is not None:
            warnings_out.append(
                f"budget: {path_str!r} has no `budgets:` key. "
                f"Expected shape: `budgets:` followed by a list of "
                f"`{{name, metric, max_increase|max_decrease|max_increase_pct}}` entries."
            )
        return [], status
    # W1038 — shared "load → check type → warn-or-default" extractor.
    from roam.commands._yaml_loader import extract_typed

    budgets = extract_typed(
        data,
        "budgets",
        list,
        [],
        warnings_out=warnings_out,
        context=f"budget: {path_str!r}",
        expected_shape="a list",
    )
    out: list[dict] = []
    for idx, b in enumerate(budgets):
        if not isinstance(b, dict):
            if warnings_out is not None:
                warnings_out.append(
                    f"budget: {path_str!r} budgets[{idx}] is "
                    f"{type(b).__name__!r}, expected a mapping with "
                    f"`name` / `metric` / `max_*` keys. Skipping entry."
                )
            continue
        out.append(b)
    return out, status


def _parse_simple_yaml_dict(text: str) -> dict:
    """Minimal YAML parser for budget rules (no PyYAML dependency).

    Returns a ``{"budgets": [...]}`` dict so the helper's root-type
    check + ``budgets`` extraction in :func:`_load_budgets` works on a
    uniform shape regardless of whether PyYAML parsed the file or this
    fallback did.

    W1058: rule-list parsing is now shared with ``cmd_fitness`` via
    :func:`roam.commands._yaml_loader.parse_rule_list`.
    """
    from roam.commands._yaml_loader import parse_rule_list

    rules = parse_rule_list(text)
    return {"budgets": rules} if rules else {}


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


@roam_capability(
    name="budget",
    category="reports",
    summary="Check pending changes against architectural budgets",
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
@click.command("budget")
@click.option("--init", "do_init", is_flag=True, help="Generate default .roam/budget.yaml.")
@click.option("--staged", is_flag=True, help="Analyse staged changes only.")
@click.option("--range", "commit_range", default=None, help="Git range, e.g. main..HEAD.")
@click.option("--explain", is_flag=True, help="Show reasoning per rule.")
@click.option("--config", "config_path", default=None, help="Custom budget config path.")
@click.pass_context
def budget(ctx, do_init, staged, commit_range, explain, config_path):
    """Check pending changes against architectural budgets.

    Unlike ``debt`` (which ranks files by accumulated technical debt),
    this command enforces delta thresholds on architectural metrics as a
    CI gate.

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

    _budget_warnings: list[str] = []
    # W1030-followup-A: use the with-status variant so the on-disk state
    # ("missing" / "empty_file" / "ok" / ...) reaches the envelope as a
    # closed-enum field — agents reading the budget envelope can
    # disambiguate "no budget.yaml configured yet" from "budget.yaml is
    # broken / empty stub" without re-statting the file. ``cfg.exists()``
    # is False when the helper has not yet been run on this project; we
    # synthesize ``missing`` status for that path so the envelope field
    # stays uniformly populated regardless of which branch ran.
    if cfg.exists():
        budgets, config_state = _load_budgets_with_status(cfg, warnings_out=_budget_warnings)
    else:
        budgets, config_state = [], "missing"
    if not budgets:
        budgets = list(_DEFAULT_BUDGETS)

    from roam.commands.metrics_history import collect_metrics
    from roam.graph.diff import find_before_snapshot

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
            results.append(
                {
                    "name": rule.get("name", "unnamed"),
                    "metric": rule.get("metric", ""),
                    "status": "SKIP",
                    "before": None,
                    "after": None,
                    "delta": None,
                    "budget": _budget_str(rule),
                    "reason": "no snapshot available",
                }
            )

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
        summary_payload: dict = {
            "verdict": verdict,
            "rules_checked": len(results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }
        # W1030-followup-A: expose the on-disk state as a closed-enum
        # string so agents can disambiguate "no budget.yaml configured
        # yet" (defaults used silently) from "budget.yaml exists but is
        # empty" (defaults used AND the user probably meant to configure
        # something) from "budget.yaml is broken" (parse_error /
        # wrong_root_type — already accompanied by a warning in
        # ``warnings_out``).
        summary_payload["config_state"] = config_state
        if _budget_warnings:
            summary_payload["warnings_out"] = list(_budget_warnings)
            summary_payload["partial_success"] = True
        # W1030-followup-A: a degraded config_state flips partial_success
        # too — even when no warning fired (e.g. empty stub on disk),
        # agents must see that the user's intent did not materialize.
        elif config_state in ("parse_error", "wrong_root_type", "read_error", "schema_invalid"):
            summary_payload["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "budget",
                    summary=summary_payload,
                    rules=results,
                    has_before_snapshot=has_before,
                )
            )
        )
        if failed > 0:
            from roam.exit_codes import EXIT_GATE_FAILURE

            ctx.exit(EXIT_GATE_FAILURE)
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
        click.echo("Note: No snapshot found. Run 'roam trends --save' to create a baseline.")

    if failed > 0:
        from roam.exit_codes import EXIT_GATE_FAILURE

        ctx.exit(EXIT_GATE_FAILURE)


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
            click.echo(
                to_json(
                    json_envelope(
                        "budget",
                        summary={
                            "verdict": "budget.yaml already exists",
                            "rules_checked": 0,
                            "passed": 0,
                            "failed": 0,
                            "skipped": 0,
                        },
                        rules=[],
                        has_before_snapshot=False,
                    )
                )
            )
        else:
            click.echo(f"Budget config already exists: {config_path}")
            click.echo("Edit it manually or delete it and re-run --init.")
        return

    config_path.write_text(_DEFAULT_YAML, encoding="utf-8")
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "budget",
                    summary={
                        "verdict": f"created {config_path}",
                        "rules_checked": 0,
                        "passed": 0,
                        "failed": 0,
                        "skipped": 0,
                    },
                    rules=[],
                    has_before_snapshot=False,
                )
            )
        )
    else:
        click.echo(f"Created {config_path} with default budgets.")
        click.echo("Edit thresholds to match your project's needs.")
