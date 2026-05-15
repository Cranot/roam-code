"""Disk-backed permit store for ``roam permit issue --persist`` (W198).

Disk layout, per repo::

    .roam/
      permits/
        <permit_id>.json     # one JSON document per issued permit

A ``permit_id`` looks like ``permit_20260515_a1b2c3`` -- a UTC date
prefix plus a short hex suffix. The hash is derived from
(issued_at, issued_to, scope) so callers can predict the id in tests by
fixing those inputs (mirrors :mod:`roam.leases.store`'s
``_make_lease_id`` and :class:`roam.evidence.approval.ApprovalRecord`'s
id scheme so the three substrates' identifiers feel consistent at a
glance).

On-disk schema (matches what ``cmd_pr_bundle._load_permits_from_disk``
reads, so a persisted permit flows through the W268 -> W292 -> W294
pipeline unchanged)::

    {
      "permit_id": "permit_20260515_a1b2c3",
      "scope": "string describing what this permits",
      "expires_at": "2026-06-15T00:00:00Z",
      "issued_to": "agent:foo",
      "issued_at": "2026-05-15T10:30:00Z",
      "issued_by": "human:operator",
      "reason": "optional single-line rationale (no body, no secrets)"
    }

Discipline:

* Atomic writes via :func:`roam.atomic_io.atomic_write_json` -- a crash
  mid-write cannot leave a torn permit on disk (Pattern 1).
* Single-line ``reason`` only -- multi-line bodies are rejected at
  construction time per the W247a body-prohibition rule.
* Best-effort GC NOT yet implemented -- permits are advisory records
  that an external system / auditor consumes; expiry semantics are
  evaluated at read time, not at write time. A future ``roam permit gc``
  can mirror :func:`roam.leases.store.gc_expired_leases` if needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from roam.atomic_io import atomic_write_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PERMITS_DIR_NAME = ".roam"
PERMITS_SUBDIR = "permits"

# Permit ids look like ``permit_YYYYMMDD_<short-hash>``. Hash is 6+ hex chars.
# Mirrors ``LEASE_ID_RE`` in roam.leases.store so the two id formats are
# visually distinguishable yet structurally analogous.
PERMIT_ID_RE = re.compile(r"^permit_\d{8}_[0-9a-f]{6,}$")

# Test-only override hook: when ``ROAM_PERMIT_ID`` is set in the environment,
# :func:`issue_permit` uses that string verbatim (after format validation)
# instead of computing a fresh id. Lets tests pin a deterministic id without
# coupling to wall-clock time. Production callers leave the env var unset.
_PERMIT_ID_ENV_OVERRIDE = "ROAM_PERMIT_ID"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermitRecord:
    """One issued permit.

    Persisted to ``.roam/permits/<permit_id>.json`` via
    :func:`atomic_write_json`. Frozen so the in-memory record matches
    the immutable on-disk artefact -- mutating a permit means issuing a
    new one with a different id, never editing the file in place.

    Fields:

    * ``permit_id`` -- stable id of the form ``permit_<YYYYMMDD>_<6-hex>``.
    * ``scope`` -- non-empty string describing what this permits.
    * ``expires_at`` -- ISO-8601 UTC timestamp after which the permit
      should be treated as expired.
    * ``issued_to`` -- non-empty identity string (e.g. ``"agent:foo"``).
      Convention matches :class:`roam.evidence.refs.ActorRef.actor_id`.
    * ``issued_at`` -- ISO-8601 UTC timestamp of when the permit was
      issued.
    * ``issued_by`` -- non-empty identity string for the operator who
      issued the permit. Convention again matches ``ActorRef.actor_id``.
    * ``reason`` -- optional single-line rationale. Multi-line strings
      are rejected at ``__post_init__`` (no body, no secrets).

    NON-GOAL: this dataclass does not store raw credentials, tokens, or
    multi-line bodies. Operators who need long-form rationale should
    reference an external audit-trail row from ``extra`` in a future
    extension wave; today's surface is intentionally tiny.
    """

    permit_id: str
    scope: str
    expires_at: str
    issued_to: str
    issued_at: str
    issued_by: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.permit_id, str) or not PERMIT_ID_RE.match(self.permit_id):
            raise ValueError(
                f"PermitRecord.permit_id must match {PERMIT_ID_RE.pattern!r}; "
                f"got {self.permit_id!r}"
            )
        if not isinstance(self.scope, str) or not self.scope.strip():
            raise ValueError("PermitRecord.scope must be a non-empty string")
        if not isinstance(self.expires_at, str) or not self.expires_at:
            raise ValueError(
                "PermitRecord.expires_at must be a non-empty ISO-8601 string"
            )
        try:
            _parse_iso(self.expires_at)
        except ValueError as exc:
            raise ValueError(
                f"PermitRecord.expires_at is not ISO-8601 parseable: "
                f"{self.expires_at!r} ({exc})"
            ) from exc
        if not isinstance(self.issued_to, str) or not self.issued_to.strip():
            raise ValueError("PermitRecord.issued_to must be a non-empty string")
        if not isinstance(self.issued_at, str) or not self.issued_at:
            raise ValueError(
                "PermitRecord.issued_at must be a non-empty ISO-8601 string"
            )
        try:
            _parse_iso(self.issued_at)
        except ValueError as exc:
            raise ValueError(
                f"PermitRecord.issued_at is not ISO-8601 parseable: "
                f"{self.issued_at!r} ({exc})"
            ) from exc
        if not isinstance(self.issued_by, str) or not self.issued_by.strip():
            raise ValueError("PermitRecord.issued_by must be a non-empty string")
        if not isinstance(self.reason, str):
            raise ValueError("PermitRecord.reason must be a string (use '' for empty)")
        # Single-line discipline: no newlines / carriage returns in reason.
        # Per the W247a body-prohibition rule applied broadly.
        if "\n" in self.reason or "\r" in self.reason:
            raise ValueError(
                "PermitRecord.reason must be a single line (no newlines); "
                "multi-line bodies are rejected per the no-body / no-secrets "
                "discipline"
            )

    def to_dict(self) -> dict:
        """Return the JSON-serialisable shape (mirrors W268 reader)."""
        return asdict(self)

    def is_expired_at(self, now: Optional[datetime] = None) -> bool:
        """Return True if ``expires_at`` has elapsed.

        Best-effort: a corrupt ``expires_at`` (shouldn't happen given the
        constructor validation, but readers may load older formats) is
        treated as not-expired so a bug in writers doesn't accidentally
        invalidate every permit on the next read.
        """
        try:
            exp = _parse_iso(self.expires_at)
        except ValueError:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        return now >= exp


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def permits_root(repo_root: Path) -> Path:
    """Return the directory that holds all per-permit JSON documents."""
    return Path(repo_root) / PERMITS_DIR_NAME / PERMITS_SUBDIR


def _permit_path(repo_root: Path, permit_id: str) -> Path:
    return permits_root(repo_root) / f"{permit_id}.json"


# ---------------------------------------------------------------------------
# Timestamp + id helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp at microsecond precision (suffix ``Z``)."""
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC ``datetime``.

    Accepts both ``Z`` and explicit-offset forms. Naive timestamps are
    treated as UTC (matches the documented convention).
    """
    normalised = value
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _make_permit_id(issued_at: str, issued_to: str, scope: str) -> str:
    """Deterministic permit id derived from issued_at + issued_to + scope.

    Date prefix sorts chronologically; hash suffix collapses the same
    inputs into 6 hex chars. Tests that fix the inputs can predict the
    id on the first try.
    """
    try:
        dt = _parse_iso(issued_at)
    except ValueError:
        dt = _utc_now()
    date_part = dt.strftime("%Y%m%d")
    payload = f"{issued_at}|{issued_to}|{scope}".encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:6]
    return f"permit_{date_part}_{digest}"


# ---------------------------------------------------------------------------
# Disk IO
# ---------------------------------------------------------------------------


def _write_permit(repo_root: Path, permit: PermitRecord) -> Path:
    """Persist *permit* to ``.roam/permits/<id>.json`` (atomic). Returns path."""
    path = _permit_path(repo_root, permit.permit_id)
    atomic_write_json(path, permit.to_dict())
    return path


def read_permit(repo_root: Path, permit_id: str) -> Optional[PermitRecord]:
    """Load a permit by id, or ``None`` if missing / unparseable."""
    path = _permit_path(repo_root, permit_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _permit_from_dict(raw)


def _permit_from_dict(raw: dict) -> Optional[PermitRecord]:
    """Build a :class:`PermitRecord` from a parsed dict; ``None`` on error."""
    try:
        return PermitRecord(
            permit_id=str(raw["permit_id"]),
            scope=str(raw["scope"]),
            expires_at=str(raw["expires_at"]),
            issued_to=str(raw["issued_to"]),
            issued_at=str(raw["issued_at"]),
            issued_by=str(raw["issued_by"]),
            reason=str(raw.get("reason", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def list_permits(repo_root: Path) -> list[PermitRecord]:
    """Enumerate every parseable permit under ``.roam/permits/``.

    Sorted newest first by ``issued_at`` (ISO timestamps sort lexically).
    Empty list when the directory does not exist (Pattern 2: never raise
    on missing-state).
    """
    root = permits_root(repo_root)
    if not root.exists():
        return []
    out: list[PermitRecord] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if child.suffix != ".json" or not child.is_file():
            continue
        try:
            raw = json.loads(child.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        rec = _permit_from_dict(raw)
        if rec is not None:
            out.append(rec)
    out.sort(key=lambda r: r.issued_at, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Validated bundle/replay reader (W383)
# ---------------------------------------------------------------------------


def load_permits_from_disk(
    repo_root: Optional[Path],
    *,
    warnings_out: Optional[list[str]] = None,
) -> list[dict]:
    """Read every ``.roam/permits/*.json`` under *repo_root*; validated.

    Single validated entry point shared by ``cmd_pr_bundle`` (which
    materialises ``AuthorityRef`` rows from each result) and
    ``cmd_pr_replay._gather_permit_policy_decisions`` (which projects
    each result into a ``PolicyDecision`` row). Prior to W383 each
    caller had its own reader; the pr-replay path skipped W380 schema
    validation entirely so a malformed permit dropped by ``pr-bundle``
    would still surface in the replay envelope (silent divergence).
    W383 consolidates onto this one reader.

    Contract:

    * Returns ``[]`` when *repo_root* is ``None`` OR the permits
      directory does not exist OR no ``.json`` file parses cleanly.
      Pattern 2 always-emit: never raises on missing state.
    * Each entry is the on-disk dict verbatim (not the validated
      ``PermitRecord``) so callers project their own shape from raw
      fields without re-implementing JSON parsing.
    * Hardening: every dict is routed through :func:`_permit_from_dict`
      (W380 schema gate). A dict that cannot reconstruct a
      ``PermitRecord`` is dropped, NOT returned. The reader is total:
      schema-invalid input never derails the read.
    * Hardening: duplicate ``permit_id`` across files (W379) is
      detected; the first-seen file wins, subsequent copies are dropped.
    * Hardening: malformed JSON (W382), non-dict top-level value, and
      schema-rejected dicts each emit an actionable warning into
      *warnings_out* when supplied. Warnings name the offending file +
      the closed-form reason so an operator can locate and repair the
      underlying permit without grepping.

    Args:
        repo_root: project root (``find_project_root()`` output) or
            ``None`` (the test/no-repo case).
        warnings_out: optional list the reader appends one warning
            string per dropped row to. ``None`` (default) silently
            drops.

    Returns:
        list of raw permit dicts that survived validation, in
        directory-sort order (lexical by file name).
    """
    if repo_root is None:
        return []
    permits_dir = permits_root(repo_root)
    if not permits_dir.is_dir():
        return []
    try:
        children = sorted(permits_dir.iterdir())
    except OSError:
        return []

    def _emit_warning(message: str) -> None:
        if warnings_out is not None:
            warnings_out.append(message)

    out: list[dict] = []
    seen_ids: dict[str, str] = {}  # permit_id -> first-seen file name

    for child in children:
        if child.suffix != ".json" or not child.is_file():
            continue
        # W382: malformed JSON file -> warn naming the file + parse-error class.
        try:
            raw = json.loads(child.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _emit_warning(
                f"permit file {child.name!s} skipped: malformed JSON "
                f"({type(exc).__name__}: {exc})"
            )
            continue
        if not isinstance(raw, dict):
            _emit_warning(
                f"permit file {child.name!s} skipped: top-level value "
                f"is not a JSON object (got {type(raw).__name__})"
            )
            continue
        # W380: route through the validator so a permit dict that cannot
        # reconstruct a ``PermitRecord`` is dropped + warned.
        record = _permit_from_dict(raw)
        if record is None:
            raw_pid = raw.get("permit_id")
            id_phrase = (
                f"permit_id={raw_pid!r}"
                if isinstance(raw_pid, str) and raw_pid
                else "permit_id=<missing>"
            )
            _emit_warning(
                f"permit file {child.name!s} skipped: schema validation "
                f"failed ({id_phrase}); fields missing or invalid per "
                f"PermitRecord contract"
            )
            continue
        # W379: detect duplicate permit_id across files. Keep first-seen.
        pid = record.permit_id
        if pid in seen_ids:
            _emit_warning(
                f"duplicate permit_id={pid!r} found in {child.name!s}; "
                f"first occurrence was {seen_ids[pid]!s}; collector will "
                f"keep only the first AuthorityRef"
            )
            continue
        seen_ids[pid] = child.name
        out.append(raw)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def issue_permit(
    repo_root: Path,
    *,
    scope: str,
    expires_at: str,
    issued_to: str,
    issued_by: str,
    reason: str = "",
    issued_at: Optional[str] = None,
    permit_id: Optional[str] = None,
) -> tuple[PermitRecord, Path]:
    """Issue and persist a new permit. Returns ``(record, on_disk_path)``.

    Test hooks:

    * ``issued_at`` lets callers pin a deterministic timestamp.
    * ``permit_id`` lets callers pin a deterministic id directly. The
      ``ROAM_PERMIT_ID`` env var has the same effect; the explicit
      kwarg takes precedence when both are set.
    """
    ts = issued_at or _utc_now_iso()

    if permit_id is None:
        override = os.environ.get(_PERMIT_ID_ENV_OVERRIDE, "").strip()
        if override:
            permit_id = override
        else:
            permit_id = _make_permit_id(ts, issued_to, scope)

    # Collision avoidance: if an id collides (two issuances at same μs
    # with same inputs), perturb the hash input with a counter. Mirrors
    # the lease store's pattern.
    counter = 0
    while _permit_path(repo_root, permit_id).exists():
        counter += 1
        permit_id = _make_permit_id(f"{ts}#{counter}", issued_to, scope)
        if counter > 1024:
            raise RuntimeError("could not allocate a unique permit_id after 1024 attempts")

    record = PermitRecord(
        permit_id=permit_id,
        scope=scope,
        expires_at=expires_at,
        issued_to=issued_to,
        issued_at=ts,
        issued_by=issued_by,
        reason=reason,
    )
    path = _write_permit(Path(repo_root), record)
    return record, path
