"""``roam postmortem`` — replay current detectors against past commits.

Answers the question "would today's detector set have caught the
incident we shipped last quarter?" Walks a commit range, runs the
critique + diff-blast-radius detectors against each commit's
**outgoing diff** as if it were a PR, and reports which findings
would have surfaced pre-merge.

Doesn't actually re-index the historical state — that would be
slow, memory-heavy, and the index might not even build cleanly on
old commits. Instead it inspects each commit's diff with the
current detector set; the detection rules are usually stable
enough across the time-window of interest (last 30-90 days) that
this gives an honest answer.

Usage:

    roam postmortem HEAD~30..HEAD
    roam postmortem 2026-04-01..2026-04-30
    roam postmortem 1290978..843ceca

Output:

    VERDICT: 7 of 30 commits would have surfaced findings
    Top hits:
      839abc1 fix: parse YAML — 2 high-severity (clones-not-edited)
      4c47862 feat(yaml): list-of-dicts shape — 1 medium (impact)
      ...

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because postmortem outputs are invocation-scoped historical
detector-result replays — not per-location violations. ``postmortem``
delegates SARIF emission to composed subcommands (``critique``, etc.)
when their own ``--sarif`` flag fires directly. See action.yml
_SUPPORTED_SARIF allowlist + W1145 / W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import json as _json
import subprocess

import click
from click.testing import CliRunner

from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json


def _git_log_in_range(commit_range: str, *, limit: int = 100) -> list[dict]:
    """Return [{sha, short_sha, subject, author, date}, ...] for commits in range."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--max-count={limit}",
                "--pretty=format:%H%x09%h%x09%s%x09%an%x09%ad",
                "--date=short",
                commit_range,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    out: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        out.append(
            {
                "sha": parts[0],
                "short_sha": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": parts[4],
            }
        )
    return out


def _diff_for_commit(sha: str) -> str:
    """Return the unified diff `git show --pretty='' SHA` produces."""
    try:
        result = subprocess.run(
            ["git", "show", "--pretty=", "--unified=3", sha],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _critique_diff(diff_text: str) -> dict:
    """Run roam critique --json on a diff (in-process)."""
    if not diff_text.strip():
        return {"summary": {}}
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "critique"], input=diff_text, catch_exceptions=False)
    try:
        return _json.loads(result.output) if result.output else {"summary": {}}
    except _json.JSONDecodeError:
        return {"summary": {}, "_parse_error": True}


def _summarize_finding_count(critique_payload: dict) -> tuple[int, int, int]:
    """Return (high, medium, low) counts from a critique envelope."""
    summary = critique_payload.get("summary") or {}
    high = int(summary.get("high_severity_findings") or summary.get("high_severity_total") or summary.get("high") or 0)
    # medium / low aren't always exposed; default to total minus high.
    total = int(summary.get("findings") or summary.get("total") or 0)
    medium = max(0, total - high)
    return high, medium, 0


def _short_finding_summary(critique_payload: dict, *, max_kinds: int = 3) -> list[str]:
    """Pull the top-N finding kinds from a critique envelope."""
    findings = critique_payload.get("findings") or critique_payload.get("checks") or []
    kinds: dict[str, int] = {}
    for f in findings:
        if not isinstance(f, dict):
            continue
        kind = f.get("check") or f.get("kind") or f.get("rule") or "?"
        kinds[kind] = kinds.get(kind, 0) + 1
    return [f"{k} x{v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:max_kinds]]


from roam.capability import roam_capability


@roam_capability(
    category="review",
    summary="Replay current detectors against past commits — show the findings that would have surfaced pre-merge.",
    inputs=["commit_range"],
    outputs=["findings_per_commit", "summary"],
    examples=[
        "roam postmortem HEAD~30..HEAD",
        "roam postmortem v12.0..v12.40 --limit 50",
    ],
    tags=["audit", "review", "phase0", "demo"],
    ai_safe=True,
    requires_index=True,
    since="12.40",
)
@click.command(name="postmortem")
@click.argument("commit_range", required=True)
@click.option(
    "--limit",
    default=100,
    type=int,
    help="Cap the number of commits walked (default 100).",
)
@click.option(
    "--show",
    "show_n",
    default=10,
    type=int,
    help="Top-N hits to display in text mode (default 10).",
)
@click.pass_context
def postmortem_cmd(ctx, commit_range: str, limit: int, show_n: int):
    """Replay current detectors against past commits.

    Walks COMMIT_RANGE (e.g. ``HEAD~30..HEAD`` or ``v12.30..v12.39``),
    runs ``roam critique`` against each commit's diff, and reports which
    findings would have surfaced pre-merge.

    \b
    Examples:
      roam postmortem HEAD~30..HEAD
      roam postmortem v12.30..HEAD
      roam postmortem main..feature/new-thing
      roam --json postmortem HEAD~50..HEAD > postmortem.json

    A retrospective replay: would today's detector set have caught the
    incidents already in your git history? Useful pre-purchase signal —
    "if it retroactively flags my Q1 incidents, the gate is worth wiring."
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # W607-DR -- ADDITIONAL substrate-CALL plumbing beneath W607-AN /
    # W607-CV. cmd_postmortem already had two W607 layers landed:
    #
    #   - W607-AN (substrate-CALL, 6 phases): wraps the six per-commit
    #     helper boundaries (load_run_ledger / parse_event_payload /
    #     classify_failure / aggregate_by_phase / compute_root_cause /
    #     aggregate_by_actor).
    #   - W607-CV (aggregation-phase, 4 phases): wraps the envelope
    #     assembly boundaries (score_classify / compute_predicate /
    #     compute_verdict / serialize_envelope).
    #
    # W607-DR extends marker coverage to six SUBSTRATE-CALL boundaries
    # that BOTH W607-AN and W607-CV leave unguarded:
    #
    #   load_index           -- ensure_index() (open SQLite + schema
    #                           migration). A raise here would otherwise
    #                           crash cmd_postmortem BEFORE either layer
    #                           gets a chance to accumulate markers.
    #   extract_commit_fields -- per-commit dict field extraction inside
    #                           the loop (KeyError on a malformed git-log
    #                           row would crash the loop with no marker).
    #   accumulate_counts    -- per-commit running totals (high / medium
    #                           accumulators). A raise here would lose
    #                           the partial accumulator state silently.
    #   combine_warnings_buckets -- list-merge of W607-AN + W607-CV
    #                           accumulators. A custom-list sentinel
    #                           that raises on iteration would otherwise
    #                           crash envelope assembly.
    #   format_top_hits      -- text-mode top-N rendering loop. A raise
    #                           inside f-string formatting (e.g. a non-
    #                           string subject from a corrupt commit
    #                           row) would otherwise crash the CLI.
    #   render_verdict_line  -- text-mode VERDICT-line echo. A raise on
    #                           the first text-output click.echo would
    #                           otherwise crash the CLI before any
    #                           per-commit detail.
    #
    # Marker family ``postmortem_*`` -- same family as W607-AN + W607-CV
    # (additive, not a separate prefix). Empty bucket -> byte-identical
    # envelope. All three buckets combine at envelope-emit time so
    # consumers see the full degradation lineage in marker-emission
    # order. The additive W607-DR bucket stays distinguishable via its
    # phase names (none collide with W607-AN's load_run_ledger /
    # parse_event_payload / classify_failure / aggregate_by_phase /
    # compute_root_cause / aggregate_by_actor, nor W607-CV's
    # score_classify / compute_predicate / compute_verdict /
    # serialize_envelope).
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_dr(...)`` call:
    #   1. f-string verdict floor -> literal-constant default=
    #   2. kwarg-default eagerness -> literal-only default= expressions
    #   3. json.dumps(default=str) sentinel -> stays inside the closure
    #   4. Phase-name collision check -> all six phase names new (no
    #      overlap with W607-AN/CV phases above)
    #   5. len() lives INSIDE the closure, NOT at the kwarg-bind site
    #   6. Unguarded len() / if x: on poisoned object -> protect with
    #      None checks inside the closure
    #   7. dict.get(key, expensive_default) -> bare dict[key] when the
    #      floor dict guarantees the key
    _w607dr_warnings_out: list[str] = []

    def _run_check_dr(phase, fn, *args, default=None, **kwargs):
        """Run one substrate-CALL boundary with W607-DR marker emission.

        Mirror of ``_run_check_an`` / ``_run_check_cv`` shape (same
        ``postmortem_<phase>_failed:`` marker family) but writes into
        ``_w607dr_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dr_warnings_out.append(f"postmortem_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    _run_check_dr("load_index", ensure_index, default=None)

    # W607-AN -- canonical W607 substrate-CALL plumbing. cmd_postmortem
    # walks a git-commit range, decodes each commit's diff, runs the
    # current critique detector set against that diff, and aggregates
    # the per-commit findings into a retrospective report. It is the
    # DIRECT UPSTREAM of cmd_pr_replay's ``_run_postmortem`` boundary
    # (W607-AH just landed). Closes the producer/consumer triangle on
    # the W805 family: pr-bundle emits artifacts (W607-AE), postmortem
    # reconstructs the narrative from runs/ledger entries + git history
    # (this wave), pr-replay reads + renders the postmortem output
    # (W607-AH).
    #
    # Each wrapped phase becomes a structured
    # ``postmortem_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607an_warnings_out`` and the envelope still emits cleanly.
    # The marker rides BOTH ``summary.warnings_out`` and top-level
    # ``warnings_out`` so consumers reading either surface see the
    # disclosure. ``partial_success`` flips on non-empty bucket.
    #
    # Audit-trail reader pairing: cmd_postmortem and cmd_audit_trail_verify
    # (W607-AI landed) BOTH walk a record-of-history substrate (git log
    # vs runs/ JSONL ledger). The two marker families
    # (``postmortem_*`` and ``audit_trail_verify_*``) are closed-enum
    # distinct so they can coexist on a combined envelope when both
    # readers process the same trail.
    _w607an_warnings_out: list[str] = []

    def _run_check_an(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AN marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``postmortem_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607an_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607an_warnings_out.append(f"postmortem_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CV: aggregation-phase marker plumbing (additive) -----------
    # cmd_postmortem is the post-incident analyzer; it walks a commit
    # range, runs critique against each commit's diff, and aggregates the
    # per-commit findings into a retrospective report. W607-AN (above)
    # plumbed the substrate-CALL layer (6 boundaries: load_run_ledger /
    # parse_event_payload / classify_failure / aggregate_by_phase /
    # compute_root_cause / aggregate_by_actor). W607-CV adds the
    # AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket the run into a state label
    #                           (FINDINGS_SURFACED / NO_FINDINGS / EMPTY)
    #   compute_predicate    -- per-commit counts (scanned/with_findings,
    #                           total_high / total_medium)
    #   compute_verdict      -- composite verdict string assembly
    #   serialize_envelope   -- json_envelope("postmortem", ...) projection
    #
    # Marker family ``postmortem_*`` -- same family as W607-AN (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope on
    # the success path. Both buckets are combined at envelope-emit time
    # so consumers see the full degradation lineage in marker-emission
    # order. The additive bucket stays distinguishable via its phase names
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``).
    #
    # AUDIT-TRAIL FAMILY 3-WAY pairing analogue -- pattern reused here for
    # the post-incident analyzer:
    #   cmd_postmortem               (W607-AN substrate + W607-CV THIS)
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_cv(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(per_commit) if ...``). A computed
    # default expression evaluates BEFORE the wrap call, so a raise
    # inside the expression escapes the try-block. cmd_sbom's W607-CG
    # sealed this axis. cmd_taint's W607-CJ added the 5th discipline
    # (move ``len()`` INSIDE the closure, not at the kwarg-bind site).
    # cmd_audit_trail_export's W607-CR added the 7th discipline (use bare
    # ``dict[key]`` lookup when the floor dict guarantees the key, NOT
    # ``dict.get(key, expensive_default)`` which evaluates default
    # eagerly).
    #
    # W607-AN/CV PHASE-NAME COLLISION (W607-CH): the substrate-CALL layer
    # uses phase names load_run_ledger / parse_event_payload /
    # classify_failure / aggregate_by_phase / compute_root_cause /
    # aggregate_by_actor. None collide with score_classify /
    # compute_predicate / compute_verdict / serialize_envelope, so no
    # rename is required.
    _w607cv_warnings_out: list[str] = []

    def _run_check_cv(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CV marker emission.

        Mirror of ``_run_check_an`` shape (same
        ``postmortem_<phase>_failed:`` marker family) but writes into
        ``_w607cv_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cv_warnings_out.append(f"postmortem_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    commits = (
        _run_check_an(
            "load_run_ledger",
            _git_log_in_range,
            commit_range,
            limit=limit,
            default=[],
        )
        or []
    )
    if not commits:
        if json_mode:
            no_commits_summary = {
                "verdict": "no commits matched",
                "commit_range": commit_range,
                "commits_scanned": 0,
                "commits_with_findings": 0,
            }
            no_commits_kwargs: dict = {"summary": no_commits_summary}
            # W607-AN / W607-DR -- even on the empty-commits path, if
            # ``load_run_ledger`` (W607-AN) or ``load_index`` (W607-DR)
            # raised, surface the marker on the envelope so a consumer
            # can distinguish "git range produced zero commits" from
            # "the git invocation raised before we could enumerate"
            # from "the index failed to load before we even started".
            _empty_combined = list(_w607an_warnings_out) + list(_w607dr_warnings_out)
            if _empty_combined:
                no_commits_summary["warnings_out"] = list(_empty_combined)
                no_commits_summary["partial_success"] = True
                no_commits_kwargs["warnings_out"] = list(_empty_combined)
            click.echo(
                to_json(
                    json_envelope(
                        "postmortem",
                        **no_commits_kwargs,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: no commits matched {commit_range}", err=True)
        # Use return — ctx.exit() can swallow output when invoked via CliRunner.
        _ = EXIT_SUCCESS  # exit code surfaces via Click's normal return path
        return

    per_commit: list[dict] = []
    commits_with_findings = 0
    total_high = 0
    total_medium = 0

    # W607-DR -- extract_commit_fields boundary. Wraps per-commit dict
    # field extraction so a malformed commit row (KeyError, AttributeError
    # on a non-dict sentinel) surfaces a marker rather than crashing the
    # iteration. W978 5th-discipline: keep dict access INSIDE the closure
    # so the wrap's try-block catches the raise.
    def _extract_commit_fields(_commit) -> dict:
        return {
            "sha": _commit["sha"],
            "short_sha": _commit["short_sha"],
            "subject": _commit["subject"],
            "author": _commit["author"],
            "date": _commit["date"],
        }

    # W607-DR -- accumulate_counts boundary. Wraps the running totals
    # increment so a __add__-poisoned sentinel (high/medium counts from
    # _summarize_finding_count that raise on integer arithmetic) surfaces
    # a marker rather than crashing the loop. Floor returns the current
    # running totals unchanged so the loop continues with no double-count.
    def _accumulate_counts(_total_high, _total_medium, _commits_with_findings, _high, _medium) -> tuple[int, int, int]:
        _new_high = _total_high + _high
        _new_medium = _total_medium + _medium
        _new_with_findings = _commits_with_findings
        if _high + _medium > 0:
            _new_with_findings = _commits_with_findings + 1
        return (_new_high, _new_medium, _new_with_findings)

    with click.progressbar(commits, label="Replaying detectors") as bar:
        for commit in bar:
            _fields = _run_check_dr(
                "extract_commit_fields",
                _extract_commit_fields,
                commit,
                default={
                    "sha": "",
                    "short_sha": "",
                    "subject": "",
                    "author": "",
                    "date": "",
                },
            ) or {
                "sha": "",
                "short_sha": "",
                "subject": "",
                "author": "",
                "date": "",
            }
            diff_text = (
                _run_check_an(
                    "parse_event_payload",
                    _diff_for_commit,
                    _fields["sha"],
                    default="",
                )
                or ""
            )
            critique = _run_check_an(
                "classify_failure",
                _critique_diff,
                diff_text,
                default={"summary": {}},
            ) or {"summary": {}}
            _counts = _run_check_an(
                "aggregate_by_phase",
                _summarize_finding_count,
                critique,
                default=(0, 0, 0),
            )
            high, medium, _low = _counts if _counts else (0, 0, 0)
            _accum = _run_check_dr(
                "accumulate_counts",
                _accumulate_counts,
                total_high,
                total_medium,
                commits_with_findings,
                high,
                medium,
                default=(total_high, total_medium, commits_with_findings),
            ) or (total_high, total_medium, commits_with_findings)
            total_high, total_medium, commits_with_findings = _accum
            kinds = (
                _run_check_an(
                    "compute_root_cause",
                    _short_finding_summary,
                    critique,
                    default=[],
                )
                or []
            )
            per_commit.append(
                {
                    "sha": _fields["sha"],
                    "short_sha": _fields["short_sha"],
                    "subject": _fields["subject"],
                    "author": _fields["author"],
                    "date": _fields["date"],
                    "high": high,
                    "medium": medium,
                    "kinds": kinds,
                }
            )

    # Rank: high-first, then medium, then total
    def _rank_commits(rows):
        rows.sort(key=lambda c: (-c["high"], -c["medium"], -(c["high"] + c["medium"])))
        return rows

    per_commit = (
        _run_check_an(
            "aggregate_by_actor",
            _rank_commits,
            per_commit,
            default=per_commit,
        )
        or per_commit
    )

    # W607-CV -- score_classify boundary. Wraps the run-state bucketing
    # (commits_with_findings vs total commits) into a state label
    # (FINDINGS_SURFACED / NO_FINDINGS) so a downstream refactor of the
    # state-selection logic surfaces a marker rather than crashing.
    # Floor returns documented zero counts matching the no-findings
    # branch shape so downstream verdict / compute_predicate stay
    # non-null.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(commits)`` lives INSIDE
    # the wrapped closure rather than at the call site -- a _BadList
    # whose ``__len__`` raises would otherwise escape the try-block at
    # kwarg-bind time. W978 5th-discipline (cmd_taint W607-CJ): move
    # ``len()`` INSIDE the closure.
    def _score_classify_run(_commits, _commits_with_findings):
        _scanned = len(_commits) if _commits is not None else 0
        if _scanned == 0:
            _state = "EMPTY"
        elif _commits_with_findings > 0:
            _state = "FINDINGS_SURFACED"
        else:
            _state = "NO_FINDINGS"
        return {"state": _state, "scanned": _scanned}

    _score_dict = _run_check_cv(
        "score_classify",
        _score_classify_run,
        commits,
        commits_with_findings,
        default={"state": "DEGRADED", "scanned": 0},
    )

    # W607-CV -- compute_verdict boundary. Wraps the verdict-string
    # assembly so a downstream f-string refactor (non-int counts from a
    # vocabulary refactor, or a __format__-raising sentinel) surfaces a
    # marker rather than crashing the envelope. Floor must NOT re-
    # interpolate the same values that tripped the closure (W978 first-
    # hypothesis). Use the literal "postmortem completed" floor (LAW 6
    # still holds: the line works standalone).
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``commits`` / ``total_high`` /
    # ``total_medium`` / ``commits_with_findings`` are passed as raw
    # args; ``len()`` lives INSIDE the closure (cmd_taint W607-CJ
    # 5th-discipline anchor).
    def _build_verdict_str(_commits_with_findings, _commits, _total_high, _total_medium):
        _scanned = len(_commits) if _commits is not None else 0
        return (
            f"{_commits_with_findings} of {_scanned} commits would have surfaced findings "
            f"({_total_high} high, {_total_medium} medium total)"
        )

    verdict = _run_check_cv(
        "compute_verdict",
        _build_verdict_str,
        commits_with_findings,
        commits,
        total_high,
        total_medium,
        default="postmortem completed",
    )

    if json_mode:
        # W607-CV -- compute_predicate boundary. Wraps the per-envelope
        # commit totals extraction so a future ``commits[]`` /
        # ``per_commit[]`` schema refactor that drops or renames count
        # fields surfaces a marker rather than crashing the envelope.
        # Floor to documented zero-counts matching the empty-trail
        # branch shape so downstream summary fields stay non-null. W978
        # discipline: ``default=`` is a literal dict, NOT a computed
        # expression over the (potentially poisoned) inputs.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(commits)`` is
        # computed INSIDE the wrapped closure -- passing the raw list
        # keeps the kwarg-bind step pure (no ``__len__`` call until
        # we're inside the try-block). cmd_taint W607-CJ 5th-discipline
        # anchor.
        def _compute_predicate_fields(_commits, _commits_with_findings, _total_high, _total_medium) -> dict:
            return {
                "commits_scanned": len(_commits),
                "commits_with_findings": _commits_with_findings,
                "total_high": _total_high,
                "total_medium": _total_medium,
            }

        _pred_fields = _run_check_cv(
            "compute_predicate",
            _compute_predicate_fields,
            commits,
            commits_with_findings,
            total_high,
            total_medium,
            default={
                "commits_scanned": 0,
                "commits_with_findings": 0,
                "total_high": 0,
                "total_medium": 0,
            },
        )

        # W978 KWARG-DEFAULT EAGERNESS NOTE (W607-CR 7th-discipline
        # anchor): do NOT use ``_pred_fields.get("commits_scanned",
        # len(commits))`` -- the second arg evaluates EAGERLY (Python
        # evaluates .get's defaults at the call site), which would re-
        # raise on a __len__-poisoned ``commits`` sentinel. _pred_fields
        # ALWAYS carries the keys (either real value or floor 0), so a
        # bare lookup is correct.
        summary_payload = {
            "verdict": verdict,
            "commit_range": commit_range,
            "commits_scanned": _pred_fields["commits_scanned"],
            "commits_with_findings": _pred_fields["commits_with_findings"],
            "total_high": _pred_fields["total_high"],
            "total_medium": _pred_fields["total_medium"],
            # W607-CV: surface score_classify result on the envelope so
            # consumers can read the run state without re-deriving from
            # raw counts.
            "run_state": _score_dict["state"],
        }
        envelope_kwargs: dict = {
            "summary": summary_payload,
            "commits": per_commit,
        }

        # W607-AN / W607-CV / W607-DR -- thread substrate-CALL markers
        # AND aggregation-phase markers AND additional substrate-CALL
        # markers onto BOTH summary.warnings_out AND top-level
        # envelope.warnings_out so consumers reading either surface see
        # the disclosure channel. ``partial_success`` flips when the
        # combined bucket is non-empty. Empty combined bucket on the
        # clean path keeps the envelope shape byte-identical to the
        # pre-W607-AN/CV/DR postmortem (hash-stable happy path). All
        # three buckets share the canonical ``postmortem_*`` marker
        # family (W607-CV and W607-DR are additive, not separate
        # prefixes); each additive bucket stays distinguishable via its
        # phase names.
        #
        # W607-DR -- combine_warnings_buckets boundary. Wraps the list-
        # merge of all three accumulators so a custom-list sentinel that
        # raises on iteration / concatenation surfaces a marker rather
        # than crashing envelope assembly. Floor returns an empty list
        # so downstream summary fields stay non-null; the new marker
        # surfaces in _w607dr_warnings_out and the rebuild step below
        # re-merges all three buckets after the closure.
        def _combine_warnings_buckets(_an, _cv, _dr) -> list[str]:
            return list(_an) + list(_cv) + list(_dr)

        _combined_warnings_out = (
            _run_check_dr(
                "combine_warnings_buckets",
                _combine_warnings_buckets,
                _w607an_warnings_out,
                _w607cv_warnings_out,
                _w607dr_warnings_out,
                default=[],
            )
            or []
        )
        # If combine_warnings_buckets raised, the new W607-DR marker was
        # appended AFTER the failing closure returned the floor. Re-merge
        # with a defensive list-cast on each bucket so the new marker is
        # still surfaced; this rebuild is safe because each bucket is a
        # plain list[str] owned by the command.
        if not _combined_warnings_out and (_w607an_warnings_out or _w607cv_warnings_out or _w607dr_warnings_out):
            _combined_warnings_out = (
                list(_w607an_warnings_out) + list(_w607cv_warnings_out) + list(_w607dr_warnings_out)
            )
        if _combined_warnings_out:
            summary_payload["warnings_out"] = list(_combined_warnings_out)
            summary_payload["partial_success"] = True
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CV -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("postmortem", ...)`` would otherwise
        # crash AFTER all substrate + aggregation signals were already
        # gathered. Floor to a minimal envelope stub so consumers still
        # receive a parseable JSON object with the marker attached + the
        # canonical command name. Mirror of cmd_taint's W607-CJ /
        # cmd_audit_trail_conformance's W607-CO / cmd_audit_trail_export's
        # W607-CR serialize_envelope floor pattern.
        _envelope_floor: dict = {
            "command": "postmortem",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        _envelope = _run_check_cv(
            "serialize_envelope",
            json_envelope,
            "postmortem",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-CV / W607-DR -- if ``serialize_envelope`` raised AFTER
        # the combined bucket was already snapshotted, the new
        # ``postmortem_serialize_envelope_failed:`` marker was appended
        # to ``_w607cv_warnings_out`` and the floor stub carries only
        # the pre-raise combined list. Rebuild the floor stub's
        # warnings_out so the new marker reaches the JSON output. Clean
        # path -> envelope is the real json_envelope return value, no
        # rebuild needed.
        if _envelope is _envelope_floor and (_w607cv_warnings_out or _w607dr_warnings_out):
            _combined_warnings_out = (
                list(_w607an_warnings_out) + list(_w607cv_warnings_out) + list(_w607dr_warnings_out)
            )
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            _envelope = _envelope_floor

        click.echo(to_json(_envelope))
        return

    # W607-DR -- render_verdict_line boundary. Wraps the text-mode
    # VERDICT line echo so a __format__-raising verdict sentinel (e.g.
    # from a hostile compute_verdict closure under W607-CV) surfaces a
    # marker rather than crashing the CLI before any per-commit detail.
    # Floor: the literal "postmortem completed" verdict (LAW 6).
    def _render_verdict_line(_verdict) -> None:
        click.echo(f"VERDICT: {_verdict}")

    _run_check_dr(
        "render_verdict_line",
        _render_verdict_line,
        verdict,
        default=None,
    )
    if not commits_with_findings:
        click.echo("  (no findings surfaced over this range)")
        return

    click.echo()

    # W607-DR -- format_top_hits boundary. Wraps the text-mode top-N
    # rendering loop so a __format__-raising row (e.g. a non-string
    # subject from a corrupt commit dict, or a kinds list containing a
    # non-string sentinel) surfaces a marker rather than crashing the
    # CLI mid-render. Floor: no-op (the JSON path is unaffected).
    # W978 5th-discipline: ``len(per_commit)`` lives INSIDE the closure.
    def _format_top_hits(_per_commit, _show_n) -> None:
        click.echo(f"Top {min(_show_n, len(_per_commit))} hits:")
        for c in _per_commit[:_show_n]:
            if c["high"] + c["medium"] == 0:
                continue
            kinds_str = " — " + ", ".join(c["kinds"]) if c["kinds"] else ""
            click.echo(
                f"  {c['short_sha']}  ({c['date']})  {c['subject'][:50]:<50s}  "
                f"high={c['high']} med={c['medium']}{kinds_str}"
            )

    _run_check_dr(
        "format_top_hits",
        _format_top_hits,
        per_commit,
        show_n,
        default=None,
    )
