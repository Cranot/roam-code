"""Detect suboptimal algorithms and suggest better approaches (algo command)."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.catalog.fixes import get_fix
from roam.catalog.tasks import get_task, get_tip
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import abbrev_kind, json_envelope, to_json


def _resolve_scope_file_ids(conn, scope_paths) -> tuple[set[int], list[str]]:
    """Map ``--path`` values to indexed file ids.

    Each value matches an exact indexed path OR, as a directory, every
    indexed path under it (prefix match on the normalized form). Returns
    ``(file_ids, misses)`` — misses are values that matched nothing, so
    the caller can disclose them instead of silently scanning zero files.
    """
    rows = conn.execute("SELECT id, path FROM files").fetchall()
    ids: set[int] = set()
    misses: list[str] = []
    for raw in scope_paths:
        norm = str(raw).replace("\\", "/").lstrip("./").rstrip("/")
        if not norm:
            continue
        matched = False
        for fid, path in rows:
            if path == norm or path.startswith(norm + "/"):
                ids.add(int(fid))
                matched = True
        if not matched:
            misses.append(str(raw))
    return ids, misses


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


@roam_capability(
    name="algo",
    category="health",
    summary="Detect suboptimal algorithms and suggest better approaches",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--task", "task_filter", default=None, help="Filter by task ID (e.g. sorting, membership)")
@click.option(
    "--confidence",
    "confidence_filter",
    default=None,
    # W1005-followup-D: widened from 3-tier {high, medium, low} to the W547
    # canonical 7-tier so agents can pass any of {critical, error, high,
    # warning, medium, low, info} and have the floor compared via
    # ``severity_rank()`` from ``roam.output._severity``. Detectors emit only
    # {high, medium, low} (CVSS 3-tier) but the Choice accepts the full
    # canonical vocabulary so canonical-aware agents can pass any tier.
    # Semantic change: equality → floor (pre-fix kept findings with EXACTLY
    # that confidence; post-fix keeps findings AT OR ABOVE that rank).
    type=click.Choice(
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    help=(
        "Minimum confidence floor. Uses the canonical W547 7-tier ordering "
        "(critical > error == high > warning > medium > low > info). "
        "Detectors emit high/medium/low today; canonical aliases rank via "
        "the same severity_rank() comparator."
    ),
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
        "Bundled profiles include django, rails, nestjs, vue3-tanstack, "
        "laravel-multitenant. Unknown names "
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
    "--list-detectors",
    "list_detectors",
    is_flag=True,
    default=False,
    help=("Print runtime detectors with metadata (task, languages, confidence-basis, query-cost, version) and exit."),
)
@click.option(
    "--list-tasks",
    "list_tasks",
    is_flag=True,
    default=False,
    help="Print task ids, detector counts, categories, and best suggestions; then exit.",
)
@click.option(
    "--only",
    "only_detectors",
    multiple=True,
    default=(),
    help=(
        "Restrict the scan to these runtime detector names (repeatable). "
        "Names match `roam math --list-detectors` output."
    ),
)
@click.option(
    "--exclude",
    "exclude_detectors",
    multiple=True,
    default=(),
    help=("Skip these runtime detector names (repeatable). Ignored if `--only` is set with the same name."),
)
@click.option(
    "--since",
    "since_baseline",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "show only NEW findings vs the JSON envelope at this path. "
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
@click.option(
    "--path",
    "scope_paths",
    multiple=True,
    default=(),
    help=(
        "Restrict the scan to these files or directories (repeatable). "
        "Scoping collapses the whole-project sweep to the named files — "
        "the dominant cost is the per-file idiom scan, so a scoped run is "
        "sub-second. Directories match by prefix."
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
    list_detectors,
    list_tasks,
    only_detectors,
    exclude_detectors,
    since_baseline,
    include_tests,
    scope_paths,
):
    """Detect suboptimal algorithms and suggest better approaches.

    Scans indexed symbols for common algorithmic anti-patterns
    (manual sort, linear search, nested-loop lookup, busy wait, etc.)
    and recommends better alternatives from a universal catalog.

    Unlike ``smells`` (which flags style and structural patterns like god
    classes or long methods), this command focuses on algorithm choices and
    computational complexity.

    Primary name: algo. Alias: math (backward compat).

    \b
    Examples:
      roam algo
      roam algo --task search-sorted
      roam algo --confidence high --max-per-task 3
      roam algo --framework django

    See also ``smells`` (style + structural anti-patterns), ``n1``
    (implicit ORM N+1 patterns), and ``complexity`` (per-symbol
    cognitive metrics).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False

    if list_frameworks:
        from roam.catalog.detectors import list_framework_profiles

        for name in list_framework_profiles():
            click.echo(name)
        return

    if list_detectors:
        # A3/W1316 — enumerate the full runtime detector surface. The
        # decorator registry covers the 34 universal catalog detectors; the
        # runtime also loads Python-specific idiom detectors from
        # python_idioms.py. Emit JSON when --json was set so CI / agents can
        # consume the metadata without parsing text columns.
        from roam.catalog.detectors import list_detector_surface

        entries = sorted(list_detector_surface(), key=lambda e: (e["source"], e["task_id"], e["name"]))
        decorated_count = sum(1 for e in entries if e.get("source") == "catalog")
        python_idiom_count = sum(1 for e in entries if e.get("source") == "python_idioms")
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "algo",
                        summary={
                            "verdict": (
                                f"{len(entries)} registered detectors "
                                f"({decorated_count} decorated detectors, "
                                f"{python_idiom_count} Python idiom detectors)"
                            ),
                            "detector_count": len(entries),
                            "decorated_detector_count": decorated_count,
                            "python_idiom_detector_count": python_idiom_count,
                        },
                        detectors=entries,
                    )
                )
            )
            return
        click.echo(
            f"VERDICT: {len(entries)} registered detectors "
            f"({decorated_count} decorated detectors, {python_idiom_count} Python idiom detectors)"
        )
        if not entries:
            return
        click.echo()
        click.echo(
            f"  {'name':<38s} {'task':<34s} {'source':<14s} {'confidence':<16s} {'cost':<8s} {'version':<8s} languages"
        )
        for e in entries:
            langs = ",".join(e["languages"]) or "any"
            click.echo(
                f"  {e['name']:<38s} {e['task_id']:<34s} {e.get('source', 'catalog'):<14s} "
                f"{e['confidence_basis']:<16s} {e['query_cost']:<8s} {e['version']:<8s} {langs}"
            )
        return

    if list_tasks:
        from collections import Counter

        from roam.catalog.detectors import list_detector_surface
        from roam.catalog.tasks import CATALOG, best_way

        entries = list_detector_surface()
        counts = Counter(e["task_id"] for e in entries)
        sources_by_task: dict[str, set[str]] = defaultdict(set)
        languages_by_task: dict[str, set[str]] = defaultdict(set)
        for e in entries:
            task_id = e["task_id"]
            sources_by_task[task_id].add(e.get("source", "catalog"))
            languages_by_task[task_id].update(e.get("languages") or ("any",))

        tasks = []
        for task_id in sorted(counts):
            task = get_task(task_id)
            best = best_way(task_id) if task_id in CATALOG else None
            tasks.append(
                {
                    "task_id": task_id,
                    "name": task["name"] if task else task_id,
                    "category": task["category"] if task else "python",
                    "kind": task["kind"] if task else "idiom",
                    "detector_count": counts[task_id],
                    "sources": sorted(sources_by_task[task_id]),
                    "languages": sorted(languages_by_task[task_id]),
                    "best_way": best["id"] if best else "",
                    "best_name": best["name"] if best else "",
                    "best_time": best["time"] if best else "",
                }
            )

        catalog_task_count = sum(1 for t in tasks if t["task_id"] in CATALOG)
        python_task_count = len(tasks) - catalog_task_count
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "algo",
                        summary={
                            "verdict": (
                                f"{len(tasks)} algo task ids "
                                f"({catalog_task_count} catalog tasks, "
                                f"{python_task_count} Python idiom tasks)"
                            ),
                            "task_count": len(tasks),
                            "catalog_task_count": catalog_task_count,
                            "python_idiom_task_count": python_task_count,
                            "detector_count": len(entries),
                        },
                        tasks=tasks,
                    )
                )
            )
            return

        click.echo(
            f"VERDICT: {len(tasks)} algo task ids "
            f"({catalog_task_count} catalog tasks, {python_task_count} Python idiom tasks)"
        )
        if not tasks:
            return
        click.echo()
        click.echo(f"  {'task':<32s} {'category':<15s} {'kind':<10s} {'detectors':<9s} best")
        for t in tasks:
            best = t["best_name"] or "-"
            click.echo(f"  {t['task_id']:<32s} {t['category']:<15s} {t['kind']:<10s} {t['detector_count']:<9d} {best}")
        return

    # validate --task against the detector task surface; on typo, show
    # the closest matches by edit distance instead of running all detectors
    # silently to find zero results.
    if task_filter:
        from roam.catalog.detectors import list_detector_surface

        known_task_ids = sorted({entry["task_id"] for entry in list_detector_surface()})
        if task_filter not in known_task_ids:
            import difflib

            # W1083-followup: cutoff=0.4/n=3 intentional — looser than the
            # canonical 0.6/2 used by the ``structured_unknown_filter``
            # helper. The CATALOG task-id vocabulary is short kebab-case
            # domain terms (``loop-lookup``, ``useeffect-missing-deps``,
            # ``chained-collection-walk``, ``regex-in-loop``) with high
            # token variety and frequent permutation typos
            # (``lookup-loop``, ``walk-collection-chained``). Cutoff 0.6
            # rejects plausible candidates at this length; 0.4 keeps the
            # suggestion useful. n=3 surfaces multiple ranked candidates
            # because the closest match by ratio is often not the
            # intended one when names share a verb prefix. Leave the
            # knobs alone; do NOT migrate this site to the canonical
            # helper without first re-balancing the catalog vocabulary.
            close = difflib.get_close_matches(task_filter, known_task_ids, n=3, cutoff=0.4)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            click.echo(
                f"NOTE: --task '{task_filter}' is not a known task id."
                f" Run `roam algo --list-detectors` to see task ids." + hint,
                err=True,
            )

    ensure_index()

    from roam.catalog.detectors import autodetect_framework_profile, run_detectors

    # auto-detect when no --framework was passed.
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
        scope_file_ids = None
        scope_misses: list[str] = []
        if scope_paths:
            scope_file_ids, scope_misses = _resolve_scope_file_ids(conn, scope_paths)

        findings, detector_meta = run_detectors(
            conn,
            task_filter,
            confidence_filter,
            profile=profile,
            return_meta=True,
            framework=framework,
            include_tests=include_tests,
            only=only_detectors,
            exclude=exclude_detectors,
            scope_file_ids=scope_file_ids,
        )
        if scope_misses:
            click.echo(
                f"NOTE: --path matched no indexed files for: {', '.join(scope_misses)}",
                err=True,
            )

        if detector_meta.get("framework_unknown"):
            click.echo(
                f"NOTE: framework '{detector_meta['framework_unknown']}' is not a "
                "bundled profile. Defaults applied. Run `roam math --list-frameworks` "
                "to see options.",
                err=True,
            )

        # W1057 (Pattern 1D + Pattern 2): surface unknown --only/--exclude
        # names so a typo doesn't silently filter the run to zero detectors.
        # Mirrors the framework_unknown discipline above. Collected for the
        # JSON envelope's warnings_out / partial_success folding below.
        # W1064: append difflib closest-match suggestions per unknown name —
        # mirrors the `--task` precedent above (line 253). Cutoff 0.6 catches
        # plausible typos ("typo_fixx" → "typo_fix") without spamming
        # unrelated names.
        # W1083-followup-3: delegate to ``structured_unknown_filter_many``
        # for the partition + per-unknown ``did_you_mean`` suggestion +
        # warnings-list formatting. Mirrors the cmd_smells migration. The
        # callsite still feeds ``detector_meta["only_unknown"]`` /
        # ``exclude_unknown`` into the fragment via the pre-computed
        # ``known`` set (run_detectors already partitioned upstream — the
        # helper re-validates against the registry so the disclosure
        # message is consistent with the single-value helper family).
        from roam.catalog.detectors import list_detector_names
        from roam.output.structured_unknowns import (
            structured_unknown_filter_many,
            to_summary_payload_many,
        )

        _filter_warnings: list[str] = []
        only_unknown = detector_meta.get("only_unknown") or []
        exclude_unknown = detector_meta.get("exclude_unknown") or []
        _registry_keys = list_detector_names()

        # ``only_frag`` / ``exclude_frag`` are stamped onto the summary
        # below conditionally. We compute them up-front so the text-mode
        # NOTE strings stay byte-close to the pre-migration shape.
        _only_frag: dict | None = None
        _exclude_frag: dict | None = None
        if only_unknown:
            _only_frag = structured_unknown_filter_many(
                list(only_unknown),
                _registry_keys,
                field_name="only_detector",
                fact_anchor="detectors",
                state="unknown_only_detectors",
            )
            msg = (
                f"--only: unknown detector name(s): {', '.join(only_unknown)}. "
                "Run `roam math --list-detectors` to see registered names." + _only_frag["verdict_suffix"]
            )
            click.echo(f"NOTE: {msg}", err=True)
            _filter_warnings.append(msg)
        if exclude_unknown:
            _exclude_frag = structured_unknown_filter_many(
                list(exclude_unknown),
                _registry_keys,
                field_name="exclude_detector",
                fact_anchor="detectors",
                state="unknown_exclude_detectors",
            )
            msg = (
                f"--exclude: unknown detector name(s): {', '.join(exclude_unknown)}. "
                "Run `roam math --list-detectors` to see registered names." + _exclude_frag["verdict_suffix"]
            )
            click.echo(f"NOTE: {msg}", err=True)
            _filter_warnings.append(msg)

        # `--since baseline.json`: keep only findings whose
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

        # W706 (Pattern 2 — silent fallback): collect malformed-suppression-file
        # warnings while annotating findings. Empty list on the happy path;
        # non-empty means `.roamignore-findings` is unreadable / has a bad
        # shape / has malformed entries — surface via summary.warnings_out +
        # flip partial_success so the agent sees WHY suppressions are not
        # firing. Mirrors the cmd_alerts / cmd_pr_risk discipline.
        _suppression_warnings: list[str] = []
        findings, suppressed_count = annotate_with_suppression(
            findings,
            command="math",
            warnings_out=_suppression_warnings,
        )
        # Default: hide suppressed findings from text output, keep in JSON.
        # JSON consumers (CI, dashboards) need them visible to detect over-suppression.

        # Sort by impact score first, then confidence.
        # W596: canonical confidence-LEVEL rank — negate for high-first sort.
        findings.sort(
            key=lambda f: (
                -float(f.get("impact_score", 0.0) or 0.0),
                -confidence_level_rank(f["confidence"], fallback=-1),
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
        # when many findings cluster on one detector, hint
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
            # informative zero-state. When 0 findings,
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

        # W-dogfood: LAW 6 (verdict-works-alone) + Pattern 2 (silent
        # fallback). When --only/--exclude contained unknown detector
        # names the run was degraded — either to zero detectors or to a
        # silently-broader scan. The structured summary already carries
        # ``partial_success`` + ``state`` + ``only_unknown`` /
        # ``exclude_unknown``, but the verdict line previously read
        # ``No algorithmic issues detected — 0 detector(s) ran cleanly``
        # or ``30 algorithmic improvements found``, indistinguishable
        # from a clean run. Prepend an explicit warning so an agent
        # that consumes only the verdict (LAW 6) sees the degradation.
        only_unknown_names = detector_meta.get("only_unknown") or []
        exclude_unknown_names = detector_meta.get("exclude_unknown") or []
        if only_unknown_names or exclude_unknown_names:
            parts: list[str] = []
            if only_unknown_names:
                parts.append(f"unknown --only: {', '.join(only_unknown_names)}")
            if exclude_unknown_names:
                parts.append(f"unknown --exclude: {', '.join(exclude_unknown_names)}")
            verdict = f"WARNING ({'; '.join(parts)}) — {verdict}"

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
            # W706 (Pattern 2): build summary first so we can fold suppression-
            # loader warnings into partial_success / warnings_count before
            # emitting. Empty accumulator = byte-identical pre-W706 envelope.
            _math_summary: dict = {
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
                # surface T7 auto-detection in JSON summary
                # so CI / dashboards can record which framework profile was
                # active without having to re-derive it from the verdict text.
                "framework": detector_meta.get("framework"),
                "framework_autodetected": framework_autodetected,
                "framework_unknown": detector_meta.get("framework_unknown"),
                "max_per_task": effective_cap,
                "deferred_by_task_cap": deferred_by_cap,
                # --path scoping disclosure: which paths were requested and
                # how many indexed files they resolved to (None = unscoped
                # whole-project sweep, the historical default).
                "scoped_paths": list(scope_paths) or None,
                "scope_file_count": (len(scope_file_ids) if scope_file_ids is not None else None),
                "max_impact_score": max(
                    [float(f.get("impact_score", 0.0) or 0.0) for f in findings],
                    default=0.0,
                ),
                # top_tasks_by_count helps CI
                # dashboards / agents prioritise without iterating
                # every finding. Format: [{task_id, count}, ...].
                "top_tasks_by_count": [
                    {"task_id": tid, "count": n}
                    for tid, n in __import__("collections")
                    .Counter(f.get("task_id", "?") for f in findings)
                    .most_common(3)
                ],
            }
            # W706 (Pattern 2): surface suppression-loader silent-fallback
            # warnings. partial_success flips True iff any warning fired —
            # mirrors the cmd_alerts / cmd_pr_risk warnings_out discipline.
            # W1057 (Pattern 1D + Pattern 2): unknown --only/--exclude names
            # fold into the same warnings_out / partial_success discipline.
            _all_warnings = list(_suppression_warnings) + list(_filter_warnings)
            if _all_warnings:
                _math_summary["partial_success"] = True
                _math_summary["warnings_count"] = len(_all_warnings)
            # W1057: surface the unknown-name lists on the summary ONLY when
            # the caller supplied --only/--exclude (detector-meta keys are
            # conditional). Keeps the default-path envelope byte-identical.
            if "only_unknown" in detector_meta:
                _math_summary["only_unknown"] = list(detector_meta["only_unknown"])
            if "exclude_unknown" in detector_meta:
                _math_summary["exclude_unknown"] = list(detector_meta["exclude_unknown"])
            # W1083-followup-3: splice the multi-value-helper fragment per
            # --only / --exclude. ``include_known=False`` keeps the existing
            # envelope close to pre-migration shape (cmd_math did not surface
            # a ``known_detectors`` field; the only-/exclude_unknown keys
            # above already carry the actionable disclosure). The fragment
            # adds per-unknown ``did_you_mean`` suggestions and the new
            # ``state`` + ``unknown_<group>_detectors`` echo fields.
            if _only_frag is not None:
                _math_summary.update(to_summary_payload_many(_only_frag, include_known=False))
            if _exclude_frag is not None:
                # When BOTH --only and --exclude have unknowns, the second
                # splice overwrites the first ``state`` + ``partial_success``
                # (already True from the only-branch). ``did_you_mean`` is
                # similarly overwritten — we re-merge it below to retain
                # both unknown-source suggestion maps under disambiguated
                # keys so the consumer can tell which group a typo came from.
                _exclude_payload = to_summary_payload_many(_exclude_frag, include_known=False)
                # Re-key did_you_mean per source so both groups survive the
                # merge (one common Pattern-3a antidote: don't let the
                # second splice silently overwrite the first).
                if "did_you_mean" in _math_summary or "did_you_mean" in _exclude_payload:
                    only_dym = _math_summary.pop("did_you_mean", None)
                    excl_dym = _exclude_payload.pop("did_you_mean", None)
                    _math_summary.update(_exclude_payload)
                    if only_dym:
                        _math_summary["only_did_you_mean"] = only_dym
                    if excl_dym:
                        _math_summary["exclude_did_you_mean"] = excl_dym
                else:
                    _math_summary.update(_exclude_payload)
            elif _only_frag is not None and "did_you_mean" in _math_summary:
                # Single-source: keep the disambiguated key name for
                # forward-compat with a future --exclude typo run.
                _math_summary["only_did_you_mean"] = _math_summary.pop("did_you_mean")
            _envelope_extras: dict = {"findings": findings}
            if _all_warnings:
                # Carry the actionable warning strings on the envelope so the
                # agent can see WHY suppressions did not load (malformed YAML,
                # missing `rules:` key, non-dict entries, etc.) OR why --only /
                # --exclude filtered to zero (unknown detector name). Empty
                # list on the happy path.
                _envelope_extras["warnings_out"] = _all_warnings
            click.echo(
                to_json(
                    json_envelope(
                        "algo",
                        summary=_math_summary,
                        **_envelope_extras,
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
        # surface the active framework profile inline so
        # users see the cache allowlist that's been layered on. when
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
                # surface matched_patterns on a single line
                # so users see WHY this fired without --json. Quiet (line
                # omitted) when the detector didn't populate the field.
                patterns = (f.get("evidence") or {}).get("matched_patterns") or []
                if patterns:
                    click.echo(f"        Matched: {', '.join(str(p) for p in patterns[:4])}")

            click.echo()

        if truncated:
            click.echo(f"  (showing {limit} of more findings, use --limit to see more)")
