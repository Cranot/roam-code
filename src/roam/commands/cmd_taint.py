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
@click.command()
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
def taint(ctx, rules_dir, max_hops, ci_mode, rule_filter, rules_pack, persist):
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
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    rules_path = Path(rules_dir) if rules_dir else _default_rules_dir()
    # W489-A: capture the W454/W479 `qualified_only` lint warnings
    # alongside the loaded rules so the envelope can disclose bare-name
    # violations without losing rule_id / kind / name fields. The lint
    # is advisory — rules still load — so this is disclosure-only and
    # never gates execution (W462 territory).
    rules, _w489_a_violations = _w489_a_capture_qualified_only_lint(rules_path)
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
            _w489_a_envelope_extra: dict = {"rules_dir": str(rules_path)}
            if _w489_a_violations:
                _w489_a_envelope_extra["qualified_only_violations"] = _w489_a_violations
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
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
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

        findings = run_taint(conn, rules, max_hops=max_hops)

        high_count = sum(1 for f in findings if f.severity == "error")
        medium_count = sum(1 for f in findings if f.severity == "warning")
        sanitized_count = sum(1 for f in findings if f.sanitizer_in_path)

        # single 0-100 risk score. ``error`` weighs 5×; ``warning``
        # 1×; sanitized findings count for half (mitigated, not eliminated).
        # The score saturates at 100 for >20 effective points so a clean
        # repo lands at 0 and any non-trivial risk is visible.
        raw_points = (high_count * 5) + medium_count - (sanitized_count * 2)
        raw_points = max(0, raw_points)
        risk_score = min(100, int(round(raw_points / 20.0 * 100)))

        verdict = (
            f"{len(findings)} finding(s) "
            f"({high_count} error, {medium_count} warning, "
            f"{sanitized_count} sanitized) across {len(rules)} rule(s); risk_score={risk_score}"
            if findings
            else f"No taint findings across {len(rules)} rule(s)"
        )

        findings_dump = [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "cwe": f.cwe,
                "owasp_top10": f.owasp_top10,
                "source": f.source_symbol,
                "sink": f.sink_symbol,
                "path_length": len(f.path_symbols),
                "path": [{"name": p.get("name"), "file": p.get("file"), "line": p.get("line")} for p in f.path_symbols],
                "sanitizer_in_path": f.sanitizer_in_path,
                "vex_justification": (vex_justification_for(f) if f.sanitizer_in_path else None),
            }
            for f in findings
        ]

        # --- W122: mirror into the central findings registry ---
        # Detector-specific output below is untouched; the registry rows
        # are the denormalised cross-detector surface (``roam findings``).
        # Wrapped so a pre-W89 DB (no ``findings`` table) silently no-ops
        # rather than crashing the standard taint command path.
        if persist:
            findings_for_emit = _build_emit_entries(conn, findings, findings_dump)
            try:
                _emit_taint_findings(conn, findings_for_emit, TAINT_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

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

        click.echo(
            write_sarif(
                taint_to_sarif(
                    findings_dump,
                    runtime_overrides=sarif_overrides or None,
                    runtime_notification_overrides=sarif_notif_overrides or None,
                )
            )
        )
        if ci_mode and high_count > 0:
            ctx.exit(5)
        return

    if json_mode:
        # R22: wrap each finding in {value, confidence, reason}.
        # Consumers that previously read findings[i]["rule_id"] must
        # now read findings[i]["value"]["rule_id"] plus
        # findings[i]["confidence"] / findings[i]["reason"].
        finding_triples = wrap_findings(findings_dump, classifier=_taint_classify)
        distribution = confidence_distribution(finding_triples)
        wrapped_verdict = verdict_with_high_count(verdict, distribution)

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
            "rules": len(rules),
            "findings": len(findings),
            "errors": high_count,
            "warnings": medium_count,
            "sanitized": sanitized_count,
            "risk_score": risk_score,
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
        click.echo(
            to_json(
                json_envelope(
                    "taint",
                    summary=_w489_a_summary,
                    **envelope_kwargs,
                )
            )
        )
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
