"""Code Graph Attestation predicate builder.

Per :mod:`roam.attest.__init__`:

* In-toto v1 Statement envelope.
* Predicate type ``https://roam-code.com/spec/CodeGraph/v1``.
* Merkle root over per-file symbol fingerprints.
* Edge bundle digest over the call/import edge set.

The predicate is structured so a downstream verifier can re-derive
both digests from the live DB and confirm a match in milliseconds —
no need to re-index, no source code in the attestation. Compliance
officer's dream, supply-chain scanner's contract.

OpenVEX correctness: the status set is the four spec-legal labels;
the justification set is the five spec-legal labels (never
``code_not_reachable``). Kept local so CGA import/verify stays independent
from the heavier taint-analysis engine.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# These are in-toto TypeURIs: stable IDENTIFIERS for the predicate shape,
# not fetchable documents. Per the in-toto v1 field-types spec a TypeURI
# "SHOULD resolve to a human-readable description, but MAY be
# unresolvable" — and today ``https://roam-code.com/spec/...`` returns 404.
# The human-readable description a reviewer actually wants is
# https://roam-code.com/docs/architecture (200), which documents the CGA
# and VSA predicate bodies.
#
# The strings themselves are frozen: every statement already signed carries
# them, and ``verify`` matches on exact equality, so changing one silently
# invalidates historical attestations. Do NOT "fix" a 404 by editing these —
# either publish the page at the existing IRI or keep pointing readers at
# the architecture doc. Earlier statements used the .dev domain; the
# verifier accepts both (see ``_LEGACY_PREDICATE_TYPES`` below).
PREDICATE_TYPE = "https://roam-code.com/spec/CodeGraph/v1"
# v12.2: fused CodeGraph + AIBOM predicate. Owns the "structurally bound
# AI authorship for tamper-evident codebases" lane that SLSA + SPDX +
# CycloneDX 1.7 + OpenVEX leave gapped. Reference impl candidate for
# the in-toto attestation registry.
PREDICATE_TYPE_AIBOM = "https://roam-code.com/spec/CodeGraph-AIBOM/v1"

# Legacy IRIs accepted by the verifier so statements signed before
# the .dev → .com migration still verify cleanly. Emitter never
# uses these; they are read-only compatibility shims.
_LEGACY_PREDICATE_TYPES = (
    "https://roam-code.dev/CodeGraph/v1",
    "https://roam-code.dev/CodeGraph-AIBOM/v1",
)

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SCHEMA_VERSION = "1"

# ---------------------------------------------------------------------------
# Graph-builder identity
# ---------------------------------------------------------------------------
#
# A CGA binds two digests to a commit: the symbol merkle root and the edge
# bundle. Both are derived from the INDEX, not from the source — so they move
# whenever the graph builder changes, even on a byte-identical tree at a fixed
# commit. Measured instance: a case-fold fix in the symbol resolver stopped it
# fabricating ~6,400 import edges; every attestation signed before that fix
# then failed against a post-fix index at the SAME commit on a CLEAN tree with
# ``edge_bundle_digest mismatch — edges changed since signing``.
#
# That message is true and useless. "Edges changed since signing" is exactly
# what a verifier prints when someone has altered the evidence, so a tool
# upgrade and an attack were rendered identically. Only one of those is a
# security event, and the reader could not tell which they had.
#
# ``GRAPH_RESOLVER_VERSION`` is the manually-owned half of the builder
# identity: bump it whenever the SEMANTICS of symbol or edge construction
# change in a way that can move the digests on unchanged source — resolver
# fixes, new edge kinds, changed qualified-name shapes, changed dedup rules.
# Do NOT bump it for a release that leaves graph construction alone; a
# needless bump costs real tamper-detection sharpness (see
# ``_describe_builder_drift`` for why).
#
# The automatic half comes from ``index_manifest`` (parser/grammar/extractor/
# bridge versions + index schema version), which the indexer already stamps
# per run. That half needs no discipline: an extractor bump is caught even if
# nobody remembers this constant exists.
GRAPH_RESOLVER_VERSION = "1"

# Verification states. Deliberately four, not two — see
# ``verify_cga_statement_state``.
VERIFY_STATE_VERIFIED = "verified"
VERIFY_STATE_MISMATCH = "mismatch"
VERIFY_STATE_BUILDER_DRIFT = "graph_builder_drift"
VERIFY_STATE_UNVERIFIABLE = "unverifiable"

# Third value for ``git_dirty_hash``, alongside ``None`` (tree is clean) and a
# sha256 hex digest (tree is dirty): the probe could not run at all — no git
# binary, not a repo, ``git status`` non-zero, or the call timed out.
#
# Before this existed, all three collapsed onto ``None``, so "I could not
# determine the tree state" was indistinguishable from "I checked, it is
# clean". That single overload produced a defect in BOTH directions:
#
#   * verify, predicate signed dirty + probe unavailable → the verifier
#     asserted "the live working tree is clean now" and returned MISMATCH,
#     i.e. it raised a tamper-shaped alarm out of an answer it did not have;
#   * verify, predicate signed clean + probe unavailable → ``None == None``
#     compared equal and the statement came back VERIFIED, so a CGA could
#     certify a clean tree on a box where the tree was never inspected;
#   * emit, probe unavailable → the ``--allow-dirty`` gate saw ``None``,
#     read it as clean, and signed a predicate asserting a clean tree.
#
# The literal is the same "unknown" sentinel this module already uses for an
# undeterminable subject SHA (see ``build_cga_statement`` and the
# ``subject_sha != "unknown"`` guard in ``verify_cga_statement``), and it can
# never collide with a real value: a dirty tree always hashes to 64 hex chars.
DIRTY_HASH_UNKNOWN = "unknown"

# Prefix stamped on every error that reports a MISSING answer rather than a
# WRONG one, so ``classify_verification_state`` can route it to
# :data:`VERIFY_STATE_UNVERIFIABLE` instead of accusing anyone of tampering.
_UNKNOWN_PREFIX = "environment_unknown"

# Prefix stamped on every reclassified fingerprint error so text-mode
# consumers (and greps in CI logs) can separate "the toolchain moved" from
# "the evidence moved".
_DRIFT_PREFIX = "graph_builder_drift"

# OpenVEX justification strings and status labels advertised by CGA predicates.
# These match the taint engine's emitted labels without importing that engine.
OPENVEX_JUSTIFICATIONS: frozenset[str] = frozenset(
    {
        "component_not_present",
        "vulnerable_code_not_present",
        "vulnerable_code_not_in_execute_path",
        "vulnerable_code_cannot_be_controlled_by_adversary",
        "inline_mitigations_already_exist",
    }
)
OPENVEX_STATUSES: frozenset[str] = frozenset({"not_affected", "affected", "fixed", "under_investigation"})


def _git_commit_sha(root: Path) -> str | None:
    """Return the HEAD commit SHA, or ``None`` outside a git repo."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        # No git binary / not a repo / timed out — None is the documented "no SHA" sentinel.
        return None
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    return sha or None


def _git_dirty_hash(root: Path) -> str | None:
    """Tri-state probe of the working tree, for the predicate's dirty binding.

    Returns exactly one of:

    * ``None`` — ``git status --porcelain`` ran and reported nothing. The
      tree is CLEAN, and that is an observation, not an assumption.
    * a 64-char sha256 hex digest — the tree is DIRTY, and this digest
      pins which uncommitted state it was.
    * :data:`DIRTY_HASH_UNKNOWN` — the probe could not run. No git binary,
      not a repository, ``git status`` exited non-zero, or it timed out.

    The third value is the whole point. It used to be ``None`` as well, which
    made "not inspected" indistinguishable from "inspected and clean" for
    every downstream reader — see :data:`DIRTY_HASH_UNKNOWN` for the three
    concrete failures that overload produced. Callers must treat
    :data:`DIRTY_HASH_UNKNOWN` as an absence of evidence and fail closed on
    it; they must never let it satisfy a clean check.
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        # Missing git / inaccessible repo / timeout. Soft on the CALL — it
        # does not raise — but not soft on the ANSWER: we learned nothing
        # about the tree, and saying "clean" here would be a fabrication.
        return DIRTY_HASH_UNKNOWN
    if proc.returncode != 0:
        return DIRTY_HASH_UNKNOWN
    out = proc.stdout
    if not out.strip():
        return None  # observed clean
    return hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()


def _strip_url_credentials(url: str) -> str:
    """Remove ``username:token@`` or ``token@`` userinfo from an HTTP(S) URL.

    A repo cloned with ``https://x:ghp_PERSONAL_TOKEN@github.com/owner/repo``
    would otherwise leak the token verbatim into ``subject.name`` of every
    signed CGA. We rewrite to ``https://github.com/owner/repo`` so the
    statement carries the repo identity but not the cloning credential.
    SSH URLs (``git@host:owner/repo``) are left untouched — the ``git@``
    prefix is conventional, not a credential.

    R9 security recheck #2: previously used ``rpartition("@")`` on the
    whole post-``://`` string, which finds the LAST ``@`` anywhere in
    the URL. A legitimate URL like
    ``https://github.com/owner/repo?reviewer=a@b.com`` would get
    rewritten to ``https://b.com`` — wrong subject in every signed CGA.
    Fix: per RFC 3986 §3, the userinfo segment is only inside the
    authority — between ``://`` and the first ``/``. Slice the
    authority first, then strip credentials from THAT slice only.
    """
    # SSH form ``user@host:path`` — leave alone.
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    # Authority = everything up to the first ``/``. Path/query/fragment
    # may legitimately contain ``@`` (email addresses in query strings,
    # for example) and MUST NOT be touched.
    if "/" in rest:
        authority, slash, path = rest.partition("/")
    else:
        authority, slash, path = rest, "", ""
    if "@" in authority:
        # Strip userinfo from the authority only.
        _userinfo, _, host = authority.rpartition("@")
        authority = host
    return f"{scheme}://{authority}{slash}{path}"


def _git_remote_url(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        # Remote URL is optional metadata; callers fall back to the project path.
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    if not url:
        return None
    return _strip_url_credentials(url)


def _hash_hex(items: list[bytes]) -> str:
    """SHA-256 hex over a sequence of byte payloads, length-prefixed for
    domain separation. Stable across Python versions and platforms.
    """
    h = hashlib.sha256()
    for chunk in items:
        h.update(len(chunk).to_bytes(4, "big"))
        h.update(chunk)
    return h.hexdigest()


def _symbol_fingerprints(conn) -> tuple[str, int]:
    """Compute the symbol Merkle root and total symbol count.

    For every symbol we hash ``(qualified_name, kind, signature, file_path)``.
    Sorted by ``symbols.id`` for determinism. The Merkle is a flat
    ``sha256`` of the concatenated per-symbol digests — a multi-level
    tree adds no value at our scales (~14k symbols at most), and the
    flat digest is auditable byte-for-byte.
    """
    rows = conn.execute(
        "SELECT s.id, s.qualified_name, s.name, s.kind, s.signature, "
        "       f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY s.id"
    ).fetchall()
    chunks: list[bytes] = []
    for r in rows:
        qname = r[1] or r[2] or ""
        kind = r[3] or ""
        sig = r[4] or ""
        path = (r[5] or "").replace("\\", "/")
        payload = f"{qname}\x00{kind}\x00{sig}\x00{path}".encode("utf-8")
        chunks.append(payload)
    return _hash_hex(chunks), len(chunks)


def _edge_bundle_digest(conn) -> tuple[str, int]:
    """Hash the call/import/inherits/template edge set.

    Sorted by ``(source_id, target_id, kind, id)`` so re-running on the
    same DB produces the same digest. The trailing ``id`` is the
    SQLite rowid alias (``edges.id INTEGER PRIMARY KEY AUTOINCREMENT``)
    and acts as the canonical tiebreaker for the W1285 sort-stability
    fix: the ``edges`` table has no UNIQUE constraint on
    ``(source_id, target_id, kind)``, so the indexer legitimately
    writes duplicate triples (e.g. two ``calls`` edges from the same
    caller to the same callee on different lines). Without the
    tiebreaker, two fresh sqlite3 connections could return tied rows
    in different orders depending on planner choice + ``sqlite_stat1``
    state, breaking the CGA emit→verify round-trip with an
    ``edge_bundle_digest mismatch``. Adding ``id`` is purely additive
    on dup-free DBs (tiebreaker never consulted) and canonical on
    dup-bearing DBs. Mirrors ``_symbol_fingerprints``' ``ORDER BY s.id``
    discipline above.
    """
    rows = conn.execute(
        "SELECT source_id, target_id, kind FROM edges ORDER BY source_id, target_id, kind, id"
    ).fetchall()
    chunks: list[bytes] = []
    for r in rows:
        chunks.append(f"{r[0]}->{r[1]}:{r[2] or ''}".encode("utf-8"))
    return _hash_hex(chunks), len(chunks)


def _canonical_json_blob(raw: Any) -> Any:
    """Re-parse a JSON TEXT column so key order can't fake a version change.

    ``index_manifest`` stores component/parser maps as JSON strings. Two runs
    of the same toolchain can serialise the same map with different key order,
    which would hash differently and manufacture a phantom builder drift.
    Parse then re-serialise canonically; fall back to the raw string when the
    column isn't parseable (older rows, hand-edited DBs).
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def graph_builder_identity(conn) -> dict[str, Any]:
    """Identity of the machinery that BUILT the graph the digests cover.

    Two halves, deliberately:

    * ``resolver_version`` — :data:`GRAPH_RESOLVER_VERSION`, hand-owned,
      covers core resolver semantics that nothing else versions.
    * ``index_schema_version`` + ``component_digest`` — derived from the
      newest ``index_manifest`` row, covering the index schema and every
      parser / grammar / extractor / bridge / detector version the indexer
      stamped. Free of maintainer discipline.

    Missing or unreadable manifest fields come back as ``None``. ``None``
    means UNKNOWN, never "equal": :func:`_describe_builder_drift` refuses to
    declare drift from an unknown, so a wiped manifest can never soften a
    verdict.
    """
    identity: dict[str, Any] = {
        "resolver_version": GRAPH_RESOLVER_VERSION,
        "index_schema_version": None,
        "component_digest": None,
    }
    try:
        row = conn.execute(
            "SELECT schema_version, parser_versions, grammar_versions, component_versions "
            "FROM index_manifest ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        # No index_manifest table (synthetic/unit DBs) or an unreadable one.
        # Fail soft to UNKNOWN — which fails CLOSED at comparison time.
        return identity
    if not row:
        return identity
    try:
        identity["index_schema_version"] = int(row[0]) if row[0] is not None else None
    except (TypeError, ValueError):
        identity["index_schema_version"] = None
    payload = json.dumps(
        {
            "parser_versions": _canonical_json_blob(row[1]),
            "grammar_versions": _canonical_json_blob(row[2]),
            "component_versions": _canonical_json_blob(row[3]),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    identity["component_digest"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return identity


_IDENTITY_FIELDS = ("resolver_version", "index_schema_version", "component_digest")


def _describe_builder_drift(signed: Any, live: dict[str, Any]) -> str | None:
    """Return a description of the builder change, or ``None`` for "same".

    Security contract — this function is the only thing standing between a
    tamper verdict and a softer one, so it is written to say ``None``
    (i.e. "treat as the same builder, use the strict wording") in every
    ambiguous case:

    * predicate carries no ``graph_builder`` block (every statement signed
      before this field existed) → ``None``. A missing field cannot buy
      leniency, so old attestations keep exactly the verdict they had.
    * a field is absent or ``None`` on EITHER side → that field is not
      comparable and is skipped. Deleting a field never creates drift.
    * no field was comparable at all → ``None``.

    Only a field present and populated on BOTH sides, with different values,
    counts. Note also what drift does NOT do: it never turns a failure into a
    pass, so the worst an attacker gains by forging this block is a different
    LABEL on a result that still fails and still exits non-zero — and forging
    it breaks the cosign signature over the predicate.
    """
    if not isinstance(signed, dict):
        return None
    changed: list[str] = []
    for field in _IDENTITY_FIELDS:
        was, now = signed.get(field), live.get(field)
        if was is None or now is None:
            continue
        if str(was) != str(now):
            was_s, now_s = str(was), str(now)
            if field == "component_digest":
                was_s, now_s = was_s[:12] + "…", now_s[:12] + "…"
            changed.append(f"{field} {was_s} → {now_s}")
    if not changed:
        return None
    return "; ".join(changed)


def _language_summary(conn) -> dict[str, int]:
    rows = conn.execute(
        "SELECT language, COUNT(*) FROM files WHERE language IS NOT NULL GROUP BY language ORDER BY COUNT(*) DESC"
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def build_cga_predicate(
    conn,
    *,
    project_root: Path,
    tool_version: str | None = None,
    taint_findings: list | None = None,
    dirty_hash: str | None = None,
) -> dict[str, Any]:
    """Build the predicate body for the Code Graph Attestation.

    Pure function over a read-only DB connection — no signing, no I/O
    beyond ``git rev-parse`` for the commit SHA. The caller wraps the
    return value in an in-toto Statement via :func:`build_cga_statement`.

    When *taint_findings* is supplied (the output of
    :func:`roam.security.taint_engine.run_taint`), each finding is
    converted to a ``reachability_claim`` with a spec-legal OpenVEX
    status + justification. This closes the v12 compliance chain:
    every CGA predicate can now ship signed evidence that "the
    sanitized paths were verified by graph-reach taint analysis."
    """
    merkle, n_symbols = _symbol_fingerprints(conn)
    edges_digest, n_edges = _edge_bundle_digest(conn)
    languages = _language_summary(conn)

    reachability_claims = [_taint_finding_to_claim(f) for f in taint_findings] if taint_findings else []

    return {
        "schema_version": SCHEMA_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "merkle_root": merkle,
        "edge_bundle_digest": edges_digest,
        "symbol_count": n_symbols,
        "edge_count": n_edges,
        # Working-tree state at sign time, tri-valued exactly as
        # ``_git_dirty_hash`` returns it: ``None`` = observed clean, a sha256
        # of ``git status --porcelain`` = dirty and pinned to that state,
        # ``DIRTY_HASH_UNKNOWN`` = the probe could not run and this statement
        # therefore asserts NOTHING about the tree. The verifier re-derives
        # the live value and refuses on mismatch, so a CGA that asserts clean
        # cannot quietly be produced on a dirty tree; and because the unknown
        # is carried explicitly rather than flattened to ``None``, it cannot
        # quietly be produced on an uninspected one either.
        "git_dirty_hash": dirty_hash,
        # Identity of the graph builder that produced the two digests above.
        # Without it a verifier cannot tell "roam's resolver was upgraded"
        # from "someone edited the evidence" — both present as a digest that
        # moved at a fixed commit on a clean tree. With it, the verifier
        # reports those as separate states.
        "graph_builder": graph_builder_identity(conn),
        "languages": languages,
        "tool": {
            "name": "roam-code",
            "version": tool_version or _detect_tool_version(),
        },
        # Compliance lattice declared in the predicate so downstream
        # verifiers know which OpenVEX labels are spec-legal and don't
        # have to import roam internals.
        "openvex_status_set": sorted(OPENVEX_STATUSES),
        "openvex_justification_set": sorted(OPENVEX_JUSTIFICATIONS),
        # Empty when --include-taint isn't passed; populated by
        # roam taint output otherwise. Each entry is OpenVEX-shaped so
        # CycloneDX/OpenVEX consumers can ingest directly.
        "reachability_claims": reachability_claims,
    }


def _taint_finding_to_claim(finding) -> dict[str, Any]:
    """Map one TaintFinding to an OpenVEX-shaped reachability claim.

    Status + justification mapping (verified spec-legal):

    * Sanitizer in path → ``status=not_affected``, justification
      ``inline_mitigations_already_exist``.
    * No sanitizer (reaches sink unsanitized) → ``status=affected``;
      justification field is intentionally absent (justification only
      applies to ``not_affected``).

    The ``vulnerability`` slot is the rule's CWE id — downstream
    consumers can map CWE→CVE via their own intel feeds. Inline
    rule_id is preserved for traceability.
    """
    is_sanitized = bool(getattr(finding, "sanitizer_in_path", False))
    status = "not_affected" if is_sanitized else "affected"
    justification = "inline_mitigations_already_exist" if is_sanitized else None

    claim: dict[str, Any] = {
        "vulnerability": getattr(finding, "cwe", "") or getattr(finding, "rule_id", ""),
        "rule_id": getattr(finding, "rule_id", ""),
        "status": status,
        "evidence": {
            "source": dict(getattr(finding, "source_symbol", {}) or {}),
            "sink": dict(getattr(finding, "sink_symbol", {}) or {}),
            "path_length": len(getattr(finding, "path_symbols", []) or []),
            "sanitizer_in_path": is_sanitized,
        },
    }
    if justification is not None:
        claim["justification"] = justification
    return claim


def build_cga_statement(
    conn,
    *,
    project_root: Path,
    tool_version: str | None = None,
    taint_findings: list | None = None,
    include_aibom: bool = False,
) -> dict[str, Any]:
    """Build the full in-toto v1 Statement wrapping the CGA predicate.

    Statement shape:
        {
          "_type": "https://in-toto.io/Statement/v1",
          "predicateType": "https://roam-code.com/spec/CodeGraph/v1",
                              # → CodeGraph-AIBOM/v1 when include_aibom=True
          "subject": [{"name": "...", "digest": {...}}],
          "predicate": {...}
        }

    With ``include_aibom=True``, the predicate type promotes to
    ``CodeGraph-AIBOM/v1`` and embeds an ``aibom`` block binding
    AI-authored commits to the indexed symbols they touched. Required
    for EU AI Act Art. 50 disclosure (effective 2026-08-02).
    """
    sha = _git_commit_sha(project_root) or "unknown"
    remote = _git_remote_url(project_root)
    subject_name = remote or str(project_root.resolve()).replace("\\", "/")
    subject = {
        "name": subject_name,
        "digest": {"git_commit_sha1": sha},
    }
    dirty_hash = _git_dirty_hash(project_root)
    predicate = build_cga_predicate(
        conn,
        project_root=project_root,
        tool_version=tool_version,
        taint_findings=taint_findings,
        dirty_hash=dirty_hash,
    )
    predicate_type = PREDICATE_TYPE
    if include_aibom:
        try:
            from roam.security.aibom_extension import (
                aibom_block_incomplete_reason,
                build_aibom_block,
            )

            block = build_aibom_block(project_root, conn)
            predicate["aibom"] = block
            predicate_type = PREDICATE_TYPE_AIBOM
            # A block that could not compute one of its symbol bindings is
            # still emitted, but it must not be signed as a complete AIBOM
            # without the same disclosure an outright failure would get.
            reason = aibom_block_incomplete_reason(block)
            if reason:
                predicate["aibom_error"] = reason
        except Exception as exc:
            predicate["aibom_error"] = str(exc)
    return {
        "_type": STATEMENT_TYPE,
        "predicateType": predicate_type,
        "subject": [subject],
        "predicate": predicate,
    }


def serialize_statement(statement: dict[str, Any]) -> str:
    """Canonical JSON serialisation — deterministic for hashing."""
    return json.dumps(statement, sort_keys=True, separators=(",", ":"))


def _extract_subject_sha(subject_list: Any) -> str | None:
    """Return ``subject[0].digest.git_commit_sha1`` if it exists, else None."""
    if not subject_list or not isinstance(subject_list[0], dict):
        return None
    return (subject_list[0].get("digest") or {}).get("git_commit_sha1")


def _describe_dirty_hash_mismatch(predicate_dirty: Any, live_dirty: Any) -> str | None:
    """Return a human-readable mismatch reason, or None when they match.

    An :data:`DIRTY_HASH_UNKNOWN` on either side is tested BEFORE equality,
    and both orderings matter:

    * Before equality, because two unknowns are not a match. ``"unknown" ==
      "unknown"`` is two absent answers agreeing about nothing, and letting
      that fall through would hand back ``None`` — the "they match" reply —
      and verify the statement on the strength of a question nobody answered.
    * At all, because the alternative is worse than useless: with the live
      side unknown, the old code reached the ``predicate_dirty is not None and
      live_dirty is None`` branch and reported "the live working tree is clean
      now" — a positive factual claim about a tree it had failed to inspect,
      rendered in the wording reserved for evidence that moved.

    So an unknown returns an :data:`_UNKNOWN_PREFIX` error instead: still an
    error, so the verify still fails, but classified as an answer we do not
    have rather than an accusation we are not entitled to make.
    """
    if DIRTY_HASH_UNKNOWN in (predicate_dirty, live_dirty):
        if predicate_dirty == DIRTY_HASH_UNKNOWN and live_dirty == DIRTY_HASH_UNKNOWN:
            side = "neither the signer nor this verifier was able to run"
        elif predicate_dirty == DIRTY_HASH_UNKNOWN:
            side = "the signer was unable to run"
        else:
            side = "this verifier was unable to run"
        return (
            f"{_UNKNOWN_PREFIX}: git_dirty_hash could not be established — "
            f"{side} `git status --porcelain` (no git binary, not a "
            "repository, non-zero exit, or timeout), so the working tree was "
            "never inspected. This is NOT evidence of tampering and NOT a "
            "clean tree; it is an absent answer, and the verify fails closed "
            "on it. Re-run where git is available to get a real comparison."
        )
    if predicate_dirty == live_dirty:
        return None
    if predicate_dirty is None and live_dirty is not None:
        return (
            "git_dirty_hash mismatch — predicate asserts clean tree, but the live working tree has uncommitted changes"
        )
    if predicate_dirty is not None and live_dirty is None:
        return (
            "git_dirty_hash mismatch — predicate was signed against a "
            "dirty tree, but the live working tree is clean now"
        )
    return (
        "git_dirty_hash mismatch — predicate's dirty-tree digest "
        "does not match the live working tree's uncommitted state"
    )


def _check_graph_fingerprints(
    predicate: dict[str, Any],
    expected_merkle: str,
    expected_edges: str,
    n_symbols: int,
    n_edges: int,
) -> list[str]:
    """Compare the signed graph fingerprints against the live DB values.

    Wording here is the TAMPER wording, and it is correct only when the same
    graph builder produced both sides. Callers that have established builder
    drift pass the result through :func:`_reclassify_as_builder_drift`.
    """
    mismatches: list[str] = []
    if predicate.get("merkle_root") != expected_merkle:
        mismatches.append("merkle_root mismatch — symbols changed since signing")
    if predicate.get("edge_bundle_digest") != expected_edges:
        mismatches.append("edge_bundle_digest mismatch — edges changed since signing")
    if int(predicate.get("symbol_count") or 0) != n_symbols:
        mismatches.append(f"symbol_count mismatch: got {predicate.get('symbol_count')}, live={n_symbols}")
    if int(predicate.get("edge_count") or 0) != n_edges:
        mismatches.append(f"edge_count mismatch: got {predicate.get('edge_count')}, live={n_edges}")
    return mismatches


def _reclassify_as_builder_drift(mismatches: list[str], drift_reason: str) -> list[str]:
    """Re-attribute fingerprint mismatches to a graph-builder change.

    Nothing is dropped and nothing is forgiven — every mismatch still appears,
    still carries its live-vs-signed numbers, and still fails the verify. The
    only thing that changes is the CAUSE the message asserts, because
    "edges changed since signing" is an accusation the verifier is not
    entitled to make once it knows the builder moved underneath it.
    """
    out: list[str] = []
    for line in mismatches:
        field, _, detail = line.partition(" mismatch")
        detail = detail.lstrip(" —:").strip()
        rendered = f"{_DRIFT_PREFIX}: {field} differs, but the graph builder changed since signing ({drift_reason})"
        # Keep the concrete numbers from count mismatches; drop the causal
        # clauses ("symbols changed since signing") that we just disowned.
        if detail.startswith("got "):
            rendered += f" — {detail}"
        out.append(
            rendered + ". This is toolchain drift, NOT evidence of tampering. Re-emit the attestation at this commit "
            "with the current toolchain to restore a comparable baseline."
        )
    return out


def _no_builder_identity_note(predicate: dict[str, Any]) -> list[str]:
    """Advisory emitted when a fingerprint moved on an identity-less predicate.

    Statements signed before ``graph_builder`` existed carry no builder
    identity, so the verifier genuinely cannot tell a toolchain upgrade from
    tampering. It says so rather than implying the stronger of the two. The
    strict mismatch lines are still present and the verify still fails.
    """
    if "graph_builder" in predicate:
        return []
    return [
        "note: this attestation predates graph-builder identity "
        "(no `graph_builder` in the predicate), so the verifier cannot "
        "distinguish a roam graph-builder upgrade from tampering. Re-emit at "
        "this commit with the current toolchain to get that distinction."
    ]


def classify_verification_state(errors: list[str]) -> str:
    """Map a :func:`verify_cga_statement` error list onto FOUR states.

    * :data:`VERIFY_STATE_VERIFIED` — no errors.
    * :data:`VERIFY_STATE_BUILDER_DRIFT` — every error is a fingerprint
      difference that :func:`_reclassify_as_builder_drift` attributed to a
      graph-builder change. The signed digests are no longer comparable, so
      the verifier can make NO claim about this graph — it can neither
      confirm it nor accuse anyone of altering it.
    * :data:`VERIFY_STATE_UNVERIFIABLE` — no error is a definite discrepancy
      and at least one is an :data:`_UNKNOWN_PREFIX` absence: a probe the
      verifier needed could not be run, so a property went unchecked.
    * :data:`VERIFY_STATE_MISMATCH` — anything else. That includes digests
      that moved under a FIXED builder (the real tamper signal) and every
      environmental failure (wrong commit, dirty tree, bad predicate type).

    Why UNVERIFIABLE is its own state and not folded into either neighbour:

    Folding it into VERIFIED is the fail-open half of the bug this state was
    added to close — it would certify a property nobody measured. Folding it
    into MISMATCH is the fail-closed half, and no less wrong: MISMATCH is the
    tamper channel, and spending it on "my CI image has no git" is how a
    tamper alarm gets trained out of its reader. The distinction UNVERIFIABLE
    draws is the one the verifier can actually support — it knows it did not
    look, and it says exactly that.

    MISMATCH keeps its priority over both softer states: a single definite
    discrepancy re-asserts it no matter how many unknowns sit beside it, so an
    unknown can never launder a wrong commit or a real digest move.

    Why drift is its own state and NOT a pass:

    A version field that let a mismatch through would be a hole you could
    drive an attack through — claim a version bump, get waved past. So drift
    is a REFUSAL, not an excuse. ``ok`` stays False, ``roam cga verify``
    still exits 5, CI still blocks. The only thing that changes is the cause
    the message asserts.

    Why drift is NOT simply "tampered":

    Because it isn't, and saying so burns the signal. A verifier that cries
    tamper on every toolchain upgrade trains its readers to ignore it, which
    costs exactly the alarm you wanted when a real one fires.

    Note the all-or-nothing rule: ONE environmental error and the verdict is
    MISMATCH again. A builder bump explains digests; it explains nothing
    about provenance, so it can never launder a wrong commit or a dirty tree.
    """
    if not errors:
        return VERIFY_STATE_VERIFIED
    if all(e.startswith(_DRIFT_PREFIX + ":") for e in errors):
        return VERIFY_STATE_BUILDER_DRIFT
    # Drift and unknown are both "cannot claim" errors, so a mix of the two
    # still supports no accusation. Report the weaker, more honest of the two.
    soft = (_DRIFT_PREFIX + ":", _UNKNOWN_PREFIX + ":")
    if all(e.startswith(soft) for e in errors):
        return VERIFY_STATE_UNVERIFIABLE
    return VERIFY_STATE_MISMATCH


def verify_cga_statement_state(
    statement: dict[str, Any],
    conn,
    *,
    project_root: Path,
) -> tuple[str, list[str]]:
    """Verify *statement* and return ``(state, errors)``.

    Front door over :func:`verify_cga_statement`, for callers that need to
    tell a graph-builder upgrade — or a probe that could not run — apart from
    tampering. See :func:`classify_verification_state` for what the states
    mean and why neither soft state is a pass.
    """
    _ok, errors = verify_cga_statement(statement, conn, project_root=project_root)
    return classify_verification_state(errors), errors


def verify_cga_statement(
    statement: dict[str, Any],
    conn,
    *,
    project_root: Path,
) -> tuple[bool, list[str]]:
    """Re-derive both digests from the live DB and compare to *statement*.

    Returns ``(ok, errors)``. The list is empty on success; otherwise it
    enumerates every mismatch for the verifier to surface.

    Graph-builder drift is NOT ok here: it returns ``False`` exactly like a
    mismatch, so no caller can be tricked into accepting a drifted
    attestation. Callers that want to act on the distinction should use
    :func:`verify_cga_statement_state` / :func:`classify_verification_state`.
    """
    if not isinstance(statement, dict):
        return False, ["statement is not a JSON object"]
    errors: list[str] = []
    if statement.get("_type") != STATEMENT_TYPE:
        errors.append(f"_type mismatch: got {statement.get('_type')!r}, expected {STATEMENT_TYPE!r}")
    accepted_types = (PREDICATE_TYPE, PREDICATE_TYPE_AIBOM, *_LEGACY_PREDICATE_TYPES)
    if statement.get("predicateType") not in accepted_types:
        errors.append(
            f"predicateType mismatch: got {statement.get('predicateType')!r}, expected one of {accepted_types!r}"
        )
    predicate = statement.get("predicate") or {}
    if not isinstance(predicate, dict):
        return False, errors + ["predicate is not a JSON object"]

    expected_merkle, n_symbols = _symbol_fingerprints(conn)
    expected_edges, n_edges = _edge_bundle_digest(conn)
    fingerprint_errors = _check_graph_fingerprints(predicate, expected_merkle, expected_edges, n_symbols, n_edges)

    # Attribution, not absolution: the fingerprint failures below stay
    # failures either way. ``drift_reason`` only decides which cause the
    # message is allowed to assert.
    drift_reason = None
    if fingerprint_errors:
        drift_reason = _describe_builder_drift(predicate.get("graph_builder"), graph_builder_identity(conn))
    if drift_reason:
        errors.extend(_reclassify_as_builder_drift(fingerprint_errors, drift_reason))
    else:
        errors.extend(fingerprint_errors)
        if fingerprint_errors:
            errors.extend(_no_builder_identity_note(predicate))
    # Everything appended past this point is environmental — never
    # attributable to the graph builder — so it keeps the verdict at
    # MISMATCH via ``classify_verification_state``'s all-or-nothing rule.

    # Subject git_commit_sha1 — the statement claims it was signed against
    # commit X. Refuse if the live tree is at commit Y. Older statements
    # without a usable subject digest (sha == "unknown") are skipped to
    # preserve forward compat with pre-bind statements; emitted statements
    # always carry a usable SHA when in a git repo.
    subject_sha = _extract_subject_sha(statement.get("subject"))
    live_sha = _git_commit_sha(project_root)
    if subject_sha and subject_sha != "unknown" and live_sha and subject_sha != live_sha:
        errors.append(
            f"git_commit_sha1 mismatch — statement signed against {subject_sha[:12]}…, live tree is at {live_sha[:12]}…"
        )

    # Predicate git_dirty_hash — refuse if predicate claims clean but live
    # tree is dirty, or vice versa. Pre-bind statements without the field
    # get a soft note (forward compat); newly-emitted ones always include it.
    if "git_dirty_hash" in predicate:
        dirty_error = _describe_dirty_hash_mismatch(predicate.get("git_dirty_hash"), _git_dirty_hash(project_root))
        if dirty_error:
            errors.append(dirty_error)

    return not errors, errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_tool_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("roam-code")
    except PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# Cosign signing (optional — graceful skip when binary or env is missing)
# ---------------------------------------------------------------------------


@dataclass
class CosignResult:
    """Outcome of a cosign signing attempt."""

    signed: bool
    statement_path: Path
    signature_path: Path | None = None
    certificate_path: Path | None = None
    bundle_path: Path | None = None
    skipped_reason: str = ""
    cosign_version: str = ""


def cosign_available() -> tuple[bool, str]:
    """Return ``(installed, version_string)``. Empty version when missing."""
    try:
        proc = subprocess.run(
            ["cosign", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return False, ""
    if proc.returncode != 0:
        return False, ""
    line = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
    return True, line.strip()


def _sign_blob_args(
    statement_path: Path,
    sig_path: Path,
    bundle_path: Path,
    cert_path: Path | None,
    key_path: Path | None,
) -> list[str]:
    """Assemble the ``cosign sign-blob`` argv for one signing mode.

    Isolated so signing modes (keyless OIDC vs. offline keypair) can
    diverge without touching the caller's outcome handling.
    """
    args = [
        "cosign",
        "sign-blob",
        "--yes",
        str(statement_path),
        "--output-signature",
        str(sig_path),
        "--bundle",
        str(bundle_path),
    ]
    if cert_path is not None:
        # Keyless: cosign uses ambient OIDC if available
        # (GitHub Actions, GCP workload identity, etc.).
        args.extend(["--output-certificate", str(cert_path)])
    if key_path is not None:
        # Offline keypair
        args.extend(["--key", str(key_path)])
    return args


def _result_if_artifacts_landed(
    statement_path: Path,
    sig_path: Path,
    bundle_path: Path,
    cert_path: Path | None,
    version_str: str,
) -> CosignResult:
    """Turn cosign's exit-0 into a verdict grounded in on-disk artifacts.

    Pattern-2 discipline: cosign exited 0 but downstream verifiers need
    an on-disk signature OR bundle to actually verify. If neither
    landed, we MUST NOT report ``signed=True`` (silent success on
    degraded resolution — the canonical Pattern-2 anti-pattern). This
    only fires when cosign's exit status disagrees with its file output
    (write race, exotic filesystem, output_dir permissions). The
    well-behaved path (which the test suite exercises) always lands
    both files and keeps the existing contract.
    """
    sig_present = sig_path.exists()
    bundle_present = bundle_path.exists()
    if not sig_present and not bundle_present:
        return CosignResult(
            signed=False,
            statement_path=statement_path,
            skipped_reason=(
                f"cosign exit 0 but neither signature nor bundle landed on disk "
                f"(expected {sig_path.name!r} and/or {bundle_path.name!r})"
            ),
            cosign_version=version_str,
        )
    return CosignResult(
        signed=True,
        statement_path=statement_path,
        signature_path=sig_path if sig_present else None,
        certificate_path=cert_path if cert_path and cert_path.exists() else None,
        bundle_path=bundle_path if bundle_present else None,
        cosign_version=version_str,
    )


def cosign_sign_statement(
    statement_path: Path,
    *,
    key_path: Path | None = None,
    keyless: bool = False,
    output_dir: Path | None = None,
) -> CosignResult:
    """Sign *statement_path* with cosign.

    Three modes:

    * ``key_path`` set → offline signing with a local keypair. Requires
      the keypair to have been generated (``cosign generate-key-pair``)
      and the password supplied via ``COSIGN_PASSWORD`` env var (or
      empty for unencrypted keys).
    * ``keyless=True`` → keyless OIDC signing via Fulcio + Rekor.
      Requires interactive OIDC flow (``COSIGN_EXPERIMENTAL=1``,
      browser-driven). Tests skip this path; CI uses
      ``sigstore/cosign-installer@v3`` then this path with ID-token env.
    * Both unset → returns a skipped result with a clear reason.

    Outputs land next to *statement_path* unless *output_dir* overrides
    it: ``<stem>.sig`` (signature), ``<stem>.cert`` (cert chain for
    keyless), and ``<stem>.bundle`` (combined signature + cert + tlog
    entry for offline verification).
    """
    available, version_str = cosign_available()
    if not available:
        return CosignResult(
            signed=False,
            statement_path=statement_path,
            skipped_reason=(
                "cosign not on PATH — install via "
                "`go install github.com/sigstore/cosign/v2/cmd/cosign@latest` "
                "or `brew install cosign`"
            ),
        )

    if not key_path and not keyless:
        return CosignResult(
            signed=False,
            statement_path=statement_path,
            skipped_reason=("no signing mode chosen — pass --key for offline or --keyless for OIDC"),
            cosign_version=version_str,
        )

    out_dir = Path(output_dir) if output_dir else statement_path.parent
    sig_path = out_dir / (statement_path.stem + ".sig")
    bundle_path = out_dir / (statement_path.stem + ".bundle")
    cert_path = out_dir / (statement_path.stem + ".cert") if keyless else None

    args = _sign_blob_args(
        statement_path,
        sig_path,
        bundle_path,
        cert_path,
        None if keyless else key_path,
    )

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return CosignResult(
            signed=False,
            statement_path=statement_path,
            skipped_reason=f"cosign invocation failed: {exc}",
            cosign_version=version_str,
        )
    if proc.returncode != 0:
        return CosignResult(
            signed=False,
            statement_path=statement_path,
            skipped_reason=(f"cosign exit {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:300]}"),
            cosign_version=version_str,
        )

    return _result_if_artifacts_landed(statement_path, sig_path, bundle_path, cert_path, version_str)


def cosign_verify_statement(
    statement_path: Path,
    *,
    bundle_path: Path | None = None,
    signature_path: Path | None = None,
    public_key_path: Path | None = None,
    certificate_identity: str | None = None,
    certificate_oidc_issuer: str | None = None,
) -> tuple[bool, str]:
    """Verify a signed CGA statement via cosign.

    Two modes:

    * Bundle (``--bundle``) — the modern offline-verifiable form.
    * Signature + key/cert pair — the classic two-file form.

    Returns ``(ok, message)``.  ``message`` carries either the cosign
    success string or the parsed stderr on failure.
    """
    available, _ = cosign_available()
    if not available:
        return False, "cosign not on PATH"

    args = ["cosign", "verify-blob", str(statement_path)]
    if bundle_path:
        args.extend(["--bundle", str(bundle_path)])
    if signature_path:
        args.extend(["--signature", str(signature_path)])
    if public_key_path:
        args.extend(["--key", str(public_key_path)])
    if certificate_identity:
        args.extend(["--certificate-identity", certificate_identity])
    if certificate_oidc_issuer:
        args.extend(["--certificate-oidc-issuer", certificate_oidc_issuer])

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"cosign invocation failed: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "verification failed").strip()
    return True, (proc.stdout or "verified").strip()
