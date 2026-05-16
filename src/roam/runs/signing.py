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


def ensure_ledger_key(repo_root: Path) -> bytes:
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
    """
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
    except OSError:
        # Permission tightening failure is non-fatal — the file is still
        # under .roam/ which is in .gitignore and inside the user's home.
        pass
    return new_key


def key_file_mode(repo_root: Path) -> Optional[int]:
    """Return the POSIX permission bits of the key file, or ``None``.

    Returns ``None`` on Windows (where st_mode doesn't carry POSIX
    semantics) or when the file is absent. Exposed primarily for tests.
    """
    path = ledger_key_path(repo_root)
    if not path.exists():
        return None
    if os.name == "nt":
        # NTFS reports a synthetic mode that isn't comparable to POSIX
        # 0o600; signal "not meaningful" instead of lying.
        return None
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


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
