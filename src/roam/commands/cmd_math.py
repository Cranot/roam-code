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
    "--list-detectors",
    "list_detectors",
    is_flag=True,
    default=False,
    help=(
        "A3 — print decorated detectors with metadata "
        "(task, languages, confidence-basis, query-cost, version) and exit."
    ),
)
@click.option(
    "--only",
    "only_detectors",
    multiple=True,
    default=(),
    help=(
        "A3 — restrict the scan to these decorated detectors (repeatable). "
        "Names match `roam math --list-detectors` output."
    ),
)
@click.option(
    "--exclude",
    "exclude_detectors",
    multiple=True,
    default=(),
    help=("A3 — skip these decorated detectors (repeatable). Ignored if `--only` is set with the same name."),
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
    only_detectors,
    exclude_detectors,
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

    \b
    Examples:
      roam algo
      roam algo --task linear-search
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
        # A3 — enumerate the decorator-registered detectors. Emit JSON when
        # --json was set so CI / agents can consume the metadata without
        # parsing the text columns.
        from roam.catalog.detectors import list_registered_detectors

        entries = sorted(list_registered_detectors(), key=lambda e: (e["task_id"], e["name"]))
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "algo",
                        summary={
                            "verdict": f"{len(entries)} decorated detectors",
                            "detector_count": len(entries),
                        },
                        detectors=entries,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {len(entries)} decorated detectors")
        if not entries:
            return
        click.echo()
        click.echo(f"  {'name':<38s} {'task':<28s} {'confidence':<16s} {'cost':<8s} {'version':<8s} languages")
        for e in entries:
            langs = ",".join(e["languages"]) or "any"
            click.echo(
                f"  {e['name']:<38s} {e['task_id']:<28s} {e['confidence_basis']:<16s} "
                f"{e['query_cost']:<8s} {e['version']:<8s} {langs}"
            )
        return

    # validate --task against the catalog; on typo, show
    # the closest matches by edit distance instead of running 49 detectors
    # silently to find zero results.
    if task_filter:
        from roam.catalog.tasks import CATALOG

        if task_filter not in CATALOG:
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
            close = difflib.get_close_matches(task_filter, list(CATALOG.keys()), n=3, cutoff=0.4)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            click.echo(
                f"NOTE: --task '{task_filter}' is not a known task id."
                f" Run `roam math --json` then look at distinct `task_id` values." + hint,
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
        from roam.catalog.detectors import _DETECTOR_REGISTRY
        from roam.output.structured_unknowns import (
            structured_unknown_filter_many,
            to_summary_payload_many,
        )

        _filter_warnings: list[str] = []
        only_unknown = detector_meta.get("only_unknown") or []
        exclude_unknown = detector_meta.get("exclude_unknown") or []
        _registry_keys = set(_DETECTOR_REGISTRY.keys())

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
                "Run `roam math --list-detectors` to see registered names."
                + _only_frag["verdict_suffix"]
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
                "Run `roam math --list-detectors` to see registered names."
                + _exclude_frag["verdict_suffix"]
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
                _math_summary.update(
                    to_summary_payload_many(_only_frag, include_known=False)
                )
            if _exclude_frag is not None:
                # When BOTH --only and --exclude have unknowns, the second
                # splice overwrites the first ``state`` + ``partial_success``
                # (already True from the only-branch). ``did_you_mean`` is
                # similarly overwritten — we re-merge it below to retain
                # both unknown-source suggestion maps under disambiguated
                # keys so the consumer can tell which group a typo came from.
                _exclude_payload = to_summary_payload_many(
                    _exclude_frag, include_known=False
                )
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
