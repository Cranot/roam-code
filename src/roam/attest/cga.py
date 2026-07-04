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

# Predicate type IRIs are served at https://roam-code.com/spec/... so
# SLSA / in-toto consumers that dereference the IRI find a real
# schema page (or at least a 200 from the canonical site, not a DNS
# 000 from an unowned domain). Earlier statements used the .dev
# domain which never resolved; verifier accepts both during the
# transition (see ``_LEGACY_PREDICATE_TYPES`` below).
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
    """SHA-256 of ``git status --porcelain`` output, or None when clean
    or non-git. Lets the predicate carry "tree was clean at sign time"
    as a verifiable property rather than an assumption.
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
        # Intentionally fail soft: missing git / inaccessible repo / timeout means no dirty-tree attestation.
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout
    if not out.strip():
        return None  # clean
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
        # Working-tree state at sign time. ``None`` means "clean" — anything
        # else is a sha256 of ``git status --porcelain``. The verifier
        # re-derives the live value and refuses on mismatch, so a CGA that
        # asserts clean cannot quietly be produced on a dirty tree, and a
        # signed-while-dirty statement can be re-verified against the same
        # uncommitted state if needed.
        "git_dirty_hash": dirty_hash,
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
            from roam.security.aibom_extension import build_aibom_block

            predicate["aibom"] = build_aibom_block(project_root, conn)
            predicate_type = PREDICATE_TYPE_AIBOM
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
    """Return a human-readable mismatch reason, or None when they match."""
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
    """Compare the signed graph fingerprints against the live DB values."""
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


def verify_cga_statement(
    statement: dict[str, Any],
    conn,
    *,
    project_root: Path,
) -> tuple[bool, list[str]]:
    """Re-derive both digests from the live DB and compare to *statement*.

    Returns ``(ok, errors)``. The list is empty on success; otherwise it
    enumerates every mismatch for the verifier to surface.
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
    errors.extend(_check_graph_fingerprints(predicate, expected_merkle, expected_edges, n_symbols, n_edges))

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
