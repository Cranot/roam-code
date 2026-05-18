"""W176 Phase 2 - envelope collector for the evidence compiler.

Turns one or more existing Roam JSON envelopes (``pr-bundle emit``,
``findings list``, ``critique``, ``pr-risk``, ``audit-trail``, plus the
event stream from ``.roam/runs/<id>/events.jsonl``) into a single
``ChangeEvidence`` packet.

The collector is **forgiving**: when it sees a field it doesn't yet
know how to map cleanly, it records a human-readable warning in the
returned warnings list rather than raising. Callers (the PR Replay
recipe in W177; the eventual control-plane API) decide whether to
fail the run or proceed with the partial packet.

Public surface:

* :func:`collect_change_evidence` - the headline function.

Design decisions worth pinning here so the next wave doesn't have to
re-derive them:

* **Caller args > envelope contents.** When the caller passes
  ``commit_sha="xyz"`` AND the pr-bundle envelope also carries
  ``commit_sha``, the caller wins. This mirrors CLAUDE.md LAW 11
  (explicit user intent beats inferred values).

* **Unknown ``subject_kind`` on a finding row is kept, not dropped.**
  ``ChangeEvidence.findings`` is typed as ``tuple[Mapping, ...]`` -
  the closed-enum validation lives on ``EvidenceSubject``, not on raw
  finding dicts. So we keep the row and emit a warning naming the
  unrecognised kind. Reasoning: a finding from a future detector
  using a kind we haven't added to ``SUBJECT_KINDS`` is still valid
  evidence; silently dropping it would erase signal.

* **Unknown redaction reasons emit a warning and are dropped.** The
  ``ChangeEvidence`` constructor enforces ``REDACTION_REASONS``; if
  we passed an unknown reason through, the packet would fail to
  construct. The collector is meant to be forgiving, so we strip
  unknowns and warn the caller. Compare with ``EvidenceArtifact`` /
  ``ChangeEvidence`` themselves, which DO raise - the collector is
  the integration layer that absorbs upstream drift.

* **Earliest / latest event timestamps drive started_at / completed_at.**
  When pr-bundle doesn't carry timestamps but run_events are present,
  we use the earliest ``ts`` as ``started_at`` and the latest as
  ``completed_at``. This mirrors the run-ledger contract
  (``meta.json`` is allowed to be stale during a run; the events
  themselves are authoritative).

* **Audit-trail envelope folds into ``extra`` via a synthetic
  finding row.** ``ChangeEvidence`` has no dedicated audit-trail
  field. The architecture memo's Phase 4 ("Governance control
  mapping") will land that hook. For now, the audit-trail envelope
  becomes one synthetic finding with ``source_detector="audit-trail"``
  so the data isn't lost.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import socket
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roam.evidence._vocabulary import REDACTION_REASONS, SUBJECT_KINDS
from roam.evidence.actor_trust import classify_actor_trust_tier
from roam.evidence.artifact import EvidenceArtifact
from roam.evidence.change_evidence import ChangeEvidence, resolve_roam_version
from roam.evidence.mcp_receipt import McpDecisionReceipt
from roam.evidence.provenance import provenance_label
from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef
from roam.evidence.subject import EvidenceSubject
from roam.output.risk import normalize_risk_level

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields the collector recognises on a pr-bundle envelope. Anything
# outside this set is reported as a warning so callers can spot drift.
# Top-level envelope keys ``schema``/``schema_version``/``command``/
# ``project``/``version``/``_meta`` come from ``json_envelope`` and are
# part of every envelope - skip them silently. ``budget``, ``schema``,
# ``schema_version`` are likewise envelope chrome rather than payload.
_PR_BUNDLE_ENVELOPE_CHROME: frozenset[str] = frozenset(
    {
        "schema",
        "schema_version",
        "command",
        "project",
        "version",
        "_meta",
        "budget",
        "summary",
    }
)

# Top-level keys we know how to extract from a pr-bundle envelope. The
# real pr-bundle envelope (``cmd_pr_bundle._build_envelope``) emits
# these plus a few state markers we tolerate but don't pull data from.
_PR_BUNDLE_KNOWN_PAYLOAD: frozenset[str] = frozenset(
    {
        # data fields the collector reads
        "intent",
        "context_read",
        "affected_symbols",
        "risks",
        "tests_required",
        "tests_run",
        "known_non_goals",
        "roam_verdict",
        "bundle_meta",
        "bundle_path",
        "mode_block",
        # caller-or-envelope identity fields the collector reads
        "actor",
        "timestamps",
        "run_ids",
        "agent_id",
        "human_actor",
        "mode",
        "verdict",
        "risk_level",
        "commit_sha",
        "git_range",
        "diff_hash",
        "repo_id",
        "redactions",
        "approvals",
        "accepted_risks",
        "context_files",
        "agent_contract",
        # W190 / W268 - agentic-assurance authority producers. W189 shipped
        # ``approvals`` / ``accepted_risks``; W268 promoted ``permits`` and
        # ``leases`` to real top-level producer fields on the pr-bundle
        # envelope (read from ``.roam/permits/*.json`` and
        # ``.roam/leases/*.json``). ``roam permit`` is still a verdict
        # facade per W198 so the on-disk permits directory is usually
        # empty - that's fine, the envelope just carries ``permits: []``.
        "permits",
        "leases",
        "rules_passed",
        # W266 - producer-side env_refs (cmd_pr_bundle now materialises them
        # via the shared ``build_environment_refs`` helper). The collector
        # still rebuilds its own EnvironmentRef tuple independently from
        # caller args + envelope git/repo_id - so this key is informational
        # to the collector and only needs the unknown-key allowlist entry.
        "environment_refs",
    }
)


# ---------------------------------------------------------------------------
# W241 - collector-side last-line-of-defense redaction primitives
# ---------------------------------------------------------------------------
#
# These constants + helpers seal the three leak surfaces named in W232:
#
# * Leak A (W236b): _normalise_findings_envelope did ``dict(row)`` -
#   open key set. Replaced with _FINDING_SAFE_KEYS allowlist + secret
#   scrubber on the surviving ``claim`` field.
# * Leak B (W236c): _inline_raw_envelope_artifact serialised the WHOLE
#   vuln-reach envelope. The vuln-reach call site now passes the
#   envelope through _safe_vuln_reach_envelope() first.
# * Leak C (W236d): CGA path was only dropped when Path.exists() returned
#   False - i.e. the leak was prevented by accident on test runners.
#   _is_suspicious_path() actively rejects user-home / credential-dir
#   absolute paths regardless of disk presence.
#
# Layer-1 defense (producer-side scrubbing) is W240's domain; this is
# the layer-2 net that catches anything bypassing W240 (third-party
# plugins, archived runs from pre-W240 roam versions, etc.).

# Closed allowlist of finding-row keys that survive the collector copy.
# Any key outside this set is dropped silently. Extending this set is
# a deliberate audit moment: each new key must be verified non-leakable
# (i.e. carries metadata, not raw content / source / credentials).
_FINDING_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "finding_id_str",
        "id",  # cmd_pr_replay shorthand for finding_id_str
        "source_detector",
        "detector",  # cmd_pr_replay shorthand for source_detector
        "source_version",
        "subject_kind",
        "subject_id",
        "confidence",
        "confidence_basis",
        "claim",
        "kind",  # detector-specific kind (e.g. "patch.clone_not_edited")
        "severity",
        "tier",
        "evidence_ref",  # path/hash reference to another packet; never raw bytes
        # vuln-reach extras the W193 flattener stamps - all scalar / well-shaped:
        "cve",
        "package",
        "reachable",
        "hops",
        "blast_radius",
        "path",  # vuln-reach reachability path (list of symbol names)
        # pr-risk / critique extras
        "check",  # critique check id, e.g. "clones_not_edited"
        "rule_id",
        "redactions",  # forward-pointer: per-row redaction stamps
        # cmd_pr_replay postmortem-aggregate extras (scalar counters):
        "total_findings",
        "commits_with_finding",
        "source",  # e.g. "postmortem-aggregate" - origin label, never raw
    }
)

# Closed allowlist of TOP-LEVEL keys that survive the vuln-reach
# raw_envelope inline. Free-form fields (description, message, snippet)
# do NOT appear here, which is the whole point.
_VULN_REACH_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "summary",
        "vulnerabilities",
        "command",
        "schema",
        "schema_version",
    }
)

# Closed allowlist of per-vulnerability-row keys that survive the
# whitelist copy. Excludes ``description``, ``snippet``, ``raw_message``
# and any other free-form field a producer might (incorrectly) populate
# with raw bytes / prompts / source.
_VULN_ROW_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "cve",
        "cve_id",
        "package",
        "package_name",
        "package_version",
        "reachable",
        "severity",
        "fix_available",
        "hops",
        "blast_radius",
        "path",  # reachability path (list of symbol names; not raw source)
    }
)

# Closed allowlist of summary fields on a vuln-reach envelope. The
# vuln-reach summary today emits scalar verdict / counts; keep the set
# narrow so a future producer cannot ride a free-form field through.
_VULN_REACH_SUMMARY_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "verdict",
        "count",
        "total",
        "total_reachable",
        "total_unreachable",
        "command",
        "schema_version",
        "state",
    }
)

# Path prefixes that name user-home / credential / config directories.
# A path containing any of these substrings (after slash-normalisation)
# is rejected by ``_is_suspicious_path``. The check is intentionally
# substring-based: a path like ``/var/spool/data/Users/foo`` would
# false-positive, but the cost of an over-aggressive reject is one
# missing path reference (the content_hash still identifies the
# artifact) - far cheaper than a leaked credential directory.
_SUSPICIOUS_PATH_PREFIXES: tuple[str, ...] = (
    "/home/",
    "/Users/",
    "/root/",
    "C:/Users/",
    "/.ssh/",
    "/.aws/",
    "/.kube/",
    "/.config/",
    "/.gnupg/",
    "/AppData/",
)

# Secret-shaped substring patterns the layer-2 scrubber catches. These
# mirror the patterns W240 will install at producer boundaries; the
# duplication is deliberate (defense-in-depth, see module-level note
# above). Patterns are documented at each line so future audits can
# verify each is intentional and not a false-positive magnet.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # GitHub PAT (classic + fine-grained share the ghp_ prefix)
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    # GitHub OAuth / app tokens
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"ghs_[A-Za-z0-9]{20,}"),
    re.compile(r"ghu_[A-Za-z0-9]{20,}"),
    # OpenAI-style sk-proj- keys
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # AWS access key ID shape (AKIA + 16 alnum)
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # JWT (header.payload.signature; the header is the recognisable
    # eyJ prefix). Capture the whole token up to the next whitespace.
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # PEM-armoured private keys (the line marker is the leak signal)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _redact_secrets_in_string(value: str) -> tuple[str, bool]:
    """Layer-2 secret scrubber - same patterns as W240's producer-side
    helper, kept here so non-W240 producers (third-party plugins,
    archived runs from older roam versions) still get scrubbed at the
    collector boundary.

    Returns ``(redacted_value, had_secrets)``. ``had_secrets`` is True
    iff at least one pattern matched (the caller stamps ``"secret"`` in
    the row's ``redactions[]`` trail in that case).
    """
    if not isinstance(value, str) or not value:
        return value, False
    redacted = value
    had_secrets = False
    for pattern in _SECRET_PATTERNS:
        new_value, count = pattern.subn("[REDACTED]", redacted)
        if count > 0:
            had_secrets = True
            redacted = new_value
    return redacted, had_secrets


# ---------------------------------------------------------------------------
# W249 - Layer-2 pr-bundle envelope field scrubber.
# ---------------------------------------------------------------------------
#
# W240 sealed the producer side: ``cmd_pr_bundle.py`` scrubs ``verdict``
# and the actor block before emitting the envelope. W249 closes the
# remaining defense-in-depth gap by re-scrubbing the SAME fields when a
# pr-bundle-shaped envelope arrives at ``collect_change_evidence``.
#
# Failure modes that need layer-2 even after W240 is live:
#
#   1. Envelopes from pre-W240 producers (older runs replayed today)
#      that were emitted before W240's scrub landed.
#   2. Envelopes from third-party plugins or external tooling that emit
#      pr-bundle-shaped JSON without W240's producer-side scrub.
#   3. Envelopes hand-crafted in test fixtures (the W232 snapshot
#      harness feeds synthetic envelopes DIRECTLY to the collector,
#      bypassing ``cmd_pr_bundle``).
#
# Reuses ``_redact_secrets_in_string`` above (originally added for W241
# finding-claim scrubbing) so the pattern set stays single-sourced.

# Fields on the actor block whose values may carry secrets and must be
# scrubbed before they land in any ActorRef / ChangeEvidence field.
_ACTOR_BLOCK_SECRET_KEYS: tuple[str, ...] = (
    "agent_id",
    "agent",
    "human_actor",
    "human",
    "user",
    "display_name",
    "mcp_client_id",
    "tool_id",
    "ci_runner_id",
)


def _scrub_actor_block(
    actor: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any] | None, bool]:
    """Return a copy of ``actor`` with every secret-key value scrubbed.

    Returns ``(scrubbed_actor, had_secrets)``. Non-string values ride
    through unchanged. Callers should stamp ``"secret"`` into the
    packet's ``redactions`` trail when ``had_secrets`` is True.

    The actor block we receive at the collector boundary may come from
    W189's producer (always sanitised by W240) OR from an older / third-
    party producer (no scrub guarantee). Re-running the scrub here is
    cheap and idempotent - W240's ``[REDACTED]`` placeholders contain
    no patterns that match the secret regexes, so a second pass is a
    no-op when the value is already clean.
    """
    if not isinstance(actor, Mapping):
        return actor, False
    scrubbed: dict[str, Any] = {}
    had_secrets = False
    for key, value in actor.items():
        if key in _ACTOR_BLOCK_SECRET_KEYS and isinstance(value, str):
            new_value, hit = _redact_secrets_in_string(value)
            if hit:
                had_secrets = True
            scrubbed[key] = new_value
        else:
            scrubbed[key] = value
    return scrubbed, had_secrets


def _is_suspicious_path(path: str) -> bool:
    """Reject paths that name user-home / credential / config directories.

    Substring-match against ``_SUSPICIOUS_PATH_PREFIXES`` after
    slash-normalisation so Windows backslash paths are caught
    identically to POSIX forward-slash paths.

    These are machine-local and almost never the right artifact home -
    a CGA / audit-trail / receipts artifact landing in ``~/.ssh`` is a
    misconfiguration we'd rather drop the path reference for than leak.
    """
    if not isinstance(path, str) or not path:
        return False
    normalised = path.replace("\\", "/")
    for prefix in _SUSPICIOUS_PATH_PREFIXES:
        if prefix.replace("\\", "/") in normalised:
            return True
    return False


def _safe_vuln_reach_envelope(envelope: Mapping[str, Any]) -> dict:
    """Return a redacted copy with only allowlisted keys.

    Recurses one level into ``summary`` (allowlisted scalar fields) and
    ``vulnerabilities[]`` (per-row allowlist). All other top-level keys
    are dropped, and free-form fields on the rows
    (``description`` / ``message`` / ``snippet`` / ...) never reach the
    inlined ``raw_envelope`` artifact body.
    """
    safe: dict[str, Any] = {}
    for k, v in envelope.items():
        if k == "vulnerabilities" and isinstance(v, list):
            safe_rows = []
            for row in v:
                if isinstance(row, Mapping):
                    safe_rows.append({kk: vv for kk, vv in row.items() if kk in _VULN_ROW_SAFE_KEYS})
            safe["vulnerabilities"] = safe_rows
        elif k == "summary" and isinstance(v, Mapping):
            safe["summary"] = {kk: vv for kk, vv in v.items() if kk in _VULN_REACH_SUMMARY_SAFE_KEYS}
        elif k in _VULN_REACH_SAFE_KEYS:
            # Scalar safe keys (command, schema, schema_version) - copy verbatim.
            safe[k] = v
    return safe


# ---------------------------------------------------------------------------
# Helpers - identity / hashing
# ---------------------------------------------------------------------------


def _evidence_id_from_inputs(
    *,
    commit_sha: str | None,
    git_range: str | None,
    diff_hash: str | None,
    pr_bundle_envelope: Mapping[str, Any] | None,
) -> str:
    """Derive a stable ``evidence_id`` from whatever identity we have.

    Order of preference:
      1. ``commit_sha`` (most specific)
      2. ``diff_hash``  (covers staged / unstaged that doesn't have a sha)
      3. ``git_range``  (covers retrospective ranges)
      4. SHA1 of the pr-bundle envelope's canonical JSON
      5. Constant fallback ``"ev_unknown"``

    The id is intentionally not a UUID - we want reruns with the same
    inputs to collapse onto the same id so consumers can dedup.
    """
    if commit_sha:
        return f"ev_commit_{commit_sha[:12]}"
    if diff_hash:
        return f"ev_diff_{diff_hash[:12]}"
    if git_range:
        # Slashes / dots / colons aren't valid id glyphs in some downstream
        # consumers - sha1 the range to flatten it.
        digest = hashlib.sha1(git_range.encode("utf-8")).hexdigest()[:12]
        return f"ev_range_{digest}"
    if pr_bundle_envelope:
        try:
            canonical = json.dumps(pr_bundle_envelope, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
            return f"ev_bundle_{digest}"
        except (TypeError, ValueError):
            pass
    return "ev_unknown"


# ---------------------------------------------------------------------------
# Helpers - pr-bundle decomposition
# ---------------------------------------------------------------------------


def _coalesce(*values: Any) -> Any | None:
    """Return the first truthy value, else None.

    Treats empty string / 0 / [] as 'not provided' so an envelope that
    explicitly sets ``commit_sha=""`` doesn't shadow a caller's real
    value.
    """
    for v in values:
        if v:
            return v
    return None


def _get_pr_bundle_field(envelope: Mapping[str, Any], key: str) -> Any | None:
    """Look up a field at top level, then under ``bundle_meta``, then
    under ``summary``. Returns the first non-empty match."""
    top = envelope.get(key)
    if top:
        return top
    bm = envelope.get("bundle_meta")
    if isinstance(bm, Mapping):
        v = bm.get(key)
        if v:
            return v
    summary = envelope.get("summary")
    if isinstance(summary, Mapping):
        v = summary.get(key)
        if v:
            return v
    return None


# W641-followup-F — risk-LEVEL projection helpers
# ---------------------------------------------------------------------------

#: Closed enum of the three lineage tokens that name WHERE the collector
#: read the risk-LEVEL field from. Distinct from PROVENANCE_SOURCES
#: (W282), which names the producer-side surface (``producer_envelope``
#: / ``run_ledger`` / ``cli_flag`` / ...). This vocabulary names the
#: COLLECTOR-side decision branch: which of the three priority lanes in
#: :func:`_resolve_risk_level_with_lineage` fired.
#:
#: Tokens:
#:
#: * ``canonical``           — envelope carried ``risk_level_canonical``
#:                              (top-level OR ``summary.risk_level_canonical``);
#:                              the producer has already gone through
#:                              ``normalize_risk_level``. Best signal.
#: * ``verdict_text_legacy`` — envelope carried only the legacy
#:                              ``risk_level`` / ``summary.risk_level``
#:                              field (no canonical mirror). Lifted +
#:                              normalised through ``normalize_risk_level``;
#:                              preserved verbatim if normalize returns
#:                              ``None`` for pre-W631 byte-stability.
#: * ``missing``             — neither source present. The packet's
#:                              ``risk_level`` field stays ``None`` and
#:                              ``evidence_completeness()`` classifies Q5
#:                              as ``not_applicable`` (when verdict is
#:                              SAFE/PASS + no findings) or ``missing``.
RISK_LEVEL_LINEAGE_SOURCES: frozenset[str] = frozenset({"canonical", "verdict_text_legacy", "missing"})


def _resolve_risk_level_with_lineage(
    envelope: Mapping[str, Any],
) -> tuple[str | None, str, str | None]:
    """Resolve the risk-LEVEL field with explicit lineage disclosure.

    Returns a ``(value, source, divergence_warning)`` triple:

    * ``value``               — the resolved risk-LEVEL string (or ``None``
                                when neither source is present). Always
                                run through :func:`normalize_risk_level`
                                when a canonical source fires; falls back
                                to the raw legacy string when normalize
                                returns ``None`` on the legacy lane (so
                                a pre-W631 envelope carrying an unknown
                                label doesn't lose its value).
    * ``source``              — one of the :data:`RISK_LEVEL_LINEAGE_SOURCES`
                                tokens naming WHICH lane fired.
    * ``divergence_warning``  — when BOTH canonical AND legacy are present
                                AND they disagree post-normalize, a stable
                                ``"risk_level_divergence:<canonical>:<legacy>"``
                                string suitable for the collector's
                                ``warnings`` channel. ``None`` when no
                                divergence (silent happy path).

    Priority chain:

    1. ``envelope["risk_level_canonical"]``                  → ``canonical``
    2. ``envelope["summary"]["risk_level_canonical"]``       → ``canonical``
    3. ``envelope["risk_level"]``                            → ``verdict_text_legacy``
       ``envelope["summary"]["risk_level"]``
    4. neither present                                       → ``missing`` (value=None)

    Canonical wins on disagreement — producers that emit
    ``risk_level_canonical`` have already normalised the bucket.
    """
    canonical_raw = _coalesce(
        envelope.get("risk_level_canonical"),
        _nested(envelope, ("summary", "risk_level_canonical")),
    )
    legacy_raw = _coalesce(
        envelope.get("risk_level"),
        _nested(envelope, ("summary", "risk_level")),
    )
    canonical_norm = normalize_risk_level(canonical_raw) if canonical_raw else None
    legacy_norm = normalize_risk_level(legacy_raw) if legacy_raw else None

    if canonical_norm is not None:
        divergence: str | None = None
        if legacy_norm is not None and legacy_norm != canonical_norm:
            divergence = f"risk_level_divergence:{canonical_norm}:{legacy_norm}"
        return canonical_norm, "canonical", divergence
    if legacy_norm is not None:
        return legacy_norm, "verdict_text_legacy", None
    if legacy_raw:
        # Legacy field present but normalize returned None (unknown label).
        # Preserve the raw string for pre-W631 byte-stability while still
        # disclosing the legacy lineage.
        return legacy_raw, "verdict_text_legacy", None
    return None, "missing", None


def resolve_risk_level_with_lineage(
    envelope: Mapping[str, Any],
) -> tuple[str | None, str, str | None]:
    """Public wrapper around :func:`_resolve_risk_level_with_lineage`.

    Exposed so tests + downstream consumers can introspect the
    collector's risk-LEVEL lineage decision without re-implementing the
    priority chain.

    W641-followup-F: closes the producer→packet projection loop. Producers
    emit ``risk_level_canonical`` (W641 cluster); the collector lifts the
    canonical value via this helper and stamps it onto
    :attr:`ChangeEvidence.risk_level`. Lineage is observable for audit
    via the second return slot.
    """
    return _resolve_risk_level_with_lineage(envelope)


def _build_changed_subjects_from_affected(
    affected: Any,
    repo_id: str | None,
    warnings: list[str],
) -> tuple[EvidenceSubject, ...]:
    """Convert pr-bundle ``affected_symbols`` rows into EvidenceSubjects.

    Each row is normally a dict with ``name`` / ``kind`` / ``file`` /
    ``blast_radius`` (see ``cmd_pr_bundle._harvest_envelope``). Plain
    strings are also tolerated.
    """
    if not isinstance(affected, list):
        return ()
    out: list[EvidenceSubject] = []
    for rec in affected:
        if isinstance(rec, str):
            try:
                out.append(
                    EvidenceSubject(
                        kind="symbol",
                        qualified_name=rec,
                        repo_id=repo_id,
                    )
                )
            except ValueError as exc:
                warnings.append(f"pr_bundle.affected_symbols: skipped {rec!r} ({exc})")
            continue
        if not isinstance(rec, Mapping):
            warnings.append("pr_bundle.affected_symbols: skipped non-dict / non-string row")
            continue
        name = rec.get("name") or rec.get("qualified_name") or rec.get("symbol")
        if not name:
            warnings.append("pr_bundle.affected_symbols: skipped row with no name")
            continue
        extra: dict[str, Any] = {}
        for field in (
            "kind",
            "file",
            "blast_radius",
            "resolution_state",
            "side_effect_kinds",
            "idempotency_kind",
            "world_model_confidence",
            "causal_diff_state",
        ):
            if field in rec and rec[field] not in (None, ""):
                extra[field] = rec[field]
        try:
            out.append(
                EvidenceSubject(
                    kind="symbol",
                    qualified_name=str(name),
                    repo_id=repo_id,
                    extra=extra,
                )
            )
        except ValueError as exc:
            warnings.append(f"pr_bundle.affected_symbols: rejected row {name!r} ({exc})")
    return tuple(out)


def _build_context_refs_from_context_files(
    context_files: Any,
    warnings: list[str],
) -> tuple[EvidenceArtifact, ...]:
    """Convert pr-bundle ``context_files`` entries into artifacts.

    Each entry becomes a path-referenced ``raw_envelope`` artifact when
    we have a ``content_hash`` to attach; otherwise we fall back to an
    inline artifact carrying the file path as its body so the reference
    isn't lost.

    Convention is permissive: the entry may be a plain string (the
    path), or a dict with ``path`` and optional ``content_hash``.
    """
    if not isinstance(context_files, list):
        return ()
    out: list[EvidenceArtifact] = []
    for idx, entry in enumerate(context_files):
        path = None
        chash = None
        if isinstance(entry, str):
            path = entry
        elif isinstance(entry, Mapping):
            path = entry.get("path")
            chash = entry.get("content_hash")
        else:
            warnings.append(f"pr_bundle.context_files[{idx}]: skipped non-string / non-dict row")
            continue
        if not path:
            warnings.append(f"pr_bundle.context_files[{idx}]: skipped row with no path")
            continue
        artifact_id = f"ctx:{idx}:{_short_path_hash(str(path))}"
        try:
            if chash:
                art = EvidenceArtifact(
                    artifact_id=artifact_id,
                    kind="raw_envelope",
                    path=str(path),
                    content_hash=str(chash),
                )
            else:
                # No content_hash on disk - embed the path as the inline
                # body so the reference is preserved. Path-only artifacts
                # require content_hash; inline-only doesn't, which is
                # exactly the lifeboat we need here.
                art = EvidenceArtifact(
                    artifact_id=artifact_id,
                    kind="raw_envelope",
                    content_inline=str(path),
                )
            out.append(art)
        except ValueError as exc:
            warnings.append(f"pr_bundle.context_files[{idx}]: rejected ({exc})")
    return tuple(out)


def _short_path_hash(s: str) -> str:
    """Short stable hash of a string for use in artifact ids."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Helpers - findings normalisation
# ---------------------------------------------------------------------------


def _normalise_findings_envelope(
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
) -> list[Mapping[str, Any]]:
    """Pull the finding rows out of one findings-shaped envelope.

    A findings envelope (from ``roam findings list``) carries a top-
    level ``findings: []`` array. A critique envelope carries
    ``findings: []`` as well. A pr-risk envelope carries factor rows
    under several keys; we look at the top-level ``findings`` first.
    Any envelope without a ``findings`` array contributes nothing and
    emits a warning.

    W241 (layer-2 redaction): rows are copied through a closed
    ``_FINDING_SAFE_KEYS`` allowlist; any free-form key a producer might
    stamp (``snippet`` / ``evidence`` / ``raw_message`` / ...) is
    dropped silently. The surviving ``claim`` field is run through
    ``_redact_secrets_in_string`` so secret-shaped substrings get
    masked to ``[REDACTED]`` and a per-row ``redactions: ["secret"]``
    trail is stamped.
    """
    rows = envelope.get("findings")
    if not isinstance(rows, list):
        warnings.append(f"{source_label}: no 'findings' array at top level - skipped")
        return []
    out: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            warnings.append(f"{source_label}: skipped non-dict finding row")
            continue
        # Track unknown subject_kind values so the caller spots drift.
        kind = row.get("subject_kind")
        if kind is not None and kind not in SUBJECT_KINDS:
            warnings.append(f"{source_label}: finding row has unknown subject_kind {kind!r} (kept anyway)")
        # W241: closed allowlist copy. Free-form keys never survive.
        safe_row: dict[str, Any] = {k: v for k, v in row.items() if k in _FINDING_SAFE_KEYS}
        # W241: scrub the ``claim`` field through the secret-pattern
        # check. Producer hardening (W240) is layer 1; this is layer 2
        # for envelopes from non-W240 producers.
        claim_val = safe_row.get("claim")
        if isinstance(claim_val, str) and claim_val:
            redacted, had_secrets = _redact_secrets_in_string(claim_val)
            if had_secrets:
                safe_row["claim"] = redacted
                existing = safe_row.get("redactions")
                if isinstance(existing, (list, tuple)):
                    trail = list(existing)
                else:
                    trail = []
                if "secret" not in trail:
                    trail.append("secret")
                safe_row["redactions"] = trail
        out.append(safe_row)
    return out


def _normalise_redactions(
    raw: Any,
    warnings: list[str],
    source_label: str,
) -> list[str]:
    """Return only redaction reasons that pass the closed-enum check.

    Unknown reasons emit a warning and are dropped, NOT raised.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for r in raw:
        if not isinstance(r, str):
            warnings.append(f"{source_label}: redaction reason is not a string ({r!r}) - dropped")
            continue
        if r not in REDACTION_REASONS:
            warnings.append(f"{source_label}: unknown redaction reason {r!r} - dropped")
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Helpers - run events
# ---------------------------------------------------------------------------


def _collect_run_event_metadata(
    events: Iterable[Mapping[str, Any]],
) -> tuple[list[str], str | None, str | None]:
    """Return (run_ids, earliest_ts, latest_ts) over an event iterable.

    ``run_id`` is read from a top-level field on each event (some emit
    sites stamp it; the helper-emitted ones don't). Missing fields are
    fine - we just skip those events for the ID collection.
    """
    run_ids: list[str] = []
    seen: set[str] = set()
    earliest: str | None = None
    latest: str | None = None
    for ev in events:
        if not isinstance(ev, Mapping):
            continue
        rid = ev.get("run_id")
        if isinstance(rid, str) and rid and rid not in seen:
            seen.add(rid)
            run_ids.append(rid)
        ts = ev.get("ts")
        if isinstance(ts, str) and ts:
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts
    return run_ids, earliest, latest


# ---------------------------------------------------------------------------
# W1234 - change-scope timestamps (context_read_at / edits_started_at /
# edits_completed_at) + evidence_stale flag.
# ---------------------------------------------------------------------------
#
# The W210 scaffold added three change-scope timestamps + an
# ``evidence_stale`` flag on :class:`ChangeEvidence` but never wired a
# producer. This block is the producer-side wire-up: walk the run-ledger
# event stream, classify each event as a "context-read" probe or a
# post-edit action, and let the earliest / latest timestamps in each
# bucket drive the three new fields.
#
# Classifier discipline (closed allowlists, no free-form pattern match):
#
# * ``_CONTEXT_READ_ACTIONS`` - actions whose semantic is "gather state
#   to plan an edit" or "describe the codebase as it stands". The LATEST
#   such timestamp seeds ``context_read_at`` (the most recent read wins,
#   matching the W210 semantic "when did the agent last refresh its
#   understanding").
#
# * ``_EDIT_PHASE_ACTIONS`` - actions whose semantic is "report on edits
#   that have already happened". The EARLIEST timestamp seeds
#   ``edits_started_at``; the LATEST seeds ``edits_completed_at``.
#
# * ``pr-bundle`` is a meta-action whose semantic varies by subcommand;
#   ``_PR_BUNDLE_CONTEXT_SUBCOMMANDS`` and ``_PR_BUNDLE_EDIT_SUBCOMMANDS``
#   split it on the ``envelope_command`` field that ``auto_log`` already
#   stamps on every emitted event. Subcommands not in either set
#   (e.g. ``pr-bundle-add-approval`` / ``pr-bundle-validate``) stay
#   unclassified - they don't move either timestamp.
#
# Pattern-2 always-emit + honest-defaults: missing timestamps mean
# "insufficient data", NOT "no staleness". ``evidence_stale`` defaults
# to ``False`` and only flips ``True`` when BOTH ``context_read_at`` AND
# ``edits_started_at`` are populated AND the read post-dates the start
# of edits. The hash-stable defaults preserve byte-identical canonical
# JSON for any packet whose run_events don't contain phase-classifiable
# entries (see :data:`_W210_OMIT_WHEN_DEFAULT_FIELDS`).

_CONTEXT_READ_ACTIONS: frozenset[str] = frozenset(
    {
        "preflight",
        "impact",
        "pr-prep",
        "pr-analyze",
        "architecture-drift",
        "brief",
        "agents-md",
        "causal-graph",
        "side-effects",
        "idempotency",
        "tx-boundaries",
        "laws-mine",
        "laws-check",
        "graph-diff",
    }
)

_EDIT_PHASE_ACTIONS: frozenset[str] = frozenset(
    {
        "diff",
        "critique",
        "attest",
        "verify",
    }
)

# ``pr-bundle`` is the umbrella action label every pr-bundle subcommand
# emits; the precise subcommand lives on ``envelope_command``. These two
# allowlists split the umbrella into the two phases the way the bundle
# lifecycle reads on the wire (init/intent/add-affected/add-risk/etc.
# are "still gathering context" steps; add-test-run / emit are
# "reporting on completed edits" steps).
_PR_BUNDLE_CONTEXT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "pr-bundle-init",
        "pr-bundle-set-intent",
        "pr-bundle-add-affected",
        "pr-bundle-add-risk",
        "pr-bundle-add-test-required",
        "pr-bundle-add-non-goal",
        "pr-bundle-add-context-cmd",
        "pr-bundle-add-context-symbol",
        "pr-bundle-add-context-file",
    }
)

_PR_BUNDLE_EDIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "pr-bundle-add-test-run",
        # The bare ``pr-bundle`` command_label is the ``emit`` subcommand
        # (see cmd_pr_bundle.py line 2788 - the only call site without a
        # subcommand suffix). ``emit`` is the canonical "edits are done,
        # producing the proof bundle" terminal step.
        "pr-bundle",
    }
)


def _classify_event_phase(event: Mapping[str, Any]) -> str | None:
    """Return ``"context_read"`` / ``"edit"`` / ``None`` for one event.

    Returns ``None`` when the event is not classifiable (unknown action,
    unclassified pr-bundle subcommand, or missing fields). The caller
    treats ``None`` as "leave the timestamps alone" - the honest
    insufficient-data path.
    """
    action = event.get("action")
    if not isinstance(action, str) or not action:
        return None
    if action in _CONTEXT_READ_ACTIONS:
        return "context_read"
    if action in _EDIT_PHASE_ACTIONS:
        return "edit"
    if action == "pr-bundle":
        env_cmd = event.get("envelope_command")
        if isinstance(env_cmd, str):
            if env_cmd in _PR_BUNDLE_CONTEXT_SUBCOMMANDS:
                return "context_read"
            if env_cmd in _PR_BUNDLE_EDIT_SUBCOMMANDS:
                return "edit"
    return None


def _collect_change_scope_timestamps(
    events: Iterable[Mapping[str, Any]],
) -> tuple[str | None, str | None, str | None]:
    """Return (context_read_at, edits_started_at, edits_completed_at).

    Walks the run-ledger event stream, classifies each event via
    :func:`_classify_event_phase`, and picks:

    * ``context_read_at`` = LATEST ts of any ``"context_read"`` event
      (the most recent read wins - matches the W210 semantic of "when
      did the agent last refresh its understanding").
    * ``edits_started_at`` = EARLIEST ts of any ``"edit"`` event (the
      first sign the agent reported on completed edits).
    * ``edits_completed_at`` = LATEST ts of any ``"edit"`` event.

    Any timestamp the stream doesn't surface returns ``None``. The
    caller treats ``None`` as insufficient data - it does NOT flip
    ``evidence_stale`` to ``True``.
    """
    context_read_at: str | None = None
    edits_started_at: str | None = None
    edits_completed_at: str | None = None

    for ev in events:
        if not isinstance(ev, Mapping):
            continue
        ts = ev.get("ts")
        if not isinstance(ts, str) or not ts:
            continue
        phase = _classify_event_phase(ev)
        if phase == "context_read":
            # Latest context-read wins.
            if context_read_at is None or ts > context_read_at:
                context_read_at = ts
        elif phase == "edit":
            # Earliest edit-event seeds the start.
            if edits_started_at is None or ts < edits_started_at:
                edits_started_at = ts
            # Latest edit-event seeds the end.
            if edits_completed_at is None or ts > edits_completed_at:
                edits_completed_at = ts

    return context_read_at, edits_started_at, edits_completed_at


def _compute_evidence_stale(
    context_read_at: str | None,
    edits_started_at: str | None,
) -> tuple[bool, tuple[str, ...]]:
    """Return ``(evidence_stale, stale_reasons)`` for the two timestamps.

    Stale iff BOTH timestamps are populated AND ``context_read_at`` is
    at-or-after ``edits_started_at`` (the agent re-read state AFTER edits
    began, so any verdict the read produced was on the modified tree).
    Missing timestamps return ``(False, ())`` - insufficient data is
    NOT a positive staleness signal (W210 honest-defaults discipline).

    The reason string names the precise comparison so a downstream
    reviewer can spot-check it without re-running the collector.
    """
    if not context_read_at or not edits_started_at:
        return False, ()
    if context_read_at >= edits_started_at:
        reason = f"context_read_at ({context_read_at}) at-or-after edits_started_at ({edits_started_at})"
        return True, (reason,)
    return False, ()


# ---------------------------------------------------------------------------
# W1253 - hash-drift detection for the three W210 config-hash fields.
# ---------------------------------------------------------------------------
#
# W1255-IMPL stamped the three canonical config hashes (rules_config_hash /
# constitution_hash / control_map_hash) into RunMeta.extra at run-start time
# via ``roam.evidence.config_hashes.stamp_all``. W1253 is the consumer
# side: at collection time, compare the packet-stamped hashes against the
# current on-disk hashes; when they differ, flip ``evidence_stale=True``
# and append a stale_reason that names the drifted field + the truncated
# hashes (first 12 hex chars are enough to fingerprint a sha256 while
# keeping the reason readable).
#
# Insufficient-data discipline (mirrors W1234 + W1255):
#   - missing packet hash (``""`` or absent) -> no drift verdict
#   - missing current hash (``""`` or absent) -> no drift verdict
#   Both honest defaults: the absence of one side is NOT a positive
#   drift signal. Hash equality on one side alone proves nothing.
#
# Combines with W1234 timestamp staleness: BOTH signals contribute to
# ``stale_reasons`` and either one flips ``evidence_stale=True``.

_CONFIG_HASH_FIELDS: tuple[str, ...] = (
    "rules_config_hash",
    "constitution_hash",
    "control_map_hash",
)


def _detect_hash_drift(
    packet_hashes: Mapping[str, str] | None,
    current_hashes: Mapping[str, str] | None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return ``(stale_reasons, current_hashes_dict)`` for config-hash drift.

    Walks the three W210 config-hash fields (``rules_config_hash`` /
    ``constitution_hash`` / ``control_map_hash``) and compares the
    packet-stamped hashes against the current on-disk hashes. A drift
    verdict requires BOTH sides populated with a non-empty string;
    missing data is NOT a positive signal (W1234 insufficient-data
    discipline).

    The returned ``current_hashes_dict`` is a fresh dict the caller can
    use to populate the ChangeEvidence packet's three hash fields. When
    both inputs are absent the dict is empty; when only one side is
    present we still pass it through so the packet records what we
    observed.

    The reason string names the field plus the first 12 hex chars of
    each side - enough to fingerprint a sha256 while keeping the
    message readable for a human reviewer.
    """
    reasons: list[str] = []
    packet = dict(packet_hashes) if packet_hashes else {}
    current = dict(current_hashes) if current_hashes else {}
    for field_name in _CONFIG_HASH_FIELDS:
        packet_h = packet.get(field_name, "")
        current_h = current.get(field_name, "")
        if not packet_h or not current_h:
            # Insufficient data on at least one side - no drift verdict.
            continue
        if packet_h != current_h:
            reasons.append(f"{field_name} mismatch (packet={packet_h[:12]}..., current={current_h[:12]}...)")
    return tuple(reasons), current


# ---------------------------------------------------------------------------
# Helpers - W190 agentic-assurance refs (actor / authority / environment)
# ---------------------------------------------------------------------------
#
# These three helpers materialise the W182 ref dataclasses
# (``ActorRef`` / ``AuthorityRef`` / ``EnvironmentRef``) from whatever
# producer surface is available.
#
# W189 / W190 race-condition note: at the time W190 was wired in,
# ``cmd_pr_bundle.py`` did NOT yet emit a dedicated ``actor`` block
# (search for "actor" returned no hits). When W189 lands, the
# pr-bundle envelope is expected to carry ``actor.{agent_id,
# human_actor, mcp_client_id, tool_id, ci_runner_id}``. Until then,
# the legacy probe paths (top-level ``agent_id`` / ``human_actor`` /
# any pre-existing ``actor`` dict the test fixture mirrors) provide
# the fallback. The shape we read is the union of both - the W189
# shape is a strict superset of what was already legal.
#
# Discovery ordering for the dedup contract:
#
#   1. pr-bundle ``actor`` block (richest, populated by W189)
#   2. pr-bundle top-level legacy ``agent_id`` / ``human_actor`` fields
#   3. run-ledger event ``agent`` strings
#   4. caller-arg ``agent_id`` (kwarg passed to ``collect_change_evidence``)
#
# Dedup rule: (actor_kind, actor_id) pair. First sighting wins so
# bundle-derived refs (which usually carry the richer ``extra`` payload)
# beat run-event refs which carry only the id string.


def _build_actor_refs(
    pr_bundle_envelope: Mapping[str, Any] | None,
    run_events: Iterable[Mapping[str, Any]],
    caller_agent_id: str | None,
    corroborated_tool_ids: frozenset[str] = frozenset(),
    corroborated_actor_ids: frozenset[str] = frozenset(),
) -> tuple[ActorRef, ...]:
    """Materialize ActorRef rows from all known sources, deduped by (kind, id).

    Sources in order of trust (first sighting wins on dedup):

    1. ``pr_bundle_envelope["actor"]`` - the W189 producer block.
       Maps ``agent_id`` / ``human_actor`` / ``mcp_client_id`` /
       ``tool_id`` / ``ci_runner_id`` to the corresponding ``actor_kind``.
    2. ``pr_bundle_envelope["agent_id"]`` / ``["human_actor"]`` -
       legacy top-level fields kept for pre-W189 envelopes.
    3. ``run_events[*]["agent"]`` - run-ledger event author strings.
       Each unique value becomes one ``ActorRef(actor_kind="agent", ...)``.
    4. ``caller_agent_id`` kwarg - explicit caller intent (CLAUDE.md
       LAW 11). Added last only if not already covered.

    W249 layer-2 scrub: every string sourced from the pr-bundle envelope
    is routed through ``_redact_secrets_in_string`` before it lands on
    an ``ActorRef``. That keeps tokens pasted into ``human_actor`` /
    ``agent_id`` from surviving into ``ChangeEvidence.actor_refs`` even
    when the envelope arrived from a non-W240 producer.

    W290 provenance wiring: each ActorRef carries an
    ``extra["provenance"]`` label naming WHICH source produced this
    identity claim. The label is a :func:`provenance_label` string
    from the closed ``PROVENANCE_SOURCES`` enumeration. Mapping:

    * pr-bundle actor block, with a sibling ``provenance_<field>``
      sub-key (W290 producer wiring in
      :func:`roam.commands.actor_helpers.resolve_actor_block`): the
      sub-key's value wins (e.g. ``"cli_flag"`` /
      ``"env_var(ROAM_AGENT_ID)"`` / ``"git_config(user.email)"`` /
      ``"ci_env_var(GITHUB_ACTIONS_RUN_ID)"`` / ``"run_ledger"``).
    * pr-bundle actor block, no sibling provenance sub-key (pre-W290
      producer): ``"producer_envelope"``.
    * pr-bundle legacy top-level ``agent_id`` / ``human_actor``:
      ``"producer_envelope"``.
    * run-ledger events: ``"run_ledger"``.
    * Caller-arg ``caller_agent_id`` kwarg: ``"cli_flag"`` (LAW 11
      explicit caller intent maps cleanly to the cli-flag tier).
    * Anything else / unable to attribute: ``"unknown"`` (Pattern 2
      always-emit - the key is present rather than silently absent).
    """
    refs: list[ActorRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, actor_id: Any, provenance: str) -> None:
        if not isinstance(actor_id, str) or not actor_id:
            return
        # W249 - scrub at the ActorRef boundary too (defense-in-depth
        # against producer paths that bypass the step-1 scrub above).
        scrubbed, _ = _redact_secrets_in_string(actor_id)
        if not scrubbed:
            return
        key = (kind, scrubbed)
        if key in seen:
            return
        seen.add(key)
        refs.append(
            ActorRef(
                actor_kind=kind,
                actor_id=scrubbed,
                extra={"provenance": provenance},
            )
        )

    # Source 1 + 2: pr-bundle actor block + legacy top-level fields.
    if isinstance(pr_bundle_envelope, Mapping):
        actor_block = pr_bundle_envelope.get("actor")
        if isinstance(actor_block, Mapping):
            # W290 - per-field provenance sub-keys win when present
            # (the W290 producer wiring stamps them; pre-W290 envelopes
            # don't carry them and fall through to ``producer_envelope``).
            def _ab_prov(field: str) -> str:
                val = actor_block.get(f"provenance_{field}")
                if isinstance(val, str) and val:
                    return val
                return "producer_envelope"

            _add("agent", actor_block.get("agent_id"), _ab_prov("agent_id"))
            # ``agent`` is an older alias some test fixtures use.
            _add("agent", actor_block.get("agent"), _ab_prov("agent_id"))
            _add("human", actor_block.get("human_actor"), _ab_prov("human_actor"))
            _add("human", actor_block.get("human"), _ab_prov("human_actor"))
            _add("human", actor_block.get("user"), _ab_prov("human_actor"))
            _add(
                "mcp_client",
                actor_block.get("mcp_client_id"),
                _ab_prov("mcp_client_id"),
            )
            _add("tool", actor_block.get("tool_id"), _ab_prov("tool_id"))
            _add(
                "ci_runner",
                actor_block.get("ci_runner_id"),
                _ab_prov("ci_runner_id"),
            )
        # Legacy top-level fallback - some pre-W189 envelopes stamp
        # agent_id / human_actor directly at the root. No provenance
        # sub-key path here - the envelope itself is the only signal.
        _add("agent", pr_bundle_envelope.get("agent_id"), "producer_envelope")
        _add("human", pr_bundle_envelope.get("human_actor"), "producer_envelope")

    # Source 3: run-ledger events. Each event's ``agent`` field is a
    # free-form string that names whoever wrote the event.
    for ev in run_events:
        if not isinstance(ev, Mapping):
            continue
        _add("agent", ev.get("agent"), "run_ledger")

    # Source 4: caller-arg explicit override. Same dedup key. The
    # collector's kwarg is the cli-flag-equivalent tier (LAW 11 -
    # explicit caller intent beats inference).
    _add("agent", caller_agent_id, "cli_flag")

    # W278 - classify each ref's trust_tier from real corroborating
    # signals (CI env + git email + run-ledger). Pure-function call;
    # no env reads here - the helpers read once and pass values in.
    # Pre-classification refs have ``trust_tier="unknown"`` per the
    # dataclass default; this pass replaces them with tier-classified
    # copies so the spoofing signal lands on the packet.
    ci_env_detected = _detect_ci_env_id() is not None
    ci_actor_id = _detect_ci_actor_id() if ci_env_detected else None
    git_email = _read_git_user_email()
    run_ledger_actor = _read_run_ledger_actor()

    classified: list[ActorRef] = []
    for ref in refs:
        tier = classify_actor_trust_tier(
            actor_id=ref.actor_id,
            actor_kind=ref.actor_kind,
            ci_env_detected=ci_env_detected,
            ci_actor_id=ci_actor_id,
            git_email=git_email,
            run_ledger_actor=run_ledger_actor,
            corroborated_tool_ids=corroborated_tool_ids,
            corroborated_actor_ids=corroborated_actor_ids,
        )
        # ``ActorRef`` is frozen; ``dataclasses.replace`` returns a
        # fresh instance with the new tier (re-runs ``__post_init__``
        # which validates the tier against ACTOR_TRUST_TIERS).
        classified.append(dataclasses.replace(ref, trust_tier=tier))

    return tuple(classified)


# ---------------------------------------------------------------------------
# W292 - Authority provenance: channel labels + precedence resolver
# ---------------------------------------------------------------------------
#
# W282 introduced ``PROVENANCE_SOURCES`` and ``provenance_label()``. W290
# wired actor_refs to stamp ``extra["provenance"]``; W292 extends the same
# pattern to authority_refs - but with DIFFERENT semantics:
#
# * ``AuthorityRef.source`` is the W211 ``AUTHORITY_SOURCES`` literal naming
#   the AUTHORITY-KIND CATEGORY (mode / permit / rule_config / ci_policy /
#   human_approval / inferred_fallback). It answers "what KIND of authority
#   was this?".
# * ``extra["provenance"]`` is the W282 ``PROVENANCE_SOURCES`` label naming
#   the DATA CHANNEL that produced THIS specific value (run_ledger /
#   producer_envelope / inferred / unknown / ...). It answers "where did
#   this specific value COME FROM?".
#
# Both fields are independently load-bearing. A mode authority might have
# ``source="mode"`` (category) AND ``provenance="run_ledger"`` (channel:
# the mode change was recorded in an HMAC-verified run-ledger event).
#
# Precedence (highest -> lowest, used by ``_resolve_authority_provenance``):
#
#   1. ``run_ledger``           - HMAC-verified ledger event vouches for this
#   2. ``audit_trail``          - tamper-evident audit-trail vouches for this
#   3. ``mcp_receipt``          - parseable MCP receipt vouches for this
#   4. ``producer_envelope(permit)`` / ``(mode)`` / ``(rule)`` / ``(lease)``
#      / ``(approval)`` - explicit producer-envelope row
#   5. ``producer_envelope``    - bare producer envelope (no detail)
#   6. ``inferred``             - heuristically synthesized
#   7. ``unknown``              - default fallback, no signal

# Authority-related fields a run-ledger event may carry that name a
# specific authority observation. Closed list kept small and explicit
# so corroboration stays deterministic and auditable.
_RUN_LEDGER_AUTHORITY_FIELDS: tuple[tuple[str, str], ...] = (
    # (event-field name, authority_kind it corroborates)
    ("mode", "mode"),
    ("active_mode", "mode"),
    ("mode_to", "mode"),
    ("mode_from", "mode"),
    ("permit_id", "permit"),
    ("lease_id", "lease"),
    ("approval_id", "approval"),
    ("rule_id", "policy_rule"),
)


def _collect_corroborated_authorities_from_runs(
    repo_root: Path,
    warnings: list[str],
) -> frozenset[tuple[str, str]]:
    """W292: harvest (authority_kind, authority_id) pairs from HMAC-verified runs.

    Walks every run under ``.roam/runs/``, loads the per-repo HMAC key,
    and verifies each run's event chain via
    :func:`roam.runs.signing.verify_chain`. ONLY events from runs whose
    chain state is ``"ok"`` contribute - tampered / unsigned chains drop
    out completely. This mirrors :func:`_collect_corroborated_ids_from_runs`
    (W285) but harvests authority observations instead of actor ids.

    Two corroboration paths per verified run:

    1. The run-meta ``mode`` field (when the run started under an active
       mode declaration) corroborates ``("mode", <mode>)``.
    2. Each verified event's authority-shaped fields (see
       :data:`_RUN_LEDGER_AUTHORITY_FIELDS`) corroborate the matching
       authority observation. This is how mode changes / permit
       issuance / lease acquisition / approval recording get HMAC-
       backed when an agent records them through ``roam runs log_event``.

    The frozenset element shape is ``(authority_kind, authority_id)``.
    Producers that surface an authority value matching one of these
    pairs earn ``run_ledger`` provenance regardless of which envelope
    channel they came from (W292 precedence).

    Returns ``frozenset()`` when ``.roam/runs/`` doesn't exist, the
    ledger key is unavailable, or no run verifies cleanly. Errors fold
    into ``warnings``.
    """
    pairs: set[tuple[str, str]] = set()
    try:
        from roam.runs.ledger import (
            list_runs,
            read_run_events,
        )
        from roam.runs.signing import ensure_ledger_key, verify_chain
    except Exception as exc:  # noqa: BLE001 - runs module optional
        warnings.append(f"authority-corroboration: runs module unavailable ({exc})")
        return frozenset()

    try:
        runs_root_path = Path(repo_root) / ".roam" / "runs"
    except (OSError, ValueError):
        return frozenset()
    if not runs_root_path.is_dir():
        return frozenset()

    try:
        key = ensure_ledger_key(Path(repo_root))
    except Exception as exc:  # noqa: BLE001 - key missing / corrupt
        warnings.append(f"authority-corroboration: ledger key unavailable ({exc})")
        return frozenset()

    try:
        run_metas = list(list_runs(Path(repo_root)))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"authority-corroboration: list_runs failed ({exc})")
        return frozenset()

    for meta in run_metas:
        try:
            events = list(read_run_events(Path(repo_root), meta.run_id))
        except OSError:
            # W746: narrowed from bare Exception. read_run_events is a
            # generator over an on-disk JSONL ledger; the only realistic
            # raise during iteration is filesystem I/O. JSONDecodeError
            # is already swallowed inside the generator. Programmer-class
            # errors (NameError / AttributeError) now propagate per W531.
            continue
        if not events:
            # Empty ledger - no events to corroborate.
            continue
        try:
            result = verify_chain(events, key)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"authority-corroboration: verify_chain crashed on {meta.run_id} ({exc})")
            continue
        if result.get("state") != "ok":
            # tampered / unsigned / unknown_run -> no corroboration.
            continue

        # The whole run verified - harvest authority observations from
        # the run-meta + every event.
        meta_mode = getattr(meta, "mode", None)
        if isinstance(meta_mode, str) and meta_mode.strip():
            pairs.add(("mode", meta_mode.strip()))
        for ev in events:
            if not isinstance(ev, Mapping):
                continue
            for field_name, kind in _RUN_LEDGER_AUTHORITY_FIELDS:
                v = ev.get(field_name)
                if isinstance(v, str) and v.strip():
                    pairs.add((kind, v.strip()))

    return frozenset(pairs)


def _resolve_authority_provenance(
    *,
    authority_kind: str,
    authority_id: str | None,
    envelope_source: str | None,
    corroborated_in_run_ledger: bool,
) -> str:
    """Return a deterministic provenance label for one AuthorityRef.

    Args:
        authority_kind: one of ``AUTHORITY_KINDS`` (``mode`` / ``permit``
            / ``lease`` / ``policy_rule`` / ``approval`` / ``token_scope``).
            Used to pick the ``detail`` suffix on
            ``producer_envelope`` labels so consumers can see which
            envelope channel produced the value.
        authority_id: the AuthorityRef's stable id. Used only as part of
            the corroboration-pair lookup at the call site; passed
            through here for symmetry with the W285-style helper
            signature.
        envelope_source: which channel surfaced the authority on the
            producer envelope. One of ``"mode"`` / ``"permit"`` /
            ``"lease"`` / ``"rule"`` / ``"approval"`` / ``None``.
            ``None`` indicates the value came from a non-envelope
            channel (e.g. caller kwarg) and the resolver falls through
            to ``inferred`` / ``unknown``.
        corroborated_in_run_ledger: whether the (authority_kind,
            authority_id) pair appears in an HMAC-verified run-ledger
            event. Wins over every other channel.

    Returns:
        A :func:`provenance_label`-shaped string drawn from the closed
        ``PROVENANCE_SOURCES`` enumeration. Examples:
        ``"run_ledger"`` / ``"producer_envelope(permit)"`` /
        ``"producer_envelope(mode)"`` / ``"inferred"`` / ``"unknown"``.

    Determinism contract: same inputs -> same output, every call. The
    branch order codes the precedence table explicitly so the
    resolution cannot drift with dict iteration order.
    """
    # del authority_id  # accepted for symmetry; not used in body
    _ = authority_id

    # Tier 1 - run_ledger. HMAC-verified evidence beats every other
    # channel. Whether the envelope ALSO surfaced this authority is
    # irrelevant once a verified ledger entry corroborates it.
    if corroborated_in_run_ledger:
        return provenance_label("run_ledger")

    # Tier 2 / 3 - audit_trail / mcp_receipt. Reserved for future
    # producer wiring. The collector today doesn't surface either as
    # an authority-channel signal (audit-trail envelopes produce
    # findings + policy_decisions; MCP receipts produce actor_refs +
    # artifacts). Keep the branches here as documentation - callers
    # that pass ``envelope_source="audit_trail"`` or ``"mcp_receipt"``
    # in a future wave will land on the correct provenance.
    if envelope_source == "audit_trail":
        return provenance_label("audit_trail")
    if envelope_source == "mcp_receipt":
        return provenance_label("mcp_receipt")

    # Tier 4 - producer_envelope(<detail>). One detail per envelope
    # channel so consumers can tell mode vs permit vs lease apart.
    if envelope_source == "permit":
        return provenance_label("producer_envelope", detail="permit")
    if envelope_source == "mode":
        return provenance_label("producer_envelope", detail="mode")
    if envelope_source == "rule":
        return provenance_label("producer_envelope", detail="rule")
    if envelope_source == "lease":
        return provenance_label("producer_envelope", detail="lease")
    if envelope_source == "approval":
        return provenance_label("producer_envelope", detail="approval")

    # Tier 5 - bare producer_envelope (channel didn't carry a detail).
    if envelope_source == "producer_envelope":
        return provenance_label("producer_envelope")

    # Tier 6 - inferred. Used when the collector synthesized the
    # authority claim WITHOUT a specific source channel (e.g. caller
    # kwarg-only mode with no envelope row).
    if envelope_source == "inferred":
        return provenance_label("inferred")

    # Tier 7 - unknown. Default fallback so every AuthorityRef carries
    # SOME provenance label (Pattern 2 always-emit).
    return provenance_label("unknown")


# ---------------------------------------------------------------------------
# W377-batch helpers - permit staleness / expiry markers
# ---------------------------------------------------------------------------

# Threshold (days) above which an issued-long-ago permit earns
# ``extra["issued_days_ago"] = N`` in ``_build_authority_refs``. Picked to
# be looser than the typical sprint window (so a routine permit issued at
# sprint start never trips the marker) but tight enough to catch year-old
# tokens that have been quietly re-used long past their intended scope.
_PERMIT_STALENESS_THRESHOLD_DAYS = 90


def _parse_iso_for_permits(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse for permit timestamps.

    Mirrors :func:`roam.permits.store._parse_iso` (accepts both ``Z`` and
    explicit-offset forms; treats naive timestamps as UTC) but returns
    ``None`` on failure instead of raising. Used by
    :func:`_build_authority_refs` to compute the W377 expired marker
    and the W378 staleness marker without coupling the collector to
    the permits substrate's exception surface.
    """
    if not isinstance(value, str) or not value:
        return None
    normalised = value
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalised)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_authority_refs(
    pr_bundle_envelope: Mapping[str, Any] | None,
    caller_mode: str | None,
    corroborated_authorities: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[AuthorityRef, ...]:
    """Materialize AuthorityRef rows from pr-bundle authority producers.

    Mapping (in declared order):

    * ``mode`` (top-level OR ``summary.active_mode`` OR
      ``mode_block.active_mode`` OR caller kwarg) ->
      ``AuthorityRef(authority_kind="mode", authority_id=<mode>)``.
      Always emitted when a mode string is available.
    * ``permits[]`` (each entry is a dict or string) ->
      ``AuthorityRef(authority_kind="permit", ...)``.
      W268 promoted ``permits[]`` from verdict-facade to a real
      pr-bundle field: ``cmd_pr_bundle._build_envelope`` now reads
      ``.roam/permits/*.json`` at emit time and stamps each row on
      the envelope. ``roam permit`` itself remains a verdict facade
      until ``--persist`` ships (W198), so the on-disk directory is
      usually empty today - the branch survives the empty case
      gracefully (Pattern 2 always-emit).
    * ``leases[]`` -> ``AuthorityRef(authority_kind="lease", ...)``.
      W268 promoted ``leases[]`` similarly: the envelope's
      ``leases[]`` array is populated from ``.roam/leases/*.json``
      via ``roam.leases.list_leases``.
    * ``rules_passed[]`` -> ``AuthorityRef(authority_kind="policy_rule", ...)``.
    * ``approvals[]`` -> ``AuthorityRef(authority_kind="approval", ...,
      granted_by=<approver>)``. These ALSO live on
      ``ChangeEvidence.approvals``; the authority-ref is an additional
      assurance-axis view of the same fact.

    W292 provenance wiring: each AuthorityRef carries an
    ``extra["provenance"]`` label naming WHICH channel produced the
    value. The label is a :func:`provenance_label` string from the
    closed ``PROVENANCE_SOURCES`` enumeration. Resolution uses the
    deterministic precedence in :func:`_resolve_authority_provenance`:
    ``run_ledger`` > ``audit_trail`` > ``mcp_receipt`` >
    ``producer_envelope(<detail>)`` > ``inferred`` > ``unknown``.

    Critically, W292 does NOT modify ``AuthorityRef.source``: that field
    carries the W211 ``AUTHORITY_SOURCES`` literal naming the AUTHORITY
    KIND category (mode / permit / rule_config / ci_policy /
    human_approval / inferred_fallback). The provenance label answers a
    DIFFERENT question - where did this specific value come from? - and
    lives on ``extra["provenance"]``. Both fields are independently
    load-bearing.

    Args:
        pr_bundle_envelope: the producer-emitted pr-bundle envelope,
            or ``None`` if no envelope was supplied.
        caller_mode: explicit mode override from the collector caller
            (CLAUDE.md LAW 11). Beats envelope ``mode`` when both are
            present.
        corroborated_authorities: W292 - frozenset of
            ``(authority_kind, authority_id)`` pairs harvested from
            HMAC-verified run-ledger events. Membership promotes the
            matching AuthorityRef's provenance to ``run_ledger``
            regardless of which envelope channel surfaced it.
    """
    refs: list[AuthorityRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(
        kind: str,
        authority_id: Any,
        *,
        granted_by: str | None = None,
        envelope_source: str | None,
        source: str = "inferred_fallback",
        extra_fields: Mapping[str, Any] | None = None,
    ) -> None:
        """Materialize one AuthorityRef.

        W294 separation of axes:

        * ``source`` (W211 AUTHORITY_SOURCES) answers the AUTHORITY KIND
          CATEGORY question - which closed-enum tier the producer learned
          about this authority through. The caller passes the literal that
          corresponds to ``kind``; default stays ``"inferred_fallback"``
          for paths we couldn't classify.
        * ``extra["provenance"]`` (W282 PROVENANCE_SOURCES) answers the
          DATA CHANNEL question - which surface produced the value.
          Computed here from ``envelope_source`` + run-ledger
          corroboration via :func:`_resolve_authority_provenance`.

        ``extra_fields`` lets the caller stamp additional structured
        metadata onto ``extra`` (e.g. ``{"permit_id": "perm_..."}``)
        without taking over the provenance slot.
        """
        if not isinstance(authority_id, str) or not authority_id:
            return
        key = (kind, authority_id)
        if key in seen:
            return
        seen.add(key)
        corroborated = (kind, authority_id) in corroborated_authorities
        provenance = _resolve_authority_provenance(
            authority_kind=kind,
            authority_id=authority_id,
            envelope_source=envelope_source,
            corroborated_in_run_ledger=corroborated,
        )
        merged_extra: dict[str, Any] = {"provenance": provenance}
        if extra_fields:
            for k, v in extra_fields.items():
                # ``provenance`` is owned by this helper - callers cannot
                # overwrite the channel answer via extra_fields.
                if k == "provenance":
                    continue
                merged_extra[k] = v
        refs.append(
            AuthorityRef(
                authority_kind=kind,
                authority_id=authority_id,
                granted_by=granted_by,
                source=source,
                extra=merged_extra,
            )
        )

    # Resolve the effective mode (caller kwarg already absorbed elsewhere
    # but we re-derive here so the helper is self-contained).
    #
    # W292 envelope_source attribution: when the value came from the
    # envelope (any of the three locations below), it earns
    # ``producer_envelope(mode)`` unless a verified run-ledger event
    # also recorded the mode (then ``run_ledger`` wins). When the
    # caller passed ``caller_mode`` directly with no envelope match,
    # we still tag it ``producer_envelope(mode)`` because the caller
    # is the surface that knew about the mode - the alternative
    # (``inferred``) would underclaim the signal.
    mode_value: str | None = caller_mode
    if mode_value is None and isinstance(pr_bundle_envelope, Mapping):
        mode_value = _coalesce(
            pr_bundle_envelope.get("mode"),
            _nested(pr_bundle_envelope, ("summary", "active_mode")),
            _nested(pr_bundle_envelope, ("mode_block", "active_mode")),
        )
    if mode_value is not None:
        # W294: source="mode" - mode AuthorityRef carries the W211 category
        # answer naming the active-mode declaration tier. Distinct from
        # extra["provenance"] which names the data channel.
        _add("mode", mode_value, envelope_source="mode", source="mode")

    if not isinstance(pr_bundle_envelope, Mapping):
        return tuple(refs)

    # Helper to coerce a list entry to its id string regardless of shape.
    def _entry_id(entry: Any, *id_keys: str) -> str | None:
        if isinstance(entry, str):
            return entry
        if isinstance(entry, Mapping):
            for key in id_keys:
                v = entry.get(key)
                if isinstance(v, str) and v:
                    return v
        return None

    permits = pr_bundle_envelope.get("permits")
    if isinstance(permits, list):
        now_for_permits = datetime.now(timezone.utc)
        for entry in permits:
            permit_id = _entry_id(entry, "permit_id", "id")
            # W294: stamp ``extra["permit_id"]`` ONLY when the entry
            # carried a real ``permit_id`` key (the W268 disk-read path
            # populates this). Synthetic envelope rows that only carry
            # ``id`` are facade-shaped and intentionally land on the
            # W198 facade detection in ``AuthorityRef.__post_init__``
            # (which auto-stamps ``extra["facade"] = True``).
            #
            # W377/W378/W381: when the row carries the full W198 permit
            # shape (permit_id + scope + issued_to + issued_at +
            # expires_at), project the sibling fields onto AuthorityRef.
            # ``extra`` so an auditor can see WHAT was authorised, to
            # WHOM, and WHEN -- without having to cross-reference the
            # raw envelope. We also stamp ``extra["expired"] = True``
            # when ``expires_at`` is already in the past (W377), and
            # ``extra["issued_days_ago"] = N`` when ``issued_at`` is
            # older than 90 days (W378). Both markers are advisory: the
            # AuthorityRef is still materialised so historical evidence
            # survives, but the markers let downstream consumers render
            # the row differently.
            extra_fields: dict[str, Any] | None = None
            if isinstance(entry, Mapping):
                local_extra: dict[str, Any] = {}
                raw_permit_id = entry.get("permit_id")
                if isinstance(raw_permit_id, str) and raw_permit_id:
                    local_extra["permit_id"] = raw_permit_id
                # W381: project sibling fields when they look usable.
                # We accept strings only (matches the W198 on-disk
                # schema); non-string types are intentionally dropped
                # so a junk envelope row cannot poison the AuthorityRef
                # extra.
                raw_scope = entry.get("scope")
                if isinstance(raw_scope, str) and raw_scope:
                    local_extra["scope"] = raw_scope
                raw_expires_at = entry.get("expires_at")
                if isinstance(raw_expires_at, str) and raw_expires_at:
                    local_extra["expires_at"] = raw_expires_at
                raw_issued_to = entry.get("issued_to")
                if isinstance(raw_issued_to, str) and raw_issued_to:
                    local_extra["issued_to"] = raw_issued_to
                # W377: expired marker. Best-effort parse: an unparseable
                # ``expires_at`` is treated as "no signal" (matches the
                # discipline in ``PermitRecord.is_expired_at``).
                if isinstance(raw_expires_at, str) and raw_expires_at:
                    exp_dt = _parse_iso_for_permits(raw_expires_at)
                    if exp_dt is not None and now_for_permits >= exp_dt:
                        local_extra["expired"] = True
                # W378: staleness marker. Stamp ``issued_days_ago`` when
                # the permit was issued more than 90 days before the
                # collector ran. The threshold is intentionally generous
                # so a typical sprint-bounded permit never trips it.
                raw_issued_at = entry.get("issued_at")
                if isinstance(raw_issued_at, str) and raw_issued_at:
                    iss_dt = _parse_iso_for_permits(raw_issued_at)
                    if iss_dt is not None:
                        delta = now_for_permits - iss_dt
                        days = int(delta.total_seconds() // 86400)
                        if days >= _PERMIT_STALENESS_THRESHOLD_DAYS:
                            local_extra["issued_days_ago"] = days
                if local_extra:
                    extra_fields = local_extra
            _add(
                "permit",
                permit_id,
                envelope_source="permit",
                source="permit",
                extra_fields=extra_fields,
            )

    leases = pr_bundle_envelope.get("leases")
    if isinstance(leases, list):
        for entry in leases:
            # W294: lease AuthorityRef intentionally keeps the default
            # ``source="inferred_fallback"``. AUTHORITY_SOURCES (W211)
            # has no ``lease`` entry; adding one would be a deliberate
            # vocabulary decision for a future wave. The asymmetry is
            # intentional: a lease ref has
            # ``source="inferred_fallback"`` (the closed enum can't
            # name the category) AND
            # ``extra["provenance"]="producer_envelope(lease)"`` (the
            # channel answer remains precise). Both fields stay
            # independently load-bearing.
            _add(
                "lease",
                _entry_id(entry, "lease_id", "id"),
                envelope_source="lease",
            )

    rules_passed = pr_bundle_envelope.get("rules_passed")
    if isinstance(rules_passed, list):
        for entry in rules_passed:
            # W294: source="rule_config" - policy rules surface through
            # the rules.yml configuration tier per AUTHORITY_SOURCES.
            _add(
                "policy_rule",
                _entry_id(entry, "rule_id", "id"),
                envelope_source="rule",
                source="rule_config",
            )

    approvals = pr_bundle_envelope.get("approvals")
    if isinstance(approvals, list):
        for entry in approvals:
            approval_id = _entry_id(entry, "approval_id", "id")
            granted_by = None
            if isinstance(entry, Mapping):
                approver = entry.get("approver") or entry.get("granted_by")
                if isinstance(approver, str) and approver:
                    granted_by = approver
            # W294: source="human_approval" - approvals always come
            # from a recorded human-approval event.
            _add(
                "approval",
                approval_id,
                granted_by=granted_by,
                envelope_source="approval",
                source="human_approval",
            )

    return tuple(refs)


# Closed-list precedence of CI-detection environment variables. Order
# matters - the first variable whose presence indicates a CI runtime
# selects the corresponding ``env_id`` value. The list mirrors the
# providers Roam already calls out elsewhere (see ``roam doctor`` and
# the runtime hotspots ingester). Stays small and explicit; broaden
# only when a real customer needs a new provider.
_CI_PROVIDER_ENV_VARS: tuple[tuple[str, str], ...] = (
    # (probe var that signals "in this provider", value-var preferred for env_id)
    ("GITHUB_ACTIONS", "GITHUB_RUN_ID"),
    ("GITLAB_CI", "CI_JOB_ID"),
    ("BUILDKITE", "BUILDKITE_BUILD_ID"),
    ("CIRCLECI", "CIRCLE_BUILD_NUM"),
    ("JENKINS_URL", "BUILD_TAG"),
    ("TF_BUILD", "BUILD_BUILDID"),  # Azure Pipelines
    # Generic CI=true fallback - lowest specificity; matches the most
    # providers but yields the least-specific env_id.
    ("CI", "CI_JOB_ID"),
)


def _detect_ci_env_id(env: Mapping[str, str] | None = None) -> str | None:
    """Return the most-specific CI job identifier from env vars.

    Precedence: provider-specific signals (GitHub Actions, GitLab CI,
    Buildkite, CircleCI, Jenkins, Azure Pipelines) probed in declared
    order, with the generic ``CI=true`` fallback last. The returned id
    is the value of the first non-empty value-var; if no value-var is
    set the literal probe-var name is returned so the EnvironmentRef
    still has a non-empty id (env_id must be a non-empty string).
    """
    e = env if env is not None else os.environ
    for probe_var, value_var in _CI_PROVIDER_ENV_VARS:
        probe = e.get(probe_var)
        if not probe:
            continue
        # "true" / "1" / any truthy string activates this provider.
        if probe.lower() in {"false", "0", ""}:
            continue
        value = e.get(value_var)
        if isinstance(value, str) and value:
            return value
        # Fallback to a synthetic id so env_id remains non-empty. The
        # provider name itself is the safest non-empty stand-in.
        return f"{probe_var.lower()}:unknown"
    return None


# Closed-list mapping from CI-provider probe var -> actor-identity var
# the provider exposes for "who triggered this run". Mirrors
# ``_CI_PROVIDER_ENV_VARS`` order; broaden only when a real customer
# needs a new provider. W278 wiring for spoofing-tier classification.
_CI_PROVIDER_ACTOR_VARS: tuple[tuple[str, str], ...] = (
    ("GITHUB_ACTIONS", "GITHUB_ACTOR"),
    ("GITLAB_CI", "GITLAB_USER_LOGIN"),
    ("BUILDKITE", "BUILDKITE_BUILD_AUTHOR_EMAIL"),
    ("CIRCLECI", "CIRCLE_USERNAME"),
    ("JENKINS_URL", "BUILD_USER_ID"),
    ("TF_BUILD", "BUILD_REQUESTEDFOREMAIL"),  # Azure Pipelines
)


def _detect_ci_actor_id(env: Mapping[str, str] | None = None) -> str | None:
    """Return the CI provider's view of "who triggered this run".

    Walks the same provider list as ``_detect_ci_env_id`` (so the two
    helpers agree on which provider is active) and returns the value
    of the provider-specific actor variable when present and
    non-empty. ``None`` when no CI is active OR the active provider
    doesn't expose an actor identity. Used by
    :func:`_build_actor_refs` to classify ``ActorRef.trust_tier`` per
    W278.
    """
    e = env if env is not None else os.environ
    for probe_var, actor_var in _CI_PROVIDER_ACTOR_VARS:
        probe = e.get(probe_var)
        if not probe:
            continue
        if probe.lower() in {"false", "0", ""}:
            continue
        value = e.get(actor_var)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Provider is active but exposes no actor identity. Stop here
        # rather than fall through to a different provider's actor var.
        return None
    return None


def _read_git_user_email() -> str | None:
    """Return ``git config user.email`` for the workspace, best-effort.

    Wraps :func:`roam.commands.git_helpers.git_actor` and treats the
    ``"<unknown>"`` sentinel as absence. Returns ``None`` when the
    git binary is missing or no email is configured. Used only as a
    corroborating signal for trust-tier classification (W278) - never
    a primary identity source.
    """
    try:
        from roam.commands.git_helpers import git_actor

        value = git_actor()
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "<unknown>":
        return None
    return value


def _read_run_ledger_actor(repo_root: Path | None = None) -> str | None:
    """Return the active run-ledger entry's agent string, best-effort.

    Wraps :func:`roam.runs.ledger.latest_in_progress_run`. ``None``
    when no run is open OR the runs module fails to import. Used only
    as a corroborating signal for trust-tier classification (W278).
    """
    if repo_root is None:
        try:
            repo_root = Path.cwd()
        except Exception:
            return None
    try:
        from roam.runs.ledger import latest_in_progress_run

        meta = latest_in_progress_run(repo_root)
    except Exception:
        return None
    if meta is None:
        return None
    agent = getattr(meta, "agent", None)
    if not isinstance(agent, str):
        return None
    agent = agent.strip()
    return agent or None


# W285 - run-ledger event fields that may name the tool whose
# invocation the event records. Kept as a small closed list so the
# corroboration check is deterministic and easy to audit.
#
# ``tool`` / ``tool_id`` / ``tool_name`` - explicit tool ids (W196
#   MCP-receipt mirror, future TOOL_USED events).
# ``action`` / ``envelope_command`` - the per-event verb / command
#   (e.g. ``"critique"``, ``"preflight"``, ``"init"``). These name
#   the roam subcommand the agent ran during the run; matching here
#   is the W285 path for ``roam_init`` / ``roam_reindex``-style
#   pseudo-actors (the actor_id is the same string the run-ledger
#   event recorded under ``action``).
_RUN_LEDGER_TOOL_FIELDS: tuple[str, ...] = (
    "tool",
    "tool_id",
    "tool_name",
    "action",
    "envelope_command",
)

# W285 - run-ledger event fields that may name a corroborated actor
# identity (the agent / mcp_client / client_id that wrote the event).
_RUN_LEDGER_ACTOR_FIELDS: tuple[str, ...] = (
    "agent",
    "agent_id",
    "actor_ref_id",
    "client_id",
    "mcp_client_id",
)


def _collect_corroborated_ids_from_runs(
    repo_root: Path,
    warnings: list[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """W285: harvest tool/actor ids from HMAC-verified run-ledger events.

    Walks every run under ``.roam/runs/``, loads the per-repo HMAC key,
    and verifies each run's event chain via
    :func:`roam.runs.signing.verify_chain`. ONLY events from runs whose
    chain state is ``"ok"`` contribute to the returned sets. Runs that
    fail verification (``state == "tampered"``), are unsigned, or whose
    meta.json / events.jsonl can't be parsed contribute NOTHING - the
    W285 contract requires real HMAC corroboration, not best-effort.

    The two returned frozensets are disjoint by intent:

    * ``tool_ids`` - values harvested from ``tool`` / ``tool_id`` /
      ``tool_name`` / ``action`` / ``envelope_command`` fields on
      verified events. These promote tool pseudo-actors (e.g.
      ``roam_init``) to ``local_env``.
    * ``actor_ids`` - values harvested from ``agent`` / ``agent_id`` /
      ``actor_ref_id`` / ``client_id`` / ``mcp_client_id`` fields on
      verified events PLUS the run-meta ``agent`` value of each
      verified run. These promote agent / mcp_client refs.

    The "verified only" gate is enforced ledger-wide, not event-wide:
    if ANY event in a run fails HMAC verification, the WHOLE run is
    excluded from corroboration. That's the same trust model as
    :func:`roam.runs.signing.verify_chain` - a tampered chain breaks
    the security guarantee for every event past the first tamper.

    Returns ``(frozenset(), frozenset())`` if ``.roam/runs/`` doesn't
    exist, the ledger key is unavailable, or no run verifies cleanly.
    Errors are absorbed into the ``warnings`` list rather than raised.
    """
    tool_ids: set[str] = set()
    actor_ids: set[str] = set()
    try:
        from roam.runs.ledger import (
            list_runs,
            read_run_events,
        )
        from roam.runs.signing import ensure_ledger_key, verify_chain
    except Exception as exc:  # noqa: BLE001 - runs module optional
        warnings.append(f"corroboration: runs module unavailable ({exc})")
        return frozenset(), frozenset()

    try:
        runs_root = Path(repo_root) / ".roam" / "runs"
    except (OSError, ValueError):
        return frozenset(), frozenset()
    if not runs_root.is_dir():
        return frozenset(), frozenset()

    try:
        key = ensure_ledger_key(Path(repo_root))
    except Exception as exc:  # noqa: BLE001 - key missing / corrupt
        warnings.append(f"corroboration: ledger key unavailable ({exc})")
        return frozenset(), frozenset()

    try:
        run_metas = list(list_runs(Path(repo_root)))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"corroboration: list_runs failed ({exc})")
        return frozenset(), frozenset()

    for meta in run_metas:
        try:
            events = list(read_run_events(Path(repo_root), meta.run_id))
        except OSError:
            # W746: narrowed from bare Exception (same rationale as the
            # authority-corroboration site above). Filesystem I/O is
            # the only realistic raise from the generator.
            continue
        if not events:
            # Empty ledger - no events to corroborate. Don't admit the
            # run-meta agent on its own; that would be a name-based
            # shortcut (W285 guardrail).
            continue
        try:
            result = verify_chain(events, key)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"corroboration: verify_chain crashed on {meta.run_id} ({exc})")
            continue
        if result.get("state") != "ok":
            # tampered / unsigned / unknown_run -> no corroboration.
            continue

        # The whole run verified - harvest tool + actor ids from every
        # event AND the run-meta agent string.
        if isinstance(meta.agent, str) and meta.agent.strip():
            actor_ids.add(meta.agent.strip())
        for ev in events:
            if not isinstance(ev, Mapping):
                continue
            for f in _RUN_LEDGER_TOOL_FIELDS:
                v = ev.get(f)
                if isinstance(v, str) and v.strip():
                    tool_ids.add(v.strip())
            for f in _RUN_LEDGER_ACTOR_FIELDS:
                v = ev.get(f)
                if isinstance(v, str) and v.strip():
                    actor_ids.add(v.strip())

    return frozenset(tool_ids), frozenset(actor_ids)


def _collect_corroborated_ids_from_mcp_receipts(
    receipts_dir: str | Path | None,
    warnings: list[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """W285: harvest tool/actor ids from parseable MCP receipts.

    Walks every ``*.json`` file under ``receipts_dir`` and constructs
    an :class:`McpDecisionReceipt` per file. ONLY receipts that load
    cleanly AND carry a non-empty ``tool_name`` (for the tool set) or
    a non-empty ``actor_ref_id`` / ``client_id`` (for the actor set)
    contribute. Malformed JSON, missing required fields, or
    construction failures are absorbed into ``warnings``.

    Validation discipline is the same as
    :func:`_read_mcp_receipts_dir` so a receipt that the W197 mirror
    rejects is also rejected here; receipts cannot pass corroboration
    while failing the artifact mirror.
    """
    tool_ids: set[str] = set()
    actor_ids: set[str] = set()
    if receipts_dir is None:
        return frozenset(), frozenset()
    try:
        dir_path = Path(receipts_dir)
    except (OSError, ValueError) as exc:
        warnings.append(f"corroboration: receipts_dir rejected ({exc})")
        return frozenset(), frozenset()
    if not dir_path.exists() or not dir_path.is_dir():
        return frozenset(), frozenset()

    try:
        files = sorted(dir_path.glob("*.json"))
    except OSError as exc:
        warnings.append(f"corroboration: receipts dir unreadable ({exc})")
        return frozenset(), frozenset()

    for file in files:
        try:
            body = file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, Mapping):
            continue
        tool_call = data.get("tool_call")
        client_id = data.get("client_id")
        tool_name = data.get("tool_name")
        if not (
            isinstance(tool_call, str)
            and tool_call
            and isinstance(client_id, str)
            and client_id
            and isinstance(tool_name, str)
            and tool_name
        ):
            continue
        try:
            receipt = McpDecisionReceipt(
                tool_call=tool_call,
                client_id=client_id,
                tool_name=tool_name,
                actor_ref_id=data.get("actor_ref_id"),
                declared_side_effects=tuple(data.get("declared_side_effects") or ()),
                required_mode=data.get("required_mode"),
                input_hash=data.get("input_hash"),
                policy_decision=data.get("policy_decision", "not_evaluated"),
                output_ref=data.get("output_ref"),
                output_hash=data.get("output_hash"),
                run_event_id=data.get("run_event_id"),
                redactions=tuple(data.get("redactions") or ()),
                extra=data.get("extra") or {},
            )
        except (ValueError, TypeError) as exc:
            # Honor the docstring promise: "Malformed JSON, missing
            # required fields, or construction failures are absorbed
            # into warnings." Pre-W907 the construction-failure branch
            # silently swallowed the exception (Pattern 1B), so an
            # unknown policy_decision literal or a redaction-reason
            # drift would drop the receipt with no audit signal.
            warnings.append(f"corroboration: rejected receipt {file.name!r} ({type(exc).__name__}: {exc})")
            continue
        # tool_name + client_id are required (validated above); both
        # are non-empty strings here.
        tool_ids.add(receipt.tool_name.strip())
        if isinstance(receipt.client_id, str) and receipt.client_id.strip():
            actor_ids.add(receipt.client_id.strip())
        if isinstance(receipt.actor_ref_id, str) and receipt.actor_ref_id.strip():
            actor_ids.add(receipt.actor_ref_id.strip())

    return frozenset(tool_ids), frozenset(actor_ids)


def _collect_corroborated_ids(
    mcp_receipts_dir: str | Path | None,
    warnings: list[str],
    repo_root: Path | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """W285: union the two corroboration sources into the classifier inputs.

    Combines the HMAC-verified run-ledger walk (tool/agent ids from
    events that pass :func:`verify_chain`) with the parseable MCP
    receipts walk. Both sources are evidence-based - neither inspects
    actor_id for "looks internal" name patterns - so the resulting
    frozensets only ever name actors whose presence the producer
    actually witnessed.

    Returns ``(tool_ids, actor_ids)`` ready to thread through to
    :func:`classify_actor_trust_tier` via :func:`_build_actor_refs`.
    Failures in either source emit a ``warnings`` entry and the other
    source still contributes (best-effort union).
    """
    if repo_root is None:
        try:
            repo_root = Path.cwd()
        except Exception:
            return frozenset(), frozenset()

    run_tools, run_actors = _collect_corroborated_ids_from_runs(repo_root, warnings)
    mcp_tools, mcp_actors = _collect_corroborated_ids_from_mcp_receipts(mcp_receipts_dir, warnings)
    return (
        frozenset(run_tools | mcp_tools),
        frozenset(run_actors | mcp_actors),
    )


def _build_environment_refs(
    pr_bundle_envelope: Mapping[str, Any] | None,
    caller_repo_id: str | None,
    caller_git_range: str | None,
    caller_commit_sha: str | None,
) -> tuple[EnvironmentRef, ...]:
    """Materialize EnvironmentRef rows from caller args + env vars.

    Probe order (most specific first):

    1. CI job id (via ``_detect_ci_env_id`` over the closed list of
       provider env vars). If present, append ``env_kind="ci_job"``.
    2. Workspace - ``caller_repo_id`` OR ``pr_bundle_envelope.repo_id``.
       Append ``env_kind="workspace"`` if either is non-empty.
    3. Branch range - ``caller_git_range`` OR
       ``pr_bundle_envelope.git_range`` OR fall back to
       ``caller_commit_sha`` / envelope commit_sha. Append
       ``env_kind="branch_range"``.
    4. Local run fallback - emitted ONLY when no CI was detected.
       Uses ``socket.gethostname()`` (best-effort; falls back to the
       literal string ``"local"`` if hostname lookup fails).

    Multiple refs are expected on one packet - per the W182 dataclass
    docstring, a typical change carries workspace + branch_range + (in
    CI) a ci_job. The local-run fallback only fires in the absence of a
    CI signal so we don't double-count.
    """
    refs: list[EnvironmentRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, env_id: Any) -> None:
        if not isinstance(env_id, str) or not env_id:
            return
        key = (kind, env_id)
        if key in seen:
            return
        seen.add(key)
        refs.append(EnvironmentRef(env_kind=kind, env_id=env_id))

    # 1. CI job (most specific).
    ci_env_id = _detect_ci_env_id()
    if ci_env_id:
        _add("ci_job", ci_env_id)

    # 2. Workspace identifier.
    repo_id: str | None = caller_repo_id
    if not repo_id and isinstance(pr_bundle_envelope, Mapping):
        v = pr_bundle_envelope.get("repo_id")
        if isinstance(v, str) and v:
            repo_id = v
    _add("workspace", repo_id)

    # 3. Branch range / commit sha.
    git_range: str | None = caller_git_range
    if not git_range and isinstance(pr_bundle_envelope, Mapping):
        v = _coalesce(
            pr_bundle_envelope.get("git_range"),
            _nested(pr_bundle_envelope, ("bundle_meta", "git_range")),
        )
        if isinstance(v, str) and v:
            git_range = v
    commit_sha: str | None = caller_commit_sha
    if not commit_sha and isinstance(pr_bundle_envelope, Mapping):
        v = _coalesce(
            pr_bundle_envelope.get("commit_sha"),
            _nested(pr_bundle_envelope, ("bundle_meta", "git", "head_sha")),
            _nested(pr_bundle_envelope, ("bundle_meta", "commit_sha")),
        )
        if isinstance(v, str) and v:
            commit_sha = v
    branch_range_id = git_range or commit_sha
    _add("branch_range", branch_range_id)

    # 4. Local run fallback - only when no CI was detected AND there's
    # at least one other environment signal. The "at least one other
    # signal" gate is critical for the W182 omit-when-empty contract:
    # a bare ``collect_change_evidence()`` call with no inputs at all
    # MUST produce an empty environment_refs tuple so the resulting
    # packet hashes identically to a pre-W182 v0 packet. Only once a
    # caller supplies repo_id or git_range / commit_sha (i.e. proves
    # they are actually describing a real change) do we add the
    # hostname-tagged local_run anchor.
    if ci_env_id is None and (repo_id or branch_range_id):
        try:
            hostname = socket.gethostname() or "local"
        except OSError:
            hostname = "local"
        _add("local_run", hostname)

    return tuple(refs)


# ---------------------------------------------------------------------------
# Helpers - W199 envelope ingestion paths (rules / vuln-reach / test-impact /
# cga / mcp-receipts)
# ---------------------------------------------------------------------------
#
# Each helper below converts one new producer's envelope into the
# existing ChangeEvidence tuple fields (findings / policy_decisions /
# tests_required / tests_run / artifacts / actor_refs). W199 does NOT
# add new ChangeEvidence fields - all five ingestion paths feed
# established slots, which preserves the v0/v1 content-hash contract.

# Recognised top-level fields per rule-result row. Anything outside this
# set on an individual rule row triggers a warning so callers can spot
# producer drift (W192).
_RULE_RESULT_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "rule_id",
        "id",
        "passed",
        "decision",
        "severity",
        "violations",
        "reason",
        "evidence_ref",
        "clause",
        "partial_success",
        "category",
        "description",
    }
)


def _flatten_rules_envelope_to_policy_decisions(
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
) -> list[Mapping[str, Any]]:
    """Convert one ``roam rules`` envelope's results[] into policy_decisions.

    Each rule result row becomes one ``policy_decisions`` entry with
    ``rule_id`` / ``decision`` (pass | fail) / optional ``reason`` /
    ``evidence_ref`` / ``severity``. Unrecognised per-row keys emit a
    warning but the row is still appended (collector stays forgiving,
    matching the existing findings-row contract).
    """
    out: list[Mapping[str, Any]] = []
    rows = envelope.get("results")
    if not isinstance(rows, list):
        warnings.append(f"{source_label}: no 'results' array at top level - skipped")
        return out
    # W293 — every row flattened from a ``roam rules`` envelope carries
    # the ``producer_envelope(rule)`` provenance: the rules-validate
    # surface IS the producer envelope for these decisions.
    _rule_prov = provenance_label("producer_envelope", detail="rule")
    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            warnings.append(f"{source_label}[{idx}]: skipped non-dict rule row")
            continue
        rule_id = row.get("rule_id") or row.get("id") or row.get("name")
        if not rule_id:
            warnings.append(f"{source_label}[{idx}]: skipped rule row with no id/name")
            continue
        # Decision: prefer explicit ``decision`` field; fall back to the
        # ``passed`` boolean shape that roam rules emits today.
        if "decision" in row and isinstance(row["decision"], str):
            decision = row["decision"]
        else:
            passed = row.get("passed")
            if passed is True:
                decision = "pass"
            elif passed is False:
                decision = "fail"
            else:
                decision = "unknown"
        entry: dict[str, Any] = {
            "rule_id": str(rule_id),
            "decision": decision,
            "evidence_ref": f"rule:{rule_id}",
        }
        sev = row.get("severity")
        if isinstance(sev, str) and sev:
            entry["severity"] = sev
        reason = row.get("reason")
        if isinstance(reason, str) and reason:
            entry["reason"] = reason
        violations = row.get("violations")
        if isinstance(violations, list) and violations:
            entry["violation_count"] = len(violations)
        # Surface unknown per-row keys so callers spot drift.
        for key in row.keys():
            if key not in _RULE_RESULT_KNOWN_FIELDS:
                warnings.append(f"{source_label}[{idx}]: unrecognised field {key!r} on rule {rule_id!r}")
        entry["provenance"] = _rule_prov
        out.append(entry)
    return out


def _inline_raw_envelope_artifact(
    artifact_id: str,
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
    max_inline_bytes: int = 8 * 1024,
) -> EvidenceArtifact | None:
    """Build a ``raw_envelope`` artifact, truncating oversize payloads.

    Returns ``None`` and appends a warning when the envelope can't be
    serialised to canonical JSON at all. When the payload exceeds
    ``max_inline_bytes`` the body is truncated and a ``size_limit``
    redaction reason is recorded - the alternative (writing to a
    sidecar file) is out of scope for the in-memory collector path.

    W348: when truncation fires, the resulting body is
    ``max_inline_bytes + len("...[truncated]")`` bytes, deliberately
    a few bytes over the W288-followup ``INLINE_CONTENT_SOFT_LIMIT_BYTES``
    advisory ceiling. We've already mitigated via the ``size_limit``
    redaction stamp above, so suppress the advisory warning at this
    helper site (the warning targets producers who haven't truncated;
    we have).
    """
    try:
        body = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        warnings.append(f"{source_label}: could not serialise raw envelope ({exc})")
        return None
    redactions: tuple[str, ...] = ()
    if len(body.encode("utf-8")) > max_inline_bytes:
        body = body[:max_inline_bytes] + "...[truncated]"
        redactions = ("size_limit",)
    try:
        # W348: suppress the W288-followup advisory warning ONLY when we
        # ourselves did the truncation + stamped ``size_limit``. The
        # warning is targeted at producers who haven't yet adopted
        # path+content_hash for large blobs; the in-memory collector
        # path explicitly opted for "inline + truncate + stamp" instead
        # (see docstring above), so the advisory is already addressed.
        import warnings as _stdlib_warnings  # local import to avoid name clash with the list param

        with _stdlib_warnings.catch_warnings():
            _stdlib_warnings.filterwarnings(
                "ignore",
                message=r"EvidenceArtifact\.content_inline exceeds INLINE_CONTENT_SOFT_LIMIT_BYTES.*",
                category=UserWarning,
            )
            return EvidenceArtifact(
                artifact_id=artifact_id,
                kind="raw_envelope",
                content_inline=body,
                redactions=redactions,
            )
    except ValueError as exc:
        warnings.append(f"{source_label}: rejected raw_envelope artifact ({exc})")
        return None


def _flatten_vuln_reach_envelope(
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
    idx: int,
) -> tuple[list[Mapping[str, Any]], EvidenceArtifact | None]:
    """Convert one vuln-reach envelope into (findings_rows, raw_artifact).

    ``cmd_vuln_reach.py`` emits ``vulnerabilities: [{cve, package,
    severity, reachable, path, hops, blast_radius}, ...]``. Each row
    becomes a finding with ``source_detector="vuln-reach"``. The full
    envelope is preserved as a ``raw_envelope`` artifact so consumers
    that need the unflattened path / hop data can recover it.
    """
    rows: list[Mapping[str, Any]] = []
    raw = envelope.get("vulnerabilities")
    if raw is None:
        # Some shapes use ``reachable_vulns`` per the prompt; tolerate both.
        raw = envelope.get("reachable_vulns")
    if isinstance(raw, list):
        for row_idx, row in enumerate(raw):
            if not isinstance(row, Mapping):
                warnings.append(f"{source_label}: skipped non-dict vuln row [{row_idx}]")
                continue
            cve = row.get("cve") or row.get("cve_id") or "?"
            pkg = row.get("package") or row.get("package_name") or "?"
            severity = row.get("severity")
            finding: dict[str, Any] = {
                "finding_id_str": f"vuln-reach:{cve}:{pkg}",
                "source_detector": "vuln-reach",
                "subject_kind": "package",
                "claim": (f"{cve} reachable in {pkg}" if row.get("reachable") else f"{cve} not reachable in {pkg}"),
                "cve": cve,
                "package": pkg,
                "reachable": bool(row.get("reachable")),
                "hops": row.get("hops"),
                "blast_radius": row.get("blast_radius"),
                "path": row.get("path"),
            }
            if isinstance(severity, str) and severity:
                finding["severity"] = severity
            rows.append(finding)
    else:
        warnings.append(f"{source_label}: no 'vulnerabilities' (or 'reachable_vulns') array - flattening skipped")
    # W241 (Leak B / W236c): apply the closed-allowlist schema BEFORE
    # inlining. The whole vuln-reach envelope used to ride through
    # verbatim; any free-form field on a vulnerability row
    # (``description`` / ``message`` / ``snippet``) leaked through to
    # ``content_inline``. Now only ``_VULN_REACH_SAFE_KEYS`` /
    # ``_VULN_ROW_SAFE_KEYS`` / ``_VULN_REACH_SUMMARY_SAFE_KEYS``
    # survive the inline.
    safe_envelope = _safe_vuln_reach_envelope(envelope)
    artifact = _inline_raw_envelope_artifact(
        artifact_id=f"vuln-reach:envelope:{idx}",
        envelope=safe_envelope,
        warnings=warnings,
        source_label=source_label,
    )
    if artifact is not None:
        # Stamp ``schema_strict`` so consumers can tell the artifact's
        # body is the redacted-schema form (not the raw envelope).
        # When the artifact also carries ``size_limit`` from truncation
        # we keep both reasons - they're orthogonal signals.
        merged = list(artifact.redactions)
        if "schema_strict" not in merged:
            merged.append("schema_strict")
        # W348: mirror the upstream helper's suppression. The artifact
        # was just produced by ``_inline_raw_envelope_artifact`` which
        # may have set ``content_inline`` to a deliberately-truncated
        # body (max_inline_bytes + len("...[truncated]") bytes). The
        # advisory W288-followup warning is already addressed via the
        # ``size_limit`` redaction stamp; rewrapping here must not
        # re-fire the warning.
        import warnings as _stdlib_warnings  # noqa: PLC0415 - local alias avoids name clash with the list param

        with _stdlib_warnings.catch_warnings():
            _stdlib_warnings.filterwarnings(
                "ignore",
                message=r"EvidenceArtifact\.content_inline exceeds INLINE_CONTENT_SOFT_LIMIT_BYTES.*",
                category=UserWarning,
            )
            artifact = EvidenceArtifact(
                artifact_id=artifact.artifact_id,
                kind=artifact.kind,
                path=artifact.path,
                content_hash=artifact.content_hash,
                content_inline=artifact.content_inline,
                redactions=tuple(merged),
                extra=artifact.extra,
            )
    return rows, artifact


def _flatten_test_impact_envelope(
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
    idx: int,
) -> tuple[list[str], list[Mapping[str, Any]], EvidenceArtifact | None]:
    """Convert one test-impact envelope into (tests_required, tests_run, artifact).

    ``cmd_test_impact.py`` emits ``tests: [{file, reach_count}, ...]`` -
    the *required* test surface. There's no built-in tests_run field
    today, but if a future shape emits one (e.g. ``tests_run`` from a
    bound pytest harness) we surface it. Either way the full envelope
    is preserved as a ``raw_envelope`` artifact.
    """
    tests_required: list[str] = []
    raw_tests = envelope.get("tests") or envelope.get("affected_tests")
    if isinstance(raw_tests, list):
        for row in raw_tests:
            if isinstance(row, str) and row:
                tests_required.append(row)
            elif isinstance(row, Mapping):
                tf = row.get("file") or row.get("test_file") or row.get("path") or row.get("test_id")
                if isinstance(tf, str) and tf:
                    tests_required.append(tf)
    else:
        warnings.append(f"{source_label}: no 'tests' (or 'affected_tests') array - tests_required not flattened")

    tests_run: list[Mapping[str, Any]] = []
    raw_runs = envelope.get("tests_run") or envelope.get("matched_runs")
    if isinstance(raw_runs, list):
        for row in raw_runs:
            if isinstance(row, Mapping):
                tests_run.append(dict(row))

    artifact = _inline_raw_envelope_artifact(
        artifact_id=f"test-impact:envelope:{idx}",
        envelope=envelope,
        warnings=warnings,
        source_label=source_label,
    )
    return tests_required, tests_run, artifact


def _fold_cga_envelope_to_artifact(
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
    idx: int,
) -> EvidenceArtifact | None:
    """Build a ``cga_predicate`` artifact from one CGA envelope.

    ``cmd_cga.py`` emits an in-toto v1 statement under ``statement`` with
    ``predicate``, ``predicateType``, ``subject``. The summary carries
    ``merkle_root`` / ``edge_bundle_digest`` / ``written_to``. The
    artifact_id encodes the predicate type and a short hash so multiple
    CGA emissions on one packet stay distinguishable.

    Returns ``None`` and appends a warning when the envelope can't be
    parsed; never raises - the collector is forgiving by contract.
    """
    if not isinstance(envelope, Mapping):
        warnings.append(f"{source_label}: expected dict, got {type(envelope).__name__}; ignored")
        return None

    statement = envelope.get("statement")
    summary = envelope.get("summary") if isinstance(envelope.get("summary"), Mapping) else {}
    predicate_type: str | None = None
    statement_hash: str | None = None
    subject_count: int | None = None
    cga_file_path: str | None = None

    if isinstance(statement, Mapping):
        pt = statement.get("predicateType")
        if isinstance(pt, str) and pt:
            predicate_type = pt
        subjects = statement.get("subject")
        if isinstance(subjects, list):
            subject_count = len(subjects)
    if predicate_type is None and isinstance(summary, Mapping):
        pt = summary.get("predicate_type")
        if isinstance(pt, str) and pt:
            predicate_type = pt

    # Statement hash: prefer merkle_root from summary (canonical CGA
    # identity); fall back to sha256 of the serialised statement.
    if isinstance(summary, Mapping):
        mr = summary.get("merkle_root")
        if isinstance(mr, str) and mr:
            statement_hash = mr
        wt = summary.get("written_to")
        if isinstance(wt, str) and wt:
            cga_file_path = wt

    if statement_hash is None and isinstance(statement, Mapping):
        try:
            canonical = json.dumps(statement, sort_keys=True, separators=(",", ":"))
            statement_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except (TypeError, ValueError) as exc:
            warnings.append(f"{source_label}: could not serialise CGA statement ({exc})")

    if statement_hash is None:
        # No statement we could hash AND no merkle root in summary -
        # the envelope is unparseable for our purposes.
        warnings.append(f"{source_label}: missing both statement and summary.merkle_root; cga artifact skipped")
        return None

    if predicate_type is None:
        predicate_type = "unknown"

    pt_slug = predicate_type.split("/")[-1] if "/" in predicate_type else predicate_type
    short_hash = statement_hash[:12]
    artifact_id = f"cga:{pt_slug}:{short_hash}"

    extra: dict[str, Any] = {
        "predicate_type": predicate_type,
        "envelope_index": idx,
    }
    if subject_count is not None:
        extra["subject_count"] = subject_count
    if isinstance(summary, Mapping):
        for k in ("merkle_root", "edge_bundle_digest", "symbol_count", "edge_count"):
            v = summary.get(k)
            if v is not None:
                extra[k] = v

    # W241 (Leak C / W236d): actively reject paths that name
    # user-home / credential / config directories REGARDLESS of disk
    # presence. Pre-W241 the path was only dropped when
    # ``Path.exists()`` returned False; the W232 snapshot for
    # ``/home/specific-user/.ssh/id_rsa`` passed by accident on the
    # test runner. Now suspicious paths are dropped (and stamped with
    # ``machine_local_path``) up-front; only non-suspicious paths even
    # get the disk-presence probe that the constructor invariant
    # requires.
    use_path: str | None = None
    machine_local_path_redacted = False
    if cga_file_path:
        if _is_suspicious_path(cga_file_path):
            machine_local_path_redacted = True
        else:
            try:
                if Path(cga_file_path).exists():
                    use_path = cga_file_path
            except (OSError, ValueError):
                use_path = None

    redactions: tuple[str, ...] = ()
    if machine_local_path_redacted:
        # Record the redaction reason on the artifact so consumers can
        # tell a path was stripped (vs. never present). The content
        # hash still identifies the artifact canonically.
        redactions = ("machine_local_path",)
        # Also surface in extra for human inspection - the explicit
        # marker beats parsing the redactions tuple.
        extra["path_redaction"] = "machine_local_path"

    try:
        if use_path:
            return EvidenceArtifact(
                artifact_id=artifact_id,
                kind="cga_predicate",
                path=use_path,
                content_hash=statement_hash,
                redactions=redactions,
                extra=extra,
            )
        # No path - content_hash alone is allowed when the artifact is
        # identified by its hash; we record nothing inline (the raw
        # statement is large and the hash is the canonical identifier).
        return EvidenceArtifact(
            artifact_id=artifact_id,
            kind="cga_predicate",
            content_inline=statement_hash,
            redactions=redactions,
            extra=extra,
        )
    except ValueError as exc:
        warnings.append(f"{source_label}: rejected CGA artifact ({exc})")
        return None


def _audit_trail_to_artifact_and_decisions(
    envelope: Mapping[str, Any],
    warnings: list[str],
    source_label: str,
) -> tuple[EvidenceArtifact | None, list[Mapping[str, Any]]]:
    """W195 promotion: audit-trail envelope -> (artifact, policy_decisions).

    Replaces the W176 stop-gap that folded the audit-trail envelope into
    findings[] as a synthetic row. Now:

    * The whole audit-trail record becomes one ``manifest`` artifact
      keyed by ``audit-trail:<run_id>``. ``content_hash`` is the sha256
      of the canonical-JSON envelope (so two collectors looking at the
      same trail produce identical artifact ids).
    * Each per-entry chain-verification issue (from
      ``envelope["issues"]``) becomes one ``policy_decisions`` entry
      with ``rule_id="audit_trail_chain_integrity"`` and
      ``decision="fail"`` (plus a pass row when the chain is clean).
    """
    issues = envelope.get("issues")
    summary = envelope.get("summary") if isinstance(envelope.get("summary"), Mapping) else {}

    # Trail hash - sha256 of canonical JSON of the whole envelope so the
    # artifact_id stays stable across collectors and the content_hash
    # can be verified by any consumer that has the envelope bytes.
    try:
        canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        trail_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as exc:
        warnings.append(f"{source_label}: could not serialise audit-trail envelope ({exc})")
        trail_hash = None

    # Identify the run / path the trail belongs to so the artifact_id
    # carries a meaningful suffix.
    run_id: str | None = None
    audit_trail_path: str | None = None
    if isinstance(summary, Mapping):
        v = summary.get("audit_trail_path")
        if isinstance(v, str) and v:
            audit_trail_path = v
        rid = summary.get("run_id")
        if isinstance(rid, str) and rid:
            run_id = rid

    chain_valid: bool | None = None
    entries_count: int | None = None
    if isinstance(summary, Mapping):
        cv = summary.get("chain_valid")
        if isinstance(cv, bool):
            chain_valid = cv
        ec = summary.get("total_records")
        if isinstance(ec, int):
            entries_count = ec

    artifact_id_suffix = run_id or (
        hashlib.sha1(audit_trail_path.encode("utf-8")).hexdigest()[:12] if audit_trail_path else "unknown"
    )
    artifact_id = f"audit-trail:{artifact_id_suffix}"

    extra: dict[str, Any] = {}
    if chain_valid is not None:
        extra["chain_valid"] = chain_valid
    if entries_count is not None:
        extra["entries_count"] = entries_count
    if audit_trail_path:
        extra["audit_trail_path"] = audit_trail_path

    # Only attach ``path`` if the file is present on disk (per the
    # path+content_hash constructor invariant).
    use_path: str | None = None
    if audit_trail_path:
        try:
            if Path(audit_trail_path).exists():
                use_path = audit_trail_path
        except (OSError, ValueError):
            use_path = None

    artifact: EvidenceArtifact | None = None
    if trail_hash is not None:
        try:
            if use_path:
                artifact = EvidenceArtifact(
                    artifact_id=artifact_id,
                    kind="manifest",
                    path=use_path,
                    content_hash=trail_hash,
                    extra=extra,
                )
            else:
                # No on-disk file - record the hash inline so the
                # artifact is still discoverable by hash.
                artifact = EvidenceArtifact(
                    artifact_id=artifact_id,
                    kind="manifest",
                    content_inline=trail_hash,
                    extra=extra,
                )
        except ValueError as exc:
            warnings.append(f"{source_label}: rejected audit-trail artifact ({exc})")
            artifact = None

    # W293 — audit-trail chain-integrity rows are produced by parsing the
    # ``audit-trail-verify`` envelope, which itself is HMAC-walked over
    # the ledger; the appropriate provenance label is ``audit_trail`` per
    # the W282 closed enumeration.
    _audit_trail_prov = provenance_label("audit_trail")

    decisions: list[Mapping[str, Any]] = []
    # If the envelope carries chain_valid in its summary we still want
    # at least one policy_decisions entry recording the overall verdict
    # so downstream consumers can answer "was the chain valid?" without
    # walking the artifact's extra dict.
    if chain_valid is True:
        decisions.append(
            {
                "rule_id": "audit_trail_chain_integrity",
                "decision": "pass",
                "evidence_ref": f"artifact:{artifact_id}",
                "provenance": _audit_trail_prov,
            }
        )
    elif chain_valid is False:
        decisions.append(
            {
                "rule_id": "audit_trail_chain_integrity",
                "decision": "fail",
                "evidence_ref": f"artifact:{artifact_id}",
                "reason": "audit trail chain verification failed",
                "provenance": _audit_trail_prov,
            }
        )

    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            # Skip the synthetic "not found" state row - it's a state
            # flag, not a per-entry verification failure (mirrors the
            # cmd_audit_trail_verify W146 filter).
            kind = issue.get("issue") or ""
            if "not found" in str(kind):
                continue
            entry_index = issue.get("line") or issue.get("entry_index")
            entry: dict[str, Any] = {
                "rule_id": "audit_trail_chain_integrity",
                "decision": "fail",
                "evidence_ref": f"artifact:{artifact_id}",
                "issue_kind": str(kind) if kind else "unknown",
            }
            if entry_index is not None:
                try:
                    entry["entry_index"] = int(entry_index)
                except (TypeError, ValueError):
                    entry["entry_index"] = entry_index
            for k in ("expected_prev", "computed_prev", "timestamp"):
                v = issue.get(k)
                if v is not None:
                    entry[k] = v
            entry["provenance"] = _audit_trail_prov
            decisions.append(entry)

    return artifact, decisions


def _read_mcp_receipts_dir(
    receipts_dir: str | Path,
    warnings: list[str],
) -> tuple[list[EvidenceArtifact], list[ActorRef]]:
    """W197: walk ``.roam/mcp_receipts/<run_id>/`` and produce artifacts + refs.

    Each ``*.json`` file is parsed into an ``McpDecisionReceipt`` and:

    * Recorded as one ``EvidenceArtifact(kind="other", ...)`` (the
      ``mcp_receipt`` vocabulary kind isn't yet in ARTIFACT_KINDS - per
      the W199 design notes we use the ``other`` escape hatch and
      stamp ``extra["receipt_kind"]="mcp_receipt"`` so a future
      migration to a dedicated kind is a one-line change).
    * Mirrored as two ActorRefs: one ``mcp_client`` for ``client_id``
      and one ``tool`` for ``tool_name``. Dedup follows the existing
      W190 rule (first (kind, id) sighting wins).

    Malformed JSON files emit a warning and are skipped - never raised.
    """
    artifacts: list[EvidenceArtifact] = []
    refs: list[ActorRef] = []
    if receipts_dir is None:
        return artifacts, refs
    try:
        dir_path = Path(receipts_dir)
    except (OSError, ValueError) as exc:
        warnings.append(f"mcp_receipts_dir: rejected ({exc})")
        return artifacts, refs
    if not dir_path.exists() or not dir_path.is_dir():
        # Missing dir is fine - W196 emitter may not have run yet. The
        # collector returns empty lists and the caller proceeds.
        return artifacts, refs

    seen_refs: set[tuple[str, str]] = set()

    try:
        files = sorted(dir_path.glob("*.json"))
    except OSError as exc:
        warnings.append(f"mcp_receipts_dir: could not list {dir_path} ({exc})")
        return artifacts, refs

    for file in files:
        try:
            body = file.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"mcp_receipts_dir: could not read {file.name} ({exc})")
            continue
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"mcp_receipts_dir: malformed JSON in {file.name} ({exc})")
            continue
        if not isinstance(data, Mapping):
            warnings.append(f"mcp_receipts_dir: {file.name} is not a JSON object - skipped")
            continue
        # McpDecisionReceipt validates its own fields; absorb the
        # ValueError defensively so one bad receipt doesn't break the
        # whole packet.
        try:
            # Coerce known fields - tolerate missing optionals.
            tool_call = data.get("tool_call")
            client_id = data.get("client_id")
            tool_name = data.get("tool_name")
            if not (
                isinstance(tool_call, str)
                and tool_call
                and isinstance(client_id, str)
                and client_id
                and isinstance(tool_name, str)
                and tool_name
            ):
                warnings.append(
                    f"mcp_receipts_dir: {file.name} missing required tool_call/client_id/tool_name - skipped"
                )
                continue
            receipt = McpDecisionReceipt(
                tool_call=tool_call,
                client_id=client_id,
                tool_name=tool_name,
                actor_ref_id=data.get("actor_ref_id"),
                declared_side_effects=tuple(data.get("declared_side_effects") or ()),
                required_mode=data.get("required_mode"),
                input_hash=data.get("input_hash"),
                policy_decision=data.get("policy_decision", "not_evaluated"),
                output_ref=data.get("output_ref"),
                output_hash=data.get("output_hash"),
                run_event_id=data.get("run_event_id"),
                redactions=tuple(data.get("redactions") or ()),
                extra=data.get("extra") or {},
            )
        except (ValueError, TypeError) as exc:
            warnings.append(f"mcp_receipts_dir: rejected receipt {file.name} ({exc})")
            continue

        content_hash = receipt.compute_content_hash()
        extra: dict[str, Any] = {
            "receipt_kind": "mcp_receipt",
            "tool_name": receipt.tool_name,
            "client_id": receipt.client_id,
            "policy_decision": receipt.policy_decision,
        }
        if receipt.required_mode:
            extra["required_mode"] = receipt.required_mode
        if receipt.run_event_id:
            extra["run_event_id"] = receipt.run_event_id

        try:
            artifact = EvidenceArtifact(
                artifact_id=f"mcp_receipt:{receipt.tool_call}",
                kind="other",
                path=str(file),
                content_hash=content_hash,
                extra=extra,
            )
        except ValueError as exc:
            warnings.append(f"mcp_receipts_dir: rejected artifact for {file.name} ({exc})")
            continue
        artifacts.append(artifact)

        # Mirror client + tool into actor_refs. W290 - the provenance
        # is ``mcp_receipt`` (the receipt file itself is the originating
        # source of the identity claim).
        for kind, actor_id in (
            ("mcp_client", receipt.client_id),
            ("tool", receipt.tool_name),
        ):
            key = (kind, actor_id)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            try:
                refs.append(
                    ActorRef(
                        actor_kind=kind,
                        actor_id=actor_id,
                        extra={"provenance": provenance_label("mcp_receipt")},
                    )
                )
            except ValueError as exc:
                warnings.append(f"mcp_receipts_dir: rejected ActorRef ({kind!r}, {actor_id!r}): {exc}")

    return artifacts, refs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def collect_change_evidence(
    *,
    pr_bundle_envelope: Mapping[str, Any] | None = None,
    findings_envelopes: Iterable[Mapping[str, Any]] = (),
    run_events: Iterable[Mapping[str, Any]] = (),
    audit_trail_envelope: Mapping[str, Any] | None = None,
    critique_envelope: Mapping[str, Any] | None = None,
    pr_risk_envelope: Mapping[str, Any] | None = None,
    rules_envelopes: Iterable[Mapping[str, Any]] = (),
    vuln_reach_envelopes: Iterable[Mapping[str, Any]] = (),
    test_impact_envelopes: Iterable[Mapping[str, Any]] = (),
    cga_envelopes: Iterable[Mapping[str, Any]] = (),
    mcp_receipts_dir: str | Path | None = None,
    extra_policy_decisions: Iterable[Mapping[str, Any]] = (),
    repo_id: str | None = None,
    commit_sha: str | None = None,
    git_range: str | None = None,
    diff_hash: str | None = None,
    mode: str | None = None,
    schema_version: str | None = None,
    packet_config_hashes: Mapping[str, str] | None = None,
    current_config_hashes: Mapping[str, str] | None = None,
) -> tuple[ChangeEvidence, list[str]]:
    """Collect evidence packet + warning list (unmappable fields).

    Returns ``(packet, warnings)`` where ``warnings`` is non-empty
    when input envelopes contain fields the collector doesn't yet
    know how to map. The caller decides whether to fail or proceed.

    Each envelope source contributes its native fields:

    * ``pr_bundle_envelope`` -> identity (commit_sha / git_range /
      diff_hash / run_ids / agent_id / human_actor / mode /
      started_at / completed_at / verdict / risk_level), the
      ``changed_subjects`` (from ``affected_symbols[]``), the
      ``tests_required`` / ``tests_run`` / ``approvals`` /
      ``accepted_risks`` collections, and the ``context_refs``
      (from ``context_files[]``).

    * ``findings_envelopes`` -> flat ``findings`` rows.

    * ``run_events`` -> ``run_ids`` (if not already set) and
      ``started_at`` / ``completed_at`` from event timestamps.

    * ``audit_trail_envelope`` -> W195 promotion: the envelope is
      promoted to a dedicated ``manifest`` ``EvidenceArtifact`` and
      per-entry chain verification results become ``policy_decisions``
      rows with ``rule_id="audit_trail_chain_integrity"``. The legacy
      W176 synthetic-finding fold is gone; tamper findings from
      ``audit-trail-verify --persist`` flow through the findings-store
      path instead.

    * ``critique_envelope`` -> ``findings`` rows from its own
      ``findings`` array (W153 patch.* kinds).

    * ``pr_risk_envelope`` -> ``findings`` rows from its
      ``findings`` array (W134 per-kind rows).

    * ``rules_envelopes`` (W192) -> per-rule rows become
      ``policy_decisions`` entries with ``rule_id`` / ``decision``
      (pass | fail | unknown) / optional ``severity`` / ``reason``.

    * ``vuln_reach_envelopes`` (W193) -> ``vulnerabilities[]`` rows
      become ``findings`` with ``source_detector="vuln-reach"``; the
      full envelope is preserved as a ``raw_envelope`` artifact.

    * ``test_impact_envelopes`` (W193) -> ``tests[]`` flatten into
      ``tests_required``; ``tests_run[]`` (when present) flow into
      ``tests_run``; the full envelope becomes a ``raw_envelope``
      artifact.

    * ``cga_envelopes`` (W194) -> each in-toto v1 statement is
      promoted to a ``cga_predicate`` artifact keyed by predicate type
      + short merkle hash.

    * ``mcp_receipts_dir`` (W197) -> walks ``.roam/mcp_receipts/<run_id>/``
      (or any caller-supplied directory), parses each ``*.json`` as an
      ``McpDecisionReceipt``, and emits one ``EvidenceArtifact(kind="other",
      extra={"receipt_kind": "mcp_receipt", ...})`` per receipt plus
      one ``ActorRef`` per ``(mcp_client, client_id)`` and
      ``(tool, tool_name)`` pair (deduped against existing actor refs).

    * ``extra_policy_decisions`` (W267) -> additional ``policy_decisions``
      rows produced by caller-side gatherers (e.g. PR Replay's
      constitution / permit / lease scanners). Each entry is a plain dict
      with at least ``rule_id`` and ``decision``; the collector
      concatenates them with rules + audit-trail decisions in stable
      order: rules first, audit-trail second, extras last. No schema
      change — ``policy_decisions`` was already a tuple-of-mapping field.

    Caller-supplied identity fields (``commit_sha``, ``git_range``,
    ``diff_hash``, ``mode``, ``repo_id``, ``schema_version``) win
    over anything the envelopes carry. This mirrors CLAUDE.md LAW 11:
    explicit caller intent beats inferred values.

    W1253 config-hash drift detection (optional inputs):

    * ``packet_config_hashes`` - the three W210 config-file hashes
      stamped onto the run at start time (lifted from
      ``RunMeta.extra`` by the W1255-IMPL producer). Keys are the
      ChangeEvidence field names (``rules_config_hash`` /
      ``constitution_hash`` / ``control_map_hash``); values are 64-char
      sha256 hex digests or the empty string for missing files. When
      provided, the packet's three hash fields record these values so
      an audit-time verifier can re-check the drift signal.
    * ``current_config_hashes`` - the same three hashes recomputed
      against the current on-disk content (typically via
      :func:`roam.evidence.config_hashes.stamp_all`). When BOTH
      ``packet_config_hashes[k]`` and ``current_config_hashes[k]`` are
      non-empty AND differ, ``evidence_stale=True`` flips and a
      ``stale_reasons`` entry names the drifted field. Missing data
      on either side is NOT a positive drift signal (insufficient-data
      discipline mirrors W1234).
    """
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Step 1 - extract identity from the pr-bundle envelope (if any).
    # Caller args still override at the end; this step gathers fallbacks.
    # ------------------------------------------------------------------
    bundle_commit_sha: str | None = None
    bundle_git_range: str | None = None
    bundle_diff_hash: str | None = None
    bundle_run_ids: tuple[str, ...] = ()
    bundle_agent_id: str | None = None
    bundle_human_actor: str | None = None
    bundle_mode: str | None = None
    bundle_started_at: str | None = None
    bundle_completed_at: str | None = None
    bundle_verdict: str | None = None
    bundle_risk_level: str | None = None
    bundle_repo_id: str | None = None
    bundle_subjects: tuple[EvidenceSubject, ...] = ()
    bundle_context_refs: tuple[EvidenceArtifact, ...] = ()
    bundle_tests_required: tuple[str, ...] = ()
    bundle_tests_run: tuple[Mapping[str, Any], ...] = ()
    bundle_approvals: tuple[Mapping[str, Any], ...] = ()
    bundle_accepted_risks: tuple[Mapping[str, Any], ...] = ()
    bundle_redactions: list[str] = []

    if pr_bundle_envelope is not None:
        if not isinstance(pr_bundle_envelope, Mapping):
            warnings.append(f"pr_bundle_envelope: expected dict, got {type(pr_bundle_envelope).__name__}; ignored")
        else:
            # Identity fields - top-level OR nested under bundle_meta /
            # actor / timestamps containers. We probe both shapes.
            bundle_commit_sha = _coalesce(
                _get_pr_bundle_field(pr_bundle_envelope, "commit_sha"),
                _nested(pr_bundle_envelope, ("bundle_meta", "git", "head_sha")),
                _nested(pr_bundle_envelope, ("bundle_meta", "commit_sha")),
            )
            bundle_git_range = _coalesce(
                _get_pr_bundle_field(pr_bundle_envelope, "git_range"),
                _nested(pr_bundle_envelope, ("bundle_meta", "git_range")),
            )
            bundle_diff_hash = _coalesce(
                _get_pr_bundle_field(pr_bundle_envelope, "diff_hash"),
            )
            run_ids_raw = _coalesce(
                pr_bundle_envelope.get("run_ids"),
                _nested(pr_bundle_envelope, ("bundle_meta", "run_ids")),
            )
            if isinstance(run_ids_raw, (list, tuple)):
                bundle_run_ids = tuple(str(r) for r in run_ids_raw if isinstance(r, str) and r)
            # W249 - layer-2 scrub. Run BEFORE picking values so the
            # secret-shaped substrings never land in bundle_agent_id /
            # bundle_human_actor (which feed ChangeEvidence.agent_id /
            # .human_actor verbatim). ``had_actor_secret`` rolls up
            # whether any pattern fired; we stamp ``"secret"`` into
            # ``bundle_redactions`` below when True.
            had_actor_secret = False
            actor_block_raw = pr_bundle_envelope.get("actor")
            scrubbed_actor_block, ab_hit = _scrub_actor_block(actor_block_raw)
            if ab_hit:
                had_actor_secret = True
            actor_block = scrubbed_actor_block
            if isinstance(actor_block, Mapping):
                bundle_agent_id = _coalesce(
                    actor_block.get("agent_id"),
                    actor_block.get("agent"),
                )
                bundle_human_actor = _coalesce(
                    actor_block.get("human_actor"),
                    actor_block.get("human"),
                    actor_block.get("user"),
                )
            # Legacy top-level fields - scrub each individually.
            legacy_agent_id, lai_hit = _redact_secrets_in_string(
                pr_bundle_envelope.get("agent_id") if isinstance(pr_bundle_envelope.get("agent_id"), str) else ""
            )
            if lai_hit:
                had_actor_secret = True
            legacy_human_actor, lha_hit = _redact_secrets_in_string(
                pr_bundle_envelope.get("human_actor") if isinstance(pr_bundle_envelope.get("human_actor"), str) else ""
            )
            if lha_hit:
                had_actor_secret = True
            bundle_agent_id = _coalesce(
                bundle_agent_id,
                legacy_agent_id or None,
            )
            bundle_human_actor = _coalesce(
                bundle_human_actor,
                legacy_human_actor or None,
            )
            bundle_mode = _coalesce(
                pr_bundle_envelope.get("mode"),
                _nested(pr_bundle_envelope, ("summary", "active_mode")),
                _nested(pr_bundle_envelope, ("mode_block", "active_mode")),
            )
            ts_block = pr_bundle_envelope.get("timestamps")
            if isinstance(ts_block, Mapping):
                bundle_started_at = _coalesce(
                    ts_block.get("started_at"),
                    ts_block.get("start"),
                    ts_block.get("created_at"),
                )
                bundle_completed_at = _coalesce(
                    ts_block.get("completed_at"),
                    ts_block.get("end"),
                    ts_block.get("updated_at"),
                )
            # bundle_meta carries created_at / updated_at on the real
            # pr-bundle envelope shape; fall back to those when no
            # explicit timestamps block is present.
            bundle_started_at = _coalesce(
                bundle_started_at,
                _nested(pr_bundle_envelope, ("bundle_meta", "created_at")),
            )
            bundle_completed_at = _coalesce(
                bundle_completed_at,
                _nested(pr_bundle_envelope, ("bundle_meta", "updated_at")),
            )
            # W249 - layer-2 scrub on verdict. The verdict is a free-
            # form string that flows verbatim into ChangeEvidence.verdict
            # so secret-shaped substrings would otherwise survive into
            # the on-wire canonical JSON.
            raw_verdict = _coalesce(
                pr_bundle_envelope.get("verdict"),
                _nested(pr_bundle_envelope, ("summary", "verdict")),
            )
            if isinstance(raw_verdict, str):
                scrubbed_verdict, verdict_hit = _redact_secrets_in_string(raw_verdict)
                if verdict_hit:
                    had_actor_secret = True
                bundle_verdict = scrubbed_verdict
            else:
                bundle_verdict = raw_verdict
            # W641-followup-F — prefer the canonical risk-LEVEL axis emitted
            # by the W641 cluster producers (pr_risk, impact, critique,
            # pr_bundle, attest). The producer→packet projection loop closes
            # here: when ``risk_level_canonical`` is present we lift it
            # verbatim; we only fall back to the legacy ``risk_level`` /
            # ``summary.risk_level`` synthesis path for backward-compat with
            # older pr-bundle envelopes that pre-date the canonical field.
            #
            # Priority chain (per "Make fallback chains loud" rule, CP45 /
            # CP46):
            #   1. ``envelope["risk_level_canonical"]``         → canonical
            #   2. ``envelope["summary"]["risk_level_canonical"]`` → canonical
            #   3. ``envelope["risk_level"]``                   → legacy verdict
            #      ``envelope["summary"]["risk_level"]``          synthesis
            #   4. neither present                              → None (Q5
            #                                                     not_applicable
            #                                                     or missing —
            #                                                     ``evidence_completeness``
            #                                                     classifies)
            #
            # Lineage disclosure: the canonical / legacy lineage is observable
            # via ``_resolve_risk_level_with_lineage`` (returns ``(value,
            # source)`` where ``source`` is one of the three closed-enum
            # tokens). The collector keeps the lineage-tuple internally; we
            # only surface a ``warnings`` entry when ACTUAL producer drift is
            # detected (the canonical + legacy sources disagree). Silent
            # happy-paths keep pre-existing collector tests green; the
            # closed-enum lineage marker is exposed for downstream tests via
            # the module-level :func:`resolve_risk_level_with_lineage` helper
            # (W641-followup-F).
            bundle_risk_level, _risk_level_source, _risk_level_divergence = _resolve_risk_level_with_lineage(
                pr_bundle_envelope
            )
            if _risk_level_divergence is not None:
                warnings.append(_risk_level_divergence)
            bundle_repo_id = _coalesce(
                pr_bundle_envelope.get("repo_id"),
            )

            # changed_subjects come straight from affected_symbols[].
            bundle_subjects = _build_changed_subjects_from_affected(
                pr_bundle_envelope.get("affected_symbols"),
                repo_id=repo_id or bundle_repo_id,
                warnings=warnings,
            )

            # context_refs come from context_files[].
            bundle_context_refs = _build_context_refs_from_context_files(
                pr_bundle_envelope.get("context_files"),
                warnings=warnings,
            )

            # tests_required is a list of dicts or strings; flatten to
            # a tuple of strings for the packet field (which is typed
            # tuple[str, ...]). Dicts get their ``test_file`` key.
            tests_req_raw = pr_bundle_envelope.get("tests_required")
            if isinstance(tests_req_raw, list):
                tr_out: list[str] = []
                for t in tests_req_raw:
                    if isinstance(t, str) and t:
                        tr_out.append(t)
                    elif isinstance(t, Mapping):
                        tf = t.get("test_file") or t.get("path") or t.get("file")
                        if isinstance(tf, str) and tf:
                            tr_out.append(tf)
                bundle_tests_required = tuple(tr_out)

            tests_run_raw = pr_bundle_envelope.get("tests_run")
            if isinstance(tests_run_raw, list):
                bundle_tests_run = tuple(dict(t) for t in tests_run_raw if isinstance(t, Mapping))

            approvals_raw = pr_bundle_envelope.get("approvals")
            if isinstance(approvals_raw, list):
                bundle_approvals = tuple(dict(a) for a in approvals_raw if isinstance(a, Mapping))
            accepted_raw = pr_bundle_envelope.get("accepted_risks")
            if isinstance(accepted_raw, list):
                bundle_accepted_risks = tuple(dict(r) for r in accepted_raw if isinstance(r, Mapping))

            # Redactions on the pr-bundle envelope.
            bundle_redactions = _normalise_redactions(
                pr_bundle_envelope.get("redactions"),
                warnings,
                source_label="pr_bundle_envelope",
            )
            # W249 - stamp ``"secret"`` if the layer-2 scrub fired on any
            # actor / verdict field above. Dedup against any ``"secret"``
            # the producer already declared (W240).
            if had_actor_secret and "secret" not in bundle_redactions:
                bundle_redactions.append("secret")

            # Unrecognised top-level keys -> warning. This is the memo's
            # "warning list for fields that cannot yet map cleanly".
            for key in pr_bundle_envelope.keys():
                if key not in _PR_BUNDLE_ENVELOPE_CHROME and key not in _PR_BUNDLE_KNOWN_PAYLOAD:
                    warnings.append(f"pr_bundle_envelope: unrecognised top-level field {key!r}")

    # ------------------------------------------------------------------
    # Step 2 - flatten findings from every findings envelope into one
    # tuple, plus the critique / pr-risk envelopes (each carries its own
    # ``findings`` array per W153 / W134).
    # ------------------------------------------------------------------
    findings: list[Mapping[str, Any]] = []
    for idx, env in enumerate(findings_envelopes):
        if not isinstance(env, Mapping):
            warnings.append(f"findings_envelopes[{idx}]: expected dict, got {type(env).__name__}; skipped")
            continue
        findings.extend(_normalise_findings_envelope(env, warnings, source_label=f"findings_envelopes[{idx}]"))
        # Also harvest redactions from findings envelopes.
        bundle_redactions.extend(
            _normalise_redactions(
                env.get("redactions"),
                warnings,
                source_label=f"findings_envelopes[{idx}]",
            )
        )

    if critique_envelope is not None:
        if not isinstance(critique_envelope, Mapping):
            warnings.append(f"critique_envelope: expected dict, got {type(critique_envelope).__name__}; ignored")
        else:
            findings.extend(
                _normalise_findings_envelope(
                    critique_envelope,
                    warnings,
                    source_label="critique_envelope",
                )
            )
            bundle_redactions.extend(
                _normalise_redactions(
                    critique_envelope.get("redactions"),
                    warnings,
                    source_label="critique_envelope",
                )
            )

    if pr_risk_envelope is not None:
        if not isinstance(pr_risk_envelope, Mapping):
            warnings.append(f"pr_risk_envelope: expected dict, got {type(pr_risk_envelope).__name__}; ignored")
        else:
            findings.extend(
                _normalise_findings_envelope(
                    pr_risk_envelope,
                    warnings,
                    source_label="pr_risk_envelope",
                )
            )
            bundle_redactions.extend(
                _normalise_redactions(
                    pr_risk_envelope.get("redactions"),
                    warnings,
                    source_label="pr_risk_envelope",
                )
            )

    # W195 promotion: the audit-trail envelope is no longer folded
    # into findings[] as a synthetic row. Instead it becomes a
    # dedicated ``manifest`` artifact + per-entry chain-verification
    # ``policy_decisions`` rows. Tamper findings (per-line hash
    # mismatches) continue to flow into findings[] via the
    # ``audit-trail-verify --persist`` path (W146) - the collector
    # does not synthesize them here.
    audit_trail_artifact: EvidenceArtifact | None = None
    audit_trail_decisions: list[Mapping[str, Any]] = []
    if audit_trail_envelope is not None:
        if not isinstance(audit_trail_envelope, Mapping):
            warnings.append(f"audit_trail_envelope: expected dict, got {type(audit_trail_envelope).__name__}; ignored")
        else:
            audit_trail_artifact, audit_trail_decisions = _audit_trail_to_artifact_and_decisions(
                audit_trail_envelope,
                warnings,
                source_label="audit_trail_envelope",
            )
            bundle_redactions.extend(
                _normalise_redactions(
                    audit_trail_envelope.get("redactions"),
                    warnings,
                    source_label="audit_trail_envelope",
                )
            )

    # W192 - rules envelopes flatten to policy_decisions.
    rules_decisions: list[Mapping[str, Any]] = []
    for idx, env in enumerate(rules_envelopes):
        if not isinstance(env, Mapping):
            warnings.append(f"rules_envelopes[{idx}]: expected dict, got {type(env).__name__}; skipped")
            continue
        rules_decisions.extend(
            _flatten_rules_envelope_to_policy_decisions(env, warnings, source_label=f"rules_envelopes[{idx}]")
        )
        bundle_redactions.extend(
            _normalise_redactions(
                env.get("redactions"),
                warnings,
                source_label=f"rules_envelopes[{idx}]",
            )
        )

    # W193a - vuln-reach envelopes flatten to findings + raw_envelope artifact.
    vuln_reach_artifacts: list[EvidenceArtifact] = []
    for idx, env in enumerate(vuln_reach_envelopes):
        if not isinstance(env, Mapping):
            warnings.append(f"vuln_reach_envelopes[{idx}]: expected dict, got {type(env).__name__}; skipped")
            continue
        rows, art = _flatten_vuln_reach_envelope(env, warnings, f"vuln_reach_envelopes[{idx}]", idx)
        findings.extend(rows)
        if art is not None:
            vuln_reach_artifacts.append(art)
        bundle_redactions.extend(
            _normalise_redactions(
                env.get("redactions"),
                warnings,
                source_label=f"vuln_reach_envelopes[{idx}]",
            )
        )

    # W193b - test-impact envelopes flatten to tests_required + tests_run.
    extra_tests_required: list[str] = []
    extra_tests_run: list[Mapping[str, Any]] = []
    test_impact_artifacts: list[EvidenceArtifact] = []
    for idx, env in enumerate(test_impact_envelopes):
        if not isinstance(env, Mapping):
            warnings.append(f"test_impact_envelopes[{idx}]: expected dict, got {type(env).__name__}; skipped")
            continue
        treq, trun, art = _flatten_test_impact_envelope(env, warnings, f"test_impact_envelopes[{idx}]", idx)
        extra_tests_required.extend(treq)
        extra_tests_run.extend(trun)
        if art is not None:
            test_impact_artifacts.append(art)
        bundle_redactions.extend(
            _normalise_redactions(
                env.get("redactions"),
                warnings,
                source_label=f"test_impact_envelopes[{idx}]",
            )
        )

    # W194 - CGA envelopes promote to cga_predicate artifacts.
    cga_artifacts: list[EvidenceArtifact] = []
    for idx, env in enumerate(cga_envelopes):
        if not isinstance(env, Mapping):
            warnings.append(f"cga_envelopes[{idx}]: expected dict, got {type(env).__name__}; skipped")
            continue
        art = _fold_cga_envelope_to_artifact(env, warnings, f"cga_envelopes[{idx}]", idx)
        if art is not None:
            cga_artifacts.append(art)
        bundle_redactions.extend(
            _normalise_redactions(
                env.get("redactions"),
                warnings,
                source_label=f"cga_envelopes[{idx}]",
            )
        )

    # W197 - MCP decision receipts directory walk.
    mcp_receipt_artifacts, mcp_receipt_actor_refs = (
        _read_mcp_receipts_dir(mcp_receipts_dir, warnings) if mcp_receipts_dir is not None else ([], [])
    )

    # ------------------------------------------------------------------
    # Step 3 - run-event timestamps and run_ids.
    # ------------------------------------------------------------------
    ev_list = list(run_events) if run_events else []
    ev_run_ids, ev_earliest, ev_latest = _collect_run_event_metadata(ev_list)

    # W1234 - W210 change-scope timestamps from the run-ledger event
    # stream. Distinct from the run-wide ``started_at`` / ``completed_at``
    # above: those bracket the WHOLE run; these bracket the change-scope
    # phases inside it (context-read vs. post-edit). When the event
    # stream doesn't surface phase-classifiable entries the three values
    # stay ``None`` (honest-default; the W210 omit-when-default rule
    # keeps the canonical-JSON / content_hash byte-stable).
    (
        change_scope_context_read_at,
        change_scope_edits_started_at,
        change_scope_edits_completed_at,
    ) = _collect_change_scope_timestamps(ev_list)
    evidence_stale, stale_reasons_tuple = _compute_evidence_stale(
        change_scope_context_read_at,
        change_scope_edits_started_at,
    )

    # W1253 - config-hash drift detection. The W1255-IMPL producer
    # stamps the three canonical config hashes into RunMeta.extra at
    # run-start; this consumer-side block compares those packet-stamped
    # hashes against the current on-disk hashes and flips
    # ``evidence_stale=True`` when any of the three drift. Drift
    # reasons combine with the W1234 timestamp staleness reasons; the
    # flag is sticky (either signal raises it). Both kwargs are
    # optional - when neither side is provided the packet's three
    # hash fields stay ``None`` and the W210 omit-when-default rule
    # keeps the canonical JSON byte-stable for pre-W1253 packets.
    #
    # The packet records the PACKET-STAMPED hashes (the run-start
    # values), NOT the current on-disk values. An audit-time consumer
    # re-computes the on-disk hashes itself and compares against the
    # packet record to re-verify the drift signal independently.
    hash_drift_reasons, _current_hashes_seen = _detect_hash_drift(
        packet_config_hashes,
        current_config_hashes,
    )
    if hash_drift_reasons:
        evidence_stale = True
    stale_reasons = stale_reasons_tuple + hash_drift_reasons

    # Lift the packet-stamped hashes onto the ChangeEvidence packet
    # fields. Empty strings ("") collapse to None so the W210
    # omit-when-default discipline kicks in and the canonical JSON
    # stays byte-stable for packets whose run never stamped a given
    # config (insufficient-data discipline: "" is the W1255 absent
    # sentinel, distinct from "computed but empty").
    _packet_h = dict(packet_config_hashes) if packet_config_hashes else {}
    _stamped_rules_config_hash = _packet_h.get("rules_config_hash") or None
    _stamped_constitution_hash = _packet_h.get("constitution_hash") or None
    _stamped_control_map_hash = _packet_h.get("control_map_hash") or None

    # ------------------------------------------------------------------
    # Step 4 - resolve final values with the caller-wins precedence:
    #   caller arg > pr-bundle envelope > run-event derived
    # ------------------------------------------------------------------
    final_commit_sha = commit_sha or bundle_commit_sha
    final_git_range = git_range or bundle_git_range
    final_diff_hash = diff_hash or bundle_diff_hash
    final_mode = mode or bundle_mode
    final_repo_id = repo_id or bundle_repo_id
    final_schema_version = schema_version  # only set if caller passed it

    # run_ids: union, caller-derived bundle ids first (preserve order),
    # then run-event ids that weren't in the bundle.
    final_run_ids_list: list[str] = list(bundle_run_ids)
    for rid in ev_run_ids:
        if rid not in final_run_ids_list:
            final_run_ids_list.append(rid)
    final_run_ids = tuple(final_run_ids_list)

    final_started_at = bundle_started_at or ev_earliest
    final_completed_at = bundle_completed_at or ev_latest

    # Redactions: union; dedup preserving order.
    final_redactions: list[str] = []
    for r in bundle_redactions:
        if r not in final_redactions:
            final_redactions.append(r)

    # ------------------------------------------------------------------
    # Step 5 - build the packet. If the caller passed a schema_version
    # explicitly we honour it; otherwise the dataclass default applies.
    # ------------------------------------------------------------------
    evidence_id = _evidence_id_from_inputs(
        commit_sha=final_commit_sha,
        git_range=final_git_range,
        diff_hash=final_diff_hash,
        pr_bundle_envelope=pr_bundle_envelope,
    )

    # W285 - collect HMAC-verified run-ledger event ids + parseable MCP
    # receipt ids so the classifier can promote tool pseudo-actors
    # (``roam_init``, ``roam_reindex``) to ``local_env`` ONLY when real
    # corroborating evidence exists. Without a verified ledger or
    # parseable receipt, the sets stay empty and tool actors fall to
    # ``unknown`` per the W285 guardrail (honest noise > name-based
    # shortcut).
    corroborated_tool_ids, corroborated_actor_ids = _collect_corroborated_ids(mcp_receipts_dir, warnings)

    # W292 - harvest (authority_kind, authority_id) pairs from
    # HMAC-verified run-ledger events. Membership promotes the matching
    # AuthorityRef's provenance to ``run_ledger`` (the highest tier in
    # the W292 precedence table). The harvest is repo-scoped via
    # ``Path.cwd()`` because the run ledger lives under ``.roam/runs/``
    # in the current workspace; no env reads inside the helper itself
    # (W285-style discipline).
    try:
        _authority_repo_root = Path.cwd()
    except Exception:
        _authority_repo_root = None
    if _authority_repo_root is not None:
        corroborated_authorities = _collect_corroborated_authorities_from_runs(_authority_repo_root, warnings)
    else:
        corroborated_authorities = frozenset()

    # W190 - materialise the three W182 ref lists from whatever
    # producer surface is available. Empty tuples are skipped from the
    # canonical JSON (see ``_W182_OMIT_WHEN_EMPTY_FIELDS``) so pre-W182
    # hashes stay stable when no refs are populated.
    actor_refs = _build_actor_refs(
        pr_bundle_envelope=pr_bundle_envelope if isinstance(pr_bundle_envelope, Mapping) else None,
        run_events=ev_list,
        caller_agent_id=None,  # no kwarg yet; bundle_agent_id flows via ``actor`` block
        corroborated_tool_ids=corroborated_tool_ids,
        corroborated_actor_ids=corroborated_actor_ids,
    )
    authority_refs = _build_authority_refs(
        pr_bundle_envelope=pr_bundle_envelope if isinstance(pr_bundle_envelope, Mapping) else None,
        caller_mode=final_mode,
        corroborated_authorities=corroborated_authorities,
    )
    environment_refs = _build_environment_refs(
        pr_bundle_envelope=pr_bundle_envelope if isinstance(pr_bundle_envelope, Mapping) else None,
        caller_repo_id=final_repo_id,
        caller_git_range=final_git_range,
        caller_commit_sha=final_commit_sha,
    )

    # W197 - merge MCP-receipt-derived ActorRefs into actor_refs,
    # deduping against the (kind, id) pairs already established above.
    # W285 - classify each merged ref through the same trust-tier pass
    # so MCP-receipt-mirrored ActorRefs aren't silently left at the
    # ``unknown`` default. The classifier sees the same corroboration
    # sets as ``_build_actor_refs`` did above so an MCP-receipt tool
    # whose tool_name appears in ``corroborated_tool_ids`` (it does -
    # the receipt walk seeds the set) lands at ``local_env`` rather
    # than ``unknown``. Refs that don't match any corroboration signal
    # (e.g. an mcp_client whose id isn't in any verified source) stay
    # ``unknown`` per the honest-noise contract.
    if mcp_receipt_actor_refs:
        existing_pairs: set[tuple[str, str]] = {(r.actor_kind, r.actor_id) for r in actor_refs}
        # Cache the env/git/run-ledger probes once - they're invariant
        # across the merged refs and re-reading would only cost cycles.
        _ci_env_detected = _detect_ci_env_id() is not None
        _ci_actor_id = _detect_ci_actor_id() if _ci_env_detected else None
        _git_email = _read_git_user_email()
        _run_ledger_actor = _read_run_ledger_actor()
        merged: list[ActorRef] = list(actor_refs)
        for ref in mcp_receipt_actor_refs:
            key = (ref.actor_kind, ref.actor_id)
            if key in existing_pairs:
                continue
            existing_pairs.add(key)
            tier = classify_actor_trust_tier(
                actor_id=ref.actor_id,
                actor_kind=ref.actor_kind,
                ci_env_detected=_ci_env_detected,
                ci_actor_id=_ci_actor_id,
                git_email=_git_email,
                run_ledger_actor=_run_ledger_actor,
                corroborated_tool_ids=corroborated_tool_ids,
                corroborated_actor_ids=corroborated_actor_ids,
            )
            merged.append(dataclasses.replace(ref, trust_tier=tier))
        actor_refs = tuple(merged)

    # W199 - assemble the unified artifacts tuple from every artifact
    # source. Order is stable for deterministic content hashing:
    # 1) audit-trail manifest, 2) cga predicates, 3) vuln-reach raw
    # envelopes, 4) test-impact raw envelopes, 5) mcp receipts.
    all_artifacts: list[EvidenceArtifact] = []
    if audit_trail_artifact is not None:
        all_artifacts.append(audit_trail_artifact)
    all_artifacts.extend(cga_artifacts)
    all_artifacts.extend(vuln_reach_artifacts)
    all_artifacts.extend(test_impact_artifacts)
    all_artifacts.extend(mcp_receipt_artifacts)

    # W199 - merge policy_decisions from rules + audit-trail sources.
    # W267 - extend with caller-supplied extras (constitution / permits /
    # leases scanners). Order is stable: rules first (W192), audit-trail
    # second (W195), extras last so a future producer adding more rows
    # appends rather than shifting the existing tail.
    all_policy_decisions: list[Mapping[str, Any]] = []
    all_policy_decisions.extend(rules_decisions)
    all_policy_decisions.extend(audit_trail_decisions)
    for idx, entry in enumerate(extra_policy_decisions):
        if not isinstance(entry, Mapping):
            warnings.append(f"extra_policy_decisions[{idx}]: expected dict, got {type(entry).__name__}; skipped")
            continue
        if not entry.get("rule_id"):
            warnings.append(f"extra_policy_decisions[{idx}]: missing rule_id; skipped")
            continue
        if not entry.get("decision"):
            warnings.append(f"extra_policy_decisions[{idx}]: missing decision; skipped")
            continue
        all_policy_decisions.append(entry)

    # W293 — Pattern-2 always-emit at the collector: every policy_decisions
    # row gets a ``provenance`` key. Producer-stamped values (constitution
    # / permit / lease / rule / audit-trail / github_review) are preserved
    # verbatim; legacy rows that arrived without provenance get
    # ``"unknown"`` so the wire field is ALWAYS present.
    _pd_unknown = provenance_label("unknown")
    _stamped_policy_decisions: list[Mapping[str, Any]] = []
    for row in all_policy_decisions:
        if isinstance(row, Mapping) and not row.get("provenance"):
            stamped = dict(row)
            stamped["provenance"] = _pd_unknown
            _stamped_policy_decisions.append(stamped)
        else:
            _stamped_policy_decisions.append(row)
    all_policy_decisions = _stamped_policy_decisions

    # W293 — Pattern-2 always-emit for approvals. ``pr-bundle
    # add-approval`` (CLI ingestion site) and the W247b github-reviews
    # parser already stamp the source-appropriate provenance label;
    # legacy approval dicts (or future shapes that forget the stamp)
    # land with ``"unknown"`` here so the wire shape is uniform.
    _appr_unknown = provenance_label("unknown")
    _stamped_approvals: list[Mapping[str, Any]] = []
    for row in bundle_approvals:
        if isinstance(row, Mapping) and not row.get("provenance"):
            stamped = dict(row)
            stamped["provenance"] = _appr_unknown
            _stamped_approvals.append(stamped)
        else:
            _stamped_approvals.append(row)
    bundle_approvals = tuple(_stamped_approvals)

    # W193 - merge bundle tests_required / tests_run with the
    # test-impact-derived extras. Dedup tests_required by string while
    # preserving order; tests_run are kept as-is (each entry is a
    # distinct run record).
    if extra_tests_required:
        merged_required: list[str] = list(bundle_tests_required)
        seen_tests: set[str] = set(merged_required)
        for t in extra_tests_required:
            if t in seen_tests:
                continue
            seen_tests.add(t)
            merged_required.append(t)
        final_tests_required: tuple[str, ...] = tuple(merged_required)
    else:
        final_tests_required = bundle_tests_required
    if extra_tests_run:
        final_tests_run: tuple[Mapping[str, Any], ...] = tuple(bundle_tests_run) + tuple(extra_tests_run)
    else:
        final_tests_run = bundle_tests_run

    packet_kwargs: dict[str, Any] = dict(
        evidence_id=evidence_id,
        repo_id=final_repo_id,
        git_range=final_git_range,
        commit_sha=final_commit_sha,
        diff_hash=final_diff_hash,
        run_ids=final_run_ids,
        agent_id=bundle_agent_id,
        human_actor=bundle_human_actor,
        mode=final_mode,
        started_at=final_started_at,
        completed_at=final_completed_at,
        verdict=bundle_verdict,
        risk_level=bundle_risk_level,
        context_refs=bundle_context_refs,
        changed_subjects=bundle_subjects,
        findings=tuple(findings),
        policy_decisions=tuple(all_policy_decisions),
        tests_required=final_tests_required,
        tests_run=final_tests_run,
        approvals=bundle_approvals,
        accepted_risks=bundle_accepted_risks,
        artifacts=tuple(all_artifacts),
        actor_refs=actor_refs,
        authority_refs=authority_refs,
        environment_refs=environment_refs,
        redactions=tuple(final_redactions),
        # W287 - stamp the real roam-code package version into every
        # collected packet. The dataclass field default stays ``None``
        # (so the W210 omit-when-default rule keeps existing golden
        # hashes byte-stable for hand-built / fixture packets); the
        # collector is the canonical producer site for real PR Replay
        # / PR Bundle packets, so the stamp lives here. Helper handles
        # ``importlib.metadata`` failure with the ``"unknown"`` sentinel
        # so collection never crashes on a malformed package install.
        roam_version=resolve_roam_version(),
        # W1234 - W210 change-scope timestamps + stale-evidence flag
        # producer wire-up. Defaults (``None`` / ``False`` / ``()``)
        # are omitted from canonical JSON per
        # ``_W210_OMIT_WHEN_DEFAULT_FIELDS`` so packets whose event
        # stream lacks classifiable entries stay byte-identical to
        # pre-W1234 packets.
        context_read_at=change_scope_context_read_at,
        edits_started_at=change_scope_edits_started_at,
        edits_completed_at=change_scope_edits_completed_at,
        evidence_stale=evidence_stale,
        stale_reasons=stale_reasons,
        # W1253 - packet-stamped config hashes (from RunMeta.extra at
        # run-start time via W1255-IMPL). None when the caller didn't
        # surface them; the W210 omit-when-default rule then keeps
        # canonical JSON byte-stable for pre-W1253 packets.
        rules_config_hash=_stamped_rules_config_hash,
        constitution_hash=_stamped_constitution_hash,
        control_map_hash=_stamped_control_map_hash,
    )
    if final_schema_version is not None:
        packet_kwargs["schema_version"] = final_schema_version

    packet = ChangeEvidence(**packet_kwargs)
    # Stamp the content hash so the packet is self-describing on the
    # wire. The memo's Phase 1 done-condition requires this.
    packet = packet.with_content_hash()
    return packet, warnings


# ---------------------------------------------------------------------------
# Helpers - dict navigation
# ---------------------------------------------------------------------------


def _nested(envelope: Mapping[str, Any], path: tuple[str, ...]) -> Any | None:
    """Walk a nested dict path, returning None on the first miss."""
    cur: Any = envelope
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


__all__ = [
    "collect_change_evidence",
    "resolve_risk_level_with_lineage",
    "RISK_LEVEL_LINEAGE_SOURCES",
]
