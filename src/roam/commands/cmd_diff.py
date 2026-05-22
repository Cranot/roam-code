"""Show blast radius of uncommitted changes.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because diff outputs are invocation-scoped change impact
summaries — not per-location violations. Editor consumers should use
the JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# W641-followup-E — canonical risk-LEVEL projection from blast-radius
# ---------------------------------------------------------------------------
#
# cmd_diff emits volumetric counts (changed_files, affected_symbols,
# affected_files) but no native severity vocabulary. The canonical W631
# risk-LEVEL bucket is derived from the same blast-radius thresholds
# cmd_impact uses (W641-followup-A) so an agent comparing
# ``roam diff`` against ``roam impact <sym>`` sees a consistent canonical
# floor. Thresholds:
#
#   affected_symbols >= 50  OR  affected_files >= 20  -> "high"
#   affected_symbols >= 10  OR  affected_files >= 5   -> "medium"
#   affected_symbols > 0                              -> "low"
#   affected_symbols == 0                             -> "low"
#
# Conservative-on-critical: cmd_diff structurally aligns with cmd_impact
# (per-symbol blast-radius count) and cmd_critique (per-region severity
# aggregation) — both floor at ``high`` because their underlying signal
# is single-axis (count or severity tier) without the multi-factor
# composite-score evidence cmd_attest's _collect_risk provides (which DOES
# legitimately reach ``critical``). The W531 CI-safety lesson: a
# threshold wobble MUST NOT promote a finding into a CI-gating rank.
# cmd_diff floors at ``high``; ``critical`` is reserved for the multi-
# factor composite-score commands.


def _diff_risk_level(
    affected_symbols: int,
    affected_files: int,
    *,
    warnings_out: list[str] | None = None,
) -> str:
    """Project blast-radius metrics onto the canonical W631 risk-LEVEL set.

    Returns a string in :data:`roam.output.risk.RISK_LEVELS`
    (``critical``/``high``/``medium``/``low``). cmd_diff saturates at
    ``high`` (W641-followup-A/B discipline — single-axis blast-radius
    signal does not justify escalating to ``critical``).

    Safe-floor: any combination producing ``affected_symbols == 0`` AND
    ``affected_files == 0`` collapses to ``low`` (W531 CI-safety: a
    clean diff MUST NOT promote into a gating rank).

    Unknown / negative inputs accumulate a marker on *warnings_out*
    (when provided) under ``diff_unknown_severity:<value>`` so Pattern-2
    silent-fallback stays loud — mirrors the W918 alerts / W989 pr-risk
    / W641-followup-B critique / W641-followup-D attest discipline.
    """
    # Guard: negative / non-int counts should never reach the projection,
    # but stay loud if they do — record a marker + safe-floor.
    if not isinstance(affected_symbols, int) or not isinstance(affected_files, int):
        if warnings_out is not None:
            warnings_out.append(f"diff_unknown_severity:non_int_counts({affected_symbols!r},{affected_files!r})")
        return "low"
    if affected_symbols < 0 or affected_files < 0:
        if warnings_out is not None:
            warnings_out.append(f"diff_unknown_severity:negative({affected_symbols},{affected_files})")
        return "low"
    if affected_symbols >= 50 or affected_files >= 20:
        return "high"
    if affected_symbols >= 10 or affected_files >= 5:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Affected tests helper
# ---------------------------------------------------------------------------


def _collect_affected_tests(conn, sym_by_file):
    """Gather affected tests for all symbols across changed files.

    Returns (test_entries, pytest_cmd) where test_entries is the list from
    ``_gather_affected_tests`` and pytest_cmd is a runnable pytest string.
    """
    from roam.commands.cmd_affected_tests import _gather_affected_tests

    all_sym_ids = set()
    all_file_paths = set()
    for path, syms in sym_by_file.items():
        all_file_paths.add(path)
        all_sym_ids.update(s["id"] for s in syms)

    if not all_sym_ids:
        return [], ""

    results = _gather_affected_tests(conn, all_sym_ids, all_file_paths)

    # Build deduplicated ordered file list for pytest command
    seen_order = []
    seen_set = set()
    for r in results:
        if r["file"] not in seen_set:
            seen_set.add(r["file"])
            seen_order.append(r["file"])

    pytest_cmd = "pytest " + " ".join(seen_order) if seen_order else ""
    return results, pytest_cmd


# ---------------------------------------------------------------------------
# Coupling warnings helper
# ---------------------------------------------------------------------------


def _collect_coupling_warnings(conn, file_map, min_cochanges=3):
    """Find temporally-coupled files that are NOT in the changeset.

    Returns a list of dicts with keys: path, cochanges, strength, partner_of.
    """
    change_fids = set(file_map.values())

    # Build lookup tables
    id_to_path = {}
    file_commits = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        id_to_path[f["id"]] = f["path"]
    for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    warnings = {}  # keyed by path, keep highest cochange

    for path, fid in file_map.items():
        rows = conn.execute(
            """SELECT file_id_a, file_id_b, cochange_count
               FROM git_cochange
               WHERE (file_id_a = ? OR file_id_b = ?)
               AND cochange_count >= ?""",
            (fid, fid, min_cochanges),
        ).fetchall()

        for r in rows:
            partner_fid = r["file_id_b"] if r["file_id_a"] == fid else r["file_id_a"]
            if partner_fid in change_fids:
                continue  # already in the diff, no warning needed

            cochanges = r["cochange_count"]
            avg = (file_commits.get(fid, 1) + file_commits.get(partner_fid, 1)) / 2
            strength = cochanges / avg if avg > 0 else 0

            partner_path = id_to_path.get(partner_fid, f"file_id={partner_fid}")
            if partner_path not in warnings or cochanges > warnings[partner_path]["cochanges"]:
                warnings[partner_path] = {
                    "path": partner_path,
                    "cochanges": cochanges,
                    "strength": round(strength, 2),
                    "partner_of": path,
                }

    return sorted(warnings.values(), key=lambda x: -x["cochanges"])


# ---------------------------------------------------------------------------
# Fitness check helper (scoped to changed files)
# ---------------------------------------------------------------------------


def _collect_fitness_violations(conn, file_map, root):
    """Run fitness rules scoped to the changed files.

    For dependency rules: only report violations where the source is in the
    changed files (i.e. edges introduced/present in the diff).
    For metric rules on per-symbol metrics: only report symbols in changed files.
    For global metrics and naming rules: run normally (not file-scoped).

    Returns (rule_results, violations) lists.
    """
    from roam.commands.cmd_fitness import _load_rules

    rules = _load_rules(root)
    if not rules:
        return [], []

    changed_paths = set(file_map.keys())
    changed_fids = set(file_map.values())

    all_violations = []
    rule_results = []

    for rule in rules:
        rtype = rule.get("type", "")
        violations = []

        if rtype == "dependency":
            violations = _check_dep_rule_scoped(rule, conn, changed_paths)
        elif rtype == "metric":
            violations = _check_metric_rule_scoped(rule, conn, changed_fids)
        elif rtype == "naming":
            violations = _check_naming_rule_scoped(rule, conn, changed_fids)

        status = "PASS" if not violations else "FAIL"
        rule_results.append(
            {
                "name": rule.get("name", "unnamed"),
                "type": rtype,
                "status": status,
                "violations": len(violations),
            }
        )
        all_violations.extend(violations)

    return rule_results, all_violations


def _check_dep_rule_scoped(rule, conn, changed_paths):
    """Check dependency rule, only reporting edges whose source is in changed files."""
    from_pattern = rule.get("from", "**")
    to_pattern = rule.get("to", "**")
    allow = rule.get("allow", False)

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
        # Only flag edges originating from changed files
        if r["source_path"] not in changed_paths:
            continue
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


def _check_metric_rule_scoped(rule, conn, changed_fids):
    """Check metric rules scoped to changed files where applicable."""
    from roam.output.formatter import loc

    metric = rule.get("metric", "")
    max_val = rule.get("max")
    violations = []

    if metric == "cognitive_complexity" and changed_fids:
        threshold = max_val if max_val is not None else 999
        ph = ",".join("?" for _ in changed_fids)
        rows = conn.execute(
            f"""SELECT sm.cognitive_complexity, s.name, s.kind,
                       s.line_start, f.path
                FROM symbol_metrics sm
                JOIN symbols s ON sm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE s.file_id IN ({ph})
                AND sm.cognitive_complexity > ?
                ORDER BY sm.cognitive_complexity DESC""",
            list(changed_fids) + [threshold],
        ).fetchall()
        for r in rows:
            violations.append(
                {
                    "rule": rule["name"],
                    "type": "metric",
                    "message": (f"{r['name']} complexity={r['cognitive_complexity']:.0f} (max={threshold})"),
                    "source": loc(r["path"], r["line_start"]),
                    "metric": "cognitive_complexity",
                    "value": r["cognitive_complexity"],
                    "threshold": threshold,
                }
            )
    elif metric in ("cycles", "health_score"):
        # Global metrics -- delegate to full checker
        from roam.commands.cmd_fitness import _check_metric_rule

        violations = _check_metric_rule(rule, conn)
    # Other count-based metrics run globally too
    elif metric in ("god_components", "bottlenecks", "dead_exports"):
        from roam.commands.cmd_fitness import _check_metric_rule

        violations = _check_metric_rule(rule, conn)

    return violations


def _check_naming_rule_scoped(rule, conn, changed_fids):
    """Check naming rules scoped to changed files."""
    import re

    from roam.output.formatter import loc

    kind = rule.get("kind", "function")
    pattern = rule.get("pattern", "")
    exclude = rule.get("exclude", "")

    if not pattern or not changed_fids:
        return []

    regex = re.compile(pattern)
    exclude_re = re.compile(exclude) if exclude else None

    ph = ",".join("?" for _ in changed_fids)
    rows = conn.execute(
        f"""SELECT s.name, s.kind, s.line_start, f.path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.kind = ? AND s.file_id IN ({ph})""",
        [kind] + list(changed_fids),
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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    category="review",
    summary="Show blast radius of uncommitted or ranged git changes.",
    inputs=["commit_range"],
    outputs=["affected_files", "verdict"],
    examples=[
        "roam diff",
        "roam diff --staged",
        "roam diff main..HEAD",
    ],
    tags=["review", "git"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("diff")
@click.argument("commit_range", required=False, default=None)
@click.option("--staged", is_flag=True, help="Analyze staged changes instead of unstaged")
@click.option(
    "--full",
    is_flag=True,
    help="Show all results without truncation and enable --tests --coupling --fitness",
)
@click.option("--tests", is_flag=True, help="Show affected test files")
@click.option("--coupling", is_flag=True, help="Warn about missing co-change partners")
@click.option("--fitness", is_flag=True, help="Check fitness rules against changed files")
@click.option(
    "--since-tag",
    is_flag=True,
    help="analyze commits since the most recent tag (auto-detected via git describe).",
)
@click.pass_context
def diff_cmd(ctx, commit_range, staged, full, tests, coupling, fitness, since_tag):
    """Show blast radius: what code is affected by your changes.

    Unlike ``pr-diff`` (which compares CI-level metrics before and after),
    this command shows the developer-facing blast radius of uncommitted or
    committed changes.

    Optionally pass a COMMIT_RANGE (e.g. HEAD~3..HEAD, abc123, main..feature)
    to analyze committed changes instead of uncommitted ones.

    Use --tests, --coupling, --fitness to add extra analysis sections,
    or --full to enable all three plus untruncated output.

    \b
    Examples:
      roam diff
      roam diff --staged                # only staged hunks
      roam diff HEAD~3..HEAD            # range
      roam diff --since-tag             # last release ➝ HEAD
      roam diff --full                  # tests + coupling + fitness

    The JSON envelope emits ``summary.risk_level_canonical`` +
    ``summary.risk_rank`` on the canonical W631 risk-LEVEL axis
    (W641-followup-E). The canonical bucket is projected from blast-
    radius thresholds (mirrors the cmd_impact W641-followup-A polarity)
    and saturates at ``high`` — ``critical`` is reserved for the
    multi-factor composite-score commands (cmd_attest). The canonical
    fields are emitted unconditionally so agents downstream can call
    ``risk_rank(summary["risk_level_canonical"]) >= 3`` to gate on
    high-or-worse blast radius without re-deriving the threshold table.

    See also ``critique`` (clones-not-edited gate on the same diff),
    ``pr-risk`` (PR-level risk score), and ``impact`` (per-symbol
    blast radius).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    root = find_project_root()

    # W607-Z -- substrate-CALL marker plumbing for cmd_diff. Mirrors the
    # canonical W607 template (latest landed: W607-Y cmd_critique). Each
    # substrate boundary inside the diff pipeline (changed-file discovery,
    # DB resolution, symbol-graph construction, affected-tests gather,
    # coupling-warnings + fitness-violations collectors, risk-LEVEL
    # projection) gets wrapped in ``_run_check`` so a raise surfaces a
    # structured ``diff_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607z_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # cmd_diff is the diff-INPUT pair complement to cmd_critique (W607-Y),
    # which consumes diff TEXT via stdin: cmd_diff consumes git REFS
    # through ``get_changed_files``. Wrapping that call surfaces the
    # SHARED-HELPER family's silent-empty fallback (returncode!=0 /
    # FileNotFoundError / TimeoutExpired -> []) at THIS call site even
    # before the root-cause helper fix lands. See W805-HHHH probe.
    #
    # The accumulator is intentionally DISTINCT from the existing
    # ``_diff_warnings_out`` bucket (W641-followup-E unknown-severity /
    # negative-count tracking) so the two axes don't entangle:
    # unknown-severity is a data-shape disclosure (a count couldn't be
    # mapped to a canonical risk-LEVEL bucket), while W607-Z is a
    # substrate-CALL disclosure (a helper raised before producing its
    # floor value). Both feed the same envelope ``warnings_out`` field on
    # emission via bucket-merge; ``partial_success`` flips when EITHER
    # bucket is non-empty -- consumers reading ``partial_success`` alone
    # need not distinguish the two flavours.
    _w607z_warnings_out: list[str] = []

    def _run_check(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-Z marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``diff_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607z_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607z_warnings_out.append(f"diff_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BP -- ADDITIVE aggregation-phase plumbing on top of the
    # W607-Z substrate-CALL markers. W607-Z already wrapped the substrate-
    # helper boundaries (get_changed_files / resolve_changed_to_db /
    # build_symbol_graph / collect_affected_tests / collect_coupling_warnings /
    # collect_fitness_violations / compute_risk_level); W607-BP extends
    # marker coverage to the AGGREGATION-PHASE boundaries that W607-Z left
    # unguarded:
    #
    #   - ``severity_classify``    -- per-affected-symbol severity
    #                                 classification (the inner
    #                                 ``_diff_risk_level`` walk; W607-Z
    #                                 wraps the CALL but the inner classify
    #                                 step has its own future-raise surface
    #                                 as the closed severity vocabulary
    #                                 evolves) -- mirror of cmd_critique
    #                                 W607-BL pattern with default=None
    #                                 driving the severity_classification
    #                                 "unknown" sentinel
    #   - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
    #                                 (normalize_risk_level + risk_rank)
    #                                 mirror of cmd_impact's W607-BB
    #                                 pattern
    #   - ``compute_verdict``      -- augmented_verdict text build with
    #                                 the canonical risk_level suffix
    #                                 (LAW 6 standalone-parse)
    #   - ``auto_log``             -- active-run ledger write (silent
    #                                 no-op if no run is active, but the
    #                                 underlying ``auto_log`` can still
    #                                 raise on HMAC chain misshape or
    #                                 filesystem failures)
    #   - ``serialize_envelope``   -- ``json_envelope("diff", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions)
    #
    # cmd_diff is the POST-EDIT signal SOURCE feeding into the critique
    # gate per ``roam diff | roam critique``. With W607-BP landed, the
    # agent-OS edit loop is W607-plumbed end-to-end on BOTH the
    # substrate-CALL layer (W607-R + W607-T + W607-S + W607-Y + W607-Z)
    # AND the aggregation-phase layer (W607-AW + W607-BB + W607-BH +
    # W607-BL + W607-BP). Each of the five edit-loop commands carries
    # dual-bucket plumbing with combined-warnings emission.
    #
    # Marker family ``diff_*`` -- same family as W607-Z (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope.
    _w607bp_warnings_out: list[str] = []

    def _run_check_bp(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-BP marker emission.

        Mirror of ``_run_check`` shape (same ``diff_<phase>_failed:``
        marker family) but writes into ``_w607bp_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bp_warnings_out.append(f"diff_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --since-tag auto-fills commit_range with <last-tag>..HEAD.
    if since_tag and not commit_range:
        import subprocess as _sub

        try:
            proc = _sub.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True,
                text=True,
                cwd=str(root),
                timeout=5,
                check=False,
            )
            if proc.returncode == 0:
                last_tag = proc.stdout.strip()
                if last_tag:
                    commit_range = f"{last_tag}..HEAD"
        except (OSError, _sub.SubprocessError):
            pass

    # --full implies all three extras
    if full:
        tests = True
        coupling = True
        fitness = True

    changed = (
        _run_check(
            "get_changed_files",
            get_changed_files,
            root,
            staged=staged,
            commit_range=commit_range,
            default=[],
        )
        or []
    )
    if not changed:
        label = commit_range or ("staged" if staged else "unstaged")
        # Pattern 1 fix (Fix A from internal/dogfood/SYNTHESIS-2026-05-12.md):
        # never emit empty stdout in --json mode. MCP wrappers crash with
        # `Expecting value: line 1 column 1 (char 0)` when given empty
        # output. Always emit a structured envelope. Mirrors the shape
        # `pr-risk` uses on clean tree.
        #
        # W641-followup-E — canonical risk-LEVEL projection. A clean diff
        # has zero affected_symbols / files, so the canonical rank is the
        # W631 floor "low". Emitted unconditionally so agents downstream
        # can call ``risk_rank(summary["risk_level_canonical"])`` without
        # None-handling (parity with W641-followup-A/B/D).
        _no_changes_canonical = normalize_risk_level("low") or "low"
        _no_changes_rank = risk_rank(_no_changes_canonical)
        # Verdict on the no-changes path stays literal ``"no changes"``
        # (NOT augmented with the canonical suffix) to preserve the
        # pre-W641-followup-E regression contract pinned in
        # ``tests/test_diff_empty_state.py``. The canonical fields are
        # still emitted on summary + top level so agents can call
        # ``risk_rank(summary["risk_level_canonical"])`` unconditionally;
        # the state field (``"no_changes"``) and ``partial_success=False``
        # already disambiguate the LAW 6 standalone-parse.
        _no_changes_summary: dict = {
            "verdict": "no changes",
            "state": "no_changes",
            "partial_success": False,
            "changed_files": 0,
            "affected_symbols": 0,
            "affected_files": 0,
            "label": label,
            "risk_level_canonical": _no_changes_canonical,
            "risk_rank": _no_changes_rank,
        }
        _no_changes_envelope_kwargs: dict = dict(
            budget=token_budget,
            label=label,
            changed_files=0,
            symbols_defined=0,
            affected_symbols=0,
            affected_files=0,
            per_file=[],
            blast_radius=[],
            risk_level_canonical=_no_changes_canonical,
            risk_rank=_no_changes_rank,
            message=f"No changes found for {label}.",
        )
        # W607-Z -- if get_changed_files raised, the substrate marker
        # rides BOTH summary.warnings_out AND the top-level mirror on
        # the no-changes envelope. ``partial_success`` flips to True so
        # consumers reading the summary alone see the degradation.
        if _w607z_warnings_out:
            _no_changes_summary["warnings_out"] = list(_w607z_warnings_out)
            _no_changes_summary["partial_success"] = True
            _no_changes_envelope_kwargs["warnings_out"] = list(_w607z_warnings_out)
            _no_changes_envelope_kwargs["partial_success"] = True
        _no_changes_envelope = json_envelope(
            "diff",
            summary=_no_changes_summary,
            **_no_changes_envelope_kwargs,
        )
        auto_log(_no_changes_envelope, action="diff", target=label, repo_root=root)
        if json_mode:
            click.echo(to_json(_no_changes_envelope))
            return
        click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=True) as conn:
        # Map changed files to file IDs
        file_map = (
            _run_check(
                "resolve_changed_to_db",
                resolve_changed_to_db,
                conn,
                changed,
                default={},
            )
            or {}
        )

        if not file_map:
            # Pattern 1 fix: --json mode must never emit empty stdout.
            # The working tree has changes but they don't intersect the
            # index — surface this explicitly as ``index_stale`` so
            # consumers don't conflate it with ``no_changes``.
            #
            # W641-followup-E — canonical risk-LEVEL projection. An
            # index-stale path produced no resolved symbols, so the
            # canonical rank is the W631 floor "low". Emit
            # unconditionally so agents downstream can call
            # ``risk_rank(summary["risk_level_canonical"])`` without
            # None-handling (parity with W641-followup-A/B/D).
            label = commit_range or ("staged" if staged else "unstaged")
            _stale_canonical = normalize_risk_level("low") or "low"
            _stale_rank = risk_rank(_stale_canonical)
            _stale_summary: dict = {
                "verdict": (f"changed files not in index (risk_level {_stale_canonical})"),
                "state": "index_stale",
                "partial_success": True,
                "changed_files": 0,
                "affected_symbols": 0,
                "affected_files": 0,
                "label": label,
                "hint": "Run `roam index` to refresh.",
                "risk_level_canonical": _stale_canonical,
                "risk_rank": _stale_rank,
            }
            _stale_envelope_kwargs: dict = dict(
                budget=token_budget,
                label=label,
                changed_files=0,
                symbols_defined=0,
                affected_symbols=0,
                affected_files=0,
                per_file=[],
                blast_radius=[],
                files_not_in_index=len(changed),
                risk_level_canonical=_stale_canonical,
                risk_rank=_stale_rank,
                message=(
                    f"Changed files not found in index ({len(changed)} files changed). Try running `roam index` first."
                ),
            )
            # W607-Z -- mirror substrate markers onto the index_stale
            # envelope so a resolve_changed_to_db raise that produced an
            # empty file_map still surfaces the underlying cause.
            if _w607z_warnings_out:
                _stale_summary["warnings_out"] = list(_w607z_warnings_out)
                _stale_envelope_kwargs["warnings_out"] = list(_w607z_warnings_out)
                _stale_envelope_kwargs["partial_success"] = True
            _stale_envelope = json_envelope(
                "diff",
                summary=_stale_summary,
                **_stale_envelope_kwargs,
            )
            auto_log(_stale_envelope, action="diff", target=label, repo_root=root)
            if json_mode:
                click.echo(to_json(_stale_envelope))
                return
            click.echo(f"Changed files not found in index ({len(changed)} files changed).")
            click.echo("Try running `roam index` first.")
            return

        # Get symbols in changed files
        sym_by_file = {}
        for path, fid in file_map.items():
            syms = conn.execute("SELECT id, name, kind FROM symbols WHERE file_id = ?", (fid,)).fetchall()
            sym_by_file[path] = syms

        total_syms = sum(len(s) for s in sym_by_file.values())

        # Build graph and compute impact
        try:
            import networkx as nx

            from roam.graph.builder import build_symbol_graph
        except ImportError:
            click.echo("Graph module not available.")
            return

        G = _run_check("build_symbol_graph", build_symbol_graph, conn, default=None)
        if G is None:
            # Substrate raise -- fall back to an empty DiGraph so the
            # loop body below produces zero affected symbols/files rather
            # than crashing. The W607-Z marker on
            # ``_w607z_warnings_out`` already discloses the failure.
            G = nx.DiGraph()
        RG = G.reverse()

        # Per-file impact analysis
        file_impacts = []
        all_affected_files = set()
        all_affected_syms = set()

        for path, syms in sym_by_file.items():
            file_dependents = set()
            file_affected_files = set()
            for s in syms:
                sid = s["id"]
                if sid in RG:
                    deps = nx.descendants(RG, sid)
                    file_dependents.update(deps)
                    for d in deps:
                        node = G.nodes.get(d, {})
                        fp = node.get("file_path")
                        if fp and fp != path:
                            file_affected_files.add(fp)

            all_affected_syms.update(file_dependents)
            all_affected_files.update(file_affected_files)

            file_impacts.append(
                {
                    "path": path,
                    "symbols": len(syms),
                    "affected_syms": len(file_dependents),
                    "affected_files": len(file_affected_files),
                }
            )

        # Sort by blast radius
        file_impacts.sort(key=lambda x: x["affected_syms"], reverse=True)

        # ── Extra analyses ───────────────────────────────────────────

        # Affected tests
        test_results = []
        pytest_cmd = ""
        if tests:
            # W607-Z -- wrap to surface helper raises as a structured
            # marker rather than crashing the whole `roam diff` invocation.
            _at = _run_check(
                "collect_affected_tests",
                _collect_affected_tests,
                conn,
                sym_by_file,
                default=([], ""),
            )
            test_results, pytest_cmd = _at if _at is not None else ([], "")

        # Coupling warnings
        coupling_warnings = []
        if coupling:
            # W607-Z -- formerly a silent ``try/except: pass`` (the
            # ``git_cochange`` table may be missing on older indexes).
            # Marker now surfaces the underlying cause via
            # ``_w607z_warnings_out`` instead of vanishing.
            coupling_warnings = (
                _run_check(
                    "collect_coupling_warnings",
                    _collect_coupling_warnings,
                    conn,
                    file_map,
                    default=[],
                )
                or []
            )

        # Fitness violations
        fitness_rule_results = []
        fitness_violations = []
        if fitness:
            # W607-Z -- formerly a silent ``try/except: pass`` (the
            # ``fitness.yaml`` file may be absent). Marker now surfaces
            # the underlying cause via ``_w607z_warnings_out``.
            _fv = _run_check(
                "collect_fitness_violations",
                _collect_fitness_violations,
                conn,
                file_map,
                root,
                default=([], []),
            )
            fitness_rule_results, fitness_violations = _fv if _fv is not None else ([], [])

        # ── Envelope construction (used by JSON output + auto-log) ───

        _diff_base_verdict = (
            f"{len(file_map)} files changed, "
            f"{len(all_affected_syms)} symbols affected, "
            f"{len(all_affected_files)} files in blast radius"
        )

        # W641-followup-E — canonical W631 risk-LEVEL projection. cmd_diff
        # has no native severity vocabulary; the canonical bucket is
        # derived from the blast-radius thresholds (see ``_diff_risk_level``
        # docstring). Mirrors W641-followup-A's polarity (cmd_impact) so a
        # cross-command consumer can ``risk_rank(summary["risk_level_canonical"])
        # >= 3`` to gate on high-or-worse without re-deriving the threshold
        # table at the call site (same Pattern-3a discipline as W632 /
        # W641 / W641-followup-A/B/C/D).
        #
        # ``or "low"`` floor mirrors the W531 CI-safety lesson — a typo'd
        # or unrecognised severity bucket MUST NOT promote into a CI-gating
        # rank. The canonical fields are emitted unconditionally so agents
        # downstream call ``risk_rank(...)`` without None-handling.
        _diff_warnings_out: list[str] = []
        # W607-Z -- substrate-CALL boundary on ``_diff_risk_level``
        # (kept intact). Pinned by tests/test_w607_z_cmd_diff_warnings_out_envelope.py.
        _diff_domain_level_z = _run_check(
            "compute_risk_level",
            _diff_risk_level,
            len(all_affected_syms),
            len(all_affected_files),
            warnings_out=_diff_warnings_out,
            default="low",
        )
        if _diff_domain_level_z is None:
            _diff_domain_level_z = "low"
        # W607-BP -- severity_classify boundary, ADDITIVE on top of the
        # W607-Z substrate-CALL wrap. We re-classify with the SAME helper
        # but flag the result on the AGGREGATION-PHASE axis: if the
        # classifier raises (a closed-vocabulary refactor or future inner
        # threshold helper), the wrap floors the domain tier to ``None``
        # and surfaces the marker alongside the canonical ``"low"`` floor +
        # ``severity_classification: "unknown"`` sentinel in the envelope
        # summary. Mirror of cmd_critique W607-BL / cmd_diagnose W607-BH
        # severity_classification sentinel.
        _bp_severity_probe = _run_check_bp(
            "severity_classify",
            _diff_risk_level,
            len(all_affected_syms),
            len(all_affected_files),
            warnings_out=_diff_warnings_out,
            default=None,
        )
        # Domain-tier raised (None floor) -> mark classification unknown so
        # the envelope discloses the degraded outcome. When W607-BP probe
        # succeeded (non-None), classification is "classified".
        _severity_classification_state = "unknown" if _bp_severity_probe is None else "classified"
        # Use the W607-Z domain level for the canonical projection (the
        # W607-Z wrap already floors to "low" on raise); the BP probe is
        # the additive aggregation-phase boundary that drives the
        # severity_classification sentinel.
        _diff_domain_level = _diff_domain_level_z
        # W607-BP -- severity_normalize boundary. Wraps the canonical W631
        # ``normalize_risk_level`` + ``risk_rank`` projections so a future
        # signature change / closed-enum vocabulary drift surfaces a marker
        # rather than crashing the envelope. Floors to ``"low"`` / rank ``1``
        # so downstream comparators stay non-null. Mirror of cmd_critique
        # W607-BL / cmd_diagnose W607-BH severity_normalize pattern.
        risk_level_canonical = _run_check_bp(
            "severity_normalize",
            lambda level: normalize_risk_level(level) or "low",
            _diff_domain_level,
            default="low",
        )
        risk_rank_int = _run_check_bp(
            "severity_normalize",
            risk_rank,
            risk_level_canonical,
            default=1,
        )

        # W607-BP -- compute_verdict boundary. Wraps the canonical
        # augmented verdict text build so a future format-spec regression
        # on the components (e.g. non-string risk_level_canonical from a
        # vocabulary refactor) surfaces a marker rather than crashing the
        # envelope. Floors to a stable verdict string so LAW 6 ("verdict
        # works standalone") stays satisfied even on degraded paths.
        #
        # Verdict augmentation: append the canonical bucket so LAW 6
        # standalone-parse holds — an agent reading just the verdict line
        # can call ``risk_rank`` on the parenthesised token without
        # consulting any other envelope field. Mirrors the W641-followup-
        # A/B/C/D verdict-augmentation contract.
        def _build_augmented_verdict() -> str:
            return f"{_diff_base_verdict} (risk_level {risk_level_canonical})"

        # Floor must NOT re-format ``risk_level_canonical`` -- the same
        # value that tripped the closure (e.g. a __format__-raising
        # sentinel under test) would re-raise inside the default
        # f-string. Use a literal "low" floor instead (LAW 6 still holds:
        # the line works standalone; the W631 floor is "low").
        _diff_verdict = _run_check_bp(
            "compute_verdict",
            _build_augmented_verdict,
            default="diff completed (risk_level low)",
        )

        _diff_label = commit_range or ("staged" if staged else "unstaged")
        envelope_data = dict(
            label=_diff_label,
            changed_files=len(file_map),
            symbols_defined=total_syms,
            affected_symbols=len(all_affected_syms),
            affected_files=len(all_affected_files),
            per_file=file_impacts,
            blast_radius=sorted(all_affected_files),
            # W641-followup-E — top-level mirror of summary.risk_level_canonical
            # / summary.risk_rank so consumers reading the top-level envelope
            # head without descending into ``summary`` see the canonical
            # bucket too (parity with cmd_impact / cmd_critique / cmd_attest).
            risk_level_canonical=risk_level_canonical,
            risk_rank=risk_rank_int,
        )

        summary = {
            "verdict": _diff_verdict,
            "changed_files": len(file_map),
            "affected_symbols": len(all_affected_syms),
            "affected_files": len(all_affected_files),
            # W641-followup-E — canonical W631 risk-LEVEL + integer rank.
            # Projected from blast-radius thresholds via
            # ``_diff_risk_level`` (Pattern-3a structural close-out).
            # Cross-command consumers can compare e.g.
            # ``risk_rank(summary.risk_level_canonical) >= 3`` to gate on
            # high-or-worse blast radius.
            "risk_level_canonical": risk_level_canonical,
            "risk_rank": risk_rank_int,
            # W607-BP -- SEVERITY-CLASSIFY DEGRADATION sentinel. When the
            # ``severity_classify`` boundary raises (and the classify
            # result floors to ``None``), surface
            # ``severity_classification: "unknown"`` so the agent sees
            # the degraded outcome alongside the canonical floor
            # ("low") rather than mistaking the floor for a real
            # classification. Clean path -> ``"classified"``. Mirror of
            # cmd_impact's ``risk_classification`` / cmd_diagnose's
            # ``severity_classification`` / cmd_critique's
            # ``severity_classification`` sentinel.
            "severity_classification": _severity_classification_state,
        }

        # Surface Pattern-2 silent-fallback markers (unknown / negative
        # counts). Empty list omitted to keep the envelope tight.
        #
        # W607-Z -- substrate-CALL markers ride the same ``warnings_out``
        # channel but accumulate in a DIFFERENT bucket
        # (``_w607z_warnings_out``) so the two axes (unknown-severity
        # data shape vs. helper-raised substrate boundary) don't conflate
        # at the call site. They MERGE into a single ``warnings_out``
        # list on emission; the marker PREFIX disambiguates them
        # downstream (``diff_unknown_severity:*`` vs. ``diff_<phase>_failed:*``).
        # ``partial_success`` flips when EITHER bucket is non-empty --
        # consumers reading ``partial_success`` alone need not distinguish
        # the two flavours. Mirrors the W607-Y cmd_critique bucket-merge.
        #
        # W607-BP -- ADDITIVE aggregation-phase markers join the same
        # combined-channel: ``_diff_warnings_out`` (unknown-severity) +
        # ``_w607z_warnings_out`` (substrate-CALL) +
        # ``_w607bp_warnings_out`` (aggregation-phase). All three share
        # the ``diff_*`` family per the marker-prefix discipline test;
        # the additive bucket stays distinguishable in tests + audits
        # via its phase names (``severity_classify`` /
        # ``severity_normalize`` / ``compute_verdict`` / ``auto_log`` /
        # ``serialize_envelope``).
        _combined_warnings_out: list[str] = (
            list(_diff_warnings_out) + list(_w607z_warnings_out) + list(_w607bp_warnings_out)
        )
        if _combined_warnings_out:
            summary["warnings_out"] = list(_combined_warnings_out)
            summary["partial_success"] = True

        if tests:
            direct = sum(1 for t in test_results if t["kind"] == "DIRECT")
            transitive = sum(1 for t in test_results if t["kind"] == "TRANSITIVE")
            colocated = sum(1 for t in test_results if t["kind"] == "COLOCATED")
            test_files = []
            seen = set()
            for t in test_results:
                if t["file"] not in seen:
                    seen.add(t["file"])
                    test_files.append(t["file"])

            summary["affected_tests"] = len(test_results)
            envelope_data["affected_tests"] = {
                "total": len(test_results),
                "direct": direct,
                "transitive": transitive,
                "colocated": colocated,
                "test_files": test_files,
                "pytest_command": pytest_cmd,
                "tests": [
                    {
                        "file": t["file"],
                        "symbol": t["symbol"],
                        "kind": t["kind"],
                        "hops": t["hops"],
                        "via": t["via"],
                    }
                    for t in test_results
                ],
            }

        if coupling:
            summary["coupling_warnings"] = len(coupling_warnings)
            envelope_data["coupling_warnings"] = coupling_warnings

        if fitness:
            failed_count = sum(1 for r in fitness_rule_results if r["status"] == "FAIL")
            summary["fitness_violations"] = len(fitness_violations)
            summary["fitness_rules_failed"] = failed_count
            # W1298 Pattern-3a: any cognitive_complexity-keyed violation
            # in ``fitness_violations`` reads from ``symbol_metrics`` — disclose
            # the scorer so consumers cannot confuse it with cyclomatic.
            if any(v.get("metric") == "cognitive_complexity" for v in fitness_violations):
                summary["complexity_definition"] = COGNITIVE_COMPLEXITY_DEFINITION
            envelope_data["fitness_violations"] = {
                "rules": fitness_rule_results,
                "violations": fitness_violations[:100],
            }

        # W607-Z / W607-BP -- top-level mirror of summary.warnings_out so
        # consumers that read the top-level envelope directly (without
        # descending into ``summary``) see the marker channel. Mirror
        # parity with W607-Y cmd_critique (and the rest of the W607 family).
        if _combined_warnings_out:
            envelope_data["warnings_out"] = list(_combined_warnings_out)
            envelope_data["partial_success"] = True

        # W607-BP -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("diff", ...)`` would otherwise crash
        # AFTER all substrate + aggregation signals were already gathered.
        # Floor to a minimal envelope stub so consumers still receive a
        # parseable JSON object with the marker attached + the canonical
        # command name. Mirror of cmd_critique's W607-BL serialize_envelope
        # floor pattern.
        _envelope_floor: dict = {
            "command": "diff",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": _diff_verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        diff_envelope = _run_check_bp(
            "serialize_envelope",
            json_envelope,
            "diff",
            default=_envelope_floor,
            budget=token_budget,
            summary=summary,
            **envelope_data,
        )
        # W607-BP -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``diff_serialize_envelope_failed:`` marker was appended to
        # ``_w607bp_warnings_out`` and the floor stub carries only the
        # old combined list. Rebuild the floor stub's warnings_out so the
        # new marker reaches the JSON output. Clean path -> envelope is
        # the real json_envelope return value, no rebuild needed.
        if diff_envelope is _envelope_floor and _w607bp_warnings_out:
            _combined_warnings_out = list(_diff_warnings_out) + list(_w607z_warnings_out) + list(_w607bp_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            diff_envelope = _envelope_floor

        # W607-BP -- auto_log boundary. Silent no-op if no active run;
        # the wrap surfaces HMAC chain-misshape / filesystem failures as
        # ``diff_auto_log_failed:...`` markers instead of crashing the
        # envelope after it was already built. Mirror of cmd_critique's
        # W607-BL auto_log pattern.
        _run_check_bp(
            "auto_log",
            auto_log,
            diff_envelope,
            action="diff",
            target=_diff_label,
            repo_root=root,
            default=None,
        )
        # W607-BP -- if ``auto_log`` raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log)
        # -> envelope stays byte-identical to the version already built
        # above.
        if _w607bp_warnings_out and not any(
            m.startswith("diff_auto_log_failed:") for m in (summary.get("warnings_out") or [])
        ):
            _combined_warnings_out = list(_diff_warnings_out) + list(_w607z_warnings_out) + list(_w607bp_warnings_out)
            summary["warnings_out"] = list(_combined_warnings_out)
            summary["partial_success"] = True
            envelope_data["warnings_out"] = list(_combined_warnings_out)
            envelope_data["partial_success"] = True
            diff_envelope = _run_check_bp(
                "serialize_envelope",
                json_envelope,
                "diff",
                default=_envelope_floor,
                budget=token_budget,
                summary=summary,
                **envelope_data,
            )

        # ── JSON output ──────────────────────────────────────────────

        if json_mode:
            click.echo(to_json(diff_envelope))
            return

        # ── Text output ──────────────────────────────────────────────

        if commit_range:
            label = commit_range
        else:
            label = "staged" if staged else "unstaged"
        # W641-followup-E — surface the canonical risk-LEVEL bucket on the
        # text-mode VERDICT line so reviewers reading the terminal output
        # see the same closed-enum token as the JSON envelope.
        _diff_verdict_text = (
            f"{len(file_map)} files changed, "
            f"{len(all_affected_syms)} symbols affected, "
            f"{len(all_affected_files)} files in blast radius "
            f"(risk_level {risk_level_canonical})"
        )
        click.echo(f"VERDICT: {_diff_verdict_text}")
        click.echo()
        click.echo(f"=== Blast Radius ({label} changes) ===\n")
        click.echo(f"Changed files: {len(file_map)}  Symbols defined: {total_syms}")
        click.echo(f"Affected symbols: {len(all_affected_syms)}  Affected files: {len(all_affected_files)}")

        # surface the top-3 affected symbols by PageRank
        # so reviewers see "this change ripples into central abstractions"
        # vs "this change is in a leaf module" without scrolling. Quiet
        # when no PageRank data is available.
        if all_affected_syms:
            try:
                from roam.db.connection import batched_in

                aff_ids = list(all_affected_syms)[:512]  # cap for DB
                rows = batched_in(
                    conn,
                    "SELECT s.id, s.name, sm.pagerank, f.path "
                    "FROM symbols s JOIN symbol_metrics sm ON sm.symbol_id = s.id "
                    "JOIN files f ON s.file_id = f.id "
                    "WHERE s.id IN ({ph}) AND sm.pagerank IS NOT NULL "
                    "ORDER BY sm.pagerank DESC LIMIT 3",
                    aff_ids,
                )
                top_pr = list(rows)[:3]
                if top_pr:
                    click.echo("Top affected by PageRank:")
                    for r in top_pr:
                        pr = float(r["pagerank"] or 0.0)
                        click.echo(f"  {r['name']:<40s} pr={pr:.4f}  {r['path']}")
            except Exception as _exc:  # noqa: BLE001 — defensive
                # Best-effort — never break `roam diff` over an enrichment fail.
                from roam.observability import log_swallowed

                log_swallowed("cmd_diff:pagerank_enrichment", _exc)
        click.echo()

        # Per-file breakdown
        rows = []
        display = file_impacts if full else file_impacts[:15]
        for fi in display:
            rows.append(
                [
                    fi["path"],
                    str(fi["symbols"]),
                    str(fi["affected_syms"]),
                    str(fi["affected_files"]),
                ]
            )
        click.echo(
            format_table(
                ["Changed file", "Symbols", "Affected syms", "Affected files"],
                rows,
            )
        )
        if not full and len(file_impacts) > 15:
            click.echo(f"\n(+{len(file_impacts) - 15} more files)")

        # List affected files
        if all_affected_files:
            click.echo(f"\nFiles in blast radius ({len(all_affected_files)}):")
            sorted_files = sorted(all_affected_files)
            show = sorted_files if full else sorted_files[:20]
            for fp in show:
                click.echo(f"  {fp}")
            if not full and len(sorted_files) > 20:
                click.echo(f"  (+{len(sorted_files) - 20} more)")

        # ── Affected tests section ───────────────────────────────────

        if tests:
            click.echo()
            if not test_results:
                click.echo("=== Affected Tests ===\n")
                click.echo("No affected tests found.")
            else:
                direct = sum(1 for t in test_results if t["kind"] == "DIRECT")
                transitive = sum(1 for t in test_results if t["kind"] == "TRANSITIVE")
                colocated = sum(1 for t in test_results if t["kind"] == "COLOCATED")
                click.echo(
                    f"=== Affected Tests ({len(test_results)}: "
                    f"{direct} direct, {transitive} transitive, "
                    f"{colocated} colocated) ===\n"
                )

                display_tests = test_results if full else test_results[:20]
                for t in display_tests:
                    kind_tag = f"{t['kind']:<12s}"
                    if t["symbol"]:
                        test_label = f"{t['file']}::{t['symbol']}"
                    else:
                        test_label = t["file"]

                    if t["kind"] == "DIRECT":
                        detail = f"({t['hops']} hop)"
                    elif t["kind"] == "TRANSITIVE":
                        via_str = f" via {t['via']}" if t["via"] else ""
                        detail = f"({t['hops']} hops{via_str})"
                    else:
                        detail = "(same directory)"

                    click.echo(f"  {kind_tag} {test_label:<55s} {detail}")

                if not full and len(test_results) > 20:
                    click.echo(f"  (+{len(test_results) - 20} more)")

                if pytest_cmd:
                    click.echo(f"\nRun: {pytest_cmd}")

        # ── Coupling warnings section ────────────────────────────────

        if coupling:
            click.echo()
            click.echo("=== Coupling Warnings ===\n")
            if not coupling_warnings:
                click.echo("No missing co-change partners.")
            else:
                click.echo(f"Missing co-change partners ({len(coupling_warnings)}):")
                click.echo("(files you usually change together but are not in this diff)")
                cpl_rows = []
                display_cpl = coupling_warnings if full else coupling_warnings[:10]
                for w in display_cpl:
                    cpl_rows.append(
                        [
                            w["path"],
                            str(w["cochanges"]),
                            f"{w['strength']:.0%}",
                            w["partner_of"],
                        ]
                    )
                click.echo(
                    format_table(
                        [
                            "Usually changes with",
                            "Co-changed",
                            "Strength",
                            "Partner of",
                        ],
                        cpl_rows,
                    )
                )
                if not full and len(coupling_warnings) > 10:
                    click.echo(f"\n(+{len(coupling_warnings) - 10} more warnings)")

        # ── Fitness violations section ───────────────────────────────

        if fitness:
            click.echo()
            if not fitness_rule_results:
                click.echo("=== Fitness Check ===\n")
                click.echo("No fitness rules found. Create .roam/fitness.yaml or run: roam fitness --init")
            else:
                failed = sum(1 for r in fitness_rule_results if r["status"] == "FAIL")
                passed = sum(1 for r in fitness_rule_results if r["status"] == "PASS")
                click.echo(
                    f"=== Fitness Check ({len(fitness_rule_results)} rules, {passed} passed, {failed} failed) ===\n"
                )

                for rr in fitness_rule_results:
                    icon = "PASS" if rr["status"] == "PASS" else "FAIL"
                    detail = f" ({rr['violations']} violations)" if rr["violations"] else ""
                    click.echo(f"  [{icon}] {rr['name']}{detail}")

                if fitness_violations:
                    click.echo(f"\nViolations in changed files ({len(fitness_violations)}):\n")
                    display_v = fitness_violations if full else fitness_violations[:15]
                    for v in display_v:
                        src = v.get("source", "")
                        click.echo(f"  {v['rule']}: {v['message']}")
                        if src:
                            click.echo(f"    at {src}")
                    if not full and len(fitness_violations) > 15:
                        click.echo(f"\n  (+{len(fitness_violations) - 15} more)")
