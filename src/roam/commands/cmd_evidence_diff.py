"""``roam evidence-diff`` — diff two ``ChangeEvidence`` packets.

W225. Reveals hash drift, schema drift, added/removed refs, missing or
new evidence, and changed verdicts between two on-disk evidence packets.
Per the W174 evidence-compiler thesis: an evidence packet is portable
audit material that travels with a code change. ``evidence-diff`` is the
reviewer-facing surface that compares two such packets — typically the
"before" and "after" of a re-run, or two runs of the same PR — and
classifies the deltas as **regressions** (evidence got worse), **drift**
(content changed without an obvious quality direction), or
**improvements** (evidence got better).

Behaviour:

* Loads each packet from disk as raw JSON (we deliberately do NOT
  reconstruct ``ChangeEvidence`` instances — the file may be from a
  newer schema this binary doesn't fully understand, and the diff must
  still work on a best-effort basis).
* Set-diffs the W182 ref lists by (kind, id) tuples (identity, not
  display name).
* Set-diffs ``findings[]`` by ``finding_id_str`` and ``artifacts[]`` by
  ``artifact_id``.
* Compares the 8-question ``evidence_completeness()`` projection
  (Q1..Q8) — but recomputes it locally from each packet's raw fields so
  the diff stays valid even on pre-W210 packets that don't carry the
  method-derived projection.
* Classifies completeness deltas:
    - **regression** when a Q dropped DOWN the ladder
      (complete -> partial / missing, partial -> missing).
    - **improvement** when a Q moved UP.
  ``not_applicable`` is treated as neither side of the ladder (so a
  Q5 transition into / out of ``not_applicable`` is just "drift" — it's
  recorded under ``changed_completeness`` but not as
  regression/improvement).
* Verdict-first JSON envelope; text mode renders a single-line verdict
  + per-section groupings similar to ``roam diff`` text output.

The command is intentionally tolerant of older / partial / wrong-shape
files: missing top-level keys are treated as "absent", not as crashes.
This mirrors the Phase 2 collector contract (``collect_change_evidence``
warns when a field can't map; the diff surfaces the resulting absences
without escalating to errors).

NON-GOALS:

* No content-hash *recomputation*. The diff trusts the ``content_hash``
  field that each packet carries; it does not re-canonicalise the packet
  and re-derive the hash. (Verification of hash integrity is the job of
  ``roam attest`` / ``pr-bundle validate``, not the diff.)
* No semantic merge. Conflicting verdicts surface as ``changed_verdicts``
  entries; deciding which is correct is left to the reviewer.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because evidence-diff outputs describe the delta between two
``ChangeEvidence`` JSON PACKETS (regressions / improvements / drift in
the 8-question completeness ladder + W182 ref-list set diffs) — not
per-location code violations in user source. The diff describes
evidence-packet shape, not findings against source coordinates. See
``cmd_rules_validate`` for the parallel validator-not-detector
disclosure pattern + action.yml _SUPPORTED_SARIF allowlist + W1192
audit memo + W1224-audit memo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import click

from roam.capability import roam_capability
from roam.evidence.completeness_compat import compute_completeness
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Completeness ladder
# ---------------------------------------------------------------------------

# Ladder used to classify completeness transitions. Higher rank = better
# evidence. ``not_applicable`` sits outside the ladder (rank = None) so a
# transition into / out of it is "drift", not regression/improvement —
# this matches how ``ChangeEvidence.evidence_completeness`` already
# treats Q5's "SAFE verdict + no findings" branch.
_COMPLETENESS_RANK: Mapping[str, int] = {
    "missing": 0,
    "partial": 1,
    "complete": 2,
}

# The 8 evidence questions, in canonical Q1..Q8 order. Mirrors
# ``ChangeEvidence.evidence_completeness``.
_Q_KEYS: tuple[str, ...] = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")


# ---------------------------------------------------------------------------
# Loaders / helpers
# ---------------------------------------------------------------------------


def _load_packet(path: str | Path) -> dict[str, Any]:
    """Load + parse a ChangeEvidence JSON packet from disk.

    Tolerates missing optional fields (older schema versions) — we
    return a raw dict, NOT a reconstructed ``ChangeEvidence`` instance,
    so a packet that's a few fields short of a current build still
    loads. The caller is responsible for ``.get(...)`` with sensible
    fallbacks on every field it touches.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise click.ClickException(f"evidence packet at {path!s} is not a JSON object (got {type(payload).__name__})")
    return payload


def _diff_refs(
    old: dict[str, Any],
    new: dict[str, Any],
    key: str,
    kind_field: str,
    id_field: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Set-diff one ref-list field by ``(kind, id)`` tuple.

    Returns ``(added, removed)``. Each element of the returned lists is
    the *original* ref dict from the corresponding packet — callers get
    full payload, not just the identity tuple.

    ``key``        - top-level key on the packet (e.g. ``"actor_refs"``).
    ``kind_field`` - sub-key naming the *kind* (e.g. ``"actor_kind"``).
    ``id_field``   - sub-key naming the *stable id* (e.g. ``"actor_id"``).
    """
    old_list = old.get(key) or []
    new_list = new.get(key) or []
    # Defensive: a malformed packet might put a string here.
    if not isinstance(old_list, list):
        old_list = []
    if not isinstance(new_list, list):
        new_list = []

    def _identity(ref: Any) -> tuple[str, str] | None:
        if not isinstance(ref, Mapping):
            return None
        k = ref.get(kind_field)
        i = ref.get(id_field)
        if not isinstance(k, str) or not isinstance(i, str):
            return None
        return (k, i)

    old_ids = {_identity(r): r for r in old_list if _identity(r) is not None}
    new_ids = {_identity(r): r for r in new_list if _identity(r) is not None}

    added = [new_ids[k] for k in new_ids.keys() - old_ids.keys()]
    removed = [old_ids[k] for k in old_ids.keys() - new_ids.keys()]
    return added, removed


def _diff_findings(
    old: dict[str, Any],
    new: dict[str, Any],
) -> tuple[list[Any], list[Any]]:
    """Set-diff the ``findings[]`` list by ``finding_id_str``.

    Each finding row is a free-form ``Mapping`` per the schema-v0
    contract (rich types are punted to a later phase) — we use
    ``finding_id_str`` as the stable identity. Findings without that
    key are ignored for the set-diff (they can't be matched safely).
    """
    old_list = old.get("findings") or []
    new_list = new.get("findings") or []
    if not isinstance(old_list, list):
        old_list = []
    if not isinstance(new_list, list):
        new_list = []

    def _id(row: Any) -> str | None:
        if not isinstance(row, Mapping):
            return None
        v = row.get("finding_id_str")
        return v if isinstance(v, str) else None

    old_ids = {_id(r): r for r in old_list if _id(r) is not None}
    new_ids = {_id(r): r for r in new_list if _id(r) is not None}
    added = [new_ids[k] for k in new_ids.keys() - old_ids.keys()]
    removed = [old_ids[k] for k in old_ids.keys() - new_ids.keys()]
    return added, removed


def _diff_artifacts(
    old: dict[str, Any],
    new: dict[str, Any],
) -> tuple[list[Any], list[Any]]:
    """Set-diff ``artifacts[]`` by ``artifact_id``."""
    old_list = old.get("artifacts") or []
    new_list = new.get("artifacts") or []
    if not isinstance(old_list, list):
        old_list = []
    if not isinstance(new_list, list):
        new_list = []

    def _id(row: Any) -> str | None:
        if not isinstance(row, Mapping):
            return None
        v = row.get("artifact_id")
        return v if isinstance(v, str) else None

    old_ids = {_id(r): r for r in old_list if _id(r) is not None}
    new_ids = {_id(r): r for r in new_list if _id(r) is not None}
    added = [new_ids[k] for k in new_ids.keys() - old_ids.keys()]
    removed = [old_ids[k] for k in old_ids.keys() - new_ids.keys()]
    return added, removed


def _diff_completeness(
    old: dict[str, Any],
    new: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Compare the 8-question completeness projection of two packets.

    Returns ``(changed, regressions, improvements)`` — each is a list of
    ``{"q": "QN", "old": "...", "new": "..."}`` dicts. A Q only shows up
    in ``changed`` when its value differs between packets; among those,
    transitions DOWN the
    ``missing < partial < complete`` ladder land in ``regressions``,
    transitions UP land in ``improvements``. Transitions to/from
    ``not_applicable`` are recorded in ``changed`` but not categorised
    on either side of the ladder (the ladder simply doesn't apply).
    """
    # W1266 - shared raw-dict helper. Mirrors W1254 stale-demotion: a
    # stale-but-otherwise-complete packet's Qs show up here as PARTIAL,
    # so a fresh -> stale re-run lands as a regression in the ladder.
    old_q = compute_completeness(old)
    new_q = compute_completeness(new)

    changed: list[dict[str, str]] = []
    regressions: list[dict[str, str]] = []
    improvements: list[dict[str, str]] = []

    for q in _Q_KEYS:
        a = old_q.get(q, "missing")
        b = new_q.get(q, "missing")
        if a == b:
            continue
        entry = {"q": q, "old": a, "new": b}
        changed.append(entry)
        ra = _COMPLETENESS_RANK.get(a)
        rb = _COMPLETENESS_RANK.get(b)
        if ra is None or rb is None:
            # One end is not_applicable - it's drift, neither regression
            # nor improvement.
            continue
        if rb < ra:
            regressions.append(entry)
        elif rb > ra:
            improvements.append(entry)

    return changed, regressions, improvements


def _diff_scalar_fields(
    old: dict[str, Any],
    new: dict[str, Any],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Return a list of ``{"field": ..., "old": ..., "new": ...}`` for
    every scalar field where the two packets disagree (``None`` counts
    as a value — a transition from ``None`` to ``"REVIEW"`` is a real
    delta the reviewer should see)."""
    changes: list[dict[str, Any]] = []
    for f in fields:
        a = old.get(f)
        b = new.get(f)
        if a != b:
            changes.append({"field": f, "old": a, "new": b})
    return changes


def _build_verdict(
    hash_drift: bool,
    schema_drift: bool,
    regressions: int,
    improvements: int,
    changed_verdicts: int,
) -> str:
    """One-line summary verdict, regression-priority-first."""
    if regressions > 0:
        return (
            f"{regressions} evidence regressions "
            f"(also: {improvements} improvements, "
            f"{changed_verdicts} changed verdicts)"
        )
    if schema_drift:
        return "schema_version changed between packets"
    if changed_verdicts > 0:
        return f"{changed_verdicts} changed verdicts (no completeness regressions)"
    if hash_drift:
        if improvements > 0:
            return f"content_hash changed; {improvements} evidence improvements"
        return "content_hash changed with no completeness regressions"
    if improvements > 0:
        return f"{improvements} evidence improvements"
    return "no drift between packets"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="evidence-diff",
    category="review",
    summary="Diff two ChangeEvidence packets and classify deltas.",
    inputs=["old_path", "new_path"],
    outputs=["verdict", "hash_drift", "schema_drift", "regressions"],
    examples=[
        "roam evidence-diff old.json new.json",
        "roam --json evidence-diff before.json after.json",
    ],
    tags=["evidence", "review", "diff"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("evidence-diff")
@click.argument(
    "old_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.argument(
    "new_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.pass_context
def evidence_diff(ctx, old_path, new_path):
    """Diff two ``ChangeEvidence`` packets.

    Shows hash drift, schema drift, added/removed refs, missing
    evidence, and changed verdicts between OLD_PATH and NEW_PATH.
    Useful for reviewing re-runs of the same PR, comparing two replay
    windows, or auditing whether a fresh evidence packet has improved
    or regressed against a stored baseline.

    Both packets are loaded from disk as raw JSON — older / partial
    files load cleanly and missing optional fields are treated as
    absent (not as crashes).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    # --- W607-AX: substrate-CALL marker plumbing -------------------------
    # cmd_evidence_diff is the SIBLING validator to cmd_evidence_doctor
    # (W607-AT). Both consume the W1266 raw-dict completeness scorer per
    # the docstring at evidence/completeness_compat.py: "Both
    # cmd_evidence_doctor and cmd_evidence_diff recompute the W210
    # evidence_completeness() projection locally." Plumbing both closes
    # the raw-dict-completeness boundary for the validator family.
    #
    # Substrate boundaries wrapped here:
    #
    #   load_packet_old / load_packet_new   (JSON read + parse x2)
    #   diff_refs_actor / diff_refs_authority / diff_refs_environment
    #   diff_findings
    #   diff_artifacts
    #   diff_completeness     (W1266 raw-dict completeness scorer)
    #   diff_scalar_verdicts  (verdict / risk_level scalar drift)
    #   diff_scalar_timing    (W210 timing drift)
    #   extract_stale_old / extract_stale_new   (W1262 staleness)
    #   build_verdict         (one-line summary scorer)
    #
    # Each raise becomes an
    # ``evidence_diff_<phase>_failed:<exc_class>:<detail>`` marker via
    # ``_w607ax_warnings_out``. partial_success flips on any non-empty
    # bucket. Empty bucket on the clean path keeps the envelope shape
    # byte-identical to the pre-W607-AX command.
    #
    # PATTERN-2 CHECK: pre-W607-AX cmd_evidence_diff has ZERO bare
    # ``except ...: pass`` Pattern-2 silent fallbacks. The defensive
    # ``isinstance(... , list)`` checks in _diff_refs / _diff_findings /
    # _diff_artifacts return structured empties (not silent passes), so
    # there is no Pattern-2 antipattern to eliminate. The AST drift-guard
    # (test 11 below) pins this for the future.
    #
    # VALIDATOR-FAMILY CLOSURE milestone: with W607-AT (doctor) and
    # W607-AX (diff) both plumbed, the evidence-compiler validator family
    # surfaces markers on the SAME shared boundary (W1266 raw-dict
    # completeness scorer). A raise anywhere in either validator surfaces
    # a marker rather than crashing.
    _w607ax_warnings_out: list[str] = []

    def _run_check_ax(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AX marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``evidence_diff_<phase>_failed:<exc_class>:<detail>`` marker
        via ``_w607ax_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ax_warnings_out.append(f"evidence_diff_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CK: aggregation-phase marker plumbing (additive) -----------
    # cmd_evidence_diff is the ChangeEvidence-packet diff renderer (W225
    # origin). With W607-AX covering the substrate-CALL layer (13 phases),
    # W607-CK additively wraps the AGGREGATION-PHASE layer that sits ON
    # TOP of those substrate signals:
    #
    #   - ``compute_drift_summary`` -- build the summary dict
    #                                  (drift counts + verdict carry).
    #                                  Floors to a minimal summary so the
    #                                  envelope still emits with the
    #                                  W607-AX-derived signals intact.
    #   - ``compute_verdict``       -- final verdict text (carries the
    #                                  literal-floor discipline; cmd_evidence_diff
    #                                  does NOT emit risk_level so the floor
    #                                  is a LITERAL "evidence-diff completed
    #                                  (risk_level low)" rather than an
    #                                  f-string interpolation of upstream
    #                                  values).
    #   - ``auto_log``              -- active-run ledger write. cmd_evidence_diff
    #                                  did NOT auto-log pre-W607-CK; W607-CK
    #                                  ADDS the call inside the wrap so an
    #                                  HMAC chain-misshape / filesystem
    #                                  failure surfaces a marker rather than
    #                                  crashing the envelope.
    #   - ``serialize_envelope``    -- ``json_envelope("evidence-diff", ...)``
    #                                  projection. Floor to a parseable
    #                                  stub so consumers still receive
    #                                  structured JSON with the marker
    #                                  attached + the canonical command
    #                                  name.
    #
    # All boundaries share the canonical ``evidence_diff_*`` marker family
    # (same as W607-AX; W607-CK is ADDITIVE, not a separate prefix). The
    # two buckets (``_w607ax_warnings_out`` substrate-CALL +
    # ``_w607ck_warnings_out`` aggregation-phase) combine at envelope-emit
    # time so consumers see the full degradation lineage.
    #
    # EVIDENCE-COMPILER QUARTET milestone: with cmd_pr_bundle (W607-AE+BW),
    # cmd_pr_replay (W607-AH+CA), cmd_evidence_doctor (W607-AT+CF), and
    # cmd_evidence_diff (W607-AX+CK), every evidence-compiler-layer
    # command carries substrate-CALL + aggregation-phase W607 coverage.
    _w607ck_warnings_out: list[str] = []

    def _run_check_ck(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CK marker emission.

        Mirror of ``_run_check_ax`` shape (same
        ``evidence_diff_<phase>_failed:`` marker family) but writes into
        ``_w607ck_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ck_warnings_out.append(f"evidence_diff_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    old = _run_check_ax("load_packet_old", _load_packet, old_path, default={})
    if old is None:
        old = {}
    new = _run_check_ax("load_packet_new", _load_packet, new_path, default={})
    if new is None:
        new = {}

    # Hash drift
    old_hash = old.get("content_hash")
    new_hash = new.get("content_hash")
    hash_drift_block: dict[str, Any] | None = None
    if old_hash != new_hash:
        hash_drift_block = {"old": old_hash, "new": new_hash}

    # Schema drift
    old_schema = old.get("schema_version")
    new_schema = new.get("schema_version")
    schema_drift_block: dict[str, Any] | None = None
    if old_schema != new_schema:
        schema_drift_block = {"old": old_schema, "new": new_schema}

    # Refs (W182)
    actor_pair = _run_check_ax(
        "diff_refs_actor",
        _diff_refs,
        old,
        new,
        "actor_refs",
        "actor_kind",
        "actor_id",
        default=([], []),
    )
    added_actor, removed_actor = actor_pair if actor_pair is not None else ([], [])
    authority_pair = _run_check_ax(
        "diff_refs_authority",
        _diff_refs,
        old,
        new,
        "authority_refs",
        "authority_kind",
        "authority_id",
        default=([], []),
    )
    added_authority, removed_authority = authority_pair if authority_pair is not None else ([], [])
    env_pair = _run_check_ax(
        "diff_refs_environment",
        _diff_refs,
        old,
        new,
        "environment_refs",
        "env_kind",
        "env_id",
        default=([], []),
    )
    added_env, removed_env = env_pair if env_pair is not None else ([], [])

    added_refs = {
        "actor_refs": added_actor,
        "authority_refs": added_authority,
        "environment_refs": added_env,
    }
    removed_refs = {
        "actor_refs": removed_actor,
        "authority_refs": removed_authority,
        "environment_refs": removed_env,
    }

    # Verdict-level scalars
    changed_verdicts = _run_check_ax(
        "diff_scalar_verdicts",
        _diff_scalar_fields,
        old,
        new,
        ("verdict", "risk_level"),
        default=[],
    )
    if changed_verdicts is None:
        changed_verdicts = []

    # Findings + artifacts
    findings_pair = _run_check_ax(
        "diff_findings",
        _diff_findings,
        old,
        new,
        default=([], []),
    )
    added_findings, removed_findings = findings_pair if findings_pair is not None else ([], [])
    artifacts_pair = _run_check_ax(
        "diff_artifacts",
        _diff_artifacts,
        old,
        new,
        default=([], []),
    )
    added_artifacts, removed_artifacts = artifacts_pair if artifacts_pair is not None else ([], [])

    # Completeness deltas (W210 item 6). W1266 - shared raw-dict scorer.
    # A raise here is the validator-family closure boundary: same shared
    # surface as cmd_evidence_doctor's classify_completeness phase.
    completeness_triple = _run_check_ax(
        "diff_completeness",
        _diff_completeness,
        old,
        new,
        default=([], [], []),
    )
    changed_completeness, regressions, improvements = (
        completeness_triple if completeness_triple is not None else ([], [], [])
    )

    # Timing drift (W210 item 2) — the three change-scope timestamps.
    timing_drift = _run_check_ax(
        "diff_scalar_timing",
        _diff_scalar_fields,
        old,
        new,
        ("context_read_at", "edits_started_at", "edits_completed_at"),
        default=[],
    )
    if timing_drift is None:
        timing_drift = []

    # W1262: staleness signal (W1254 producer). Read ``evidence_stale``
    # + ``stale_reasons`` from each packet so the diff surfaces the
    # W1234 producer signal alongside the coverage delta. Always-emit
    # (Pattern-2): consumers see ``stale=False`` + empty reasons as a
    # real signal, not a missing-data case.
    def _extract_stale(packet: dict[str, Any]) -> tuple[bool, list[str]]:
        raw = packet.get("evidence_stale")
        flag = bool(raw) if isinstance(raw, bool) else False
        raw_reasons = packet.get("stale_reasons") or []
        reasons = [r for r in raw_reasons if isinstance(r, str)] if isinstance(raw_reasons, list) else []
        return flag, reasons

    old_stale_pair = _run_check_ax(
        "extract_stale_old",
        _extract_stale,
        old,
        default=(False, []),
    )
    old_stale, old_stale_reasons = old_stale_pair if old_stale_pair is not None else (False, [])
    new_stale_pair = _run_check_ax(
        "extract_stale_new",
        _extract_stale,
        new,
        default=(False, []),
    )
    new_stale, new_stale_reasons = new_stale_pair if new_stale_pair is not None else (False, [])
    stale_drift = (old_stale != new_stale) or (sorted(old_stale_reasons) != sorted(new_stale_reasons))

    # Verdict + summary counts
    verdict = _run_check_ax(
        "build_verdict",
        _build_verdict,
        hash_drift=hash_drift_block is not None,
        schema_drift=schema_drift_block is not None,
        regressions=len(regressions),
        improvements=len(improvements),
        changed_verdicts=len(changed_verdicts),
        default="verdict scorer raised; see warnings_out",
    )
    if verdict is None:
        verdict = "verdict scorer raised; see warnings_out"

    # W607-CK -- compute_drift_summary boundary. Builds the drift-totals
    # summary block from the W607-AX substrate signals. Floors to a
    # MINIMAL summary so the envelope still emits with the substrate
    # signals intact when a future refactor breaks the dict-build path
    # (e.g. a stale ``str``/``int`` cast that raises). W978 literal-floor
    # discipline: the floor's ``verdict`` is a LITERAL string, NOT an
    # f-string-interpolated copy of the upstream ``verdict`` -- because
    # the same value that tripped the closure could re-raise inside the
    # default f-string formatter. Mirror of cmd_evidence_doctor W607-CF
    # compute_verdict floor discipline.
    def _build_drift_summary() -> dict:
        return {
            "verdict": verdict,
            "partial_success": False,
            "hash_drift": hash_drift_block is not None,
            "schema_drift": schema_drift_block is not None,
            "regressions": len(regressions),
            "improvements": len(improvements),
            "changed_verdicts": len(changed_verdicts),
            "added_refs_total": (len(added_actor) + len(added_authority) + len(added_env)),
            "removed_refs_total": (len(removed_actor) + len(removed_authority) + len(removed_env)),
            "added_findings": len(added_findings),
            "removed_findings": len(removed_findings),
            "added_artifacts": len(added_artifacts),
            "removed_artifacts": len(removed_artifacts),
            # W1262: staleness signal pair from W1254. ``stale_drift`` is
            # True when the boolean flipped OR the reasons set changed; it
            # surfaces a re-run that addressed (or introduced) staleness.
            "old_stale": old_stale,
            "new_stale": new_stale,
            "stale_drift": stale_drift,
        }

    summary = _run_check_ck(
        "compute_drift_summary",
        _build_drift_summary,
        # W978 literal-floor discipline: every value is a LITERAL (no
        # captured locals from upstream), so a closure raise above cannot
        # re-crash inside the floor stub. ``partial_success: True`` because
        # a degraded summary IS partial success.
        default={
            "verdict": "evidence-diff completed (risk_level low)",
            "partial_success": True,
            "hash_drift": False,
            "schema_drift": False,
            "regressions": 0,
            "improvements": 0,
            "changed_verdicts": 0,
            "added_refs_total": 0,
            "removed_refs_total": 0,
            "added_findings": 0,
            "removed_findings": 0,
            "added_artifacts": 0,
            "removed_artifacts": 0,
            "old_stale": False,
            "new_stale": False,
            "stale_drift": False,
        },
    )

    # W607-CK -- compute_verdict boundary. cmd_evidence_diff does NOT emit
    # a risk_level (it's a diff renderer, not a risk scorer), so the
    # aggregation-phase compute_verdict simply rewrites the existing
    # summary["verdict"] through a closure that could in principle apply
    # post-aggregation transformations. The wrap surfaces a marker if a
    # future "augment verdict with delta-count suffix" refactor (or any
    # post-aggregation projection) raises. W978 literal-floor: the floor
    # is a LITERAL constant, NOT an f-string of summary["verdict"].
    def _compute_final_verdict() -> str:
        return summary["verdict"]

    final_verdict = _run_check_ck(
        "compute_verdict",
        _compute_final_verdict,
        # W978 literal-floor discipline: NEVER f-string-interpolate the
        # upstream verdict here -- it could be a non-string sentinel
        # whose __format__/__str__ raises. Use a LITERAL value (mirror of
        # cmd_evidence_doctor W607-CF "evidence-doctor completed
        # (risk_level low)" literal-floor pattern; cmd_evidence_diff
        # does not emit risk_level but keeps the same canonical floor
        # shape for cross-quartet uniformity).
        default="evidence-diff completed (risk_level low)",
    )
    if final_verdict is None:
        final_verdict = "evidence-diff completed (risk_level low)"
    summary["verdict"] = final_verdict

    # W607-AX + W607-CK -- combined buckets. ``partial_success`` flips
    # when EITHER bucket is non-empty -- mirrors the W607-AT + W607-CF
    # bucket-merge pattern. Both buckets share the ``evidence_diff_*``
    # marker family; the additive W607-CK bucket stays distinguishable
    # in tests + audits via its phase names (compute_drift_summary /
    # compute_verdict / auto_log / serialize_envelope).
    _combined_warnings_out_ck: list[str] = list(_w607ax_warnings_out) + list(_w607ck_warnings_out)
    if _combined_warnings_out_ck:
        summary["warnings_out"] = list(_combined_warnings_out_ck)
        summary["partial_success"] = True

    if json_mode:
        # Build the agent_contract.facts strings as concrete-noun
        # anchored claims (LAW 4). Each terminal token is in the
        # anchor set: regressions, improvements, findings, records.
        facts = [
            f"{len(regressions)} regressions",
            f"{len(improvements)} improvements",
            f"{len(added_findings)} added findings",
            f"{len(removed_findings)} removed findings",
            (
                f"{summary['added_refs_total']} added reference records, "
                f"{summary['removed_refs_total']} removed reference records"
            ),
        ]
        # W1262: staleness fact. Anchors on "flagged" (analytical verb in
        # tests/test_law4_lint.py _ANALYTICAL_VERBS) when staleness
        # drifted between packets, on "scanned" (analytical verb)
        # otherwise - so a no-drift fact still satisfies LAW 4.
        if stale_drift:
            facts.append(f"{int(old_stale)} old + {int(new_stale)} new stale flags flagged")
        else:
            facts.append(f"{int(old_stale) + int(new_stale)} stale flags scanned")
        next_commands: list[str] = []
        if regressions:
            # First regression is the most useful pointer.
            q = regressions[0]["q"]
            next_commands.append(
                f"# inspect Q-regression: {q} dropped from {regressions[0]['old']} to {regressions[0]['new']}"
            )
        if hash_drift_block is not None:
            next_commands.append("roam attest verify")

        # W607-CK -- serialize_envelope boundary. Wraps the envelope
        # serialisation so a downstream schema-shape refactor that breaks
        # ``json_envelope("evidence-diff", ...)`` would otherwise crash
        # AFTER all substrate + aggregation signals were already gathered.
        # Floor to a minimal envelope stub so consumers still receive a
        # parseable JSON object with the marker attached + the canonical
        # command name. Mirror of cmd_evidence_doctor W607-CF /
        # cmd_pr_analyze W607-BY / cmd_pr_risk W607-BU / cmd_attest
        # W607-BT serialize_envelope floor pattern. W978 first-hypothesis
        # discipline: the floor uses LITERAL string values rather than
        # captured upstream locals -- a downstream sentinel that crashed
        # in upstream phase would re-crash inside json.dumps when the
        # floor stub is serialised. Literal values keep the floor
        # JSON-safe.
        _envelope_floor_ck: dict = {
            "command": "evidence-diff",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": "evidence-diff completed (risk_level low)",
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out_ck),
            },
            "warnings_out": list(_combined_warnings_out_ck),
        }
        envelope = _run_check_ck(
            "serialize_envelope",
            json_envelope,
            "evidence-diff",
            default=_envelope_floor_ck,
            summary=summary,
            budget=token_budget,
            old_path=str(old_path),
            new_path=str(new_path),
            hash_drift=hash_drift_block,
            schema_drift=schema_drift_block,
            added_refs=added_refs,
            removed_refs=removed_refs,
            changed_verdicts=changed_verdicts,
            added_findings=added_findings,
            removed_findings=removed_findings,
            added_artifacts=added_artifacts,
            removed_artifacts=removed_artifacts,
            changed_completeness=changed_completeness,
            regressions=regressions,
            improvements=improvements,
            timing_drift=timing_drift,
            # W1262: top-level staleness block mirroring the W1254
            # producer signal on both packets. Always emitted
            # (Pattern-2 always-emit) so JSON consumers don't branch
            # on "did the diff read evidence_stale?".
            staleness={
                "old": {
                    "stale": old_stale,
                    "stale_reasons": old_stale_reasons,
                },
                "new": {
                    "stale": new_stale,
                    "stale_reasons": new_stale_reasons,
                },
                "drift": stale_drift,
            },
            agent_contract={
                "facts": facts,
                "next_commands": next_commands,
            },
            # W607-AX + W607-CK: mirror substrate-CALL + aggregation-phase
            # markers at the top level too so consumers reading
            # envelope.warnings_out (rather than envelope.summary.warnings_out)
            # see the same disclosure. Use the combined bucket.
            **({"warnings_out": list(_combined_warnings_out_ck)} if _combined_warnings_out_ck else {}),
        )
        # W607-CK -- if serialize_envelope raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``evidence_diff_serialize_envelope_failed:`` marker was
        # appended to ``_w607ck_warnings_out`` and the floor stub carries
        # only the old combined list. Rebuild the floor stub's
        # warnings_out so the new marker reaches the JSON output. Clean
        # path -> envelope is the real json_envelope return, no rebuild.
        if envelope is _envelope_floor_ck and _w607ck_warnings_out:
            _combined_warnings_out_ck = list(_w607ax_warnings_out) + list(_w607ck_warnings_out)
            _envelope_floor_ck["summary"]["warnings_out"] = list(_combined_warnings_out_ck)
            _envelope_floor_ck["warnings_out"] = list(_combined_warnings_out_ck)
            envelope = _envelope_floor_ck

        # W607-CK -- auto_log boundary. cmd_evidence_diff did NOT
        # auto-log pre-W607-CK; W607-CK ADDS the call inside the wrap so
        # an HMAC chain-misshape / filesystem failure surfaces a marker
        # rather than crashing the envelope after it was already built.
        # Mirror of cmd_evidence_doctor W607-CF / cmd_pr_analyze W607-BY
        # / cmd_pr_risk W607-BU auto_log pattern.
        _run_check_ck(
            "auto_log",
            auto_log,
            envelope,
            action="evidence-diff",
            target=f"{old_path} -> {new_path}",
            default=None,
        )
        # W607-CK -- if auto_log raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log)
        # -> envelope stays byte-identical to the version already built.
        _existing_summary_wo_ck = summary.get("warnings_out") or []
        if _w607ck_warnings_out and not any(
            m.startswith("evidence_diff_auto_log_failed:") for m in _existing_summary_wo_ck
        ):
            _combined_warnings_out_ck = list(_w607ax_warnings_out) + list(_w607ck_warnings_out)
            summary["warnings_out"] = list(_combined_warnings_out_ck)
            summary["partial_success"] = True
            # Rebuild via wrapped serialize_envelope so a later
            # rebuild-time raise still surfaces a marker.
            envelope = _run_check_ck(
                "serialize_envelope",
                json_envelope,
                "evidence-diff",
                default=_envelope_floor_ck,
                summary=summary,
                budget=token_budget,
                old_path=str(old_path),
                new_path=str(new_path),
                hash_drift=hash_drift_block,
                schema_drift=schema_drift_block,
                added_refs=added_refs,
                removed_refs=removed_refs,
                changed_verdicts=changed_verdicts,
                added_findings=added_findings,
                removed_findings=removed_findings,
                added_artifacts=added_artifacts,
                removed_artifacts=removed_artifacts,
                changed_completeness=changed_completeness,
                regressions=regressions,
                improvements=improvements,
                timing_drift=timing_drift,
                staleness={
                    "old": {
                        "stale": old_stale,
                        "stale_reasons": old_stale_reasons,
                    },
                    "new": {
                        "stale": new_stale,
                        "stale_reasons": new_stale_reasons,
                    },
                    "drift": stale_drift,
                },
                agent_contract={
                    "facts": facts,
                    "next_commands": next_commands,
                },
                warnings_out=list(_combined_warnings_out_ck),
            )

        click.echo(to_json(envelope))
        return

    # Text mode -- W607-CK: surface the final_verdict (carries the
    # aggregation-phase compute_verdict result) so the text rendering
    # stays in lockstep with the JSON envelope summary.verdict.
    click.echo(f"VERDICT: {final_verdict}")
    click.echo(f"  old: {old_path}")
    click.echo(f"  new: {new_path}")
    click.echo("")

    if schema_drift_block is not None:
        click.echo(f"schema_version: {schema_drift_block['old']!r} -> {schema_drift_block['new']!r}")
    if hash_drift_block is not None:
        click.echo(f"content_hash:   {hash_drift_block['old']} -> {hash_drift_block['new']}")

    # W1262: staleness banner. Surface the W1254 producer signal when
    # either packet is stale OR when staleness drifted between packets.
    # ASCII-only per project conventions (no emoji). Skipped entirely
    # when both packets are fresh and no drift fired.
    if old_stale or new_stale or stale_drift:
        click.echo(f"[STALE] evidence_stale: {old_stale} -> {new_stale}" + (" (drift)" if stale_drift else ""))
        if old_stale_reasons:
            click.echo(f"  old stale_reasons ({len(old_stale_reasons)}):")
            for reason in old_stale_reasons:
                click.echo(f"    - {reason}")
        if new_stale_reasons:
            click.echo(f"  new stale_reasons ({len(new_stale_reasons)}):")
            for reason in new_stale_reasons:
                click.echo(f"    - {reason}")

    if changed_verdicts:
        click.echo("\nChanged verdicts:")
        rows = [[v["field"], str(v["old"]), str(v["new"])] for v in changed_verdicts]
        click.echo(format_table(["Field", "Old", "New"], rows, budget=0))

    if regressions:
        click.echo(f"\nRegressions ({len(regressions)}):")
        rows = [[r["q"], r["old"], r["new"]] for r in regressions]
        click.echo(format_table(["Q", "Old", "New"], rows, budget=0))

    if improvements:
        click.echo(f"\nImprovements ({len(improvements)}):")
        rows = [[i["q"], i["old"], i["new"]] for i in improvements]
        click.echo(format_table(["Q", "Old", "New"], rows, budget=0))

    other_completeness = [c for c in changed_completeness if c not in regressions and c not in improvements]
    if other_completeness:
        click.echo(f"\nCompleteness drift ({len(other_completeness)}):")
        rows = [[c["q"], c["old"], c["new"]] for c in other_completeness]
        click.echo(format_table(["Q", "Old", "New"], rows, budget=0))

    added_total = summary["added_refs_total"]
    removed_total = summary["removed_refs_total"]
    if added_total or removed_total:
        click.echo(f"\nRefs: +{added_total} added / -{removed_total} removed")
        for label, kind_field, id_field, added_list, removed_list in (
            ("actor_refs", "actor_kind", "actor_id", added_actor, removed_actor),
            (
                "authority_refs",
                "authority_kind",
                "authority_id",
                added_authority,
                removed_authority,
            ),
            (
                "environment_refs",
                "env_kind",
                "env_id",
                added_env,
                removed_env,
            ),
        ):
            if not (added_list or removed_list):
                continue
            click.echo(f"  {label}:")
            for ref in added_list:
                click.echo(f"    + {ref.get(kind_field)}:{ref.get(id_field)}")
            for ref in removed_list:
                click.echo(f"    - {ref.get(kind_field)}:{ref.get(id_field)}")

    if added_findings or removed_findings:
        click.echo(f"\nFindings: +{len(added_findings)} added / -{len(removed_findings)} removed")
        for row in added_findings:
            click.echo(f"  + {row.get('finding_id_str')}: {(row.get('claim') or '')[:60]}")
        for row in removed_findings:
            click.echo(f"  - {row.get('finding_id_str')}: {(row.get('claim') or '')[:60]}")

    if added_artifacts or removed_artifacts:
        click.echo(f"\nArtifacts: +{len(added_artifacts)} added / -{len(removed_artifacts)} removed")
        for art in added_artifacts:
            click.echo(f"  + {art.get('artifact_id')}")
        for art in removed_artifacts:
            click.echo(f"  - {art.get('artifact_id')}")

    if timing_drift:
        click.echo("\nTiming drift:")
        rows = [[t["field"], str(t["old"]), str(t["new"])] for t in timing_drift]
        click.echo(format_table(["Field", "Old", "New"], rows, budget=0))

    if not (
        schema_drift_block
        or hash_drift_block
        or changed_verdicts
        or regressions
        or improvements
        or other_completeness
        or added_total
        or removed_total
        or added_findings
        or removed_findings
        or added_artifacts
        or removed_artifacts
        or timing_drift
        # W1262: stale_drift / stale flags also count as "drift" so the
        # tail "(no drift detected)" line stays honest. Either packet
        # being stale OR a staleness toggle qualifies.
        or old_stale
        or new_stale
        or stale_drift
    ):
        # No drift at all - the packets are functionally equivalent.
        click.echo("(no drift detected)")
