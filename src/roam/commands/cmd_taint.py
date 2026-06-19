"""roam taint — graph-reach taint analysis with OpenVEX justifications.

Ships in 2 weeks (per the v12 brainstorm), not a year. The 80/20 cut
between Semgrep CE (intra-procedural only) and CodeQL Pro (paid full
abstract interpretation): a YAML-rule driven path BFS over the
existing edges table with sanitizer-stop nodes.

Examples
--------

    roam taint
    roam taint --rules-dir src/roam/security/taint_rules
    roam taint --ci   # exit 5 on findings (gateable in CI)
    roam --json taint --max-hops 8
    roam taint --persist  # mirror findings into the central registry
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.db.edge_kinds import call_or_ref_in_clause
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import json_envelope, to_json
from roam.security.taint_engine import (
    OPENVEX_JUSTIFICATIONS,
    OPENVEX_STATUSES,
    run_taint,
    vex_justification_for,
)
from roam.security.taint_rules_lint import capture_qualified_only_lint

# W122: taint is the fifth detector migrating onto the central findings
# registry (after `clones` W95, `dead` W99, `complexity` W102, `n1`
# W110). The shape mirrors those — a stable detector version stamp and
# a deterministic ``finding_id_str`` so re-runs upsert instead of
# duplicating rows. Bump this when the confidence-derivation rule in
# :func:`_taint_confidence_tier` or the BFS / co-call predicates in
# :mod:`roam.security.taint_engine` change meaningfully — both shape
# the registry row's ``claim`` / ``confidence``.
TAINT_DETECTOR_VERSION: str = "1.0.0"

# W489-A: hoisted to ``roam.security.taint_rules_lint`` so cmd_cga (and
# any future command loading taint rules out-of-band) can reuse the
# same capture path. The local name is kept as a thin alias to preserve
# any test or downstream importer expecting it inside cmd_taint.
_w489_a_capture_qualified_only_lint = capture_qualified_only_lint


def _taint_finding_id(rule_id: str, source_id: int, sink_id: int, path_ids: list[int]) -> str:
    """Stable, deterministic finding id for one taint flow.

    The (rule_id, source_id, sink_id, path_signature) tuple uniquely
    identifies one source -> sink flow under a given rule. Hashing the
    full path id sequence (not just endpoints) lets two distinct
    intermediate paths between the same endpoints register as separate
    findings — agents reviewing flow A vs flow B need both rows. The
    intraprocedural co-call shape passes a 3-element path
    ``[source, enclosing, sink]`` so its id stays distinct from any
    forward-BFS path that happens to share the same endpoints.
    """
    path_signature = "-".join(str(p) for p in path_ids)
    raw = f"{rule_id}|{source_id}|{sink_id}|{path_signature}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"taint:{rule_id}:{digest}"


def _taint_confidence_tier(finding_dump: dict) -> str:
    """Map one taint finding-dump dict to a registry confidence tier.

    The taint engine produces three flow shapes; each gets a distinct
    registry tier per the W122 brief:

    - **Forward BFS path** (real edges from source -> sink, possibly with
      a sanitizer on the path): the engine proved an edge-by-edge call
      chain through the indexed graph. Tier: ``static_analysis``.

    - **Intraprocedural co-call** (the enclosing function calls BOTH
      the source AND the sink, but no forward edge connects them; the
      ``y = source(); sink(y)`` shape that Pass 2 of run_taint catches):
      the pattern is graph-evident (two real edges exist from the
      enclosing fn) but the engine did NOT prove dataflow between them.
      Tier: ``structural``.

    - **Truncated forward path** (BFS hit ``max_hops`` or the per-node
      fan-out cap): the returned path is still real edge-by-edge, just
      one of many candidate paths the engine couldn't enumerate.
      Tier: ``static_analysis`` (the path itself is concrete; the cap
      affects search exhaustiveness, not flow validity).

    Sanitizer presence does NOT downgrade the registry tier — a
    sanitized flow is still a proven dataflow; the OpenVEX layer cites
    the sanitizer separately via ``inline_mitigations_already_exist``.
    """
    from roam.db.findings import CONFIDENCE_STATIC_ANALYSIS, CONFIDENCE_STRUCTURAL

    if finding_dump.get("flow_shape") == "co_call":
        return CONFIDENCE_STRUCTURAL
    return CONFIDENCE_STATIC_ANALYSIS


def _classify_flow_shape(conn, path_ids: list[int], path_truncated: bool) -> str:
    """Infer whether a finding came from forward BFS or intraprocedural co-call.

    The taint engine emits two flow shapes but doesn't tag findings:

    - **Forward BFS**: a real call-graph path source -> ... -> sink, with
      a directed edge (path[i] -> path[i+1]) between every consecutive
      pair. Truncated forward paths still satisfy this — the cap affects
      search exhaustiveness, not edge validity on the path returned.

    - **Intraprocedural co-call**: a length-3 path ``[source, enclosing,
      sink]`` where the enclosing function calls BOTH source and sink,
      but no forward edge connects source -> enclosing -> sink in that
      direction. The middle node has edges TO source and TO sink, not
      FROM them.

    We recover the shape by querying the edges table for every
    consecutive pair: if every adjacent pair (path[i] -> path[i+1]) is a
    forward call/reference edge, it's forward BFS; otherwise co-call.
    A truncated forward path is still forward_bfs (its returned path
    edges are real; truncation only means OTHER candidate paths weren't
    enumerated).

    Returns ``"forward_bfs"`` or ``"co_call"``.
    """
    if len(path_ids) < 2:
        # Defensive — a 1-node path shouldn't normally reach here. Treat
        # as forward_bfs (the safer default; static_analysis tier).
        return "forward_bfs"
    # Check every consecutive pair has a directed call/reference edge.
    for src, tgt in zip(path_ids, path_ids[1:]):
        row = conn.execute(
            f"SELECT 1 FROM edges WHERE source_id = ? AND target_id = ? AND {call_or_ref_in_clause()} LIMIT 1",
            (src, tgt),
        ).fetchone()
        if row is None:
            # Missing forward edge between consecutive nodes -> co-call
            # signature (enclosing calls both but no source->sink chain).
            return "co_call"
    return "forward_bfs"


def _build_emit_entries(conn, findings, findings_dump: list[dict]) -> list[dict]:
    """Augment the public ``findings_dump`` with registry-emit fields.

    Each emit entry adds: ``flow_shape`` (forward_bfs | co_call),
    ``source_id``, ``sink_id``, ``sink_subject_id`` (= sink_id), and
    ``path_ids``. The public ``findings_dump`` stays untouched — these
    fields are only consumed by :func:`_emit_taint_findings` and never
    shipped in the public JSON envelope.

    ``findings`` and ``findings_dump`` are zipped 1:1; both lists are
    built from the same ``run_taint`` output and stay aligned.
    """
    out: list[dict] = []
    for taint_finding, dump in zip(findings, findings_dump):
        path_syms = taint_finding.path_symbols or []
        path_ids = [int(p["id"]) for p in path_syms if p.get("id") is not None]
        source_sym = taint_finding.source_symbol or {}
        sink_sym = taint_finding.sink_symbol or {}
        source_id = source_sym.get("id")
        sink_id = sink_sym.get("id")
        flow_shape = _classify_flow_shape(conn, path_ids, taint_finding.path_truncated)
        entry = dict(dump)
        entry["flow_shape"] = flow_shape
        entry["source_id"] = int(source_id) if source_id is not None else None
        entry["sink_id"] = int(sink_id) if sink_id is not None else None
        entry["sink_subject_id"] = int(sink_id) if sink_id is not None else None
        entry["path_ids"] = path_ids
        entry["path_truncated"] = bool(taint_finding.path_truncated)
        out.append(entry)
    return out


def _emit_taint_findings(conn, findings_dump: list[dict], source_version: str) -> None:
    """Emit one ``FindingRecord`` per taint finding into the registry.

    Each entry in ``findings_dump`` is the dict shape produced by the
    detector's JSON build path, augmented with ``flow_shape``
    (``"forward_bfs"`` | ``"co_call"``), ``source_id``, ``sink_id``,
    ``sink_subject_id``, and ``path_ids``. The dict shape is the
    contract — emit doesn't peek at the raw TaintFinding objects.

    Subject is the **sink** symbol (the call site where the
    vulnerability manifests — most actionable for an agent deciding
    where to insert a sanitizer or where to escape input). Forward-BFS
    findings store the full path through ``evidence_json``; consumers
    that need the source-side symbol JOIN on the evidence payload.

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard taint command.
    """
    # Local import keeps the cost out of the readonly read-only path —
    # callers without --persist never reach here, so the import only
    # runs when we're actually writing.
    from roam.db.findings import FindingRecord, emit_finding

    for f in findings_dump:
        source_id = f.get("source_id")
        sink_id = f.get("sink_id")
        if source_id is None or sink_id is None:
            continue
        path_ids = f.get("path_ids") or []
        rule_id = f.get("rule_id") or "unknown"
        finding_id = _taint_finding_id(rule_id, int(source_id), int(sink_id), path_ids)
        # Subject is the sink (where to fix). subject_id may be None if
        # the sink symbol didn't resolve back to a symbols row — the
        # registry permits NULL subjects.
        subject_id = f.get("sink_subject_id")
        src = f.get("source") or {}
        sink = f.get("sink") or {}
        evidence = {
            "rule_id": rule_id,
            "severity": f.get("severity"),
            "cwe": f.get("cwe"),
            # W492: OWASP Top 10 category copied from the rule. Empty
            # string when the rule did not declare one — consumers
            # filtering by owasp_top10 should treat "" as "not tagged"
            # rather than dropping the row.
            "owasp_top10": f.get("owasp_top10", ""),
            "flow_shape": f.get("flow_shape"),
            "source": {
                "name": src.get("name"),
                "file": src.get("file"),
                "line": src.get("line"),
            },
            "sink": {
                "name": sink.get("name"),
                "file": sink.get("file"),
                "line": sink.get("line"),
            },
            "path_length": f.get("path_length"),
            "path": f.get("path"),
            "sanitizer_in_path": f.get("sanitizer_in_path", False),
            "path_truncated": f.get("path_truncated", False),
            "vex_justification": f.get("vex_justification"),
        }
        sanitized_suffix = " (sanitized)" if f.get("sanitizer_in_path") else ""
        claim = (
            f"Taint flow [{rule_id}] {src.get('name')} -> {sink.get('name')} at "
            f"{sink.get('file')}:{sink.get('line')} ({f.get('flow_shape')}, "
            f"{f.get('path_length')} hop(s)){sanitized_suffix}"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol",
                subject_id=int(subject_id) if subject_id is not None else None,
                claim=claim,
                evidence_json=json.dumps(evidence, sort_keys=True),
                confidence=_taint_confidence_tier(f),
                source_detector="taint",
                source_version=source_version,
            ),
        )


# R22 — confidence classifier for taint findings.
#
# We map the taint-engine's existing severity grading + sanitizer
# presence + path length into a confidence label:
#
#   high   — severity "error" AND no sanitizer on the path; the rule
#            considers this a direct source→sink reach with no
#            mitigation.
#   medium — severity "warning", OR severity "error" with a sanitizer
#            on the path (sanitiser presence downgrades — the
#            attestation layer can still cite the finding as mitigated).
#   low    — anything else / inferred indirect paths.
def _taint_classify(finding: dict) -> tuple[str, str]:
    """Map a taint finding to a (confidence, reason) tuple."""
    severity = (finding.get("severity") or "").lower()
    sanitized = bool(finding.get("sanitizer_in_path"))
    path_length = finding.get("path_length", 0) or 0
    if severity == "error" and not sanitized:
        return "high", f"direct source→sink reach, no sanitiser; path_length={path_length}"
    if severity == "error" and sanitized:
        return "medium", f"source→sink reach but sanitiser on path (mitigated); path_length={path_length}"
    if severity == "warning":
        return "medium", f"severity=warning; sanitiser={sanitized}; path_length={path_length}"
    return "low", f"severity={severity or 'unknown'}; path_length={path_length}"


def _default_rules_dir() -> Path:
    """Locate the bundled taint-rules directory.

    W643: prefer ``importlib.resources`` (mirrors W554/W570/W577/W624
    discipline) so wheel installs resolve the directory through the
    canonical wheel-safe lookup instead of the brittle
    ``Path(__file__).parents[N]`` walk. Falls back to the source-tree
    location for editable installs / dev checkouts.
    """
    try:
        from importlib.resources import files

        package_resource = files("roam.security.taint_rules")
        # W668: previously wrapped this in ``as_file(...)`` and captured
        # the result OUTSIDE the ``with`` block — the W643 anti-pattern
        # that originally manifested on THIS package before W643's
        # ``__init__.py`` fix. The W664 lint now structurally enforces
        # ``__init__.py`` on every package-data directory, so
        # ``files()`` returns a concrete on-disk Path and ``as_file()``
        # is a no-op. Skip ``as_file()`` and normalise directly.
        resolved = Path(str(package_resource))
        if resolved.is_dir():
            return resolved
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass

    # Source-checkout fallback — pre-W643 layout.
    return Path(__file__).resolve().parents[1] / "security" / "taint_rules"


@roam_capability(
    name="taint",
    category="reports",
    summary="Reach-analysis from rule sources to sinks over the indexed edges",
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
@click.command("taint")
@click.option(
    "--rules-dir",
    "rules_dir",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help=(
        "Directory of YAML rule files (default: built-in pack at "
        "src/roam/security/taint_rules/). Each file declares one rule "
        "with sources / sinks / sanitizers / cwe / severity."
    ),
)
@click.option(
    "--max-hops",
    type=int,
    default=6,
    show_default=True,
    help="Cap on BFS depth from source → sink. Tune for large graphs.",
)
@click.option(
    "--ci",
    "ci_mode",
    is_flag=True,
    help="Exit 5 on any high-severity finding (CI gate).",
)
@click.option(
    "--rule",
    "rule_filter",
    type=str,
    default=None,
    help="Only run rules whose id contains this substring.",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each taint finding into the central findings registry "
        "(findings table) for downstream consumers (roam findings, "
        "central SARIF emit). Detector-specific output is unchanged."
    ),
)
@click.option(
    "--rules-pack",
    "rules_pack",
    type=click.Choice(
        [
            "sqli",
            "xss",
            "ssrf",
            "ssti",
            "path-traversal",
            "command-injection",
            "deserialization",
            "open-redirect",
            "urllib",
            "socketio",
            "fileupload",
        ],
        case_sensitive=False,
    ),
    default=None,
    help=(
        "Run a single starter pack: sqli, xss, ssrf, ssti (python "
        "render_template_string / jinja2), path-traversal, "
        "command-injection, deserialization, open-redirect (urllib.parse), "
        "socketio (python-socketio remote input), or fileupload "
        "(Java FileItem / Part path traversal). Sugar over --rule for "
        "discoverability. Combinable with --rules-dir to filter inside "
        "a custom pack directory."
    ),
)
@click.pass_context
def taint_command(ctx, rules_dir, max_hops, ci_mode, rule_filter, rules_pack, persist):
    """Reach-analysis from rule sources to sinks over the indexed edges.

    Each finding lists the source, the sink, the path that connects
    them, and a flag indicating whether a sanitizer was on the path.
    Sanitized findings are kept (not dropped) so the attestation layer
    can later cite ``inline_mitigations_already_exist`` per OpenVEX.

    \b
    Examples:
      roam taint
      roam taint --rules-pack sqli
      roam taint --max-hops 8 --ci
      roam --sarif taint > taint.sarif

    See also ``vuln-reach`` (CVE reachability), ``rules`` (rule
    pack management), and ``cga`` (the audit-grade attestation
    that cites taint findings).
    """
    # W107/W120 composition: global `roam --ci` also enables the local
    # exit-5 gate. LAW 11: explicit local `--ci` still wins (no-op when
    # already True).
    if not ci_mode and ctx.obj and ctx.obj.get("ci_mode"):
        ci_mode = True
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    # W607-AY -- substrate-boundary plumbing for the taint dataflow-reach
    # leg of the security-reachability triad (cmd_vuln_reach W607-AU is
    # the call-graph reachability sibling, cmd_supply_chain W607-AK is
    # the supply-chain projection sibling). Prior to W607-AY a raise
    # inside any of the substrate boundaries -- capture_qualified_only_lint
    # (load_rules + W454/W479 lint capture), query_symbol_count
    # (corpus-empty probe), run_taint (the BFS source->sink propagation),
    # build_emit_entries (flow-shape classifier), emit_findings (registry
    # write), wrap_findings (confidence classifier), taint_to_sarif
    # (SARIF projection), serialize_envelope (on-text JSON serialization)
    # -- crashed the whole taint invocation wholesale. Each is wrapped
    # via ``_run_check_ay`` so a raise becomes a structured
    # ``taint_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607ay_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # Marker prefix discipline: every W607-AY substrate marker uses the
    # canonical ``taint_<phase>_failed:<exc_class>:<detail>`` shape.
    # cmd_taint has NO pre-existing warnings_out channel -- W607-AY is
    # FRESH: the accumulator-based markers become the canonical
    # ``summary.warnings_out`` field outright.
    _w607ay_warnings_out: list[str] = []

    def _run_check_ay(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AY marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``taint_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ay_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ay_warnings_out.append(f"taint_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-CJ -- ADDITIVE aggregation-phase plumbing on top of the W607-AY
    # substrate-CALL markers. W607-AY already wrapped the substrate-helper
    # boundaries on the build path (capture_qualified_only_lint /
    # query_symbol_count / run_taint / build_emit_entries / emit_findings /
    # wrap_findings / taint_to_sarif / serialize_envelope); W607-CJ extends
    # marker coverage to the AGGREGATION-PHASE boundaries that W607-AY left
    # unguarded:
    #
    #   - ``score_classify``       -- per-flow severity classification +
    #                                 risk_score computation (high_count /
    #                                 medium_count / sanitized_count + the
    #                                 raw_points -> risk_score normalization).
    #                                 A future ``TaintFinding`` schema
    #                                 refactor that drops/renames the
    #                                 .severity or .sanitizer_in_path fields
    #                                 would otherwise crash the envelope
    #                                 post-BFS.
    #   - ``compute_predicate``    -- per-field extraction of the metrics
    #                                 fields (rules / findings / errors /
    #                                 warnings / sanitized / risk_score)
    #                                 used to compose the verdict string +
    #                                 envelope. Floor to documented empty-
    #                                 shape ints matching the happy-path
    #                                 shape so downstream verdict/summary
    #                                 fields stay non-null.
    #   - ``compute_verdict``      -- verdict string assembly based on
    #                                 findings count (LAW 6 standalone-
    #                                 parse). Floor to a literal "Taint
    #                                 analysis completed" string per LAW 6
    #                                 + W978 first-hypothesis discipline
    #                                 (no re-interpolation of the same
    #                                 values that just raised).
    #   - ``serialize_envelope``   -- ``json_envelope("taint", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions).
    #
    # cmd_taint is the dataflow-reach leg of the security-reachability
    # triad. Closes the SECURITY-FLOW RING together with W607-AQ/CH
    # (cmd_vulns -- ingestion / catalog) and W607-AU (cmd_vuln_reach --
    # call-graph reachability sibling). The W607-CJ markers fire AT
    # RUNTIME when an aggregation-phase boundary raises, complementing
    # the W607-AY substrate-CALL coverage.
    #
    # Marker family ``taint_*`` -- same family as W607-AY (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope on
    # the success path.
    #
    # No ``auto_log`` phase: cmd_taint has no active-run ledger write at
    # present, so the W607-CD 3-phase set is kept here with the addition
    # of ``score_classify`` (taint-specific severity-grading step that
    # cmd_supply_chain / cmd_sbom don't have).
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_cj(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(findings) if ...``). A computed
    # default expression evaluates BEFORE the wrap call, so a raise
    # inside the expression escapes the try-block. cmd_sbom's W607-CG
    # sealed this axis after a regression where a ``len(_BadDeps())``
    # default eagerly raised. Floors below are documented constants.
    _w607cj_warnings_out: list[str] = []

    def _run_check_cj(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CJ marker emission.

        Mirror of ``_run_check_ay`` shape (same ``taint_<phase>_failed:``
        marker family) but writes into ``_w607cj_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cj_warnings_out.append(f"taint_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    rules_path = Path(rules_dir) if rules_dir else _default_rules_dir()
    # W489-A: capture the W454/W479 `qualified_only` lint warnings
    # alongside the loaded rules so the envelope can disclose bare-name
    # violations without losing rule_id / kind / name fields. The lint
    # is advisory — rules still load — so this is disclosure-only and
    # never gates execution (W462 territory).
    # W607-AY: wrap the rule-load + lint capture so a corrupt YAML file
    # or unreadable rules-dir surfaces a marker rather than crashing.
    rules, _w489_a_violations = _run_check_ay(
        "capture_qualified_only_lint",
        _w489_a_capture_qualified_only_lint,
        rules_path,
        default=([], []),
    )
    _w489_a_total_rules = len(rules)
    # W1061-followup: capture the pre-filter rule-id set so the SARIF emit
    # branch below can disclose which rules ``--rule`` / ``--rules-pack``
    # disabled at runtime. The post-filter ``rules`` list no longer
    # contains the dropped entries, so any future filter-disclosure must
    # diff against this baseline.
    _pre_filter_rule_ids = [r.rule_id for r in rules]
    if rules_pack:
        # Pack name → substring matched against rule id (e.g. "sqli"
        # matches "python-sqli", "xss" matches "js-xss").
        pack_match = rules_pack.lower()
        rules = [r for r in rules if pack_match in r.rule_id.lower()]
    if rule_filter:
        rules = [r for r in rules if rule_filter.lower() in r.rule_id.lower()]

    if not rules:
        verdict = f"No rules in {rules_path}"
        if json_mode:
            # W489-A: surface the qualified_only lint even on the
            # no-rules branch — N=0 violations against M=0 rules is
            # still useful disclosure (M == 0 itself signals an empty
            # / broken pack).
            _w489_a_summary = {
                "verdict": verdict,
                "rules": 0,
                "findings": 0,
                "rules_lint": {
                    "qualified_only_violations": len(_w489_a_violations),
                    "total_rules": _w489_a_total_rules,
                },
            }
            if _w489_a_violations:
                _w489_a_summary["partial_success"] = True
                _w489_a_summary["warnings_out"] = [
                    f"qualified_only lint flagged {len(_w489_a_violations)} bare-name violations"
                ]
            # W607-AY: stamp substrate markers if rule-load itself raised.
            if _w607ay_warnings_out:
                _w489_a_summary["partial_success"] = True
                existing = list(_w489_a_summary.get("warnings_out") or [])
                _w489_a_summary["warnings_out"] = existing + list(_w607ay_warnings_out)
            _w489_a_envelope_extra: dict = {"rules_dir": str(rules_path)}
            if _w489_a_violations:
                _w489_a_envelope_extra["qualified_only_violations"] = _w489_a_violations
            if _w607ay_warnings_out:
                _w489_a_envelope_extra["warnings_out"] = list(_w607ay_warnings_out)
            click.echo(
                to_json(
                    json_envelope(
                        "taint",
                        summary=_w489_a_summary,
                        **_w489_a_envelope_extra,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    ensure_index()

    # ``--persist`` mirrors each finding into the central findings
    # registry; that requires a writable connection. Default stays
    # readonly so the standard call has no side effects (matching the
    # readonly contract every other taint invocation already honours).
    with open_db(readonly=not persist) as conn:
        # W826 (Pattern 2: silent fallbacks) — distinguish "scan ran
        # against a populated graph and found zero taints" from "graph
        # has zero symbols, so no source/sink could ever match". The
        # previous code emitted "No taint findings across N rule(s)" in
        # both cases, which silently asserted a clean security verdict
        # on an unanalyzed corpus. Mirror cmd_vulns Fix E: emit
        # state="empty_corpus" + partial_success=True + a verdict that
        # names the absent state and points at `roam index --force`.
        # W607-AY: corpus probe boundary -- a raise here (stale schema /
        # locked DB) used to crash the whole taint command.
        _symbol_count_row = _run_check_ay(
            "query_symbol_count",
            lambda c: c.execute("SELECT COUNT(*) FROM symbols").fetchone(),
            conn,
            default=(0,),
        )
        symbol_count = _symbol_count_row[0] if _symbol_count_row else 0
        if symbol_count == 0:
            verdict = (
                f"no symbols to analyze (corpus empty; "
                f"{len(rules)} rules loaded but not run — "
                f"run `roam index --force` to populate the graph)"
            )
            if json_mode:
                # W489-A: stamp the qualified_only lint result on the
                # empty-corpus branch too. partial_success is already
                # True here (empty corpus is itself degraded); the
                # warnings_out entry appends without overriding.
                _w489_a_summary = {
                    "verdict": verdict,
                    "state": "empty_corpus",
                    "partial_success": True,
                    "rules": len(rules),
                    "findings": 0,
                    # Keep the distribution shape consistent with the
                    # populated-corpus branch (line 611) so consumers
                    # don't have to special-case empty_corpus.
                    "findings_confidence_distribution": {"high": 0, "medium": 0, "low": 0},
                    "rules_lint": {
                        "qualified_only_violations": len(_w489_a_violations),
                        "total_rules": _w489_a_total_rules,
                    },
                }
                if _w489_a_violations:
                    _w489_a_summary["warnings_out"] = [
                        f"qualified_only lint flagged {len(_w489_a_violations)} bare-name violations"
                    ]
                # W607-AY: stamp substrate markers on the empty-corpus
                # branch -- a raise in query_symbol_count surfaces here.
                if _w607ay_warnings_out:
                    existing = list(_w489_a_summary.get("warnings_out") or [])
                    _w489_a_summary["warnings_out"] = existing + list(_w607ay_warnings_out)
                _w489_a_envelope_extra: dict = {
                    "rules_dir": str(rules_path),
                    "rule_ids": [r.rule_id for r in rules],
                    "findings": [],
                    "agent_contract": {
                        "facts": [
                            "0 symbols in graph",
                            f"{len(rules)} rules loaded but not run against 0 symbols",
                            "run `roam index --force` to populate the indexed symbols",
                        ],
                        "next_commands": [
                            "roam index --force",
                            "roam taint",
                        ],
                    },
                }
                if _w489_a_violations:
                    _w489_a_envelope_extra["qualified_only_violations"] = _w489_a_violations
                if _w607ay_warnings_out:
                    _w489_a_envelope_extra["warnings_out"] = list(_w607ay_warnings_out)
                click.echo(
                    to_json(
                        json_envelope(
                            "taint",
                            summary=_w489_a_summary,
                            **_w489_a_envelope_extra,
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            return

        # W607-AY: the BFS source->sink propagation is THE critical
        # correctness boundary (W493/W499/W512 audit history -- edge-kind
        # vocabulary, sanitizer-stop semantics, sub-pass co-call
        # detection). A raise inside ``run_taint`` previously crashed the
        # whole taint command; now it degrades to "zero findings" plus a
        # marker so the envelope still discloses the rules / corpus
        # state.
        findings = _run_check_ay(
            "run_taint",
            run_taint,
            conn,
            rules,
            max_hops=max_hops,
            default=[],
        )

        # W607-CJ -- score_classify boundary. Wraps the per-flow severity
        # classification + the 0-100 risk_score computation + the
        # ``findings_dump`` projection (all three walk the same findings
        # list and access the same fields). A future ``TaintFinding``
        # schema refactor that drops/renames the .severity or
        # .sanitizer_in_path fields would otherwise raise inside any of
        # these unguarded f.<attr> accesses. Floor to literal-zero counts
        # + risk_score 0 + an empty ``findings_dump`` (LAW 6: standalone
        # parse). W978 discipline: ``default=`` is a literal dict, NOT a
        # comprehension that re-walks ``findings`` (a corrupt finding
        # raising in its ``.severity`` getter would otherwise re-raise
        # inside the default expression).
        def _classify_scores(_findings) -> dict:
            _high = sum(1 for f in _findings if f.severity == "error")
            _med = sum(1 for f in _findings if f.severity == "warning")
            _san = sum(1 for f in _findings if f.sanitizer_in_path)
            # ``error`` weighs 5×; ``warning`` 1×; sanitized findings
            # count for half (mitigated, not eliminated). The score
            # saturates at 100 for >20 effective points so a clean repo
            # lands at 0 and any non-trivial risk is visible.
            _raw = max(0, (_high * 5) + _med - (_san * 2))
            _score = min(100, int(round(_raw / 20.0 * 100)))
            _dump = [
                {
                    "rule_id": f.rule_id,
                    "severity": f.severity,
                    "cwe": f.cwe,
                    "owasp_top10": f.owasp_top10,
                    "source": f.source_symbol,
                    "sink": f.sink_symbol,
                    "path_length": len(f.path_symbols),
                    "path": [
                        {"name": p.get("name"), "file": p.get("file"), "line": p.get("line")} for p in f.path_symbols
                    ],
                    "sanitizer_in_path": f.sanitizer_in_path,
                    "vex_justification": (vex_justification_for(f) if f.sanitizer_in_path else None),
                }
                for f in _findings
            ]
            return {
                "high_count": _high,
                "medium_count": _med,
                "sanitized_count": _san,
                "risk_score": _score,
                "findings_dump": _dump,
            }

        _score_dict = _run_check_cj(
            "score_classify",
            _classify_scores,
            findings,
            default={
                "high_count": 0,
                "medium_count": 0,
                "sanitized_count": 0,
                "risk_score": 0,
                "findings_dump": [],
            },
        )
        high_count = _score_dict["high_count"]
        medium_count = _score_dict["medium_count"]
        sanitized_count = _score_dict["sanitized_count"]
        risk_score = _score_dict["risk_score"]

        # W607-CJ -- compute_verdict boundary. Wraps the verdict-string
        # assembly so a downstream f-string refactor (e.g. a non-int
        # count from a vocabulary refactor) surfaces a marker rather
        # than crashing the envelope. Floor must NOT re-interpolate the
        # same values that tripped the closure (W978 first-hypothesis
        # discipline: a __format__-raising sentinel under test would
        # re-raise inside the default f-string). Use a literal "Taint
        # analysis completed" floor instead (LAW 6 still holds: the
        # line works standalone).
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(findings)`` /
        # ``len(rules)`` are computed INSIDE the wrapped closure rather
        # than at the call site -- a ``_BadFindingList`` whose
        # ``__len__`` raises would otherwise escape the try-block at
        # kwarg-bind time.
        def _build_verdict_str(
            _findings,
            _h: int,
            _m: int,
            _s: int,
            _rules,
            _risk: int,
        ) -> str:
            _findings_len = len(_findings)
            _rules_len = len(_rules)
            if _findings_len:
                return (
                    f"{_findings_len} finding(s) "
                    f"({_h} error, {_m} warning, "
                    f"{_s} sanitized) across {_rules_len} rule(s); "
                    f"risk_score={_risk}"
                )
            return f"No taint findings across {_rules_len} rule(s)"

        verdict = _run_check_cj(
            "compute_verdict",
            _build_verdict_str,
            findings,
            high_count,
            medium_count,
            sanitized_count,
            rules,
            risk_score,
            default="Taint analysis completed",
        )

        # W607-CJ: ``findings_dump`` is built inside the ``score_classify``
        # boundary above so a raise during per-finding field access
        # surfaces a marker rather than crashing the envelope. The floor
        # is an empty list (literal constant per W978 discipline).
        findings_dump = _score_dict.get("findings_dump", [])

        # --- W122: mirror into the central findings registry ---
        # Detector-specific output below is untouched; the registry rows
        # are the denormalised cross-detector surface (``roam findings``).
        # Wrapped so a pre-W89 DB (no ``findings`` table) silently no-ops
        # rather than crashing the standard taint command path.
        if persist:
            # W607-AY: flow-shape classifier substrate (build_emit_entries
            # walks every finding to classify forward_bfs vs co_call via
            # adjacent-pair edge probes -- a raise inside the classifier
            # used to crash the whole persist path).
            findings_for_emit = _run_check_ay(
                "build_emit_entries",
                _build_emit_entries,
                conn,
                findings,
                findings_dump,
                default=[],
            )
            try:
                # W607-AY: registry-write substrate. Still keeps the
                # existing OperationalError swallow for pre-W89 schemas;
                # the W607-AY wrap layers underneath catches any other
                # exception class (e.g. constraint failures) and surfaces
                # a marker rather than crashing.
                _run_check_ay(
                    "emit_findings",
                    _emit_taint_findings,
                    conn,
                    findings_for_emit,
                    TAINT_DETECTOR_VERSION,
                    default=None,
                )
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_taint:emit_findings", _exc)

    if sarif_mode:
        from roam.output.sarif import (
            runtime_filter_disclosure,
            taint_to_sarif,
            write_sarif,
        )

        # W1061-followup-2: rule-level + finding-level filter disclosure
        # delegated to the shared :func:`runtime_filter_disclosure`
        # helper. Original W1061-followup semantics preserved:
        #   --rule    / --rules-pack  -> rule-id-level disables; every
        #                                pre-filter rule_id NOT in the
        #                                post-filter set surfaces as a
        #                                ``ruleConfigurationOverride``
        #                                with ``configuration.enabled:
        #                                false``.
        #   --rules-dir               -> alternate rule pack location;
        #                                surfaces as a finding-level
        #                                notification (``rules-dir-filter``
        #                                synthetic descriptor) because the
        #                                rule_id namespace itself is
        #                                replaced rather than narrowed.
        rule_disabled: list[tuple[str, dict]] = []
        finding_filters: list[tuple[str, dict]] = []
        active_rule_ids = {r.rule_id for r in rules}
        if rule_filter or rules_pack:
            disabled_rule_ids = sorted(rid for rid in _pre_filter_rule_ids if rid not in active_rule_ids)
            for rid in disabled_rule_ids:
                disabled_by = []
                if rule_filter:
                    disabled_by.append("--rule")
                if rules_pack:
                    disabled_by.append("--rules-pack")
                props: dict = {"disabled_by": ",".join(disabled_by)}
                if rule_filter:
                    props["rule_filter"] = rule_filter
                if rules_pack:
                    props["rules_pack"] = rules_pack
                rule_disabled.append((rid, props))
        if rules_dir:
            finding_filters.append(
                (
                    "rules-dir-filter",
                    {"filter": "--rules-dir", "filter_value": str(rules_path)},
                )
            )
        sarif_overrides, sarif_notif_overrides = runtime_filter_disclosure(
            rule_ids_disabled=rule_disabled,
            finding_level_filters=finding_filters,
        )

        # W607-AY: SARIF projection substrate -- isolate the rendering
        # pipeline so a renderer raise surfaces a marker. The SARIF
        # output path is text-only (no envelope to stamp markers onto),
        # so this falls back to "empty SARIF document" if the projection
        # raises -- consumers downstream still get well-formed JSON.
        _sarif_doc = _run_check_ay(
            "taint_to_sarif",
            taint_to_sarif,
            findings_dump,
            runtime_overrides=sarif_overrides or None,
            runtime_notification_overrides=sarif_notif_overrides or None,
            default={},
        )
        _sarif_text = _run_check_ay(
            "write_sarif",
            write_sarif,
            _sarif_doc,
            default="{}",
        )
        click.echo(_sarif_text if _sarif_text is not None else "{}")
        if ci_mode and high_count > 0:
            ctx.exit(5)
        return

    if json_mode:
        # R22: wrap each finding in {value, confidence, reason}.
        # Consumers that previously read findings[i]["rule_id"] must
        # now read findings[i]["value"]["rule_id"] plus
        # findings[i]["confidence"] / findings[i]["reason"].
        # W607-AY: wrap the confidence classifier so a raise inside
        # ``_taint_classify`` (or wrap_findings itself) surfaces a marker
        # and falls back to the raw findings_dump.
        finding_triples = _run_check_ay(
            "wrap_findings",
            wrap_findings,
            findings_dump,
            classifier=_taint_classify,
            default=list(findings_dump),
        )
        distribution = confidence_distribution(finding_triples)
        wrapped_verdict = verdict_with_high_count(verdict, distribution)

        # W607-CJ -- compute_predicate boundary. Wraps the per-field
        # extraction of metrics so a future ``TaintFinding`` schema
        # refactor that drops/renames count fields surfaces a marker
        # rather than crashing the envelope. Floor to documented empty-
        # shape ints matching the happy-path return so downstream
        # verdict/summary fields stay non-null. W978 discipline:
        # ``default=`` is a literal dict, NOT a computed expression
        # over the (potentially poisoned) inputs.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(rules)`` /
        # ``len(findings)`` are computed INSIDE the wrapped closure --
        # passing the raw lists keeps the kwarg-bind step pure (no
        # ``__len__`` call until we're inside the try-block).
        def _compute_predicate_fields(
            _rules,
            _findings,
            _h: int,
            _m: int,
            _s: int,
            _risk: int,
        ) -> dict:
            return {
                "rules": len(_rules),
                "findings": len(_findings),
                "errors": _h,
                "warnings": _m,
                "sanitized": _s,
                "risk_score": _risk,
            }

        _pred_fields = _run_check_cj(
            "compute_predicate",
            _compute_predicate_fields,
            rules,
            findings,
            high_count,
            medium_count,
            sanitized_count,
            risk_score,
            default={
                "rules": 0,
                "findings": 0,
                "errors": 0,
                "warnings": 0,
                "sanitized": 0,
                "risk_score": 0,
            },
        )

        # Round 3 #23: only ship the OpenVEX vocabulary lists when there
        # are findings to attach them to. Empty taint runs returning the
        # static lists every call was metadata noise.
        envelope_kwargs = dict(
            budget=token_budget,
            rules_dir=str(rules_path),
            rule_ids=[r.rule_id for r in rules],
            findings=finding_triples,
        )
        if findings_dump:
            envelope_kwargs["openvex_justification_strings"] = sorted(OPENVEX_JUSTIFICATIONS)
            envelope_kwargs["openvex_statuses"] = sorted(OPENVEX_STATUSES)
        # W489-A: stamp the qualified_only lint result on the main JSON
        # branch. rules_lint is always present (symmetric emission per
        # W1101/W1006); qualified_only_violations[] only when non-empty
        # (W1006 redactions[] precedent for content lists).
        _w489_a_summary = {
            "verdict": wrapped_verdict,
            "rules": _pred_fields.get("rules", 0),
            "findings": _pred_fields.get("findings", 0),
            "errors": _pred_fields.get("errors", 0),
            "warnings": _pred_fields.get("warnings", 0),
            "sanitized": _pred_fields.get("sanitized", 0),
            "risk_score": _pred_fields.get("risk_score", 0),
            "findings_confidence_distribution": distribution,
            "rules_lint": {
                "qualified_only_violations": len(_w489_a_violations),
                "total_rules": _w489_a_total_rules,
            },
        }
        if _w489_a_violations:
            _w489_a_summary["partial_success"] = True
            _w489_a_summary["warnings_out"] = [
                f"qualified_only lint flagged {len(_w489_a_violations)} bare-name violations"
            ]
            envelope_kwargs["qualified_only_violations"] = _w489_a_violations
        # W607-AY / W607-CJ: append BOTH substrate-CALL markers AND
        # aggregation-phase markers to ``summary.warnings_out`` and
        # top-level ``warnings_out``. Both buckets share the canonical
        # ``taint_*`` marker family (W607-CJ is additive, not a separate
        # prefix); the additive bucket stays distinguishable via its
        # phase names (``score_classify`` / ``compute_predicate`` /
        # ``compute_verdict`` / ``serialize_envelope``). Non-empty
        # combined bucket flips partial_success.
        _combined_warnings_out = list(_w607ay_warnings_out) + list(_w607cj_warnings_out)
        if _combined_warnings_out:
            _w489_a_summary["partial_success"] = True
            existing = list(_w489_a_summary.get("warnings_out") or [])
            _w489_a_summary["warnings_out"] = existing + list(_combined_warnings_out)
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CJ -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("taint", ...)`` would otherwise crash
        # AFTER all substrate + aggregation signals were already
        # gathered. Floor to a minimal envelope stub so consumers still
        # receive a parseable JSON object with the marker attached + the
        # canonical command name. Mirror of cmd_supply_chain's W607-CD
        # serialize_envelope floor pattern.
        _envelope_floor: dict = {
            "command": "taint",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        _envelope = _run_check_cj(
            "serialize_envelope",
            json_envelope,
            "taint",
            default=_envelope_floor,
            summary=_w489_a_summary,
            **envelope_kwargs,
        )
        # W607-CJ -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``taint_serialize_envelope_failed:`` marker was appended to
        # ``_w607cj_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output. Clean path ->
        # envelope is the real json_envelope return value, no rebuild
        # needed.
        if _envelope is _envelope_floor and _w607cj_warnings_out:
            _combined_warnings_out = list(_w607ay_warnings_out) + list(_w607cj_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            _envelope = _envelope_floor

        # W607-AY: wrap the JSON serialization itself so a circular-ref
        # bug or hostile field surfaces a marker rather than crashing.
        _output_text = _run_check_ay(
            "serialize_envelope",
            to_json,
            _envelope,
            default="{}",
        )
        click.echo(_output_text if _output_text is not None else "{}")
        if ci_mode and high_count > 0:
            ctx.exit(5)
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Rules:   {', '.join(r.rule_id for r in rules)}")
    click.echo()
    for f in findings_dump:
        click.echo(f"[{f['severity'].upper()}] {f['rule_id']} ({f['cwe'] or 'no CWE'})")
        src = f["source"]
        sink = f["sink"]
        click.echo(f"  src: {src.get('name')} at {src.get('file')}:{src.get('line')}")
        click.echo(f"  sink: {sink.get('name')} at {sink.get('file')}:{sink.get('line')}")
        click.echo(f"  path: {f['path_length']} hop(s)")
        if f["sanitizer_in_path"]:
            click.echo(f"  sanitized: yes  (VEX: {f['vex_justification']})")
        click.echo()

    if ci_mode and high_count > 0:
        ctx.exit(5)


taint = taint_command
