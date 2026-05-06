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
@click.option(
    "--framework",
    "framework",
    default=None,
    help=(
        "Layer a framework-specific cache allowlist on top of defaults. "
        "Bundled profiles: vue3-tanstack, laravel-multitenant. Unknown names "
        "are tolerated (defaults still apply). See `roam math --list-frameworks`."
    ),
)
@click.option(
    "--list-frameworks",
    "list_frameworks",
    is_flag=True,
    default=False,
    help="Print bundled framework profiles and exit.",
)
@click.option(
    "--since",
    "since_baseline",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "T5 — show only NEW findings vs the JSON envelope at this path. "
        "Pair with `roam --json math > .roam/math-baseline.json` to take a "
        "snapshot, then `roam math --since .roam/math-baseline.json` to see "
        "only what regressed since."
    ),
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help=(
        "X14 — include test files in the scan. Default excludes them because "
        "test fixtures intentionally use anti-patterns for assertion. Use "
        "this flag when you want to lint your test code too."
    ),
)
@click.pass_context
def math_cmd(
    ctx,
    task_filter,
    confidence_filter,
    profile,
    limit,
    max_per_task,
    framework,
    list_frameworks,
    since_baseline,
    include_tests,
):
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

    if list_frameworks:
        from roam.catalog.detectors import list_framework_profiles

        for name in list_framework_profiles():
            click.echo(name)
        return

    # redacted — validate --task against the catalog; on typo, show
    # the closest matches by edit distance instead of running 49 detectors
    # silently to find zero results.
    if task_filter:
        from roam.catalog.tasks import CATALOG

        if task_filter not in CATALOG:
            import difflib

            close = difflib.get_close_matches(task_filter, list(CATALOG.keys()), n=3, cutoff=0.4)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            click.echo(
                f"NOTE: --task '{task_filter}' is not a known task id."
                f" Run `roam math --json` then look at distinct `task_id` values." + hint,
                err=True,
            )

    ensure_index()

    from roam.catalog.detectors import autodetect_framework_profile, run_detectors

    # redacted — auto-detect when no --framework was passed.
    # Sniffs package.json / composer.json for known stack signals and
    # silently activates a profile. Surfaces the choice in the verdict
    # text so it's not invisible.
    framework_autodetected = False
    if not framework:
        auto = autodetect_framework_profile()
        if auto:
            framework = auto
            framework_autodetected = True

    with open_db(readonly=True) as conn:
        findings, detector_meta = run_detectors(
            conn,
            task_filter,
            confidence_filter,
            profile=profile,
            return_meta=True,
            framework=framework,
            include_tests=include_tests,
        )

        if detector_meta.get("framework_unknown"):
            click.echo(
                f"NOTE: framework '{detector_meta['framework_unknown']}' is not a "
                "bundled profile. Defaults applied. Run `roam math --list-frameworks` "
                "to see options.",
                err=True,
            )

        # redacted — `--since baseline.json`: keep only findings whose
        # (task_id, location, symbol_name) tuple isn't in the baseline. The
        # tuple is the same shape used by `_finding()` for finding_id derivation,
        # so two runs over the same indexed code produce stable matches.
        baseline_count = 0
        since_kept = 0
        if since_baseline:
            import json as _json

            try:
                baseline = _json.loads(open(since_baseline, encoding="utf-8").read())
                baseline_findings = baseline.get("findings", []) or []
            except (OSError, _json.JSONDecodeError) as exc:
                click.echo(f"NOTE: --since baseline could not be loaded: {exc}", err=True)
                baseline_findings = []
            baseline_keys = {(f.get("task_id"), f.get("location"), f.get("symbol_name")) for f in baseline_findings}
            baseline_count = len(baseline_findings)
            before_n = len(findings)
            findings = [
                f for f in findings if (f.get("task_id"), f.get("location"), f.get("symbol_name")) not in baseline_keys
            ]
            since_kept = len(findings)
            click.echo(
                f"--since: baseline had {baseline_count} finding(s); "
                f"current run has {before_n}; showing {since_kept} NEW since baseline.",
                err=True,
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
        # redacted — when many findings cluster on one detector, hint
        # at the dominant category in the verdict line so users skim it before
        # the detail. e.g. "12 algorithmic improvements (10 high; mostly: io-in-loop)"
        category_hint = ""
        if total >= 5:
            from collections import Counter

            cat_counts = Counter(f.get("task_id", "?") for f in findings)
            top_cat, top_n = cat_counts.most_common(1)[0]
            if top_n >= max(3, total // 2):
                category_hint = f"; mostly: {top_cat}"
        if total == 0:
            # redacted — informative zero-state. When 0 findings,
            # tell the user (a) which profile filter was active, (b) how
            # many detectors ran, (c) what to try next.
            profile_note = ""
            if profile != "balanced":
                profile_note = f" (profile={profile} may be too strict; try --profile balanced)"
            verdict = (
                f"No algorithmic issues detected{profile_note} — "
                f"{detector_meta.get('detectors_executed', 0)} detector(s) ran cleanly. "
                f"Try `roam math --profile aggressive` for more candidates "
                f"or `roam debt --top 10` for refactoring ROI hotspots."
            )
        elif suppressed_count > 0:
            verdict = (
                f"{unsuppressed_total} unsuppressed candidate{'s' if unsuppressed_total != 1 else ''} "
                f"surfaced ({conf_str}{category_hint}); {suppressed_count} suppressed via "
                ".roamignore-findings / inline annotation / suppressions.json"
            )
        else:
            verdict = f"{total} algorithmic improvement{'s' if total != 1 else ''} found ({conf_str}{category_hint})"

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
                            # redacted — surface T7 auto-detection in JSON summary
                            # so CI / dashboards can record which framework profile was
                            # active without having to re-derive it from the verdict text.
                            "framework": detector_meta.get("framework"),
                            "framework_autodetected": framework_autodetected,
                            "framework_unknown": detector_meta.get("framework_unknown"),
                            "max_per_task": effective_cap,
                            "deferred_by_task_cap": deferred_by_cap,
                            "max_impact_score": max(
                                [float(f.get("impact_score", 0.0) or 0.0) for f in findings],
                                default=0.0,
                            ),
                            # redacted — top_tasks_by_count helps CI
                            # dashboards / agents prioritise without iterating
                            # every finding. Format: [{task_id, count}, ...].
                            "top_tasks_by_count": [
                                {"task_id": tid, "count": n}
                                for tid, n in __import__("collections")
                                .Counter(f.get("task_id", "?") for f in findings)
                                .most_common(3)
                            ],
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
        profile_line = (
            f"Profile: {detector_meta.get('profile', profile)} "
            f"(filtered {detector_meta.get('profile_filtered', 0)} low-signal findings)"
        )
        # redacted — surface the active framework profile inline so
        # users see the cache allowlist that's been layered on. T7 — when
        # we auto-detected, mark it so the user knows the profile wasn't
        # silently chosen.
        if detector_meta.get("framework"):
            tag = "framework (auto)" if framework_autodetected else "framework"
            profile_line += f"  |  {tag}: {detector_meta['framework']}"
        click.echo(profile_line)
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
                # redacted — surface matched_patterns on a single line
                # so users see WHY this fired without --json. Quiet (line
                # omitted) when the detector didn't populate the field.
                patterns = (f.get("evidence") or {}).get("matched_patterns") or []
                if patterns:
                    click.echo(f"        Matched: {', '.join(str(p) for p in patterns[:4])}")

            click.echo()

        if truncated:
            click.echo(f"  (showing {limit} of more findings, use --limit to see more)")
