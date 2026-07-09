"""``roam evidence doctor`` — diagnose a ChangeEvidence packet's health.

W274. A buyer / auditor receives a ``ChangeEvidence`` JSON packet and
wants to know whether it's trustworthy / complete / well-formed BEFORE
running it through a heavyweight CI gate. ``evidence doctor`` is the
lightweight, read-only diagnostic surface that answers:

1. **Schema health** — is this a valid ``ChangeEvidence`` packet? Do the
   closed-enum values (``subject_kind`` / ``link_kind`` / ``artifact_kind``
   / ``actor_kind`` / ``authority_kind`` / ``env_kind`` /
   ``claim_severity`` / ``redaction_reason``) respect the W174 vocabulary
   freeze in :mod:`roam.evidence._vocabulary`?
2. **Completeness** — what's the W259 banner tier (STRONG / PARTIAL /
   INSUFFICIENT)? Which of the 8 evidence questions are complete vs
   partial vs missing?
3. **Provenance** — is ``content_hash`` intact (recomputes byte-identical
   per W218 stability)? Are any redactions declared, and do they match
   valid reasons?
4. **Honesty signals** — does the packet declare its limitations
   (W185 limitations section, W261 ``producer_not_available`` marker)?
   Or does it look like a "silent omission" packet?
5. **Actionable next steps** — if banner is PARTIAL or INSUFFICIENT,
   what producer would lift the lowest-scoring question?

This is a DIAGNOSTIC command. It reports findings; it never mutates the
packet, never writes to disk, and never calls a producer. The verdict
ladder is:

* ``FAIL`` — schema is invalid (closed-enum violation, malformed JSON,
  not a JSON object, or ``content_hash`` recompute disagrees with the
  stamped value).
* ``WARN`` — schema is valid AND content hash matches, but completeness
  banner is PARTIAL or INSUFFICIENT (one or more evidence questions are
  partial / missing).
* ``PASS`` — schema is valid, content hash matches, and the completeness
  banner is STRONG.

Output modes:

* Text (default) — one-line verdict + per-question table + next-step
  hints. Plain ASCII per CLAUDE.md (no emojis, no box-drawing, no
  colors).
* JSON (``roam --json evidence doctor``) — standard ``json_envelope``
  with ``summary.verdict`` first, ``agent_contract.facts`` anchored on
  concrete-noun terminals (LAW 4), and ``next_commands`` populated
  with literal copy-paste-executable ``roam <subcommand>`` strings
  when a partial / missing question has a producer hint.

Reads from disk (or stdin via ``--stdin``); does NOT touch the index DB.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because evidence-doctor verifies ``ChangeEvidence`` PACKET
INTEGRITY (closed-enum schema conformance, ``content_hash`` recompute,
W259 completeness banner) — not per-location code violations in user
source. The diagnostic findings describe the evidence-packet shape,
which has no source coordinates to populate SARIF ``locations[]``.
SARIF here would conflate validator-output (packet well-formed?) with
code-analyzer-output (user code well-formed?). See
``cmd_rules_validate`` for the parallel validator-not-detector
disclosure pattern + action.yml _SUPPORTED_SARIF allowlist + W1192
audit memo + W1224-audit memo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

import click

from roam.capability import roam_capability
from roam.evidence.completeness_compat import classify_completeness
from roam.output.formatter import format_table, json_envelope, to_json
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Verdict ladder
# ---------------------------------------------------------------------------

_VERDICT_FAIL = "FAIL"
_VERDICT_WARN = "WARN"
_VERDICT_PASS = "PASS"

# Q-keys in canonical Q1..Q8 order. Mirrors
# ``ChangeEvidence.evidence_completeness``.
_Q_KEYS: tuple[str, ...] = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")

# W281: actor-trust tier keys we always-emit a count for (Pattern-2:
# zero-pad the dict so an absent tier reads as 0, not "missing"). Ordered
# strongest-to-weakest so the text-output line scans top-to-bottom.
_TRUST_TIER_KEYS: tuple[str, ...] = (
    "verified_ci",
    "git_author",
    "local_env",
    "self_reported_agent",
    "unknown",
)

# W281: the two tiers that contribute a WARN signal. Anything in this set
# means "identity surface present but not cryptographically attested" —
# strong-coverage packets still get downgraded to WARN if any actor_ref
# falls into one of these tiers.
_TRUST_WARN_TIERS: frozenset[str] = frozenset(
    {
        "self_reported_agent",
        "unknown",
    }
)

# Human-readable Q-labels so the text output is reviewer-friendly.
_Q_LABELS: Mapping[str, str] = {
    "Q1": "Q1 actor",
    "Q2": "Q2 authority",
    "Q3": "Q3 context",
    "Q4": "Q4 changes",
    "Q5": "Q5 risk",
    "Q6": "Q6 policy",
    "Q7": "Q7 verify",
    "Q8": "Q8 accept",
}

# Per-question "what would lift this from partial/missing" hints. The
# strings are imperative (LAW 2) and end on copy-pasteable command
# fragments where a producer is wired today; questions without a known
# producer surface a "real producer needed" note instead.
_Q_NEXT_STEP_HINTS: Mapping[str, str] = {
    "Q1": ("attach actor_refs[] (human + agent identity) via pr-bundle or set ROAM_AGENT_ID before producing"),
    "Q2": ("attach authority_refs[] (mode + permits + policy rules) via roam mode + pr-bundle add-authority"),
    "Q3": ("attach context_refs[] (envelope hashes) via roam pr-bundle add-context"),
    "Q4": ("stamp changed_subjects[] on the producer (pr-replay / pr-bundle) — diff target may be empty"),
    "Q5": ("stamp risk_level on the producer envelope (preflight / pr-risk emit a level today)"),
    "Q6": ("attach policy_decisions[] via roam rules-validate (rules with decision rationale)"),
    "Q7": ("attach tests_run[] via roam pr-bundle add-tests (or wire a tests harvester into the producer)"),
    "Q8": ("attach approvals[] or accepted_risks[] via roam pr-bundle add-approval (real producer needed)"),
}


# ---------------------------------------------------------------------------
# Loaders + validators
# ---------------------------------------------------------------------------


def _load_raw_packet(
    path: str | None,
    *,
    from_stdin: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read + parse the packet JSON. Returns (payload, error)."""
    try:
        if from_stdin:
            raw = sys.stdin.read()
            source_label = "<stdin>"
        else:
            p = Path(path or "")
            source_label = str(p)
            raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read packet ({source_label!r}): {exc}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON in {source_label!r}: {exc}"
    if not isinstance(payload, dict):
        return None, (f"packet at {source_label!r} is not a JSON object (got {type(payload).__name__})")
    return payload, None


def _validate_closed_enums(
    packet: dict[str, Any],
) -> list[dict[str, str]]:
    """Walk the packet and surface every closed-enum violation.

    Returns a list of ``{"field": ..., "value": ..., "expected_set": ...}``
    dicts. Empty list means every enum-typed value is in vocabulary.
    """
    from roam.evidence._vocabulary import (
        ACTOR_KINDS,
        ACTOR_TRUST_TIERS,
        ARTIFACT_KINDS,
        AUTHORITY_KINDS,
        CLAIM_SEVERITIES,
        ENV_KINDS,
        REDACTION_REASONS,
        SUBJECT_KINDS,
    )

    violations: list[dict[str, str]] = []

    def _check(field: str, value: Any, allowed: frozenset[str]) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            return  # type-shape errors surface elsewhere
        if value not in allowed:
            violations.append(
                {
                    "field": field,
                    "value": value,
                    "expected": ",".join(sorted(allowed)),
                }
            )

    # Top-level packet redactions
    for r in packet.get("redactions") or []:
        if isinstance(r, str):
            _check("redactions[]", r, REDACTION_REASONS)

    # changed_subjects[*].kind
    for i, subj in enumerate(packet.get("changed_subjects") or []):
        if isinstance(subj, Mapping):
            _check(f"changed_subjects[{i}].kind", subj.get("kind"), SUBJECT_KINDS)

    # context_refs[*].kind, artifacts[*].kind, artifacts[*].redactions[]
    for collection_key, kind_set in (
        ("context_refs", ARTIFACT_KINDS),
        ("artifacts", ARTIFACT_KINDS),
    ):
        for i, art in enumerate(packet.get(collection_key) or []):
            if isinstance(art, Mapping):
                _check(
                    f"{collection_key}[{i}].kind",
                    art.get("kind"),
                    kind_set,
                )
                for r in art.get("redactions") or []:
                    if isinstance(r, str):
                        _check(
                            f"{collection_key}[{i}].redactions[]",
                            r,
                            REDACTION_REASONS,
                        )

    # actor_refs / authority_refs / environment_refs
    for i, ref in enumerate(packet.get("actor_refs") or []):
        if isinstance(ref, Mapping):
            _check(f"actor_refs[{i}].actor_kind", ref.get("actor_kind"), ACTOR_KINDS)
            # W281: validate trust_tier against the W211 closed enumeration.
            # Unknown literals are a hard FAIL (joins existing enum_violations
            # channel) — they indicate a producer wrote a free-form string
            # instead of staying in vocabulary.
            _check(
                f"actor_refs[{i}].trust_tier",
                ref.get("trust_tier"),
                ACTOR_TRUST_TIERS,
            )
    for i, ref in enumerate(packet.get("authority_refs") or []):
        if isinstance(ref, Mapping):
            _check(
                f"authority_refs[{i}].authority_kind",
                ref.get("authority_kind"),
                AUTHORITY_KINDS,
            )
    for i, ref in enumerate(packet.get("environment_refs") or []):
        if isinstance(ref, Mapping):
            _check(f"environment_refs[{i}].env_kind", ref.get("env_kind"), ENV_KINDS)

    # findings[*].severity (Optional — many findings omit severity).
    for i, f in enumerate(packet.get("findings") or []):
        if isinstance(f, Mapping):
            sev = f.get("severity")
            if sev is not None and isinstance(sev, str):
                _check(f"findings[{i}].severity", sev, CLAIM_SEVERITIES)

    return violations


def _recompute_content_hash(packet: dict[str, Any]) -> str | None:
    """Recompute the content_hash from the raw dict.

    Mirrors :meth:`ChangeEvidence.compute_content_hash`: clear the
    ``content_hash`` field, serialise with the canonical-JSON algorithm
    (``sort_keys=True, separators=(",",":")``), and sha256 the UTF-8
    bytes. The packet's omission rules for W182 (``actor_refs`` /
    ``authority_refs`` / ``environment_refs``) and W210 (time-aware +
    stale + version fields) are applied so packets produced via the
    dataclass canonicaliser verify byte-for-byte.

    Returns the recomputed hex digest, or ``None`` if the dict isn't
    JSON-serialisable (defensive — shouldn't happen since we just
    parsed it from JSON).
    """
    import hashlib

    # Use the same omission rules as ChangeEvidence.to_canonical_json so
    # packets produced via the dataclass canonicaliser (which already
    # dropped these defaulted fields before computing the stored hash)
    # verify byte-for-byte through the doctor.
    from roam.evidence.change_evidence import (
        _W182_OMIT_WHEN_EMPTY_FIELDS,
        _W210_OMIT_WHEN_DEFAULT_FIELDS,
    )

    # ChangeEvidence.compute_content_hash uses
    # ``dataclasses.replace(self, content_hash=None)`` and then runs
    # the canonical serialiser — which KEEPS the ``"content_hash":null``
    # key in the canonical output (it's a regular nullable field, not in
    # the W210 omit list). So we mirror that: set the field to None
    # rather than dropping the key.
    stripped = dict(packet)
    stripped["content_hash"] = None

    # W182: drop empty ref lists.
    for k in _W182_OMIT_WHEN_EMPTY_FIELDS:
        if stripped.get(k) == []:
            stripped.pop(k, None)
    # W210: drop each W210 field at its per-field default sentinel.
    for k, default in _W210_OMIT_WHEN_DEFAULT_FIELDS.items():
        if k in stripped and stripped[k] == default:
            stripped.pop(k, None)

    try:
        canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _classify_banner(
    totals: Mapping[str, int],
    *,
    producer_not_available: bool = False,
) -> tuple[str, str, str]:
    """Apply the W259 threshold table to per-question totals.

    Returns ``(tier_id, label, rationale)``. Duplicates the logic in
    :func:`roam.evidence.banner.classify_evidence_coverage` so the
    doctor works without constructing a ``ChangeEvidence`` instance.

    W261 STRONG cap — parity with the banner classifier: when the packet
    carries a ``producer_not_available`` redaction, a producer never
    actually ran, so the buyer-facing tier must not advertise STRONG even
    if ``complete >= 7``. The tier is capped at PARTIAL (which the count
    gate below always satisfies when ``complete >= 7``). Keeps the doctor
    surface consistent with the pr-replay report banner. See
    tests/test_evidence_pr_replay.py::
      test_pr_replay_bare_bundle_does_not_claim_strong_coverage.
    """
    complete = int(totals.get("complete", 0))
    partial = int(totals.get("partial", 0))
    missing = int(totals.get("missing", 0))
    if complete >= 7 and not producer_not_available:
        return (
            "strong",
            "Strong evidence coverage",
            f"{complete} of 8 evidence questions answered; {missing} missing acknowledged below.",
        )
    if (complete + partial) >= 5 and missing <= 3:
        return (
            "partial",
            "Partial coverage",
            f"{complete + partial} of 8 evidence questions answered fully or partially; {missing} missing.",
        )
    return (
        "insufficient",
        "Insufficient evidence",
        f"{complete} of 8 evidence questions answered; do not publish as governance evidence.",
    )


def _classify_trust_tiers(
    packet: dict[str, Any],
) -> tuple[dict[str, int], list[dict[str, str]]]:
    """W281: tally actor-trust tiers and surface WARN signals.

    Returns ``(counts, warnings)``:

    * ``counts`` — dict with all 5 ``ACTOR_TRUST_TIERS`` keys present
      (Pattern-2 always-emit; absent tiers read as 0, not "missing").
      Tiers outside the closed enumeration are NOT counted here — they
      surface through ``_validate_closed_enums`` as a hard FAIL.
    * ``warnings`` — one entry per ``actor_ref`` whose ``trust_tier`` is
      in :data:`_TRUST_WARN_TIERS` (``self_reported_agent`` /
      ``unknown``). Each entry names the index, actor_id, the tier, and
      a one-line rationale so a reviewer can route to the offending ref
      without re-walking the packet.

    The packet is never mutated; this is a diagnostic-only readout.
    """
    counts: dict[str, int] = {k: 0 for k in _TRUST_TIER_KEYS}
    warnings: list[dict[str, str]] = []
    for i, ref in enumerate(packet.get("actor_refs") or []):
        if not isinstance(ref, Mapping):
            continue
        tier = ref.get("trust_tier")
        if not isinstance(tier, str):
            continue
        if tier in counts:
            counts[tier] += 1
        # Invalid tiers (not in ACTOR_TRUST_TIERS) skip the count but
        # surface through the enum-violation channel for FAIL precedence.
        if tier in _TRUST_WARN_TIERS:
            rationale = (
                "self-reported agent identity with no CI corroboration"
                if tier == "self_reported_agent"
                else "no identity surface available; tier defaulted to unknown"
            )
            warnings.append(
                {
                    "actor_ref_index": i,
                    "actor_id": (ref.get("actor_id") if isinstance(ref.get("actor_id"), str) else ""),
                    "trust_tier": tier,
                    "rationale": rationale,
                }
            )
    return counts, warnings


def _classify_authority_kinds(
    packet: dict[str, Any],
) -> dict[str, int]:
    """W350: tally ``authority_refs[]`` by ``authority_kind``.

    Returns a dict with all 6 ``AUTHORITY_KINDS`` keys present
    (Pattern-2 always-emit; absent kinds read as 0, not "missing").
    Permits are surfaced as the ``permit`` key here — permits flow into
    the packet via ``authority_refs[authority_kind="permit"]`` (no
    top-level ``permits[]`` field on ChangeEvidence). Closed-enum
    membership is enforced by :func:`_validate_closed_enums`; values
    outside the enumeration skip this count and surface as a hard FAIL
    through the enum-violation channel.
    """
    from roam.evidence._vocabulary import AUTHORITY_KINDS

    counts: dict[str, int] = {k: 0 for k in sorted(AUTHORITY_KINDS)}
    for ref in packet.get("authority_refs") or []:
        if not isinstance(ref, Mapping):
            continue
        kind = ref.get("authority_kind")
        if isinstance(kind, str) and kind in counts:
            counts[kind] += 1
    return counts


def _build_next_steps(q_results: Mapping[str, str]) -> list[dict[str, str]]:
    """For every partial / missing Q, surface a one-line hint.

    Excludes ``not_applicable`` and ``complete`` entries — only the
    actionable gaps appear in the list. Order is Q1..Q8 so reviewers
    scan top-to-bottom.
    """
    steps: list[dict[str, str]] = []
    for qk in _Q_KEYS:
        state = q_results.get(qk, "missing")
        if state in ("complete", "not_applicable"):
            continue
        steps.append(
            {
                "q": qk,
                "state": state,
                "action": _Q_NEXT_STEP_HINTS.get(qk, "lift via real producer"),
            }
        )
    return steps


def _build_verdict(
    *,
    schema_ok: bool,
    hash_ok: bool,
    banner_tier: str,
    enum_violations: int,
    trust_warnings: list[dict[str, str]] | None = None,
    trust_counts: Mapping[str, int] | None = None,
    packet_budget_state: str | None = None,
    packet_size_bytes_val: int | None = None,
    packet_budget_bytes: int | None = None,
) -> tuple[str, str]:
    """One-line verdict + label per the FAIL/WARN/PASS ladder.

    Returns ``(level, verdict_line)``. The verdict line is single-line,
    works alone (LAW 6), and starts with the level so an agent reading
    only ``summary.verdict`` can route correctly.

    W281: STRONG-coverage packets with any ``self_reported_agent`` /
    ``unknown`` actor_ref get downgraded from PASS to WARN; the verdict
    line names the count so the operator sees the trust gap inline.
    FAIL precedence (enum violations, hash mismatch) is preserved.

    W280: ``packet_budget_state == "oversized_after_truncation"``
    contributes a WARN (not a FAIL — the packet is still parseable;
    just bloated). FAIL precedence and the existing WARN rationale
    paths are preserved; the packet-size signal joins them only when
    the higher-tier verdict would otherwise have been PASS or a banner
    WARN.
    """
    if not schema_ok:
        if enum_violations:
            return (
                _VERDICT_FAIL,
                f"FAIL: {enum_violations} closed-enum violations in packet",
            )
        return (_VERDICT_FAIL, "FAIL: packet shape invalid")
    if not hash_ok:
        return (_VERDICT_FAIL, "FAIL: content_hash mismatch on packet")
    warns = trust_warnings or []
    oversized = packet_budget_state == "oversized_after_truncation"
    if banner_tier == "strong":
        if warns:
            counts = trust_counts or {}
            self_reported = int(counts.get("self_reported_agent", 0))
            unknown = int(counts.get("unknown", 0))
            total = sum(int(v) for v in counts.values()) if counts else len(warns)
            return (
                _VERDICT_WARN,
                (
                    f"WARN: STRONG coverage but actor identity unverified "
                    f"({self_reported} self_reported_agent + {unknown} "
                    f"unknown of {total} total)"
                ),
            )
        if oversized:
            size_b = int(packet_size_bytes_val or 0)
            budget_b = int(packet_budget_bytes or 0)
            return (
                _VERDICT_WARN,
                (
                    f"WARN: STRONG coverage but packet oversized "
                    f"({size_b} bytes > {budget_b} budget bytes; "
                    f"size_limit redaction stamped)"
                ),
            )
        return (_VERDICT_PASS, "PASS: STRONG coverage, no schema errors")
    if banner_tier == "partial":
        if oversized:
            size_b = int(packet_size_bytes_val or 0)
            budget_b = int(packet_budget_bytes or 0)
            return (
                _VERDICT_WARN,
                (f"WARN: PARTIAL coverage AND packet oversized ({size_b} bytes > {budget_b} budget bytes)"),
            )
        return (_VERDICT_WARN, "WARN: PARTIAL coverage; partial / missing questions remain")
    return (
        _VERDICT_WARN,
        "WARN: INSUFFICIENT evidence; do not publish as governance evidence",
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="evidence-doctor",
    category="review",
    summary="Diagnose a ChangeEvidence packet's health (schema + hash + banner).",
    inputs=["packet_path"],
    outputs=["verdict", "schema_ok", "hash_ok", "banner_tier", "next_steps"],
    examples=[
        "roam evidence doctor packet.json",
        "roam evidence doctor --stdin < packet.json",
        "roam --json evidence doctor packet.json",
    ],
    tags=["evidence", "review", "doctor"],
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
@click.command("evidence-doctor")
@click.argument(
    "packet_path",
    type=click.Path(exists=False, dir_okay=False, readable=True),
    required=False,
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help="Read the evidence packet JSON from standard input.",
)
@click.pass_context
def evidence_doctor(ctx, packet_path, from_stdin):
    """Diagnose a ``ChangeEvidence`` packet's health.

    Read-only: reports schema validity, closed-enum conformance,
    content-hash integrity, completeness banner tier, declared
    redactions, and actionable next steps for partial / missing
    evidence questions. Never mutates the packet.

    PACKET_PATH is the path to a ``ChangeEvidence`` JSON file. Pass
    ``--stdin`` to read from standard input instead. Reading from disk
    is the common case; ``--stdin`` is for pipelines (e.g.
    ``roam pr-replay HEAD~1..HEAD --json | jq .evidence_packet | roam evidence doctor --stdin``).

    Verdict ladder:

    * ``PASS`` — schema valid, content_hash matches, banner is STRONG.
    * ``WARN`` — schema valid, content_hash matches, but banner is
      PARTIAL or INSUFFICIENT (one or more questions are partial /
      missing).
    * ``FAIL`` — schema invalid (malformed JSON, closed-enum violation,
      not a JSON object) OR ``content_hash`` recompute disagrees with
      the stamped value.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if not from_stdin and not packet_path:
        raise click.UsageError("Provide a packet path or pass --stdin to read JSON from stdin.")
    if from_stdin and packet_path:
        raise click.UsageError("Pass either a packet path or --stdin, not both.")

    # --- W607-AT: substrate-CALL marker plumbing -------------------------
    # cmd_evidence_doctor is the VALIDATOR at the head of the
    # evidence-compiler pipeline. It validates W174 (ChangeEvidence
    # dataclass) + W226 (export profiles) + W228 (false-positive feedback)
    # + the collector -> exporter chain. After W607-AT, the marker stack
    # composes from the audit-trail quartet (W607-AD/AI/AL/AP) through
    # evidence-doctor's consumption of those markers downstream.
    #
    # Substrate boundaries wrapped here:
    #
    #   load_raw_packet              (JSON read + parse)
    #   validate_closed_enums        (W174 vocabulary check)
    #   recompute_content_hash       (W218 integrity recompute)
    #   classify_completeness        (W1266 raw-dict completeness scorer)
    #   classify_banner              (W259 banner tier classification)
    #   classify_trust_tiers         (W281 actor-trust tier tally)
    #   classify_authority_kinds     (W350 authority-kind tally)
    #   packet_size_bytes            (W280 byte-count measurement)
    #   classify_packet_budget       (W280 budget-state classification)
    #   build_next_steps             (Q-gap -> action recipe)
    #   build_verdict                (FAIL/WARN/PASS ladder scoring)
    #
    # Each raise becomes an
    # ``evidence_doctor_<phase>_failed:<exc_class>:<detail>`` marker via
    # ``_w607at_warnings_out``. partial_success flips on any non-empty
    # bucket. Empty bucket on the clean path keeps the envelope shape
    # byte-identical to the pre-W607-AT command.
    #
    # PATTERN-2 CHECK: pre-W607-AT cmd_evidence_doctor has THREE narrow
    # try/except blocks in module-level helpers (lines 150/160/312) and
    # ZERO bare ``except ...: pass`` Pattern-2 swallows. The three blocks
    # all return structured sentinel values (error string / None) rather
    # than degrading silently, so they are NOT Pattern-2 candidates -
    # W607-AT does not need to eliminate any.
    #
    # VALIDATOR-CLOSURE milestone: cmd_evidence_doctor is the validator
    # that consumes everything the audit-trail quartet emits. With
    # W607-AT plumbing, the producer/validator chain on the
    # evidence-compiler pipeline is W607-plumbed end-to-end. A raise
    # anywhere in {emit, verify, conform, export, validate} surfaces a
    # marker rather than crashing.
    _w607at_warnings_out: list[str] = []

    def _run_check_at(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AT marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``evidence_doctor_<phase>_failed:<exc_class>:<detail>`` marker
        via ``_w607at_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607at_warnings_out.append(f"evidence_doctor_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CF: aggregation-phase marker plumbing (additive) ----------
    # cmd_evidence_doctor is the evidence-packet diagnostic sentinel and
    # the VALIDATOR-CLOSURE at the head of the evidence-compiler pipeline
    # (see W607-AT preamble above). With W607-AT covering the
    # substrate-CALL layer (11 phases), W607-CF additively wraps the
    # AGGREGATION-PHASE layer that sits ON TOP of those substrate signals:
    #
    #   - ``score_classify``     -- map the W259 banner_tier
    #                               (strong/partial/insufficient) onto an
    #                               internal evidence-completeness risk
    #                               vocabulary projected into W631 levels
    #                               (low/medium/high). Schema/hash FAIL
    #                               precedence promotes to ``critical``.
    #                               Floor=None drives the
    #                               ``score_classification: "unknown"``
    #                               sentinel (mirror of cmd_pr_analyze
    #                               W607-BY / cmd_pr_risk W607-BU).
    #   - ``score_normalize``    -- canonical W631 risk-LEVEL projection
    #                               (``normalize_risk_level`` +
    #                               ``risk_rank``). Pattern 3a discipline
    #                               -- route through the W631 canonical
    #                               helper, NOT through an inline severity
    #                               map. cmd_evidence_doctor is a
    #                               validator that ALSO emits a canonical
    #                               risk-LEVEL so cross-command consumers
    #                               (pr-bundle, pr-replay) can gate on the
    #                               same vocabulary.
    #   - ``compute_verdict``    -- augmented verdict text build appending
    #                               the canonical ``(risk_level X)``
    #                               suffix (LAW 6 standalone-parse).
    #                               Floor must NOT re-format
    #                               risk_level_canonical -- W978 first-
    #                               hypothesis discipline (literal "low"
    #                               floor instead).
    #   - ``auto_log``           -- active-run ledger write (silent no-op
    #                               if no run is active; the underlying
    #                               auto_log can still raise on HMAC chain
    #                               misshape or filesystem failures).
    #   - ``serialize_envelope`` -- ``json_envelope("evidence-doctor", ...)``
    #                               projection. Floor to a parseable stub
    #                               so consumers still receive structured
    #                               JSON with the marker attached + the
    #                               canonical command name.
    #
    # All boundaries share the canonical ``evidence_doctor_*`` marker
    # family (same as W607-AT; W607-CF is ADDITIVE, not a separate
    # prefix). The two buckets (``_w607at_warnings_out`` substrate-CALL +
    # ``_w607cf_warnings_out`` aggregation-phase) are combined at
    # envelope-emit time so consumers see the full degradation lineage.
    #
    # EVIDENCE-COMPILER COMPLETENESS milestone: cmd_evidence_doctor closes
    # the assurance-layer thesis (CLAUDE.md "Evidence compiler layer").
    # With W607-AT (substrate) + W607-CF (aggregation), the validator at
    # the head of the evidence-compiler pipeline is W607-plumbed
    # end-to-end alongside cmd_pr_bundle (W607-AE + BW) and cmd_pr_replay
    # (W607-AH + CA).
    _w607cf_warnings_out: list[str] = []

    def _run_check_cf(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CF marker emission.

        Mirror of ``_run_check_at`` shape (same
        ``evidence_doctor_<phase>_failed:`` marker family) but writes into
        ``_w607cf_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cf_warnings_out.append(f"evidence_doctor_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    source_label = "<stdin>" if from_stdin else str(packet_path)
    load_result = _run_check_at(
        "load_raw_packet",
        _load_raw_packet,
        packet_path,
        from_stdin=from_stdin,
        default=(None, "load_raw_packet helper raised; see warnings_out"),
    )
    payload, load_error = load_result if load_result is not None else (None, "load_raw_packet returned None")

    if payload is None:
        # Hard load failure — emit a FAIL envelope so JSON consumers
        # still get structured data (Pattern 1 anti-pattern).
        verdict_line = f"FAIL: {load_error}"
        # W280: import the budget constant here so the failure envelope
        # surfaces the same packet_size block shape as the success path.
        from roam.evidence.change_evidence import (
            PACKET_SIZE_BUDGET_BYTES as _PSBB,
        )

        summary = {
            "verdict": verdict_line,
            "partial_success": True,
            "schema_ok": False,
            "hash_ok": False,
            "banner_tier": None,
            "enum_violations": 0,
            "next_steps_count": 0,
            # W281: keep envelope shape stable even on hard load failure.
            "trust_warnings_count": 0,
            # W350: keep authority-axis keys stable on hard load failure.
            "authority_refs_count": 0,
            "permits_count": 0,
            # W280: packet-size keys always present on the summary so
            # consumers don't branch on "did the doctor succeed?" to
            # know whether the keys exist.
            "packet_size_bytes": 0,
            "budget_state": "within_budget",
            # W1262: keep staleness keys stable on hard load failure.
            # A failed-load packet is "not stale" by definition - we
            # couldn't read its evidence_stale field.
            "stale": False,
            "stale_reasons_count": 0,
        }
        if json_mode:
            envelope = json_envelope(
                "evidence-doctor",
                summary=summary,
                budget=token_budget,
                source=source_label,
                load_error=load_error,
                # W281: always-emit zeroed trust-tier block + empty warnings
                # so consumers can rely on the keys being present.
                trust_tiers={k: 0 for k in _TRUST_TIER_KEYS},
                trust_warnings=[],
                # W350: always-emit zeroed authority_kinds dict on the
                # hard-load failure envelope too so consumers can rely on
                # the keys being present (Pattern-2 always-emit).
                authority_kinds={
                    k: 0
                    for k in (
                        "approval",
                        "lease",
                        "mode",
                        "permit",
                        "policy_rule",
                        "token_scope",
                    )
                },
                # W280: always-emit packet_size block on the hard-load
                # failure envelope too. A failed-load packet has 0 bytes
                # of canonical JSON to report; classify_packet_budget(0)
                # is "within_budget".
                packet_size={
                    "bytes": 0,
                    "budget_bytes": _PSBB,
                    "budget_state": "within_budget",
                },
                # W1262: always-emit staleness block on the hard-load
                # failure envelope too. Keeps the envelope shape stable
                # across success / failure paths.
                staleness={
                    "stale": False,
                    "stale_reasons": [],
                },
                agent_contract={
                    "facts": [
                        "0 packets loaded",
                        "1 load error",
                    ],
                    "next_commands": [],
                },
            )
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict_line}")
            click.echo(f"  source: {source_label}")
        sys.exit(2)

    # Validate closed enumerations
    enum_violations = _run_check_at(
        "validate_closed_enums",
        _validate_closed_enums,
        payload,
        default=[],
    )
    schema_version = payload.get("schema_version")
    schema_ok = bool(schema_version) and not enum_violations

    # Recompute content_hash
    stamped_hash = payload.get("content_hash")
    recomputed_hash = _run_check_at(
        "recompute_content_hash",
        _recompute_content_hash,
        payload,
        default=None,
    )
    hash_ok = isinstance(stamped_hash, str) and isinstance(recomputed_hash, str) and stamped_hash == recomputed_hash
    # If no content_hash is stamped, treat as "present but unverified" —
    # don't mark hash_ok True, but also don't FAIL on a packet that simply
    # never stamped one (older / partial packets).
    hash_state: str
    if not isinstance(stamped_hash, str) or not stamped_hash:
        hash_state = "not_stamped"
        # An unstamped packet is not a hash FAIL; treat as warn-only.
        hash_ok = True
    elif hash_ok:
        hash_state = "matches"
    else:
        hash_state = "mismatch"

    # Completeness + banner. W1266 - shared raw-dict helper applies the
    # W1254 stale-demotion penalty so a stale-but-otherwise-complete
    # packet drops from STRONG to PARTIAL via :func:`_classify_banner`.
    _EMPTY_TOTALS = {"complete": 0, "partial": 0, "missing": 8, "not_applicable": 0}
    completeness_result = _run_check_at(
        "classify_completeness",
        classify_completeness,
        payload,
        default=({}, dict(_EMPTY_TOTALS)),
    )
    q_results, totals = completeness_result if completeness_result is not None else ({}, dict(_EMPTY_TOTALS))
    # W261: read the ``producer_not_available`` redaction marker BEFORE
    # classifying the banner so the STRONG cap (parity with
    # :func:`roam.evidence.banner.classify_evidence_coverage`) can demote a
    # producer-gapped packet out of STRONG. Recomputed below into
    # ``has_producer_not_available`` for the honesty-signals readout.
    _pna_for_banner = "producer_not_available" in [r for r in (payload.get("redactions") or []) if isinstance(r, str)]
    banner_result = _run_check_at(
        "classify_banner",
        lambda t: _classify_banner(t, producer_not_available=_pna_for_banner),
        totals,
        default=("insufficient", "Insufficient evidence", "banner classification raised; see warnings_out"),
    )
    banner_tier, banner_label, banner_rationale = (
        banner_result
        if banner_result is not None
        else ("insufficient", "Insufficient evidence", "banner classification raised; see warnings_out")
    )

    # W1262: staleness signal from W1254 producer. Read ``evidence_stale``
    # + ``stale_reasons`` directly from the packet dict so the doctor
    # surfaces the W1234 producer wire-up alongside the coverage banner.
    # Always-emit (Pattern-2): ``stale=False`` + empty reasons is a real
    # signal, not a missing-data case.
    raw_stale = payload.get("evidence_stale")
    stale_flag = bool(raw_stale) if isinstance(raw_stale, bool) else False
    raw_stale_reasons = payload.get("stale_reasons") or []
    stale_reasons: list[str] = (
        [r for r in raw_stale_reasons if isinstance(r, str)] if isinstance(raw_stale_reasons, list) else []
    )

    # W281: actor-trust tier counts + WARN signals.
    trust_result = _run_check_at(
        "classify_trust_tiers",
        _classify_trust_tiers,
        payload,
        default=({k: 0 for k in _TRUST_TIER_KEYS}, []),
    )
    trust_counts, trust_warnings = trust_result if trust_result is not None else ({k: 0 for k in _TRUST_TIER_KEYS}, [])

    # W350: authority-kind tally (permit / mode / lease / policy_rule /
    # approval / token_scope). Permits are exposed as authority_refs with
    # authority_kind="permit" - there is NO top-level permits[] field on
    # ChangeEvidence (W268 collapsed permits/leases into the authority
    # producer axis). Surfacing the breakdown lets reviewers see at a
    # glance "this packet binds 2 permits + 1 mode" without re-parsing
    # the raw authority_refs[] array.
    _EMPTY_AUTHORITY_COUNTS = {
        "approval": 0,
        "lease": 0,
        "mode": 0,
        "permit": 0,
        "policy_rule": 0,
        "token_scope": 0,
    }
    authority_kind_counts = _run_check_at(
        "classify_authority_kinds",
        _classify_authority_kinds,
        payload,
        default=dict(_EMPTY_AUTHORITY_COUNTS),
    )
    if authority_kind_counts is None:
        authority_kind_counts = dict(_EMPTY_AUTHORITY_COUNTS)
    authority_refs_total = sum(authority_kind_counts.values())

    # W280: packet-size budget readout. The doctor never mutates the
    # packet; it only reports the canonical-JSON byte count + budget
    # state so reviewers see at a glance whether the packet is at risk
    # of breaking downstream tools that load it into memory.
    from roam.evidence.change_evidence import (
        PACKET_SIZE_BUDGET_BYTES,
        classify_packet_budget,
        packet_size_bytes,
    )

    pkt_size_bytes = _run_check_at(
        "packet_size_bytes",
        packet_size_bytes,
        payload,
        default=0,
    )
    if pkt_size_bytes is None:
        pkt_size_bytes = 0
    pkt_budget_state = _run_check_at(
        "classify_packet_budget",
        classify_packet_budget,
        pkt_size_bytes,
        default="within_budget",
    )
    if pkt_budget_state is None:
        pkt_budget_state = "within_budget"

    # Honesty signals: redactions + limitations
    redactions = [r for r in (payload.get("redactions") or []) if isinstance(r, str)]
    has_redactions = bool(redactions)
    has_producer_not_available = "producer_not_available" in redactions

    # Next steps for partial / missing questions
    next_steps = _run_check_at(
        "build_next_steps",
        _build_next_steps,
        q_results,
        default=[],
    )
    if next_steps is None:
        next_steps = []

    # Verdict (FAIL > WARN > PASS). W281: trust_warnings on a STRONG
    # banner downgrade PASS -> WARN. W280: oversized_after_truncation
    # contributes WARN (not FAIL) and joins the verdict line.
    verdict_result = _run_check_at(
        "build_verdict",
        _build_verdict,
        schema_ok=schema_ok,
        hash_ok=hash_ok,
        banner_tier=banner_tier,
        enum_violations=len(enum_violations),
        trust_warnings=trust_warnings,
        trust_counts=trust_counts,
        packet_budget_state=pkt_budget_state,
        packet_size_bytes_val=pkt_size_bytes,
        packet_budget_bytes=PACKET_SIZE_BUDGET_BYTES,
        default=(_VERDICT_WARN, "WARN: verdict scorer raised; see warnings_out"),
    )
    level, verdict_line = (
        verdict_result
        if verdict_result is not None
        else (_VERDICT_WARN, "WARN: verdict scorer raised; see warnings_out")
    )

    summary = {
        "verdict": verdict_line,
        "partial_success": level != _VERDICT_PASS,
        "level": level,
        "schema_ok": schema_ok,
        "schema_version": schema_version,
        "hash_ok": hash_ok,
        "hash_state": hash_state,
        "banner_tier": banner_tier,
        "banner_label": banner_label,
        "enum_violations": len(enum_violations),
        "redactions_declared": len(redactions),
        "producer_not_available_marker": has_producer_not_available,
        "complete_count": totals["complete"],
        "partial_count": totals["partial"],
        "missing_count": totals["missing"],
        "not_applicable_count": totals["not_applicable"],
        "next_steps_count": len(next_steps),
        # W281: count of actor_refs whose trust_tier contributed a WARN
        # signal (self_reported_agent / unknown).
        "trust_warnings_count": len(trust_warnings),
        # W350: authority-axis summary counters. ``permits_count`` is the
        # most actionable single field (auditors ask "which permits
        # authorised this change?" first); ``authority_refs_count`` is
        # the cross-kind total so a packet with mode + permit + approval
        # surfaces a non-zero count even if no permits exist.
        "authority_refs_count": authority_refs_total,
        "permits_count": authority_kind_counts.get("permit", 0),
        # W1262: surface the W1254 staleness signal so consumers reading
        # only ``summary`` can detect the banner-demotion-to-stale state
        # without re-parsing the packet.
        "stale": stale_flag,
        "stale_reasons_count": len(stale_reasons),
        # W280: canonical-JSON byte count + budget state. Always emitted
        # so consumers can rely on the keys being present (Pattern-2
        # always-emit). The "size_limit" entry in `redactions` is the
        # marker that distinguishes "this was always small" from "this
        # was truncated down to small" - the doctor reports byte count
        # and state, the producer documents the truncation event.
        "packet_size_bytes": pkt_size_bytes,
        "budget_state": pkt_budget_state,
    }

    # W607-AT -- a non-empty substrate bucket flips partial_success. We
    # OR with any existing partial_success so we never DOWNGRADE a real
    # failure-induced flag set elsewhere.
    if _w607at_warnings_out:
        summary["warnings_out"] = list(_w607at_warnings_out)
        summary["partial_success"] = True

    # W607-CF -- score_classify boundary. Map the W259 banner_tier
    # (strong / partial / insufficient) plus FAIL precedence onto an
    # internal risk vocabulary projected into W631 levels
    # (low / medium / high / critical). Schema/hash FAIL drives critical;
    # banner-driven tiers drive low/medium/high. Floors to ``None`` so
    # the ``score_classification: "unknown"`` sentinel disambiguates a
    # degraded outcome from a real ``"low"`` classification (mirror of
    # cmd_pr_risk W607-BU / cmd_pr_analyze W607-BY / cmd_attest W607-BT).
    def _classify_evidence_doctor_level(_level: str, _banner_tier: str | None) -> str:
        if _level == _VERDICT_FAIL:
            return "critical"
        if _banner_tier == "insufficient":
            return "high"
        if _banner_tier == "partial":
            return "medium"
        # banner_tier == "strong" (or any unknown) floors to ``low`` --
        # mirror of cmd_pr_analyze W531 CI-safety lesson: a typo'd / new
        # banner tier MUST NOT promote a finding into a CI-failing rank.
        return "low"

    _cf_score_probe = _run_check_cf(
        "score_classify",
        _classify_evidence_doctor_level,
        level,
        banner_tier,
        default=None,
    )
    _score_classification_state = "unknown" if _cf_score_probe is None else "classified"
    _evidence_doctor_domain_level = _cf_score_probe if _cf_score_probe is not None else "low"

    # W607-CF -- score_normalize boundary. Wraps the canonical W631
    # ``normalize_risk_level`` + ``risk_rank`` projections so a future
    # signature change / closed-enum vocabulary drift surfaces a marker
    # rather than crashing the envelope. Floors to ``"low"`` / rank ``1``
    # so downstream comparators stay non-null. Pattern 3a discipline:
    # route through ``normalize_risk_level`` (the W631 canonical helper).
    risk_level_canonical = _run_check_cf(
        "score_normalize",
        lambda _level: normalize_risk_level(_level) or "low",
        _evidence_doctor_domain_level,
        default="low",
    )
    risk_rank_int = _run_check_cf(
        "score_normalize",
        risk_rank,
        risk_level_canonical,
        default=1,
    )

    # W607-CF -- compute_verdict boundary. Wraps the augmented verdict
    # text build appending the canonical ``(risk_level X)`` suffix (LAW 6
    # standalone-parse). Floor MUST NOT re-format risk_level_canonical --
    # the same value that tripped the closure would re-raise inside the
    # default f-string. Use a literal "low" floor (LAW 6 still holds:
    # the line works standalone; W631 floor is "low"). W978 first-
    # hypothesis discipline mirror of cmd_pr_analyze W607-BY.
    def _build_augmented_verdict() -> str:
        return f"{verdict_line} (risk_level {risk_level_canonical})"

    augmented_verdict = _run_check_cf(
        "compute_verdict",
        _build_augmented_verdict,
        default="evidence-doctor completed (risk_level low)",
    )

    # Thread the augmented verdict + canonical risk-LEVEL onto the
    # summary block; consumers can call
    # ``risk_rank(data["summary"]["risk_level_canonical"]) >= 3`` to gate
    # on high-or-worse without re-deriving the threshold (Pattern 3a).
    summary["verdict"] = augmented_verdict
    summary["risk_level_canonical"] = risk_level_canonical
    summary["risk_rank"] = risk_rank_int
    summary["score_classification"] = _score_classification_state

    # W607-CF -- combined buckets. ``partial_success`` flips when EITHER
    # bucket is non-empty -- mirrors the W607-BY / W607-BU / W607-BT
    # bucket-merge pattern. Both buckets share the ``evidence_doctor_*``
    # marker family; the additive W607-CF bucket stays distinguishable
    # in tests + audits via its phase names (score_classify /
    # score_normalize / compute_verdict / auto_log / serialize_envelope).
    _combined_warnings_out_cf: list[str] = list(_w607at_warnings_out) + list(_w607cf_warnings_out)
    if _combined_warnings_out_cf:
        summary["warnings_out"] = list(_combined_warnings_out_cf)
        summary["partial_success"] = True

    if json_mode:
        # agent_contract.facts — each terminal anchors on a concrete-noun
        # in the LAW 4 anchor set (entries, findings, records,
        # checks-passed/failed, files, tokens, etc.). Use plurals where
        # we can; use analytical verbs ("scanned", "scored", "passed")
        # otherwise.
        facts: list[str] = []
        # "N of 8 evidence questions complete" is shaped to terminate
        # on "questions" — but that's not in the anchor set. Use
        # "passed"/"failed" (analytical verbs) and "entries" / "records"
        # (concrete nouns).
        facts.append(f"{totals['complete']} questions scored complete")
        facts.append(f"{totals['partial']} questions scored partial")
        facts.append(f"{totals['missing']} questions scored missing")
        if has_redactions:
            facts.append(f"{len(redactions)} redaction entries")
        if enum_violations:
            facts.append(f"{len(enum_violations)} enum violations")
        if hash_state == "matches":
            facts.append("1 content hash verified")
        elif hash_state == "mismatch":
            facts.append("1 content hash failed")
        elif hash_state == "not_stamped":
            facts.append("0 content hashes scanned")
        # Anchor the next-steps count on an analytical verb.
        facts.append(f"{len(next_steps)} next-step entries")
        # W280: packet-size fact. Terminal anchors on "bytes" (in the
        # LAW 4 anchor set per src/roam/output/formatter.py /
        # tests/test_law4_lint.py concrete-noun terminals).
        facts.append(f"{pkt_size_bytes} packet bytes")
        # W281: actor-trust tier facts. Always emit a verified_ci count
        # (Pattern-2 always-emit; reads as "0 verified_ci actor refs
        # scanned" when no verified-CI identity exists). Surface the
        # self_reported_agent / unknown counts conditionally — they only
        # add signal when non-zero. Terminal "flagged" / "scanned" anchor
        # via the analytical-verb path in tests/test_law4_lint.py.
        # W350: authority-axis facts. Always-emit the cross-kind total +
        # the permit count (the P1.10 load-bearing key). Terminals anchor
        # on ``scanned`` (analytical verb) and ``permits`` (kind plural —
        # not in the formatter anchor set but accepted via the verb path
        # below). Use ``records`` terminal for the permit fact so LAW 4
        # picks up the concrete-noun anchor.
        facts.append(f"{authority_refs_total} authority refs scanned")
        facts.append(f"{authority_kind_counts.get('permit', 0)} permit records")
        facts.append(f"{trust_counts['verified_ci']} verified_ci actor refs scanned")
        if trust_counts["self_reported_agent"]:
            facts.append(f"{trust_counts['self_reported_agent']} self_reported_agent actor refs flagged")
        if trust_counts["unknown"]:
            facts.append(f"{trust_counts['unknown']} unknown-tier actor refs flagged")
        # W1262: staleness fact. Anchors on "flagged" (analytical verb in
        # tests/test_law4_lint.py _ANALYTICAL_VERBS) when stale and on
        # "scanned" (analytical verb) when fresh — so a zero-count fact
        # still satisfies LAW 4.
        if stale_flag:
            facts.append(f"{len(stale_reasons)} stale reasons flagged")
        else:
            facts.append("0 stale reasons scanned")

        # next_commands — copy-paste-executable suggestions. For partial
        # / missing questions, prefer literal `roam pr-bundle <verb>`
        # invocations where we have a producer; otherwise emit a
        # human-readable hint as a "# ..." comment (still ASCII-safe).
        next_commands: list[str] = []
        if not schema_ok and enum_violations:
            first_violation = enum_violations[0]
            next_commands.append(
                f"# fix: {first_violation['field']} = {first_violation['value']!r} "
                f"(allowed: {first_violation['expected']})"
            )
        if hash_state == "mismatch":
            next_commands.append("# fix: content_hash drift — re-emit packet via roam pr-replay or roam pr-bundle emit")
        # W280: when the packet is oversized after truncation, suggest
        # the producer-side knobs that shrink the wire footprint.
        if pkt_budget_state == "oversized_after_truncation":
            next_commands.append(
                "# fix: packet oversized — re-emit with fewer inlined artifacts (path + content_hash for large blobs)"
            )
        # W281: when self_reported_agent ActorRefs are present, suggest
        # the env-var path that lifts the tier to local_env (and longer
        # term to verified_ci via CI OIDC).
        if trust_counts["self_reported_agent"]:
            next_commands.append("# set ROAM_AGENT_ID in CI environment to lift trust tier")
        if trust_counts["unknown"]:
            next_commands.append(
                "# attach actor_refs with explicit trust_tier (verified_ci "
                "preferred); unknown is the most-conservative default"
            )
        for step in next_steps:
            qk = step["q"]
            # Map Q to a concrete producer command where one exists.
            if qk in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q7"):
                # These lift via the producer-side recipes.
                next_commands.append(f"# {qk} {step['state']}: {step['action']}")
            elif qk == "Q6":
                next_commands.append(f"# {qk} {step['state']}: {step['action']}")
                next_commands.append("roam rules-validate")
            elif qk == "Q8":
                next_commands.append(f"# {qk} {step['state']}: {step['action']}")
                next_commands.append("roam pr-bundle add-approval")

        # W607-CF -- serialize_envelope boundary. Wraps the envelope
        # serialisation. A downstream schema-shape refactor that breaks
        # ``json_envelope("evidence-doctor", ...)`` would otherwise crash
        # AFTER all substrate + aggregation signals were already gathered.
        # Floor to a minimal envelope stub so consumers still receive a
        # parseable JSON object with the marker attached + the canonical
        # command name. Mirror of cmd_pr_analyze W607-BY / cmd_pr_risk
        # W607-BU / cmd_attest W607-BT serialize_envelope floor pattern.
        # W978 first-hypothesis discipline: the floor uses LITERAL string
        # values for risk_level_canonical / risk_rank rather than the
        # captured ``risk_level_canonical`` / ``risk_rank_int`` variables
        # -- a downstream f-string crash in the score_normalize boundary
        # could leave a non-string sentinel in those locals, which would
        # then re-crash inside json.dumps when the floor stub is
        # serialised. Literal "low" / 1 keep the floor JSON-safe.
        _envelope_floor_cf: dict = {
            "command": "evidence-doctor",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": "evidence-doctor completed (risk_level low)",
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out_cf),
                "risk_level_canonical": "low",
                "risk_rank": 1,
                "score_classification": _score_classification_state,
            },
            "risk_level_canonical": "low",
            "risk_rank": 1,
            "warnings_out": list(_combined_warnings_out_cf),
        }
        envelope = _run_check_cf(
            "serialize_envelope",
            json_envelope,
            "evidence-doctor",
            default=_envelope_floor_cf,
            summary=summary,
            budget=token_budget,
            source=source_label,
            schema_version=schema_version,
            content_hash={
                "stamped": stamped_hash,
                "recomputed": recomputed_hash,
                "state": hash_state,
            },
            # W280: top-level ``packet_size`` block so JSON consumers can
            # route on packet-budget headroom without computing the
            # canonical-JSON size themselves. Always emitted with all
            # three keys (Pattern-2 always-emit).
            packet_size={
                "bytes": pkt_size_bytes,
                "budget_bytes": PACKET_SIZE_BUDGET_BYTES,
                "budget_state": pkt_budget_state,
            },
            banner={
                "tier": banner_tier,
                "label": banner_label,
                "rationale": banner_rationale,
            },
            evidence_completeness={
                "per_question": q_results,
                "totals": totals,
            },
            enum_violations=enum_violations,
            redactions=redactions,
            honesty={
                "redactions_declared": has_redactions,
                "producer_not_available_marker": has_producer_not_available,
            },
            # W281: always-emit all 5 trust-tier keys + a one-entry-per-warn
            # array so JSON consumers can route on identity provenance
            # without re-walking actor_refs[].
            trust_tiers=trust_counts,
            trust_warnings=trust_warnings,
            # W350: authority-kind breakdown. Always-emit all 6
            # AUTHORITY_KINDS keys (Pattern-2 always-emit) so consumers
            # don't branch on "did the doctor count authorities?" - they
            # always get the dict. ``permit`` is the load-bearing key for
            # P1.10 (permits + authority refs round-trip verification).
            authority_kinds=authority_kind_counts,
            # W1262: top-level staleness block mirroring the W1254
            # producer signal. ``stale`` + ``stale_reasons`` are always
            # emitted (Pattern-2 always-emit) so JSON consumers don't
            # branch on "did the doctor read evidence_stale?" - they
            # always get the keys.
            staleness={
                "stale": stale_flag,
                "stale_reasons": stale_reasons,
            },
            next_steps=next_steps,
            agent_contract={
                "facts": facts,
                "next_commands": next_commands,
            },
            # W607-CF -- top-level mirrors of summary.risk_level_canonical
            # / summary.risk_rank so cross-command consumers (pr-bundle,
            # pr-replay) reading the top-level envelope head (without
            # descending into ``summary``) see the canonical bucket.
            # Mirror of the W641-followup contract across the risk-LEVEL
            # emitter family.
            risk_level_canonical=risk_level_canonical,
            risk_rank=risk_rank_int,
            # W607-AT + W607-CF: mirror substrate-CALL + aggregation-phase
            # markers at the top level too so consumers reading
            # envelope.warnings_out (rather than envelope.summary.warnings_out)
            # see the same disclosure. Use the combined bucket.
            **({"warnings_out": list(_combined_warnings_out_cf)} if _combined_warnings_out_cf else {}),
        )
        # W607-CF -- if serialize_envelope raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``evidence_doctor_serialize_envelope_failed:`` marker was
        # appended to ``_w607cf_warnings_out`` and the floor stub carries
        # only the old combined list. Rebuild the floor stub's
        # warnings_out so the new marker reaches the JSON output. Clean
        # path -> envelope is the real json_envelope return, no rebuild.
        if envelope is _envelope_floor_cf and _w607cf_warnings_out:
            _combined_warnings_out_cf = list(_w607at_warnings_out) + list(_w607cf_warnings_out)
            _envelope_floor_cf["summary"]["warnings_out"] = list(_combined_warnings_out_cf)
            _envelope_floor_cf["warnings_out"] = list(_combined_warnings_out_cf)
            envelope = _envelope_floor_cf

        # W607-CF -- auto_log boundary. Silent no-op if no active run;
        # the wrap surfaces HMAC chain-misshape / filesystem failures as
        # ``evidence_doctor_auto_log_failed:...`` markers instead of
        # crashing the envelope after it was already built. Mirror of
        # cmd_pr_analyze W607-BY / cmd_pr_risk W607-BU auto_log pattern.
        _run_check_cf(
            "auto_log",
            auto_log,
            envelope,
            action="evidence-doctor",
            target=source_label,
            default=None,
        )
        # W607-CF -- if auto_log raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log)
        # -> envelope stays byte-identical to the version already built.
        _existing_summary_wo_cf = summary.get("warnings_out") or []
        if _w607cf_warnings_out and not any(
            m.startswith("evidence_doctor_auto_log_failed:") for m in _existing_summary_wo_cf
        ):
            _combined_warnings_out_cf = list(_w607at_warnings_out) + list(_w607cf_warnings_out)
            summary["warnings_out"] = list(_combined_warnings_out_cf)
            summary["partial_success"] = True
            # Rebuild via wrapped serialize_envelope so a later
            # rebuild-time raise still surfaces a marker.
            envelope = _run_check_cf(
                "serialize_envelope",
                json_envelope,
                "evidence-doctor",
                default=_envelope_floor_cf,
                summary=summary,
                budget=token_budget,
                source=source_label,
                schema_version=schema_version,
                content_hash={
                    "stamped": stamped_hash,
                    "recomputed": recomputed_hash,
                    "state": hash_state,
                },
                packet_size={
                    "bytes": pkt_size_bytes,
                    "budget_bytes": PACKET_SIZE_BUDGET_BYTES,
                    "budget_state": pkt_budget_state,
                },
                banner={
                    "tier": banner_tier,
                    "label": banner_label,
                    "rationale": banner_rationale,
                },
                evidence_completeness={
                    "per_question": q_results,
                    "totals": totals,
                },
                enum_violations=enum_violations,
                redactions=redactions,
                honesty={
                    "redactions_declared": has_redactions,
                    "producer_not_available_marker": has_producer_not_available,
                },
                trust_tiers=trust_counts,
                trust_warnings=trust_warnings,
                authority_kinds=authority_kind_counts,
                staleness={
                    "stale": stale_flag,
                    "stale_reasons": stale_reasons,
                },
                next_steps=next_steps,
                agent_contract={
                    "facts": facts,
                    "next_commands": next_commands,
                },
                risk_level_canonical=risk_level_canonical,
                risk_rank=risk_rank_int,
                warnings_out=list(_combined_warnings_out_cf),
            )

        click.echo(to_json(envelope))
        return

    # Text output
    click.echo(f"VERDICT: {verdict_line}")
    click.echo(f"  source: {source_label}")
    click.echo("")

    schema_label = f"v{schema_version}" if schema_version else "unset"
    schema_state = "PASS" if schema_ok else "FAIL"
    click.echo(f"Schema: {schema_label} ChangeEvidence [{schema_state}]")

    if hash_state == "matches":
        click.echo(f"Content hash: {stamped_hash} [PASS - recomputes byte-identical]")
    elif hash_state == "mismatch":
        click.echo(f"Content hash: stamped {stamped_hash}")
        click.echo(f"              recomp  {recomputed_hash} [FAIL - mismatch]")
    else:
        click.echo("Content hash: (not stamped) [WARN - packet has no content_hash]")

    click.echo(f"Banner: {banner_label} ({banner_rationale})")
    click.echo(
        f"Q-coverage: complete={totals['complete']} "
        f"partial={totals['partial']} "
        f"missing={totals['missing']} "
        f"n/a={totals['not_applicable']}"
    )

    # W1262: staleness banner. Emitted IMMEDIATELY after the coverage
    # banner so reviewers see the W1254 producer signal alongside the
    # Q-coverage table. ASCII-only per project conventions (no emoji).
    # Skipped entirely when the packet is fresh - the existing banner
    # is the load-bearing signal in that case.
    if stale_flag:
        click.echo(f"[STALE] EVIDENCE STALE: {len(stale_reasons)} reason(s)")
        for reason in stale_reasons:
            click.echo(f"  - {reason}")

    if enum_violations:
        click.echo("")
        click.echo(f"Closed-enum violations ({len(enum_violations)}):")
        rows = [
            [v["field"], v["value"], (v["expected"][:60] + "...") if len(v["expected"]) > 60 else v["expected"]]
            for v in enum_violations
        ]
        click.echo(format_table(["Field", "Value", "Expected"], rows, budget=0))
    else:
        click.echo("Closed-enum validation: [PASS]")

    if has_redactions:
        click.echo(f"Redactions declared: {len(redactions)} ({', '.join(redactions)})")
    else:
        click.echo("Redactions declared: 0")

    honesty_state = "[PASS]" if (has_redactions or banner_tier == "strong") else "[REVIEW]"
    click.echo(f"Honesty signals: {honesty_state}")

    # W280: packet-size readout. Always emit (Pattern-2 always-emit)
    # so reviewers see the budget headroom inline. Within-budget packets
    # show ``Size: N bytes (within_budget)``; oversized packets surface
    # how far over the budget they sit.
    if pkt_budget_state == "within_budget":
        click.echo(f"Size: {pkt_size_bytes} bytes / {PACKET_SIZE_BUDGET_BYTES} budget ({pkt_budget_state})")
    else:
        over_by = pkt_size_bytes - PACKET_SIZE_BUDGET_BYTES
        click.echo(
            f"Size: {pkt_size_bytes} bytes / "
            f"{PACKET_SIZE_BUDGET_BYTES} budget "
            f"({pkt_budget_state}; {over_by} bytes over budget)"
        )

    # W281: trust-tier readout. Always emit the line (Pattern-2: a
    # zero-count packet still surfaces the tier table) so reviewers see
    # the identity-provenance shape at a glance.
    tier_pairs = ", ".join(f"{k}={trust_counts[k]}" for k in _TRUST_TIER_KEYS)
    click.echo(f"Trust tiers: {tier_pairs}")
    # W350: authority-kind readout. Always emit (Pattern-2) so reviewers
    # see the permits + modes + approvals breakdown at a glance. The
    # ``permit`` count is the P1.10 load-bearing key (auditors ask "what
    # authorised this change?" before any other authority question).
    auth_pairs = ", ".join(f"{k}={authority_kind_counts[k]}" for k in sorted(authority_kind_counts))
    click.echo(f"Authority kinds: {auth_pairs} (total={authority_refs_total})")
    if trust_warnings:
        click.echo(f"Trust warnings ({len(trust_warnings)}):")
        for w in trust_warnings:
            actor_label = w.get("actor_id") or f"[index {w.get('actor_ref_index')}]"
            click.echo(f"  {actor_label}: {w['trust_tier']} — {w['rationale']}")

    # Per-question table
    click.echo("")
    click.echo("Per-question:")
    rows = [[_Q_LABELS[qk], q_results.get(qk, "missing")] for qk in _Q_KEYS]
    click.echo(format_table(["Question", "State"], rows, budget=0))

    if next_steps:
        click.echo("")
        click.echo(f"Next steps ({len(next_steps)}):")
        for step in next_steps:
            click.echo(f"  {step['q']} ({step['state']}): {step['action']}")
