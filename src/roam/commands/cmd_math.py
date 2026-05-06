"""Detect suboptimal algorithms and suggest better approaches (algo command)."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.catalog.fixes import get_fix
from roam.catalog.tasks import get_task, get_tip
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, json_envelope, to_json


def _apply_task_cap(findings: list[dict], limit: int, max_per_task: int) -> tuple[list[dict], int]:
    """Apply a first-page per-task cap, then backfill to preserve limit."""
    if limit <= 0:
        return [], 0
    if max_per_task <= 0:
        return findings[:limit], 0

    selected: list[dict] = []
    deferred: list[dict] = []
    counts: dict[str, int] = {}
    deferred_count = 0

    for finding in findings:
        task_id = str(finding.get("task_id") or "")
        seen = counts.get(task_id, 0)
        if seen < max_per_task:
            selected.append(finding)
            counts[task_id] = seen + 1
        else:
            deferred.append(finding)
            deferred_count += 1

    if len(selected) >= limit:
        return selected[:limit], deferred_count

    selected.extend(deferred[: max(0, limit - len(selected))])
    return selected, deferred_count


@click.command()
@click.option("--task", "task_filter", default=None, help="Filter by task ID (e.g. sorting, membership)")
@click.option(
    "--confidence",
    "confidence_filter",
    default=None,
    type=click.Choice(["high", "medium", "low"], case_sensitive=False),
    help="Filter by confidence level",
)
@click.option(
    "--profile",
    "profile",
    default="balanced",
    type=click.Choice(["balanced", "strict", "aggressive"], case_sensitive=False),
    help="Precision profile (strict reduces false positives; aggressive surfaces more candidates)",
)
@click.option(
    "--top",
    "--limit",
    "-n",
    "limit",
    default=30,
    type=int,
    help="Max findings to show (alias: --limit, -n)",
)
@click.option(
    "--max-per-task",
    default=5,
    type=click.IntRange(min=0),
    help="Diversity cap for first page (0 disables, default 5 before backfill).",
)
@click.pass_context
def math_cmd(ctx, task_filter, confidence_filter, profile, limit, max_per_task):
    """Detect suboptimal algorithms and suggest better approaches.

    Scans indexed symbols for common algorithmic anti-patterns
    (manual sort, linear search, nested-loop lookup, busy wait, etc.)
    and recommends better alternatives from a universal catalog.

    Unlike ``smells`` (which flags style and structural patterns like god
    classes or long methods), this command focuses on algorithm choices and
    computational complexity.

    Primary name: algo. Alias: math (backward compat).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    ensure_index()

    from roam.catalog.detectors import run_detectors

    with open_db(readonly=True) as conn:
        findings, detector_meta = run_detectors(
            conn,
            task_filter,
            confidence_filter,
            profile=profile,
            return_meta=True,
        )

        # Build symbol_id -> language mapping for language-aware tips
        sym_ids = [f["symbol_id"] for f in findings if f.get("symbol_id")]
        lang_map: dict[int, str] = {}
        if sym_ids:
            from roam.db.connection import batched_in

            rows = batched_in(
                conn,
                "SELECT s.id, f.language FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
                sym_ids,
            )
            for r in rows:
                if r["language"]:
                    lang_map[r["id"]] = r["language"]

        # Enrich findings with language-aware tips
        for f in findings:
            lang = lang_map.get(f.get("symbol_id"), "")
            f["language"] = lang
            f["tip"] = get_tip(f["task_id"], f["suggested_way"], lang)
            if not f.get("fix"):
                f["fix"] = get_fix(f["task_id"], lang)

        # M7 — suppression: annotate findings (don't drop). Three sources
        # checked in order: per-finding suppressions.json, .roamignore-findings
        # globs, inline `roam: ignore-math[task-id]` annotations.
        from roam.commands.finding_suppress import annotate_with_suppression, filter_suppressed

        findings, suppressed_count = annotate_with_suppression(findings, command="math")
        # Default: hide suppressed findings from text output, keep in JSON.
        # JSON consumers (CI, dashboards) need them visible to detect over-suppression.

        # Sort by impact score first, then confidence.
        _conf_order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(
            key=lambda f: (
                -float(f.get("impact_score", 0.0) or 0.0),
                _conf_order.get(f["confidence"], 9),
            )
        )

        # Apply limit + optional first-page diversity cap.
        effective_cap = max_per_task if not task_filter else 0
        truncated = len(findings) > limit
        findings, deferred_by_cap = _apply_task_cap(findings, limit, effective_cap)

        # Group by task category
        by_category = defaultdict(list)
        for f in findings:
            task = get_task(f["task_id"])
            cat = task["category"] if task else "other"
            by_category[cat].append(f)

        by_confidence = defaultdict(int)
        for f in findings:
            by_confidence[f["confidence"]] += 1

        total = len(findings)
        conf_parts = []
        for c in ("high", "medium", "low"):
            if by_confidence.get(c):
                conf_parts.append(f"{by_confidence[c]} {c}")
        conf_str = ", ".join(conf_parts) if conf_parts else "none"

        # M14: honest verdict — distinguish raw counts from verified status.
        # Suppressed findings stay in `findings` so they appear in JSON; we
        # subtract from `total` for the verdict line (unsuppressed candidates).
        unsuppressed_total = total - suppressed_count
        if total == 0:
            verdict = "No algorithmic issues detected"
        elif suppressed_count > 0:
            verdict = (
                f"{unsuppressed_total} unsuppressed candidate{'s' if unsuppressed_total != 1 else ''} "
                f"surfaced ({conf_str}); {suppressed_count} suppressed via "
                ".roamignore-findings / inline annotation / suppressions.json"
            )
        else:
            verdict = f"{total} algorithmic improvement{'s' if total != 1 else ''} found ({conf_str})"

        if sarif_mode:
            from roam.output.sarif import algo_to_sarif, write_sarif

            sarif = algo_to_sarif(
                findings,
                detector_meta.get("detector_metadata", {}),
            )
            click.echo(write_sarif(sarif))
            return

        # --- JSON output ---
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "algo",
                        summary={
                            "verdict": verdict,
                            "total": total,
                            "unsuppressed_total": unsuppressed_total,
                            "suppressed_count": suppressed_count,
                            "by_category": dict((k, len(v)) for k, v in by_category.items()),
                            "by_confidence": dict(by_confidence),
                            "truncated": truncated,
                            "detectors_executed": detector_meta.get("detectors_executed", 0),
                            "detectors_failed": detector_meta.get("detectors_failed", 0),
                            "failed_detectors": detector_meta.get("failed_detectors", []),
                            "detector_metadata": detector_meta.get("detector_metadata", {}),
                            "profile": detector_meta.get("profile", profile),
                            "profile_filtered": detector_meta.get("profile_filtered", 0),
                            "max_per_task": effective_cap,
                            "deferred_by_task_cap": deferred_by_cap,
                            "max_impact_score": max(
                                [float(f.get("impact_score", 0.0) or 0.0) for f in findings],
                                default=0.0,
                            ),
                        },
                        findings=findings,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        if effective_cap > 0 and not task_filter:
            click.echo(f"Ordering: highest impact first (diversity cap {effective_cap}/task on first page)")
        else:
            click.echo("Ordering: highest impact first")
        click.echo(
            f"Profile: {detector_meta.get('profile', profile)} "
            f"(filtered {detector_meta.get('profile_filtered', 0)} low-signal findings)"
        )
        if detector_meta.get("detectors_failed"):
            click.echo(f"NOTE: {detector_meta['detectors_failed']} detector(s) failed (use --json for details).")
        if not findings:
            return

        # M7 — text output hides suppressed findings (JSON keeps them).
        # Use the unfiltered list for the JSON envelope above; here we filter.
        text_findings = filter_suppressed(findings)
        if not text_findings:
            click.echo()
            click.echo("(all findings suppressed; pass --json to see them)")
            return

        click.echo()

        # Group by task_id for display
        by_task = defaultdict(list)
        for f in text_findings:
            by_task[f["task_id"]].append(f)

        for task_id, task_findings in by_task.items():
            task = get_task(task_id)
            task_name = task["name"] if task else task_id
            click.echo(f"{task_name} ({len(task_findings)}):")

            for f in task_findings:
                kind_abbr = abbrev_kind(f["kind"])
                name = f["symbol_name"]
                location = f["location"]
                conf = f["confidence"]
                impact_score = float(f.get("impact_score", 0.0) or 0.0)

                # Get catalog info for display
                detected = None
                suggested = None
                if task:
                    for w in task["ways"]:
                        if w["id"] == f["detected_way"]:
                            detected = w
                        if w["id"] == f["suggested_way"]:
                            suggested = w

                click.echo(f"  {kind_abbr:<5s} {name:<40s} {location}  [{conf}, impact={impact_score:.1f}]")
                if detected:
                    click.echo(f"        Current: {detected['name']} -- {detected['time']}")
                if suggested:
                    click.echo(f"        Better:  {suggested['name']} -- {suggested['time']}")
                    tip_text = f.get("tip", "")
                    if tip_text:
                        click.echo(f"        Tip: {tip_text}")
                    fix_text = f.get("fix", "")
                    if fix_text:
                        click.echo(f"        Fix: {fix_text.splitlines()[0]}")

            click.echo()

        if truncated:
            click.echo(f"  (showing {limit} of more findings, use --limit to see more)")
