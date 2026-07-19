"""roam critique — graph-grounded patch verifier (A.2).

Reads a unified diff (stdin) and runs roam-grounded checks against it:

    git diff | roam critique
    git diff main..HEAD | roam critique --json

The killer signal is *clones-not-edited*: for every changed symbol that
has a persisted clone sibling (see ``roam clones --persist``) outside the
diff, we flag the sibling as a likely missed change. v12.0 ships this
plus a minimal blast-radius caller count; v12.1 wires intent ↔
semantic-diff and dark-matter expectations.
"""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.critique.aggregator import aggregate
from roam.critique.checks import (
    check_clones_not_edited,
    check_impact,
    check_intent_alignment,
    find_changed_symbols,
    looks_like_unified_diff,
    parse_diff,
)
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log

# W641-followup-B — critique severity → canonical risk-LEVEL projection.
#
# Pattern-3a structural close-out, fifth axis. W641 / W641-followup-A
# canonicalised pr-risk + impact onto the W631 risk-LEVEL vocabulary
# (``critical``/``high``/``medium``/``low``); critique is the natural
# #2 candidate — it emits a ``severity`` field on each diff-region
# finding (``high``/``medium``/``low``/``info`` per the closed
# ``Finding`` constructor vocabulary) but had no risk-axis projection.
#
# Polarity: **higher = worse**, matching the W631 canonical rank.
# Aggregation across multiple findings: MAX-severity (the worst
# critique finding drives the report's risk_level). Empty findings →
# ``low`` floor (W531 CI-safety lesson — a typo'd or absent label MUST
# NOT promote a finding into a CI-failing rank).
#
# The table below is the inline projection. We deliberately do NOT
# re-use ``roam.output._severity._DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL``
# even though its polarity matches: that helper projects severity onto
# the *confidence-LEVEL* axis (the ranker's "high/medium/low"
# confidence rating), which is a distinct axis from risk. Reusing it
# would conflate two independent concepts — see the "NOTE on
# vocabulary axes" comment in ``roam.output.risk`` for the discipline.
#
# Conservative-on-critical: the critique severity vocabulary tops out
# at ``high`` (no ``critical`` tier — see the closed ``Finding``
# constructor vocab). The projection
# saturates at ``high``; we do NOT escalate to ``critical`` on a
# threshold the underlying detector can't reach (mirrors the W641-
# followup-A discipline on ``_impact_risk_level``).
_CRITIQUE_SEVERITY_TO_RISK_LEVEL: dict[str, str] = {
    "critical": "high",  # not emitted by checks today; mapped for forward-compat
    "error": "high",  # not emitted by checks today; mapped for forward-compat
    "high": "high",
    "warning": "medium",
    "medium": "medium",
    "info": "low",
    "low": "low",
    "note": "low",
    "unknown": "low",
}


def _critique_risk_level(
    findings: list[dict],
    *,
    warnings_out: list[str] | None = None,
) -> str:
    """Aggregate finding severities onto the canonical W631 risk-LEVEL set.

    Walks *findings* (the per-region dicts produced by
    :func:`roam.critique.aggregator.aggregate`) and returns a string in
    :data:`roam.output.risk.RISK_LEVELS`
    (``critical``/``high``/``medium``/``low``).

    Aggregation rule: **max-severity** — the worst finding's projected
    risk_level wins. Empty findings → ``low`` (the W531 CI-safety floor:
    no findings means nothing to gate on; a typo'd or absent label MUST
    NOT promote a finding into a CI-failing rank).

    Unknown severities accumulate a marker on *warnings_out* (when
    provided) under the ``critique_unknown_severity:<label>`` key so
    Pattern-2 silent-fallback discipline stays loud — the projection
    safe-floors to ``low`` but the marker disambiguates a real-low
    finding from an unrecognised-label drop.
    """
    if not findings:
        return "low"
    worst_rank = -1
    worst_level = "low"
    for f in findings:
        sev = (f.get("severity") or "").strip().lower() if isinstance(f, dict) else ""
        if not sev:
            # Empty / None severity — record + safe-floor.
            if warnings_out is not None:
                warnings_out.append("critique_unknown_severity:")
            continue
        if sev in _CRITIQUE_SEVERITY_TO_RISK_LEVEL:
            level = _CRITIQUE_SEVERITY_TO_RISK_LEVEL[sev]
        else:
            # Unknown label — safe-floor to ``low`` + record marker.
            if warnings_out is not None:
                warnings_out.append(f"critique_unknown_severity:{sev}")
            level = "low"
        r = risk_rank(level)
        if r > worst_rank:
            worst_rank = r
            worst_level = level
    return worst_level


# W153 — critique is the SEVENTH detector migrating onto the central
# findings registry (after clones W95, dead W99, complexity W102,
# smells W109, bus-factor W115, pr-risk W134). Like pr-risk, critique is
# INVOCATION-SCOPED — a run produces findings tied to a specific diff
# (read from stdin / --input / a --batch file). The ``diff_sha`` is
# stamped into ``evidence_json`` so a consumer can group findings by
# PR / branch and tell stale (since-merged) rows apart from fresh ones.
#
# critique is the FIRST detector to claim the ``patch.*`` namespace and
# the ``subject_kind="diff_region"`` vocabulary — every finding here
# attaches to a span of changed code, not to a stable workspace symbol.
# ``subject_id`` stays NULL because a diff region doesn't map to a
# ``symbols.id`` row. When the edited region is wholly contained within a
# single symbol, that symbol's id is captured in
# ``evidence_json.affected_symbol_id`` for consumers that want to drill
# down; the registry-level join is still keyed on diff_sha.
#
# Confidence tiers per kind (see _CRITIQUE_KIND_TO_CONFIDENCE below):
# * ``patch.clone_not_edited`` — the killer signal. Reads from
#   ``clone_pairs`` (a persisted, deterministic AST/structural detector
#   output); same diff, same clone table → same finding set. Tier:
#   ``static_analysis``.
# * ``patch.high_blast`` — derived from a raw COUNT() over the ``edges``
#   table at the changed-symbol target. Caller-count threshold; pure
#   structural signal. Tier: ``structural``.
# * ``patch.intent_mismatch`` — pattern-matches the PR title against an
#   intent verb vocabulary (add / remove / fix / rename / ...) and
#   compares to the diff's net additions/deletions. NLP-style; tier:
#   ``heuristic``.
#
# Bump this version when the check vocabulary / kind names / evidence
# shape change meaningfully so registry consumers can spot rows produced
# under an older shape.
CRITIQUE_DETECTOR_VERSION: str = "1.0.0"


# W153 — per-kind confidence tier mapping. Every emitted kind picks a
# tier from the central CONFIDENCE_* enum so a downstream consumer can
# weight signals without re-deriving the rule.
_CRITIQUE_KIND_TO_CONFIDENCE: dict[str, str] = {
    "patch.clone_not_edited": "static_analysis",
    "patch.high_blast": "structural",
    "patch.intent_mismatch": "heuristic",
}


# W153 — mapping from the in-memory ``Finding.check`` label produced by
# roam.critique.checks → the registry kind. Single source of truth for
# the routing so a check rename only needs one edit here.
_CHECK_TO_KIND: dict[str, str] = {
    "clones-not-edited": "patch.clone_not_edited",
    "impact": "patch.high_blast",
    "intent": "patch.intent_mismatch",
}


def _diff_sha(
    *,
    label: str,
    intent_text: str | None,
    file_paths: list[str],
) -> str:
    """Stable id for one critique invocation's diff.

    Mirrors :func:`roam.commands.cmd_pr_risk._diff_id` in shape: folds
    the input-source label, the (intent) commit-range analogue, a
    staged-flag placeholder (always 0 — critique reads from stdin / a
    file, not the git staging area), and the sorted list of changed file
    paths into a sha1 prefix.

    Two invocations against the same diff (same file set + same intent
    text + same input source) produce the same sha so ``--persist``
    upserts in place; changing any of the inputs — even just adding one
    more changed file — produces a fresh sha so the prior finding stays
    as an audit-trail row.
    """
    raw = f"{label}|range={intent_text or ''}|staged=0|files={','.join(sorted(file_paths))}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _critique_finding_id(kind: str, diff_sha: str, region_qname: str) -> str:
    """Stable, deterministic finding id for one critique row.

    Convention: ``"critique:patch.<kind>:<diff_sha>:<region_digest>"``.

    ``kind`` is the registry kind (e.g. ``patch.clone_not_edited``);
    ``diff_sha`` ties the row to a specific invocation's diff so reruns
    upsert; ``region_qname`` (``"<file>:<start>-<end>"``) discriminates
    the per-region rows within one diff. A diff that touches three
    symbols emits three rows of each conditional kind.
    """
    region_digest = hashlib.sha1(region_qname.encode("utf-8")).hexdigest()[:8]
    return f"critique:{kind}:{diff_sha}:{region_digest}"


def _emit_critique_findings(
    conn: sqlite3.Connection,
    findings_data: list[dict],
    diff_data: dict,
    source_version: str,
) -> int:
    """Mirror critique's invocation result into the central findings registry.

    Returns the count of finding rows written. ``findings_data`` is the
    list of finding dicts produced by :func:`aggregate` — passed in
    rather than recomputed so the persist path stays a pure
    transcription of what the read path already calculated. ``diff_data``
    carries the invocation-scope envelope (diff_sha, label, intent,
    file_list) so every emitted row shares the same audit-trail base.

    Subject vocabulary: every emitted row uses ``subject_kind="diff_region"``
    with ``subject_id=None``. The qualified diff-region id (file +
    line range) lives in ``evidence_json.qualified_name``. When the
    finding's underlying check exposes a resolvable symbol id (impact
    findings always do; intent rarely), it's stamped into
    ``evidence_json.affected_symbol_id`` so a consumer can drill down to
    the symbol without rejoining via name.

    Caller commits the transaction. ``emit_finding`` does not commit on
    its own (matches the W95 / W99 / W102 / W109 / W115 / W134
    convention).

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard read path.
    """
    from roam.db.findings import FindingRecord, emit_finding

    diff_sha = diff_data["diff_sha"]
    label = diff_data["label"]
    intent_text = diff_data["intent_text"]
    file_list = diff_data["file_list"]
    created_at = int(time.time())

    # Shared invocation metadata — every row carries this so a consumer
    # can group findings by PR / commit / branch without joining back.
    base_evidence = {
        "diff_sha": diff_sha,
        "label": label,
        "intent_text": intent_text,
        "file_list": file_list,
        "changed_files_count": len(file_list),
        "created_at_epoch": created_at,
    }

    written = 0
    for f in findings_data:
        check = f.get("check", "")
        kind = _CHECK_TO_KIND.get(check)
        if kind is None:
            # Unknown check — skip rather than mint a kind on the fly.
            # Future checks that should land in the registry need a
            # mapping entry plus a confidence tier (LAW 8 — closed
            # enumeration over free-string composition).
            continue
        evidence = f.get("evidence") or {}
        severity = f.get("severity", "info")
        title = f.get("title", "")
        detail = f.get("detail", "")

        # Resolve the diff_region qualified name. Prefer explicit file +
        # line range from the evidence; fall back to "<file>:0-0" when
        # the check doesn't carry positional evidence (intent findings
        # are diff-wide, not per-region — we anchor them to a synthetic
        # whole-diff region).
        if check == "impact":
            ev_file = evidence.get("file", "")
            ev_line = int(evidence.get("line", 0) or 0)
            region_qname = f"{ev_file}:{ev_line}-{ev_line}"
            affected_symbol_id = evidence.get("symbol_id")
        elif check == "clones-not-edited":
            changed = evidence.get("changed_symbol") or {}
            ev_file = changed.get("file", "")
            # clones-not-edited evidence doesn't carry the start/end
            # range on the *changed* symbol — anchor on the file with a
            # 0-0 placeholder. The persisted clone_pairs table is the
            # authoritative source for the sibling locations; the
            # finding row only records that the diff lacked an
            # analogous edit.
            region_qname = f"{ev_file}:0-0"
            affected_symbol_id = changed.get("id")
        else:
            # intent — diff-wide. Anchor on a synthetic "<diff>:<label>"
            # marker with a 0-0 range placeholder. One intent finding
            # per invocation per intent-label, so the region_qname
            # stays stable across reruns on the same diff. Using the
            # intent label (instead of the full file list joined) keeps
            # the evidence_json bounded — a 200-file diff would
            # otherwise produce a multi-KB qualified_name string.
            intent_label = evidence.get("intent_label") or "intent"
            region_qname = f"<diff>:{intent_label}:0-0"
            affected_symbol_id = None

        row_evidence = {
            **base_evidence,
            "qualified_name": region_qname,
            "severity": severity,
            "check": check,
            "title": title,
            "detail": detail,
            "raw_evidence": evidence,
        }
        if affected_symbol_id is not None:
            row_evidence["affected_symbol_id"] = int(affected_symbol_id)

        claim = title or f"critique {kind} finding on {label}"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=_critique_finding_id(kind, diff_sha, region_qname),
                subject_kind="diff_region",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(row_evidence, sort_keys=True),
                confidence=_CRITIQUE_KIND_TO_CONFIDENCE[kind],
                source_detector="critique",
                source_version=source_version,
            ),
        )
        written += 1
    return written


# Hot-path → bench command. When a diff touches any of these path
# prefixes, the default critique rules can pass while the change
# materially alters retrieval/scoring/graph algorithms. The hint
# names the bench so the user includes it in their verification
# loop. Order matters: first match wins (most specific first).
_BENCH_RELEVANCE_RULES = [
    (
        ("src/roam/retrieve/", "src/roam/eval/"),
        "pytest tests/test_retrieve_cross_repo.py + roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl",
    ),
    (
        ("src/roam/graph/pagerank.py", "src/roam/graph/clusters.py"),
        "pytest tests/test_personalized_pagerank.py tests/test_fallback_contracts.py",
    ),
    (("src/roam/graph/",), "pytest tests/ -k graph_ -m 'not slow'"),
    (
        ("src/roam/languages/", "src/roam/index/parser.py"),
        "pytest tests/test_languages.py tests/test_extractor_grammar_drift.py",
    ),
    (("src/roam/security/taint",), "pytest tests/test_taint_analysis.py tests/test_taint_classifier.py"),
    (("src/roam/critique/",), "pytest tests/test_critique.py"),
    (
        ("src/roam/commands/cmd_oracle.py", "src/roam/commands/cmd_health.py"),
        "pytest tests/test_oracle.py tests/test_commands_health.py",
    ),
]


def _load_critique_overrides() -> list[tuple[tuple[str, ...], str]]:
    """Load project-local bench-hint overrides from ``.roam-critique.yml``.

    Format (deliberately minimal — no nested PyYAML required)::

        bench_hints:
          - paths: ["src/foo/", "src/bar/"]
            hint: "pytest tests/test_foo.py"

    Overrides are PREPENDED to the built-in rules so project-specific
    hints always match first. Silently returns ``[]`` when the file
    is absent or unparseable — this is a hint, not a gate.
    """
    config_path = Path(".roam-critique.yml")
    if not config_path.exists():
        return []
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    rules: list[tuple[tuple[str, ...], str]] = []
    in_bench = False
    cur_paths: list[str] = []
    cur_hint = ""
    pending = False

    def _flush() -> None:
        nonlocal cur_paths, cur_hint, pending
        if cur_paths and cur_hint:
            rules.append((tuple(cur_paths), cur_hint))
        cur_paths = []
        cur_hint = ""
        pending = False

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            _flush()
            in_bench = stripped[:-1] == "bench_hints"
            continue
        if not in_bench:
            continue
        # Item start: "- paths: [...]" or "- hint: ..."
        if stripped.startswith("- "):
            _flush()
            pending = True
            stripped = stripped[2:].strip()
        if not pending:
            continue
        if stripped.startswith("paths:"):
            val = stripped.split(":", 1)[1].strip()
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                cur_paths = [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
        elif stripped.startswith("hint:"):
            cur_hint = stripped.split(":", 1)[1].strip().strip('"').strip("'")
    _flush()
    return rules


def _bench_relevance_hint(regions, overrides=None) -> str:
    """Return a one-line bench/test suggestion when the diff touches a
    structurally-significant path. ``regions`` is the
    ``critique.checks.ChangedRegion`` list from the diff parser; we
    look at each region's file path and pick the first matching rule.

    Project-local rules from ``.roam-critique.yml`` (loaded via
    :func:`_load_critique_overrides`) are searched before the built-in
    list so they can shadow defaults — this is the v12.12 hook the
    dogfood notes asked for.
    """
    paths = []
    for r in regions:
        path = getattr(r, "file_path", None) or getattr(r, "file", None) or ""
        if path:
            paths.append(path.replace("\\", "/"))
    if not paths:
        return ""
    rules = list(overrides or []) + _BENCH_RELEVANCE_RULES
    for path in paths:
        for prefixes, hint in rules:
            if any(path.startswith(p) or p in path for p in prefixes):
                return hint
    return ""


def _has_clone_pairs(conn: sqlite3.Connection) -> bool:
    """Return True when ``clone_pairs`` table exists AND has at least one row.

    W832: lifted out of ``check_clones_not_edited`` so the orchestrator
    can distinguish "ran" from "skipped:no_clone_pairs" without
    duplicating the inner ``LIMIT 1`` query. A pre-W89 schema (no table
    at all) is treated as skipped, not errored.
    """
    try:
        row = conn.execute("SELECT 1 FROM clone_pairs LIMIT 1").fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _run_checks_with_status(
    conn: sqlite3.Connection,
    changed_symbols: list,
    regions: list,
    *,
    high_callers: int,
    effective_intent: str | None,
) -> tuple[list, dict[str, str]]:
    """Run all three critique checks and track per-check status.

    W832: Pattern 2 silent-fallback fix. Returns ``(findings,
    check_status)`` where ``check_status`` maps check name → one of
    ``"ran"`` / ``"skipped:<reason>"`` / ``"errored:<exc_class>:<msg>"``.

    The clean-path verdict ("No concerns from roam critique") used to
    fire even when 0-of-3 checks had actually run cleanly (e.g. user
    hasn't run ``roam clones --persist`` AND no intent text is
    available AND the diff resolves zero changed symbols). Tracking
    status here lets the aggregator emit an honest verdict.

    A check is counted as ``skipped`` when it returned early on a
    structural precondition (no changed symbols, no clone table, no
    intent text) and ``errored`` when it raised an exception. Errored
    checks are caught here so one broken check can't crash the whole
    critique invocation — partial signal beats total failure (Pattern
    1-B).
    """
    findings: list = []
    status: dict[str, str] = {}

    # clones-not-edited
    if not changed_symbols:
        status["clones-not-edited"] = "skipped:no_changed_symbols"
    elif not _has_clone_pairs(conn):
        status["clones-not-edited"] = "skipped:no_clone_pairs (run `roam clones --persist`)"
    else:
        try:
            findings.extend(check_clones_not_edited(conn, changed_symbols, regions))
            status["clones-not-edited"] = "ran"
        except Exception as exc:  # noqa: BLE001 — surface error, never crash
            status["clones-not-edited"] = f"errored:{type(exc).__name__}:{exc}"

    # impact
    if not changed_symbols:
        status["impact"] = "skipped:no_changed_symbols"
    else:
        try:
            findings.extend(check_impact(conn, changed_symbols, high_callers=high_callers))
            status["impact"] = "ran"
        except Exception as exc:  # noqa: BLE001
            status["impact"] = f"errored:{type(exc).__name__}:{exc}"

    # intent
    if not effective_intent:
        status["intent"] = "skipped:no_intent_text"
    elif not changed_symbols:
        status["intent"] = "skipped:no_changed_symbols"
    else:
        try:
            findings.extend(check_intent_alignment(effective_intent, changed_symbols, regions))
            status["intent"] = "ran"
        except Exception as exc:  # noqa: BLE001
            status["intent"] = f"errored:{type(exc).__name__}:{exc}"

    return findings, status


def _critique_one(diff_text: str, high_callers: int, intent_text: str | None) -> tuple[dict, list]:
    """Run the check pipeline against a single diff. Returns (result, findings).

    Pulled out of ``critique`` so ``--batch`` can iterate without
    duplicating the check setup.
    """
    regions = parse_diff(diff_text)
    effective_intent = intent_text
    if effective_intent is None:
        try:
            proc = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if proc.returncode == 0:
                effective_intent = proc.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            effective_intent = None
    with open_db(readonly=True) as conn:
        changed_symbols = find_changed_symbols(conn, regions)
        findings, check_status = _run_checks_with_status(
            conn,
            changed_symbols,
            regions,
            high_callers=high_callers,
            effective_intent=effective_intent,
        )
    result = aggregate(findings, check_status=check_status)
    result["regions"] = regions
    result["changed_symbols"] = changed_symbols
    result["intent"] = effective_intent
    return result, findings


def _run_batch(batch_dir: str, high_callers: int, intent_text: str | None, json_mode: bool, token_budget: int) -> None:
    """review every *.diff / *.patch in ``batch_dir``."""
    from pathlib import Path as _Path

    base = _Path(batch_dir)
    diffs = sorted([*base.glob("*.diff"), *base.glob("*.patch")])
    if not diffs:
        from roam.output.errors import EMPTY_INPUT, structured_usage_error

        raise structured_usage_error(EMPTY_INPUT, f"no *.diff or *.patch files found in {batch_dir}")
    ensure_index()
    per_file = []
    high_count = 0
    for diff_path in diffs:
        try:
            diff_text = diff_path.read_text(encoding="utf-8")
        except OSError as exc:
            per_file.append({"file": diff_path.name, "error": f"read failed: {exc}"})
            continue
        if not diff_text.strip() or not looks_like_unified_diff(diff_text):
            per_file.append({"file": diff_path.name, "error": "not a unified diff"})
            continue
        result, _ = _critique_one(diff_text, high_callers, intent_text)
        high_count += result["severity_breakdown"].get("high", 0)
        # W641-followup-B — per-diff canonical risk_level. Max-severity
        # across the diff's findings; empty findings floor to ``low``.
        per_diff_warnings: list[str] = []
        per_diff_domain = _critique_risk_level(result["findings"], warnings_out=per_diff_warnings)
        per_diff_canonical = normalize_risk_level(per_diff_domain) or "low"
        per_file.append(
            {
                "file": diff_path.name,
                "verdict": result["verdict"],
                "changed_files": len(result["regions"]),
                "changed_symbols": len(result["changed_symbols"]),
                "findings": len(result["findings"]),
                "severity_breakdown": result["severity_breakdown"],
                "risk_level_canonical": per_diff_canonical,
                "risk_rank": risk_rank(per_diff_canonical),
            }
        )
    # W641-followup-B — batch-level canonical risk_level. Max across all
    # per-diff canonical levels (the worst diff drives the batch's
    # gate); empty batches floor to ``low``.
    if per_file:
        _batch_worst_rank = -1
        _batch_worst_level = "low"
        for entry in per_file:
            lvl = entry.get("risk_level_canonical") or "low"
            r = risk_rank(lvl)
            if r > _batch_worst_rank:
                _batch_worst_rank = r
                _batch_worst_level = lvl
        batch_risk_level_canonical = _batch_worst_level
    else:
        batch_risk_level_canonical = "low"
    batch_risk_rank_int = risk_rank(batch_risk_level_canonical)
    base_verdict = (
        f"{len(per_file)} diff(s) reviewed, {high_count} high-severity finding(s)"
        if high_count == 0
        else f"GATE FAIL — {high_count} high-severity finding(s) across {len(per_file)} diff(s)"
    )
    summary = {
        "verdict": f"{base_verdict} (risk_level {batch_risk_level_canonical})",
        "diff_count": len(per_file),
        "high_severity_total": high_count,
        # W641-followup-B — batch-level canonical projection + integer
        # rank. Aggregated max-of-max across the batch (worst diff
        # wins). Mirrors the W641 pr-risk / W641-followup-A cmd_impact
        # contract so cross-command floor comparators read the same
        # axis without re-deriving the rank vocabulary.
        "risk_level_canonical": batch_risk_level_canonical,
        "risk_rank": batch_risk_rank_int,
    }
    batch_envelope = json_envelope(
        "critique",
        summary=summary,
        budget=token_budget,
        diffs=per_file,
        # W641-followup-B — top-level mirrors of summary fields.
        risk_level_canonical=batch_risk_level_canonical,
        risk_rank=batch_risk_rank_int,
    )
    auto_log(batch_envelope, action="critique", target=str(base))
    if json_mode:
        click.echo(to_json(batch_envelope))
    else:
        # W641-followup-B — text-mode verdict also carries the canonical
        # risk_level suffix (already embedded in summary["verdict"] above
        # at envelope build time).
        click.echo(f"VERDICT: {summary['verdict']}")
        click.echo()
        click.echo(f"{'File':<40}  {'Findings':>8}  {'High':>4}  Verdict")
        click.echo(f"{'-' * 40}  {'-' * 8}  {'-' * 4}  {'-' * 30}")
        for entry in per_file:
            if "error" in entry:
                click.echo(f"{entry['file']:<40}  {'—':>8}  {'—':>4}  {entry['error']}")
                continue
            high = entry["severity_breakdown"].get("high", 0)
            click.echo(f"{entry['file']:<40}  {entry['findings']:>8}  {high:>4}  {entry['verdict'][:30]}")
    if high_count > 0:
        from roam.exit_codes import GateFailureError

        raise GateFailureError(f"batch critique: {high_count} high-severity finding(s)")


@roam_capability(
    category="review",
    summary="Verify a patch against the indexed graph — clones-not-edited + blast radius.",
    inputs=["diff_text"],
    outputs=["findings", "verdict"],
    examples=[
        "git diff | roam critique",
        "git diff main..HEAD | roam critique --json",
    ],
    tags=["review", "ci", "gate"],
    ai_safe=True,
    requires_index=True,
    since="12.0",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=True,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read diff from a file instead of stdin.",
)
@click.option(
    "--batch",
    "batch_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=None,
    help="review every *.diff/*.patch in this directory in one pass.",
)
@click.option(
    "--high-callers",
    type=int,
    default=10,
    show_default=True,
    help="Direct-caller threshold above which `impact` emits a medium-severity finding.",
)
@click.option(
    "--intent",
    "intent_text",
    type=str,
    default=None,
    help=(
        "PR title or commit subject to check for alignment with the diff's "
        "semantic shape (e.g. 'fix login bug', 'rename UserSession -> "
        "Session'). Falls back to the latest git commit subject if a git "
        "repo is detected and this flag is omitted."
    ),
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror this invocation's critique findings into the central findings "
        "registry (queryable via `roam findings list --detector critique`). "
        "INVOCATION-SCOPED: critique runs against a specific diff, so the "
        "persisted rows are tied to the diff's file set + intent text. The "
        "diff identifier is stamped into ``evidence_json.diff_sha`` so "
        "consumers can distinguish fresh rows from rows tied to a "
        "since-merged PR. Reruns on the same diff upsert in place; reruns "
        "on a different diff insert fresh rows (older rows stay as audit "
        'trail). Each finding uses subject_kind="diff_region" with '
        "subject_id=NULL; the resolvable symbol id (when one edit covers "
        "exactly one function) lives in evidence_json.affected_symbol_id."
    ),
)
@click.pass_context
def critique(ctx, input_path, batch_dir, high_callers, intent_text, persist):
    """Verify a patch against the indexed graph.

    Pipe a unified diff in via stdin (``git diff | roam critique``) or
    pass a file with ``--input``. The output is a ranked list of
    findings: clone siblings that may need the same change, symbols
    with high blast radius, and intent / dark-matter checks.

    Returns exit code 5 when at least one *high* severity finding is
    present (mirrors ``cmd_rules`` ``EXIT_GATE_FAILURE``) so CI can
    gate on it.

    The JSON envelope carries canonical W631 risk-LEVEL fields
    (``summary.risk_level_canonical`` + ``summary.risk_rank``) projected
    from the max-severity of the aggregated critique findings. Empty
    findings floor to ``risk_level_canonical="low"``. The verdict line
    terminates on ``(risk_level <canonical>)`` so a consumer reading
    only the verdict string parses the canonical bucket directly (LAW 6).

    \b
    Examples:
      git diff | roam critique
      git diff | roam critique --json
      roam critique --input my.patch
      roam critique --batch ./patches/   # process every diff in a dir

    See also ``preflight`` (pre-change safety, before you've drafted a
    diff), ``diff`` (blast radius of working-tree changes), and
    ``rules`` (gate-style policy checks).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if batch_dir:
        if input_path:
            from roam.output.errors import INVALID_OPTIONS, structured_usage_error

            raise structured_usage_error(INVALID_OPTIONS, "--batch and --input are mutually exclusive")
        _run_batch(batch_dir, high_callers, intent_text, json_mode, token_budget)
        return

    if input_path:
        with open(input_path, encoding="utf-8") as fh:
            diff_text = fh.read()
    else:
        if sys.stdin.isatty():
            from roam.output.errors import MISSING_REQUIRED_ARG, structured_usage_error

            raise structured_usage_error(
                MISSING_REQUIRED_ARG,
                "no diff on stdin and no --input — pipe `git diff` in or pass --input PATH",
            )
        diff_text = sys.stdin.read()

    from roam.output.errors import EMPTY_INPUT, INVALID_DIFF, structured_usage_error

    if not diff_text.strip():
        raise structured_usage_error(EMPTY_INPUT, "diff is empty")

    if not looks_like_unified_diff(diff_text):
        # Earlier silent failures: shell substitutions that lost the diff,
        # paste-buffer truncation, or wrong-format input. Erroring loudly
        # here keeps "no concerns" from masking a no-op invocation.
        raise structured_usage_error(
            INVALID_DIFF,
            "input is not a recognisable unified diff "
            "(no diff/--- /+++/@@ headers found). Pass `git diff` output verbatim.",
        )

    ensure_index()

    # W607-Y -- substrate-CALL marker plumbing for cmd_critique. Mirrors the
    # canonical W607 template (latest landed: W607-W cmd_relate). Each
    # substrate boundary inside the critique pipeline (diff parsing, changed-
    # symbol resolution, check fan-out, finding aggregation, override load,
    # bench-hint computation) gets wrapped in ``_run_check`` so a raise
    # surfaces a structured ``critique_<phase>_failed:<exc_class>:<detail>``
    # marker on ``_w607y_warnings_out`` -- the envelope still emits cleanly
    # with whatever signal the remaining substrates produced.
    #
    # The accumulator is intentionally distinct from the existing
    # ``_critique_warnings_out`` bucket (W641-followup-B unknown-severity
    # tracking) so the two axes don't entangle: unknown-severity is a
    # data-shape disclosure (a finding's severity label couldn't be mapped),
    # while W607-Y is a substrate-CALL disclosure (a helper raised before
    # producing its floor value). Both feed the same envelope ``warnings_out``
    # field on emission; ``partial_success`` flips when EITHER bucket is
    # non-empty.
    _w607y_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-Y marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``critique_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607y_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607y_warnings_out.append(f"critique_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BL -- ADDITIVE aggregation-phase plumbing on top of the
    # W607-Y substrate-CALL markers. W607-Y already wrapped the substrate-
    # helper boundaries (parse_diff / find_changed_symbols / run_checks /
    # aggregate / emit_findings / load_overrides / bench_relevance_hint /
    # compute_risk_level); W607-BL extends marker coverage to the
    # AGGREGATION-PHASE boundaries that W607-Y left unguarded:
    #
    #   - ``severity_classify``    -- per-finding severity classification
    #                                 (the inner ``_critique_risk_level``
    #                                 walk; W607-Y wraps the CALL but the
    #                                 inner classify step has its own
    #                                 future-raise surface as the closed
    #                                 severity vocabulary evolves)
    #   - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
    #                                 (normalize_risk_level + risk_rank)
    #                                 mirror of cmd_impact's W607-BB pattern
    #   - ``compute_verdict``      -- augmented_verdict text build with
    #                                 the canonical risk_level suffix
    #   - ``auto_log``             -- active-run ledger write (silent
    #                                 no-op if no run is active, but the
    #                                 underlying ``auto_log`` can still
    #                                 raise on HMAC chain misshape or
    #                                 filesystem failures)
    #   - ``serialize_envelope``   -- ``json_envelope("critique", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions)
    #
    # cmd_critique is the POST-EDIT GATE completing the agent-OS edit
    # loop (pre-edit triangle preflight/impact/diagnose -> edit -> post-
    # edit critique). With W607-BL landed, the agent-OS edit loop is
    # W607-plumbed end-to-end on BOTH the substrate-CALL layer
    # (W607-R + W607-T + W607-S + W607-Y) AND the aggregation-phase
    # layer (W607-AW + W607-BB + W607-BH + W607-BL). Each command has
    # dual-bucket plumbing with combined-warnings emission.
    #
    # Marker family ``critique_*`` -- same family as W607-Y (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope.
    _w607bl_warnings_out: list[str] = []

    def _run_check_bl(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-BL marker emission.

        Mirror of ``_run_check`` shape (same ``critique_<phase>_failed:``
        marker family) but writes into ``_w607bl_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bl_warnings_out.append(f"critique_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    regions = _run_check("parse_diff", parse_diff, diff_text, default=[]) or []

    # Auto-pick up latest commit subject if --intent wasn't passed.
    effective_intent = intent_text
    if effective_intent is None:
        try:
            proc = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if proc.returncode == 0:
                effective_intent = proc.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            effective_intent = None

    with open_db(readonly=not persist) as conn:
        changed_symbols = _run_check("find_changed_symbols", find_changed_symbols, conn, regions, default=[]) or []
        _checks_result = _run_check(
            "run_checks",
            _run_checks_with_status,
            conn,
            changed_symbols,
            regions,
            high_callers=high_callers,
            effective_intent=effective_intent,
            default=([], {}),
        )
        findings, check_status = _checks_result if _checks_result is not None else ([], {})

        result = _run_check(
            "aggregate",
            aggregate,
            findings,
            check_status=check_status,
            default={
                "verdict": "No concerns from roam critique",
                "findings": [],
                "severity_breakdown": {},
                "top_finding": None,
                "check_status": check_status,
                "partial_success": False,
            },
        )

        # --- W153: mirror into the central findings registry ---
        # INVOCATION-SCOPED: critique runs against a specific diff, so the
        # persisted rows carry a ``diff_sha`` (sha1 of label + intent +
        # sorted file paths, matching the W134 pr-risk template) inside
        # ``evidence_json``. Reruns on the same diff upsert in place;
        # reruns on a different diff insert fresh rows so consumers can
        # tell findings apart from rows tied to a since-merged PR.
        # Wrapped in try/except so a pre-W89 schema (no ``findings``
        # table) degrades cleanly. ``aggregate`` is invoked inside the
        # connection scope above so the persist branch sees the same
        # findings list the read path emits.
        if persist:
            try:
                file_list_for_id = sorted({r.file_path for r in regions})
                diff_sha_val = _diff_sha(
                    label=("input:" + input_path) if input_path else "stdin",
                    intent_text=effective_intent,
                    file_paths=file_list_for_id,
                )
                _run_check(
                    "emit_findings",
                    _emit_critique_findings,
                    conn,
                    result["findings"],
                    {
                        "diff_sha": diff_sha_val,
                        "label": ("input:" + input_path) if input_path else "stdin",
                        "intent_text": effective_intent,
                        "file_list": file_list_for_id,
                    },
                    CRITIQUE_DETECTOR_VERSION,
                    default=0,
                )
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) — degrade
                # gracefully. Surface lineage so a non-expected variant
                # (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_critique:emit_findings", _exc)

    # Bench-relevance hint:
    # when the diff touches files in the retrieve / graph / catalog hot
    # path, the default rule set ("clones not edited", "blast radius")
    # can legitimately say "no concerns" while the change quietly
    # alters the structural-rerank scoring formula. Surfacing the bench
    # command makes the verifier conversation include the one
    # validation that actually exercises the modified code. Loaded
    # before output so it lands in BOTH text and JSON.
    overrides = _run_check("load_overrides", _load_critique_overrides, default=[]) or []
    bench_hint = (
        _run_check(
            "bench_relevance_hint",
            _bench_relevance_hint,
            regions,
            overrides=overrides,
            default="",
        )
        or ""
    )

    # W641-followup-B — canonical W631 risk-LEVEL projection from the
    # max-severity of the aggregated findings. Empty findings safe-floor
    # to ``low`` (W531 CI-safety: a no-findings result must NOT promote
    # into a CI-failing rank). Unknown severities accumulate a marker on
    # ``_critique_warnings_out`` so Pattern-2 silent-fallback discipline
    # stays loud — the projection safe-floors to ``low`` but the marker
    # disambiguates a real-low finding from an unrecognised-label drop.
    _critique_warnings_out: list[str] = []
    # W607-Y -- substrate-CALL boundary on ``_critique_risk_level``
    # (kept intact). Pinned by tests/test_w607_y_cmd_critique_warnings_out_envelope.py.
    _critique_domain_level_y = _run_check(
        "compute_risk_level",
        _critique_risk_level,
        result["findings"],
        warnings_out=_critique_warnings_out,
        default="low",
    )
    if _critique_domain_level_y is None:
        _critique_domain_level_y = "low"
    # W607-BL -- severity_classify boundary, ADDITIVE on top of the
    # W607-Y substrate-CALL wrap. We re-classify with the SAME helper
    # but flag the result on the AGGREGATION-PHASE axis: if the
    # classifier raises (a closed-vocabulary refactor or future inner
    # inspection helper), the wrap floors the domain tier to ``None``
    # and surfaces the marker alongside the canonical ``"low"`` floor +
    # ``severity_classification: "unknown"`` sentinel in the envelope
    # summary. Mirror of cmd_impact W607-BB / cmd_diagnose W607-BH
    # severity_classification sentinel.
    _bl_severity_probe = _run_check_bl(
        "severity_classify",
        _critique_risk_level,
        result["findings"],
        warnings_out=_critique_warnings_out,
        default=None,
    )
    # Domain-tier raised (None floor) -> mark classification unknown so
    # the envelope discloses the degraded outcome. When W607-BL probe
    # succeeded (non-None), classification is "classified".
    _severity_classification_state = "unknown" if _bl_severity_probe is None else "classified"
    # Use the W607-Y domain level for the canonical projection (the
    # W607-Y wrap already floors to "low" on raise); the BL probe is
    # the additive aggregation-phase boundary that drives the
    # severity_classification sentinel.
    _critique_domain_level = _critique_domain_level_y
    # W607-BL -- severity_normalize boundary. Wraps the canonical W631
    # ``normalize_risk_level`` + ``risk_rank`` projections so a future
    # signature change / closed-enum vocabulary drift surfaces a marker
    # rather than crashing the envelope. Floors to ``"low"`` / rank ``1``
    # so downstream comparators stay non-null. Mirror of cmd_impact
    # W607-BB / cmd_diagnose W607-BH severity_normalize pattern.
    risk_level_canonical = _run_check_bl(
        "severity_normalize",
        lambda level: normalize_risk_level(level) or "low",
        _critique_domain_level,
        default="low",
    )
    risk_rank_int = _run_check_bl(
        "severity_normalize",
        risk_rank,
        risk_level_canonical,
        default=1,
    )

    # W607-BL -- compute_verdict boundary. Wraps the canonical augmented
    # verdict text build so a future format-spec regression on
    # ``result["verdict"]`` (e.g. non-string verdict from an aggregator
    # shape refactor) surfaces a marker rather than crashing the
    # envelope. Floors to a stable verdict string so LAW 6 ("verdict
    # works standalone") stays satisfied even on degraded paths.
    def _build_augmented_verdict() -> str:
        # Verdict augmentation per LAW 6 (line works standalone): append the
        # canonical risk_level in a closed-enum parenthesis so a consumer
        # parsing only the verdict string sees the canonical bucket directly.
        return f"{result['verdict']} (risk_level {risk_level_canonical})"

    augmented_verdict = _run_check_bl(
        "compute_verdict",
        _build_augmented_verdict,
        default=f"critique completed (risk_level {risk_level_canonical})",
    )

    summary = {
        "verdict": augmented_verdict,
        "changed_files": len(regions),
        "changed_symbols": len(changed_symbols),
        "findings": len(result["findings"]),
        "high_severity": result["severity_breakdown"].get("high", 0),
        # W641-followup-B — canonical W631 risk-LEVEL projection +
        # integer rank. Aggregated via max-severity across the
        # critique's per-region findings (the worst finding's projected
        # risk_level wins). Empty findings safe-floor to ``low`` (rank
        # 1). Mirrors the W641 (pr-risk) + W641-followup-A (cmd_impact)
        # contract so cross-command floor comparators
        # (``risk_rank(summary.risk_level_canonical) >= 3`` to gate on
        # high-or-worse) work without each consumer re-deriving the
        # rank vocabulary at the call site (Pattern-3a).
        "risk_level_canonical": risk_level_canonical,
        "risk_rank": risk_rank_int,
        # W607-BL -- SEVERITY-CLASSIFY DEGRADATION sentinel. When the
        # ``severity_classify`` boundary raises (and the classify result
        # floors to ``None``), surface ``severity_classification:
        # "unknown"`` so the agent sees the degraded outcome alongside
        # the canonical floor ("low") rather than mistaking the floor
        # for a real classification. Clean path -> ``"classified"``.
        # Mirror of cmd_impact's ``risk_classification`` /
        # cmd_diagnose's ``severity_classification`` sentinel.
        "severity_classification": _severity_classification_state,
        "intent": effective_intent,
        "bench_hint": bench_hint or None,
        # W832 — disclose per-check status so consumers can tell
        # "0 concerns because clean" apart from "0 concerns because
        # nothing ran". ``state`` is closed-enum:
        # ``all_checks_ran`` | ``partial_critique``.
        "check_status": result.get("check_status", {}),
        "partial_success": result.get("partial_success", False),
        "state": ("partial_critique" if result.get("partial_success") else "all_checks_ran"),
    }
    # W641-followup-B — record any unknown-severity drops on the
    # summary so Pattern-2 silent-fallback stays visible. Non-empty
    # ``warnings_out`` flips ``partial_success`` (mirrors W989 pr-risk
    # + W918 alerts discipline) so a downstream consumer reading
    # ``partial_success`` alone sees the degradation.
    #
    # W607-Y — substrate-CALL markers ride the same ``warnings_out`` channel
    # but accumulate in a DIFFERENT bucket (``_w607y_warnings_out``) so the
    # two axes (unknown-severity data shape vs. helper-raised substrate
    # boundary) don't conflate at the call site. They merge into a single
    # ``warnings_out`` list on emission; the marker PREFIX disambiguates
    # them downstream (``critique_unknown_severity:*`` vs.
    # ``critique_<phase>_failed:*``). ``partial_success`` flips when EITHER
    # bucket is non-empty -- consumers reading ``partial_success`` alone
    # need not distinguish the two flavours.
    #
    # W607-BL -- ADDITIVE aggregation-phase markers join the same
    # combined-channel: ``_critique_warnings_out`` (unknown-severity) +
    # ``_w607y_warnings_out`` (substrate-CALL) +
    # ``_w607bl_warnings_out`` (aggregation-phase). All three share the
    # ``critique_*`` family per the marker-prefix discipline test; the
    # additive bucket stays distinguishable in tests + audits via its
    # phase names (``severity_classify`` / ``severity_normalize`` /
    # ``compute_verdict`` / ``auto_log`` / ``serialize_envelope``).
    _combined_warnings_out: list[str] = (
        list(_critique_warnings_out) + list(_w607y_warnings_out) + list(_w607bl_warnings_out)
    )
    if _combined_warnings_out:
        summary["warnings_out"] = list(_combined_warnings_out)
        summary["partial_success"] = True
        summary["state"] = "partial_critique"

    _envelope_kwargs: dict = dict(
        summary=summary,
        budget=token_budget,
        severity_breakdown=result["severity_breakdown"],
        findings=result["findings"],
        top_finding=result["top_finding"],
        bench_hint=bench_hint,
        check_status=result.get("check_status", {}),
        # W641-followup-B — top-level mirrors of summary.risk_level_canonical
        # / summary.risk_rank so consumers that read the top-level envelope
        # directly (without descending into ``summary``) see the canonical
        # bucket. Mirror of the W641-followup-A cmd_impact contract.
        risk_level_canonical=risk_level_canonical,
        risk_rank=risk_rank_int,
        changed_symbols=[
            {
                "symbol_id": s.symbol_id,
                "name": s.name,
                "qualified_name": s.qualified_name,
                "kind": s.kind,
                "file_path": s.file_path,
                "line_start": s.line_start,
                "line_end": s.line_end,
            }
            for s in changed_symbols
        ],
    )
    # W607-Y / W607-BL — top-level mirror of summary.warnings_out so
    # consumers that read the top-level envelope directly (without
    # descending into ``summary``) see the marker channel. Mirror parity
    # with W607-W cmd_relate (and the rest of the W607 family).
    if _combined_warnings_out:
        _envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
        _envelope_kwargs["partial_success"] = True
    # W607-BL -- serialize_envelope boundary. Wraps the envelope
    # serialization itself. A downstream schema-shape refactor that
    # breaks ``json_envelope("critique", ...)`` would otherwise crash
    # AFTER all substrate + aggregation signals were already gathered.
    # Floor to a minimal envelope stub so consumers still receive a
    # parseable JSON object with the marker attached + the canonical
    # command name. Mirror of cmd_diagnose's W607-BH serialize_envelope
    # floor pattern.
    _envelope_floor: dict = {
        "command": "critique",
        "schema_version": "1.0.0",
        "summary": {
            "verdict": augmented_verdict,
            "partial_success": True,
            "warnings_out": list(_combined_warnings_out),
        },
        "warnings_out": list(_combined_warnings_out),
    }
    critique_envelope = _run_check_bl(
        "serialize_envelope",
        json_envelope,
        "critique",
        default=_envelope_floor,
        **_envelope_kwargs,
    )
    # W607-BL -- if ``serialize_envelope`` raised AFTER the combined
    # bucket was already snapshotted, the new
    # ``critique_serialize_envelope_failed:`` marker was appended to
    # ``_w607bl_warnings_out`` and the floor stub carries only the old
    # combined list. Rebuild the floor stub's warnings_out so the new
    # marker reaches the JSON output. Clean path -> envelope is the
    # real json_envelope return value, no rebuild needed.
    if critique_envelope is _envelope_floor and _w607bl_warnings_out:
        _combined_warnings_out = list(_critique_warnings_out) + list(_w607y_warnings_out) + list(_w607bl_warnings_out)
        _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
        _envelope_floor["warnings_out"] = list(_combined_warnings_out)
        critique_envelope = _envelope_floor
    # Auto-log into the active run; target is the intent string (e.g. PR
    # title / commit subject) when available, else the input path.
    # W607-BL -- auto_log boundary. Silent no-op if no active run; the
    # wrap surfaces HMAC chain-misshape / filesystem failures as
    # ``critique_auto_log_failed:...`` markers instead of crashing the
    # envelope after it was already built. Mirror of cmd_diagnose's
    # W607-BH auto_log emit pattern.
    _critique_target = effective_intent or (input_path or "")
    _run_check_bl(
        "auto_log",
        auto_log,
        critique_envelope,
        action="critique",
        target=_critique_target,
        default=None,
    )
    # W607-BL -- if ``auto_log`` raised, rebuild the envelope so the
    # marker reaches the JSON output. Empty bucket (clean auto_log) ->
    # envelope stays byte-identical to the version already built above.
    if _w607bl_warnings_out and not any(
        m.startswith("critique_auto_log_failed:") for m in (summary.get("warnings_out") or [])
    ):
        _combined_warnings_out = list(_critique_warnings_out) + list(_w607y_warnings_out) + list(_w607bl_warnings_out)
        summary["warnings_out"] = list(_combined_warnings_out)
        summary["partial_success"] = True
        summary["state"] = "partial_critique"
        _envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
        _envelope_kwargs["partial_success"] = True
        critique_envelope = _run_check_bl(
            "serialize_envelope",
            json_envelope,
            "critique",
            default=_envelope_floor,
            **_envelope_kwargs,
        )

    if sarif_mode:
        # W1146: SARIF projection for CI / GitHub Code Scanning integration.
        # The auto_log call above + the exit-5 gate below stay identical to
        # the JSON / text paths so the audit ledger and CI behaviour are
        # invariant across output formats. The --text / --json paths are
        # byte-identical to pre-W1146 (this branch short-circuits before
        # the legacy branches; nothing above it changed shape).
        from roam.output.sarif import critique_to_sarif, write_sarif

        sarif = critique_to_sarif(result["findings"])
        click.echo(write_sarif(sarif))
    elif json_mode:
        click.echo(to_json(critique_envelope))
    else:
        # W641-followup-B — the text-mode verdict also carries the
        # canonical risk_level suffix so a human-readable run discloses
        # the bucket (LAW 6 — the line works standalone).
        click.echo(f"VERDICT: {augmented_verdict}")
        click.echo()
        click.echo(f"  changed files:   {len(regions)}")
        click.echo(f"  changed symbols: {len(changed_symbols)}")
        if result["findings"]:
            click.echo()
            for f in result["findings"]:
                click.echo(f"[{f['severity'].upper()}] {f['check']} :: {f['title']}")
                for line in f["detail"].splitlines():
                    click.echo(f"    {line}")
                click.echo()

        if bench_hint:
            click.echo()
            click.echo(f"BENCH HINT: {bench_hint}")

        # — point at the natural next command.
        from roam.commands.next_steps import format_next_steps_text, suggest_next_steps

        _ns = suggest_next_steps(
            "critique",
            {
                "high_severity": result["severity_breakdown"].get("high", 0),
                "bench_hint": bench_hint,
            },
        )
        _ns_text = format_next_steps_text(_ns)
        if _ns_text:
            click.echo(_ns_text)

    if result["severity_breakdown"].get("high", 0) > 0:
        ctx.exit(5)
