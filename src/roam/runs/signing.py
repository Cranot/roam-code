"""HMAC rolling-hash signing for the run-ledger event stream (R20 phase 4).

Currently ``events.jsonl`` is append-only but *unsigned* — any process with
write access to ``.roam/runs/<run_id>/events.jsonl`` can rewrite history
without leaving a trace. This module adds a rolling HMAC chain that makes
the ledger tamper-evident:

    sig_n = HMAC(key, sig_{n-1}.bytes() || canonical_json(event_n).bytes())

Each event carries a ``signature`` field equal to ``sig_n`` for that event.
Mutating event ``k`` invalidates every subsequent signature, so a verifier
can scan the chain and report the first ``seq`` at which the recomputed
signature disagrees with the stored one.

What this is NOT
================

This is a **local HMAC** keyed on a per-repo file (``.ledger_key``), not
a public-key signature. It proves "this ledger has not been tampered with
since it was written, using this key" — it does NOT prove **who** wrote
the events. (See the existing CGA emit/verify machinery in
``roam.attest.cga`` for cryptographic identity attestation; that's a
distinct concern.) HMAC is the right primitive here:

* No PKI to manage; the key is created on first ``runs start``.
* Verification is identical to signing — same code path, no key-id
  resolution dance.
* Fast: SHA-256 HMAC is ~1 µs per event.

Backward compatibility
======================

Events written before this module landed have no ``signature`` field.
:func:`verify_chain` treats those as **unsigned** (advisory, not failure)
— the chain reports ``state: "unsigned"`` and ``partial_success: True``,
but does not fail with ``state: "tampered"``. Once any event in a run is
signed, every subsequent event must also be signed; a signed run that
goes unsigned mid-stream is treated as tampered.

Disk layout
===========

The key lives at ``.roam/runs/.ledger_key`` — one file per repo, shared
by every run in the repo. It contains 32 random bytes (256 bits) written
in raw binary; chmod 0o600 on POSIX. Generation happens on first
``start_run`` if absent; the file is never rotated automatically (doing
so would invalidate every existing run's chain).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from roam.atomic_io import atomic_write_bytes
from roam.observability import log_swallowed
from roam.output.formatter import WarningsOut

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEDGER_KEY_FILE = ".ledger_key"
LEDGER_KEY_BYTES = 32  # 256-bit HMAC key — matches SHA-256 block size.

# Seed signature used by the FIRST event in any run. Sixty-four zero hex
# chars matches the width of a SHA-256 hex digest; this keeps the chain
# math homogeneous (every link is the same shape).
SEED_SIGNATURE = "0" * 64


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


def ledger_key_path(repo_root: Path) -> Path:
    """Return the on-disk path of the HMAC key file for *repo_root*.

    The key lives under ``.roam/runs/.ledger_key`` so it's per-repo and
    co-located with the run directories it secures. Callers should not
    assume the file exists; use :func:`ensure_ledger_key` to materialise
    it.
    """
    return Path(repo_root) / ".roam" / "runs" / LEDGER_KEY_FILE


def ensure_ledger_key(
    repo_root: Path,
    *,
    warnings_out: WarningsOut = None,
) -> bytes:
    """Read (or generate) the per-repo HMAC key. Returns raw bytes.

    Generation policy:
      * First call on a repo: write 32 fresh bytes from
        :func:`secrets.token_bytes`, chmod 0o600 on POSIX (best-effort
        on Windows — the underlying NTFS ACL stops other users from
        reading by default for files under the user's profile, which is
        where ``.roam/`` lives in practice).
      * Subsequent calls: read the existing bytes and return them.

    The file is created atomically via
    :func:`roam.atomic_io.atomic_write_bytes` so a crash mid-write can
    never leave a truncated key (which would invalidate every chain).

    If the existing file is the wrong length, we treat it as corrupt and
    refuse to silently regenerate (regenerating would invalidate every
    existing chain). Callers see a :class:`ValueError`.

    W601 lineage disclosure ("Make fallback chains loud", agi-in-md
    CP45/CP46/CP52/CP53). The chmod permission-tighten failure on the
    write-side of first-generation was previously swallowed silently;
    when ``warnings_out`` is supplied, this function appends ONE
    closed-enum marker on the silent path. The return semantic is
    PRESERVED — the function still returns the 32-byte key regardless of
    whether chmod succeeded. ``warnings_out=None`` (default) preserves
    pre-W601 silent behaviour.

    Closed-enum marker (exactly 1 kind — per W978 first-hypothesis
    discipline: the read-side paths at lines 112-122 already raise
    ``ValueError`` loudly; only the write-side chmod was silent):

      * ``signing_key_perm_tighten_failed:<rel_path>:<exc_class>:<detail>``
        — operational anomaly. The file was generated successfully but
        the subsequent ``os.chmod`` raised ``OSError`` (typically on
        Windows, read-only FS, or noexec mounts). The key is usable; the
        permission tightening is informational. Mirrors W596's
        ``run_meta_read_failed`` marker shape with a ``signing_key_``
        prefix.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    path = ledger_key_path(repo_root)
    if path.exists():
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"ledger key at {path} unreadable: {exc}") from exc
        if len(data) != LEDGER_KEY_BYTES:
            raise ValueError(
                f"ledger key at {path} has wrong length "
                f"({len(data)} bytes; expected {LEDGER_KEY_BYTES}); "
                f"refusing to overwrite — manually delete to regenerate."
            )
        return data

    # Generate. token_bytes uses the OS CSPRNG (CryptGenRandom on Windows,
    # /dev/urandom on POSIX); safe for cryptographic use.
    new_key = secrets.token_bytes(LEDGER_KEY_BYTES)
    atomic_write_bytes(path, new_key)
    # Best-effort tighten permissions to owner-only on POSIX. On Windows
    # ``os.chmod`` only toggles the read-only bit; we accept that the
    # filesystem ACL is the real defense.
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        # Permission tightening failure is non-fatal — the file is still
        # under .roam/ which is in .gitignore and inside the user's home.
        # W601: disclose lineage when a warnings_out bucket is supplied.
        _emit(f"signing_key_perm_tighten_failed:{LEDGER_KEY_FILE}:{type(exc).__name__}:{exc}")
    return new_key


def key_file_mode(
    repo_root: Path,
    *,
    warnings_out: WarningsOut = None,
) -> Optional[int]:
    """Return the POSIX permission bits of the key file, or ``None``.

    Returns ``None`` on Windows (where st_mode doesn't carry POSIX
    semantics) or when the file is absent. Exposed primarily for tests.

    W601-bonus lineage disclosure: when ``warnings_out`` is supplied,
    each silent ``None`` return path (other than the Windows
    not-meaningful branch, which is a deliberate non-applicability per
    W597 daemon-discipline intentional-absence pattern) appends one
    closed-enum marker. ``warnings_out=None`` (default) preserves the
    pre-W601 silent contract.

    Closed-enum markers (exactly 2 kinds — per W978 first-hypothesis
    discipline: ``stat`` is the only I/O, no parse step exists, and
    the Windows branch is intentional non-applicability, not failure):

      * ``signing_key_not_found:<rel_path>`` — informational. The
        key file does not exist (callers in tests / before
        ``ensure_ledger_key`` has run). Mirrors W596's
        ``run_meta_not_found`` informational missing-state marker.
      * ``signing_key_stat_failed:<rel_path>:<exc_class>:<detail>`` —
        operational anomaly. ``Path.stat`` raised ``OSError`` on a file
        the ``exists()`` check just saw (TOCTOU window, permission-
        flipped mid-call, etc.).

    The Windows branch (``os.name == "nt"``) does NOT emit a marker —
    returning ``None`` there is intentional design (st_mode doesn't
    carry POSIX semantics), not a silent failure. Per "Make fallback
    chains loud" rule, lineage emission is reserved for paths where
    the caller might otherwise mistake the result for a successful
    POSIX-meaningful return.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    path = ledger_key_path(repo_root)
    # Single stat() — discriminating FileNotFoundError (expected absence,
    # emits signing_key_not_found:) from other OSErrors (permission/IO
    # failure, emits signing_key_stat_failed:). Merging the prior
    # exists()+stat() pair into one call closes the TOCTOU window the
    # docstring above mentions AND routes ALL stat-permission failures
    # through the loud-fallback emit path (the prior exists() call was
    # NOT wrapped, so PermissionError leaked on Python 3.11+ where
    # Path.exists() no longer swallows it). The not_found marker fires
    # on Windows too (caller-contract preserved); Windows only skips
    # the *mode* return below, not the existence check.
    try:
        st = path.stat()
    except FileNotFoundError:
        _emit(f"signing_key_not_found:{LEDGER_KEY_FILE}")
        return None
    except OSError as exc:
        _emit(f"signing_key_stat_failed:{LEDGER_KEY_FILE}:{type(exc).__name__}:{exc}")
        return None
    if os.name == "nt":
        # NTFS reports a synthetic mode that isn't comparable to POSIX
        # 0o600; signal "not meaningful" instead of lying.
        # No marker: this is deliberate non-applicability, not a failure.
        return None
    return stat.S_IMODE(st.st_mode)


# ---------------------------------------------------------------------------
# Signature computation
# ---------------------------------------------------------------------------


def canonical_event_bytes(event: dict) -> bytes:
    """Canonicalise *event* for signing.

    The ``signature`` field itself is excluded from the canonical form —
    otherwise we'd be signing the field whose value we're computing.
    Keys are sorted and whitespace is stripped so the bytes are
    deterministic across Python versions and JSON libraries.
    """
    clean = {k: v for k, v in event.items() if k != "signature"}
    return json.dumps(
        clean,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_event_signature(prev_signature: str, event: dict, key: bytes) -> str:
    """Compute the rolling HMAC signature for *event*.

    ``prev_signature`` is the previous event's signature hex string, or
    :data:`SEED_SIGNATURE` for the first event in a run. The signed
    payload is ``prev_sig_bytes || canonical_event_json_bytes`` — both
    pieces are encoded as UTF-8.

    Returns a 64-char lowercase hex SHA-256 HMAC digest. The format
    matches Git's commit hash width so it composes cleanly with
    ``hexdigest()`` from other roam-code subsystems.
    """
    body = prev_signature.encode("utf-8") + canonical_event_bytes(event)
    return hmac.new(key, body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------


def verify_chain(events: list[dict], key: bytes) -> dict:
    """Verify the rolling-HMAC chain over *events* (seq order).

    Returns a dict shaped for direct use in a ``runs-verify`` envelope::

        {
          "state": "ok" | "tampered" | "unsigned",
          "events_verified": int,
          "first_tamper_at_seq": int | None,
          "partial_success": bool,
          "final_signature": str | None,
          "details": str,
        }

    Decision matrix:
      * Every event has a valid signature → ``state: "ok"``.
      * No event has a ``signature`` field at all → ``state: "unsigned"``;
        treated as advisory (``partial_success: True``) per backward-compat
        contract. This is the "legacy events from before signing landed"
        path.
      * At least one event lacks a signature but at least one has one →
        treated as tampered at the first missing-signature seq; the chain
        contract is "once signed, always signed".
      * A signature is present but doesn't match the recomputed value →
        ``state: "tampered"`` with ``first_tamper_at_seq`` set to that
        event's ``seq``.

    Robust to events that don't carry a ``seq`` field (rare, but possible
    for malformed ledgers): we fall back to the 1-indexed position in the
    list so callers always see a real integer in
    ``first_tamper_at_seq``.
    """
    if not events:
        return {
            "state": "ok",
            "events_verified": 0,
            "first_tamper_at_seq": None,
            "partial_success": False,
            "final_signature": None,
            "details": "ledger is empty",
        }

    has_any_signature = any("signature" in e for e in events)
    if not has_any_signature:
        return {
            "state": "unsigned",
            "events_verified": len(events),
            "first_tamper_at_seq": None,
            # Advisory: legacy ledgers from before signing landed are
            # readable but cannot be integrity-checked. partial_success=True
            # surfaces the "we couldn't verify" state without failing the
            # command outright (Pattern 2: explicit state, not silent SAFE).
            "partial_success": True,
            "final_signature": None,
            "details": "no events carry signatures — likely a pre-signing ledger",
        }

    prev_sig = SEED_SIGNATURE
    last_good_sig: Optional[str] = None
    for idx, event in enumerate(events, start=1):
        seq = event.get("seq", idx)
        stored = event.get("signature")
        if stored is None:
            # Once a chain has any signatures, every event must be signed.
            # A signed run going unsigned mid-stream is treated as tampered
            # — an attacker who can strip ``signature`` from event K and
            # everything after would otherwise pass as "unsigned" silently.
            return {
                "state": "tampered",
                "events_verified": idx - 1,
                "first_tamper_at_seq": seq,
                "partial_success": False,
                "final_signature": last_good_sig,
                "details": (f"event seq={seq} is missing the 'signature' field after a signed prefix"),
            }
        expected = compute_event_signature(prev_sig, event, key)
        if not hmac.compare_digest(expected, stored):
            return {
                "state": "tampered",
                "events_verified": idx - 1,
                "first_tamper_at_seq": seq,
                "partial_success": False,
                "final_signature": last_good_sig,
                "details": (f"signature mismatch at seq={seq}: chain breaks here"),
            }
        prev_sig = stored
        last_good_sig = stored

    return {
        "state": "ok",
        "events_verified": len(events),
        "first_tamper_at_seq": None,
        "partial_success": False,
        "final_signature": last_good_sig,
        "details": f"chain verified across {len(events)} event(s)",
    }


# ---------------------------------------------------------------------------
# MCP-P0.3 — receipt-integrity verification on top of the HMAC chain
# ---------------------------------------------------------------------------


#: Closed enumeration of receipt-integrity sub-states surfaced as the
#: ``receipt_integrity`` field on the dict returned by
#: :func:`verify_chain_with_receipts`. Drift-checked by the W_MCP-P0.3
#: tests so adding a new sub-state requires a deliberate source edit.
#:
#: * ``ok``           - every ``mcp_receipt`` event resolves to an on-disk
#:                      receipt whose canonical-JSON sha256 matches the
#:                      ``receipt_hash`` baked into the signed event.
#: * ``missing``      - the chain references at least one receipt that is
#:                      no longer on disk (rm'd / never written /
#:                      filesystem error).
#: * ``tampered``     - at least one on-disk receipt's recomputed sha256
#:                      disagrees with the chain-baked hash. The receipt
#:                      JSON was edited after the event was signed.
#: * ``not_linked``   - no event carries a ``receipt_hash`` field. Either
#:                      this is a pre-P0.3 run, or no sensitive tool fired
#:                      a receipt. Advisory; NOT a failure.
RECEIPT_INTEGRITY_STATES: frozenset[str] = frozenset(
    {
        "ok",
        "missing",
        "tampered",
        "not_linked",
    }
)


def _receipt_file_for(repo_root: Path, run_id: str, tool_call: str) -> Path:
    """Mirror the on-disk layout used by ``mcp_server._write_mcp_receipt``.

    Receipts live at ``<repo>/.roam/mcp_receipts/<run_id>/<tool_call>.json``.
    Pulled here so :func:`verify_chain_with_receipts` does not import the
    MCP server module (which would create a heavy import-time cycle).
    """
    return Path(repo_root) / ".roam" / "mcp_receipts" / run_id / f"{tool_call}.json"


def _collect_receipt_events(events: list[dict]) -> list[dict]:
    """Filter ``events`` to the subset that links an on-disk MCP receipt:
    ``action == "mcp_receipt"`` AND ``receipt_hash`` is a non-empty
    string. Events without those keys are skipped silently — pre-P0.3
    chains have no receipt-linked events, which the orchestrator maps
    to ``receipt_integrity="not_linked"``."""
    out: list[dict] = []
    for event in events:
        if event.get("action") != "mcp_receipt":
            continue
        rh = event.get("receipt_hash")
        if isinstance(rh, str) and rh:
            out.append(event)
    return out


def _check_one_receipt(repo_root: Path, run_id: str, event: dict) -> tuple[str, Optional[int], Optional[str]]:
    """Verify one mcp_receipt event's on-disk file against its stored
    ``receipt_hash``. Returns ``(status, seq, tool_call)`` where status
    is one of:

      * ``"ok"`` - sha256 matches; ``tool_call`` is None
      * ``"tampered"`` - sha256 mismatch; ``tool_call`` carries the
        offending receipt id so the caller can surface it in ``details``
      * ``"missing"`` - file is absent OR unreadable (OSError folds in;
        loud-fallback log_swallowed surfaces the cause under
        ROAM_VERBOSE per CLAUDE.md §"Make fallback chains loud")
      * ``"malformed"`` - event lacks string ``tool_call`` / ``receipt_hash``;
        treated as not-linked anomaly, not a crash.

    The writer appends a trailing newline; strip it to recover the
    canonical bytes that produced ``receipt_hash``."""
    import hashlib

    seq_raw = event.get("seq")
    seq = seq_raw if isinstance(seq_raw, int) else None
    tool_call = event.get("tool_call")
    expected = event.get("receipt_hash")
    if not isinstance(tool_call, str) or not isinstance(expected, str):
        return ("malformed", seq, None)
    path = _receipt_file_for(repo_root, run_id, tool_call)
    if not path.exists():
        return ("missing", seq, None)
    try:
        on_disk = path.read_bytes()
    except OSError as exc:
        log_swallowed(f"runs.signing:verify_receipts:read:{tool_call}", exc)
        return ("missing", seq, None)
    canonical = on_disk.rstrip(b"\n")
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != expected:
        return ("tampered", seq, tool_call)
    return ("ok", seq, None)


def _apply_tamper_verdict(base: dict, seq: Optional[int], tool_call: Optional[str]) -> dict:
    """When a single receipt is tampered, the tamper overrides the
    chain-level verdict — the receipt-bearing event's run-time
    integrity claim is invalidated. Mutates ``base`` in place and
    returns it for inline-return ergonomics."""
    base["state"] = "tampered"
    base["first_tamper_at_seq"] = seq if seq is not None else base.get("first_tamper_at_seq")
    base["partial_success"] = False
    base["receipt_integrity"] = "tampered"
    base["details"] = (
        f"mcp_receipt sha256 mismatch at seq={seq}: "
        f"on-disk receipt for tool_call={tool_call} was modified after signing"
    )
    return base


def verify_chain_with_receipts(
    events: list[dict],
    key: bytes,
    repo_root: Path,
    run_id: str,
) -> dict:
    """Run :func:`verify_chain` and additionally walk ``mcp_receipt`` events.

    Returns the same dict shape as :func:`verify_chain` with one added
    field:

      * ``receipt_integrity`` - one of :data:`RECEIPT_INTEGRITY_STATES`.

    Per-receipt walk rules:

      * Every event whose payload carries ``action == "mcp_receipt"`` AND
        a non-empty ``receipt_hash`` is followed to disk via
        :func:`_receipt_file_for`. The on-disk bytes are sha256'd and
        compared to ``receipt_hash``.
      * Mismatch → ``receipt_integrity = "tampered"`` AND
        ``first_tamper_at_seq`` is set to the event's ``seq`` (overrides
        the chain-level result, since a tampered receipt invalidates the
        run-time integrity claim of the receipt-bearing event).
      * Missing on disk → ``receipt_integrity = "missing"`` AND a
        ``first_missing_receipt_at_seq`` field is set. ``state`` is left
        at its chain-level value (the chain itself is intact; the
        receipt artefact is gone).
      * No events carry ``receipt_hash`` at all → ``receipt_integrity =
        "not_linked"``. ``state`` is left at the chain-level value
        (typically ``ok``); ``partial_success`` is NOT raised because
        not-linked is a normal pre-P0.3 / no-sensitive-call state.

    Hash discipline (W210 omit-when-default): events that pre-date
    receipt-linkage have no ``receipt_hash`` field, so they are skipped
    silently — the chain bytes are unchanged from a pre-P0.3 chain when
    no receipts have been emitted.

    Implementation: split across ``_collect_receipt_events`` (filter),
    ``_check_one_receipt`` (per-receipt verify with closed-enum status),
    and ``_apply_tamper_verdict`` (terminal tamper-state mutation).
    This orchestrator wires them: HMAC pass → filter → loop → final
    state assignment.
    """
    base = verify_chain(events, key)
    # Chain-level tamper dominates: propagate without walking receipts.
    if base["state"] == "tampered":
        base["receipt_integrity"] = "not_linked"
        return base
    receipt_events = _collect_receipt_events(events)
    if not receipt_events:
        base["receipt_integrity"] = "not_linked"
        return base
    first_missing_seq: Optional[int] = None
    for event in receipt_events:
        status, seq, tool_call = _check_one_receipt(repo_root, run_id, event)
        if status == "tampered":
            return _apply_tamper_verdict(base, seq, tool_call)
        if status == "missing" and first_missing_seq is None:
            first_missing_seq = seq
    if first_missing_seq is not None:
        base["receipt_integrity"] = "missing"
        base["first_missing_receipt_at_seq"] = first_missing_seq
        # Chain itself is fine; the receipt artefact is gone. Surface as
        # partial_success so callers see "we found a hole" without
        # collapsing the chain-level integrity verdict.
        base["partial_success"] = True
        return base
    base["receipt_integrity"] = "ok"
    return base
