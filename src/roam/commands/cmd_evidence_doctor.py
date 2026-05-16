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
            raw = p.read_text(encoding="utf-8")
            source_label = str(p)
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


def _classify_banner(totals: Mapping[str, int]) -> tuple[str, str, str]:
    """Apply the W259 threshold table to per-question totals.

    Returns ``(tier_id, label, rationale)``. Duplicates the logic in
    :func:`roam.evidence.banner.classify_evidence_coverage` so the
    doctor works without constructing a ``ChangeEvidence`` instance.
    """
    complete = int(totals.get("complete", 0))
    partial = int(totals.get("partial", 0))
    missing = int(totals.get("missing", 0))
    if complete >= 7:
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

    source_label = "<stdin>" if from_stdin else str(packet_path)
    payload, load_error = _load_raw_packet(packet_path, from_stdin=from_stdin)

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
    enum_violations = _validate_closed_enums(payload)
    schema_version = payload.get("schema_version")
    schema_ok = bool(schema_version) and not enum_violations

    # Recompute content_hash
    stamped_hash = payload.get("content_hash")
    recomputed_hash = _recompute_content_hash(payload)
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
    q_results, totals = classify_completeness(payload)
    banner_tier, banner_label, banner_rationale = _classify_banner(totals)

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
    trust_counts, trust_warnings = _classify_trust_tiers(payload)

    # W280: packet-size budget readout. The doctor never mutates the
    # packet; it only reports the canonical-JSON byte count + budget
    # state so reviewers see at a glance whether the packet is at risk
    # of breaking downstream tools that load it into memory.
    from roam.evidence.change_evidence import (
        PACKET_SIZE_BUDGET_BYTES,
        classify_packet_budget,
        packet_size_bytes,
    )

    pkt_size_bytes = packet_size_bytes(payload)
    pkt_budget_state = classify_packet_budget(pkt_size_bytes)

    # Honesty signals: redactions + limitations
    redactions = [r for r in (payload.get("redactions") or []) if isinstance(r, str)]
    has_redactions = bool(redactions)
    has_producer_not_available = "producer_not_available" in redactions

    # Next steps for partial / missing questions
    next_steps = _build_next_steps(q_results)

    # Verdict (FAIL > WARN > PASS). W281: trust_warnings on a STRONG
    # banner downgrade PASS -> WARN. W280: oversized_after_truncation
    # contributes WARN (not FAIL) and joins the verdict line.
    level, verdict_line = _build_verdict(
        schema_ok=schema_ok,
        hash_ok=hash_ok,
        banner_tier=banner_tier,
        enum_violations=len(enum_violations),
        trust_warnings=trust_warnings,
        trust_counts=trust_counts,
        packet_budget_state=pkt_budget_state,
        packet_size_bytes_val=pkt_size_bytes,
        packet_budget_bytes=PACKET_SIZE_BUDGET_BYTES,
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

        envelope = json_envelope(
            "evidence-doctor",
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
        click.echo("Closed-enum validation: PASS")

    if has_redactions:
        click.echo(f"Redactions declared: {len(redactions)} ({', '.join(redactions)})")
    else:
        click.echo("Redactions declared: 0")

    honesty_state = "PASS" if (has_redactions or banner_tier == "strong") else "REVIEW"
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
