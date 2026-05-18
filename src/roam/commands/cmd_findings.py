"""roam findings — query the central findings registry.

Cross-detector dedup, suppression management, and the SARIF-emit substrate
all live in the ``findings`` table (the A4 registry — see
``roam.db.findings``). Each detector continues to write to its
detector-specific table (``math_signals``, ``taint_findings``,
``clone_pairs`` …) and ALSO emits a row here. This command is the
read-side surface for that denormalised cross-detector view.

Three subcommands:

  - ``roam findings list``               — paginated rows, optionally filtered
  - ``roam findings show <finding_id>``  — single record
  - ``roam findings count``              — per-detector totals

The registry starts empty on a fresh project: per-detector emit-site
migration is a separate per-wave effort, so until detectors are migrated
the registry will be empty on most repos. The command handles that
empty state explicitly — never emits empty stdout (Pattern 1 from the
CLAUDE.md anti-pattern catalogue).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because findings outputs are invocation-scoped registry-query
rows — not per-location violations (per-detector ``--sarif`` carriers
upstream of the registry handle SARIF emission). See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.db.findings import (
    count_by_detector,
    get_finding,
    known_detector_names,
    list_findings,
)
from roam.output.formatter import format_table, json_envelope, to_json
from roam.output.structured_unknowns import (
    structured_unknown_filter,
    to_summary_payload,
)

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="findings",
    category="exploration",
    summary="Query the central findings registry across all detectors.",
    inputs=[],
    outputs=["findings"],
    examples=[
        "roam findings list",
        "roam findings list --detector clones",
        "roam findings show clones:sym:abcd",
        "roam findings count",
    ],
    tags=["findings", "registry", "detectors"],
    ai_safe=True,
    requires_index=True,
    # W93 follow-up: the clones detector now emits to the registry, so
    # ``roam findings`` returns real data on any repo where structural
    # duplicates exist. Other detectors (taint, math, smells, …) still
    # to migrate — but the read-side surface is stable enough to expose.
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.group("findings")
@click.pass_context
def findings(ctx):
    """Query the central findings registry (cross-detector view).

    The ``findings`` table denormalises every detector's output behind a
    single schema. Use the subcommands below to list, filter, or count
    rows without needing to know which detector-specific table owns them.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# findings list
# ---------------------------------------------------------------------------


@findings.command("list")
@click.option("--detector", help="Filter by source_detector (exact match).")
@click.option(
    "--subject-kind",
    help="Filter by subject_kind (e.g. symbol / file / edge / commit).",
)
@click.option(
    "--subject-id",
    type=int,
    help="Filter by subject_id (numeric). Typically combined with --subject-kind.",
)
@click.option("--limit", default=100, show_default=True, type=int, help="Cap rows returned.")
@click.pass_context
def findings_list(ctx, detector, subject_kind, subject_id, limit):
    """List findings, optionally filtered by detector or subject.

    Examples:

    \b
      roam findings list
      roam findings list --detector clones
      roam findings list --subject-kind symbol --subject-id 42
      roam --json findings list --limit 500
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-C: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the findings-registry query path. ``db/findings.py``
    # is W604 fail-loud-correct (no try/except inside the substrate;
    # ``sqlite3.OperationalError`` propagates loudly). The consumer-side
    # disclosure shape therefore lives at the **outer-guard boundary**:
    # any uncaught exception from the registry query (substrate
    # corruption, schema drift, locked DB, malformed migration) emits
    # the canonical marker
    # ``findings_query_failed:<exc_class>:<detail>`` and the envelope
    # surfaces with empty findings + ``partial_success=True``. Mirrors
    # the cmd_retrieve W607-B / cmd_search_semantic W607-A idiom.
    # Empty bucket → byte-identical envelope (hash-stable per
    # ``json_envelope`` W817 always-emit discipline).
    warnings_out: list[str] = []

    with open_db(readonly=True) as conn:
        # W1063 (sibling of W1057): when ``--detector`` is supplied,
        # validate against the registry's known-detectors set BEFORE
        # querying. Unknown names previously fell into the generic
        # "registry empty or filters too narrow" branch — indistinguishable
        # from a clean codebase. Pattern-1D silent-success on degraded
        # filter resolution. W1080: delegated to the shared
        # ``structured_unknown_filter`` helper.
        #
        # W1259 sibling-fix: ``count_by_detector(conn)`` alone returns ONLY
        # the detectors that have ALREADY emitted rows on THIS project,
        # which was rejecting ``--detector taint`` (etc.) as "unknown"
        # even though ``taint`` is a perfectly valid detector that just
        # hadn't been invoked yet. Validate against the canonical vocabulary
        # UNION live counts (see ``known_detector_names``), then disambiguate
        # the two states:
        #
        #   * ``unknown_detector``   — truly not in the canonical vocabulary
        #   * ``not_yet_emitted``    — canonical detector, 0 rows on this project
        #
        # The agent now knows the difference between "fix the typo" and
        # "run the detector first" instead of being told both are equally
        # unknown.
        try:
            live_counts = count_by_detector(conn)
            full_vocabulary = known_detector_names(conn)
        except Exception as exc:
            # W607-C outer-guard: registry query raised before we could
            # build the detector vocabulary. Disclose loudly via the
            # canonical ``findings_query_failed:<exc_class>:<detail>``
            # marker (mirrors cmd_retrieve W607-B
            # ``retrieve_pipeline_failed:...`` outer-guard idiom) and
            # fall back to empty floors so the rest of the envelope
            # still emits consistent fields. Without this, the agent
            # got a Click traceback and no structured signal.
            warnings_out.append(f"findings_query_failed:{type(exc).__name__}:{exc}")
            live_counts = {}
            full_vocabulary = frozenset()
        frag = (
            structured_unknown_filter(
                requested=detector,
                known=full_vocabulary,
                state="unknown_detector",
                requested_field="requested_detector",
                known_field="known_detectors",
                fact_anchor="detectors",
                did_you_mean_omit_when_empty=True,
            )
            if detector is not None
            else None
        )
        # W1259: canonical-but-not-yet-emitted is a distinct second state.
        # The structured_unknown_filter call above only fires when ``detector``
        # is NOT in the union — so reaching this branch means ``detector``
        # IS canonical. If it has 0 rows we still want to emit a disclosure
        # envelope (different state, different verdict) rather than dropping
        # into the generic empty-result branch.
        canonical_empty = detector is not None and frag is None and detector not in live_counts
        if frag is not None:
            # W1082: ``did_you_mean_omit_when_empty=True`` makes the helper
            # omit the field when empty so the splice stays unconditional.
            # The verdict still deliberately does NOT carry the
            # "Did you mean: …?" suffix for findings (it is emitted only
            # as a separate ``click.echo`` text line + as a summary
            # ``did_you_mean`` field) — leave ``frag['verdict_suffix']``
            # unused here.
            verdict_unknown = f"unknown detector {detector!r} ({len(full_vocabulary)} known)"
            close_matches = frag.get("did_you_mean", [])
            if json_mode:
                # W1083: ``to_summary_payload`` extracts the splice subset
                # (``state``, ``partial_success``, ``requested_detector``,
                # ``known_detectors``, and ``did_you_mean`` when the helper
                # carried it) so callsite-specific fields (``total_findings``,
                # ``filters``) compose without hand-stamping the shared keys.
                summary_payload: dict[str, object] = {
                    "verdict": verdict_unknown,
                    **to_summary_payload(frag),
                    "total_findings": 0,
                    "filters": {
                        "detector": detector,
                        "subject_kind": subject_kind,
                        "subject_id": subject_id,
                    },
                }
                if warnings_out:
                    # W607-C Pattern-2 disclosure: surface markers AND
                    # flip partial_success so consumers can distinguish
                    # "clean unknown-detector branch" from "registry
                    # query failed mid-build of the vocabulary".
                    summary_payload["warnings_out"] = list(warnings_out)
                    summary_payload["partial_success"] = True
                click.echo(
                    to_json(
                        json_envelope(
                            "findings-list",
                            summary=summary_payload,
                            budget=token_budget,
                            findings=[],
                            agent_contract={
                                # LAW 4: helper emits facts anchored on
                                # ``detectors`` (in the formatter set).
                                "facts": frag["facts"],
                                "next_commands": ["roam findings count"],
                            },
                            **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict_unknown}")
            if close_matches:
                quoted = " or ".join(f"'{m}'" for m in close_matches)
                click.echo(f"Did you mean: {quoted}?")
            if full_vocabulary:
                click.echo("Known detectors: " + ", ".join(sorted(full_vocabulary)))
            else:
                click.echo("Registry is empty (no detectors have emitted yet).")
            return

        # W1259 second state: canonical detector with 0 rows on this project.
        if canonical_empty:
            verdict_not_emitted = (
                f"detector {detector!r} is canonical but has not emitted "
                f"findings on this project yet (run `roam {detector}` first)"
            )
            if json_mode:
                summary_not_emitted: dict[str, object] = {
                    "verdict": verdict_not_emitted,
                    "partial_success": True,
                    "state": "not_yet_emitted",
                    "requested_detector": detector,
                    "known_detectors": sorted(full_vocabulary),
                    "total_findings": 0,
                    "filters": {
                        "detector": detector,
                        "subject_kind": subject_kind,
                        "subject_id": subject_id,
                    },
                }
                if warnings_out:
                    # W607-C disclosure — partial_success is already
                    # True for this state, but the marker bucket must
                    # still surface so consumers see the substrate
                    # signal beyond the not_yet_emitted state.
                    summary_not_emitted["warnings_out"] = list(warnings_out)
                click.echo(
                    to_json(
                        json_envelope(
                            "findings-list",
                            summary=summary_not_emitted,
                            budget=token_budget,
                            findings=[],
                            agent_contract={
                                "facts": [
                                    f"0 {detector} findings",
                                    f"{len(full_vocabulary)} canonical detectors",
                                ],
                                "next_commands": [
                                    f"roam {detector}",
                                    "roam findings count",
                                ],
                            },
                            **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict_not_emitted}")
            click.echo(
                f"Run `roam {detector}` to populate the registry, then re-run "
                f"`roam findings list --detector {detector}`."
            )
            return

        try:
            rows = list_findings(
                conn,
                detector=detector,
                subject_kind=subject_kind,
                subject_id=subject_id,
                limit=limit,
            )
        except Exception as exc:
            # W607-C outer-guard: ``list_findings`` raised before
            # returning rows. Same canonical marker family as the
            # vocabulary guard above. Fall back to empty rows so the
            # envelope still emits the populated-vs-empty-state contract
            # cleanly.
            warnings_out.append(f"findings_query_failed:{type(exc).__name__}:{exc}")
            rows = []

    # Empty state: the registry may be empty either because no detector
    # has migrated yet (typical today) OR because the filters excluded
    # every row. The verdict names both possibilities so the agent can
    # tell which.
    if not rows:
        verdict_empty = "no findings registered (registry empty or filters too narrow)"
        if json_mode:
            summary_empty: dict[str, object] = {
                "verdict": verdict_empty,
                "partial_success": False,
                "state": "empty",
                "total_findings": 0,
                "filters": {
                    "detector": detector,
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                },
            }
            if warnings_out:
                # W607-C disclosure: registry query raised (e.g. list_findings
                # OperationalError) but the empty-floor fallback let us still
                # produce a structured envelope. Flip partial_success +
                # surface markers so the agent knows the empty rows are NOT
                # the same as a legitimately empty registry.
                summary_empty["warnings_out"] = list(warnings_out)
                summary_empty["partial_success"] = True
            click.echo(
                to_json(
                    json_envelope(
                        "findings-list",
                        summary=summary_empty,
                        budget=token_budget,
                        findings=[],
                        agent_contract={
                            "facts": ["0 findings registered"],
                            "next_commands": ["roam findings count"],
                        },
                        **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict_empty}")
        return

    # Populated path.
    detectors_present = sorted({r["source_detector"] for r in rows})
    verdict = f"{len(rows)} findings from {len(detectors_present)} detectors"

    if json_mode:
        summary_populated: dict[str, object] = {
            "verdict": verdict,
            "partial_success": False,
            "state": "populated",
            "total_findings": len(rows),
            "detectors": detectors_present,
            "filters": {
                "detector": detector,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
            },
        }
        if warnings_out:
            # W607-C disclosure: extremely rare on the populated branch
            # (we already passed list_findings successfully) — but if a
            # partial substrate fault was disclosed during vocabulary
            # build, surface it here so the agent knows results may be
            # incomplete relative to the canonical detector set.
            summary_populated["warnings_out"] = list(warnings_out)
            summary_populated["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "findings-list",
                    summary=summary_populated,
                    budget=token_budget,
                    findings=rows,
                    agent_contract={
                        "facts": [f"{len(rows)} findings"],
                        "next_commands": [f"roam findings show {rows[0]['finding_id_str']}"],
                    },
                    **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                )
            )
        )
        return

    # Text mode — cap at 50 rows for readability; --json users get the
    # full ``limit`` set (default 100). Truncation is signalled inline.
    click.echo(f"VERDICT: {verdict}\n")
    table_rows: list[list[str]] = []
    for r in rows[:50]:
        claim_preview = (r["claim"] or "")[:60]
        table_rows.append(
            [
                r["source_detector"],
                r["subject_kind"],
                str(r["subject_id"]) if r["subject_id"] is not None else "-",
                r["confidence"],
                claim_preview,
            ]
        )
    click.echo(
        format_table(
            ["Detector", "Subject", "Subj.ID", "Confidence", "Claim"],
            table_rows,
            budget=0,
        )
    )
    if len(rows) > 50:
        click.echo(f"\n... {len(rows) - 50} more (use --json for the full list).")


# ---------------------------------------------------------------------------
# findings show
# ---------------------------------------------------------------------------


@findings.command("show")
@click.argument("finding_id_str")
@click.pass_context
def findings_show(ctx, finding_id_str):
    """Show full detail for a single finding by its stable id."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-C: Pattern-2 consumer-layer wiring — outer-guard around the
    # registry ``get_finding`` query (mirrors the ``findings_list``
    # idiom). Empty bucket → byte-identical envelope (hash-stable).
    warnings_out: list[str] = []

    with open_db(readonly=True) as conn:
        try:
            record = get_finding(conn, finding_id_str)
        except Exception as exc:
            warnings_out.append(f"findings_query_failed:{type(exc).__name__}:{exc}")
            record = None

    if record is None:
        # W607-C: when the registry query raised, ``record is None``
        # could be either "no row matched" or "substrate failure".
        # The verdict + state stays unknown_finding (consumer-visible
        # behaviour) but the warnings_out bucket disambiguates.
        verdict_missing = f"finding id {finding_id_str!r} not found (run `roam findings list` to discover valid ids)"
        if json_mode:
            summary_missing: dict[str, object] = {
                "verdict": verdict_missing,
                "partial_success": True,
                "state": "unknown_finding",
                "error": "finding_not_found",
                "total_findings": 0,
            }
            if warnings_out:
                summary_missing["warnings_out"] = list(warnings_out)
            click.echo(
                to_json(
                    json_envelope(
                        "findings-show",
                        summary=summary_missing,
                        budget=token_budget,
                        finding=None,
                        agent_contract={
                            "facts": [f"no finding with id {finding_id_str}"],
                            "next_commands": ["roam findings list"],
                        },
                        **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict_missing}")
        ctx.exit(2)

    verdict = f"finding {record['finding_id_str']} from {record['source_detector']} ({record['confidence']})"

    if json_mode:
        summary_found: dict[str, object] = {
            "verdict": verdict,
            "partial_success": False,
            "state": "found",
            "total_findings": 1,
        }
        if warnings_out:
            # Unreachable in practice (we have a record → no exception
            # was raised) but kept for shape symmetry across the three
            # subcommand envelope-emit sites.
            summary_found["warnings_out"] = list(warnings_out)
            summary_found["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "findings-show",
                    summary=summary_found,
                    budget=token_budget,
                    finding=record,
                    **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  id:              {record['id']}")
    click.echo(f"  finding_id_str:  {record['finding_id_str']}")
    click.echo(f"  source_detector: {record['source_detector']}")
    click.echo(f"  source_version:  {record['source_version'] or '-'}")
    click.echo(f"  subject_kind:    {record['subject_kind']}")
    click.echo(f"  subject_id:      {record['subject_id'] if record['subject_id'] is not None else '-'}")
    click.echo(f"  confidence:      {record['confidence']}")
    click.echo(f"  supersedes_id:   {record['supersedes_id'] if record['supersedes_id'] is not None else '-'}")
    click.echo(f"  created_at:      {record['created_at']}")
    click.echo("")
    click.echo(f"  claim:           {record['claim']}")
    click.echo("")
    click.echo("  evidence_json:")
    click.echo(f"    {record['evidence_json']}")
    if record["suppressions_json"] not in (None, "", "[]"):
        click.echo("")
        click.echo(f"  suppressions:    {record['suppressions_json']}")


# ---------------------------------------------------------------------------
# findings count
# ---------------------------------------------------------------------------


@findings.command("count")
@click.pass_context
def findings_count(ctx):
    """Per-detector finding counts.

    Useful for spotting which detectors have migrated to the central
    registry vs which are still only emitting to their detector-specific
    tables.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-C: Pattern-2 consumer-layer wiring — outer-guard around the
    # registry ``count_by_detector`` query (mirrors ``findings_list`` /
    # ``findings_show``). Empty bucket → byte-identical envelope
    # (hash-stable).
    warnings_out: list[str] = []

    with open_db(readonly=True) as conn:
        try:
            counts = count_by_detector(conn)
        except Exception as exc:
            warnings_out.append(f"findings_query_failed:{type(exc).__name__}:{exc}")
            counts = {}

    total = sum(counts.values())
    detector_count = len(counts)

    if total == 0:
        verdict_empty = "no findings registered (no detector has emitted to the registry yet)"
        if json_mode:
            summary_empty_count: dict[str, object] = {
                "verdict": verdict_empty,
                "partial_success": False,
                "state": "empty",
                "total_findings": 0,
                "total_detectors": 0,
            }
            if warnings_out:
                # W607-C disclosure: registry query raised but the
                # empty-floor fallback produced a structured envelope.
                # Flip partial_success so the agent knows the empty
                # counts are NOT the same as a legitimately empty
                # registry.
                summary_empty_count["warnings_out"] = list(warnings_out)
                summary_empty_count["partial_success"] = True
            click.echo(
                to_json(
                    json_envelope(
                        "findings-count",
                        summary=summary_empty_count,
                        budget=token_budget,
                        counts={},
                        agent_contract={
                            "facts": ["0 findings registered"],
                            "next_commands": ["roam findings list"],
                        },
                        **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict_empty}")
        return

    verdict = f"{total} findings across {detector_count} detectors"

    if json_mode:
        summary_populated_count: dict[str, object] = {
            "verdict": verdict,
            "partial_success": False,
            "state": "populated",
            "total_findings": total,
            "total_detectors": detector_count,
        }
        if warnings_out:
            # Unreachable on the populated branch (we have counts → no
            # exception raised) but kept for shape symmetry across the
            # three subcommand envelope-emit sites.
            summary_populated_count["warnings_out"] = list(warnings_out)
            summary_populated_count["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "findings-count",
                    summary=summary_populated_count,
                    budget=token_budget,
                    counts=counts,
                    agent_contract={
                        "facts": [
                            f"{total} findings",
                            f"{detector_count} detectors",
                        ],
                        "next_commands": ["roam findings list"],
                    },
                    **({"warnings_out": list(warnings_out)} if warnings_out else {}),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}\n")
    table_rows = [[det, str(n)] for det, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    click.echo(format_table(["Detector", "Findings"], table_rows, budget=0))
