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

Expiry-filtering design (W1067, asymmetric to leases):
``load_permits_from_disk`` and ``_permit_from_dict`` do NOT filter
expired permits. ``PermitRecord.is_expired_at`` exists but is unused on
the read path *by design*. Expired permits flow through to consumers
and the W377 collector stamps ``extra["expired"]=True`` on the resulting
AuthorityRef — audit-completeness pattern: "this authority was
exercised at the time the bundle was emitted." Filtering at read time
would silently drop evidence from the audit trail. Compare with
:mod:`roam.leases.store` which DOES filter at read time (live conflict
resolution semantic). See ``(internal memo)``.

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
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from roam.atomic_io import atomic_write_json
from roam.leases.store import _is_wall_clock_expired_at
from roam.output.formatter import WarningsOut

log = logging.getLogger(__name__)

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
# Field-level invariant helpers
# ---------------------------------------------------------------------------


def _require_machine_permit_id(value: str) -> None:
    """Permit ids must follow the machine-generated ``permit_YYYYMMDD_<hex>`` format."""
    if not isinstance(value, str) or not PERMIT_ID_RE.match(value):
        raise ValueError(f"PermitRecord.permit_id must match {PERMIT_ID_RE.pattern!r}; got {value!r}")


def _require_identity_field(value: str, field_name: str) -> None:
    """Scope / issued_to / issued_by must be non-empty strings that actually identify something."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"PermitRecord.{field_name} must be a non-empty string")


def _require_parseable_timestamp(value: str, field_name: str) -> None:
    """Timestamps must be non-empty ISO-8601 strings that round-trip through the parser."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"PermitRecord.{field_name} must be a non-empty ISO-8601 string")
    try:
        _parse_iso(value)
    except ValueError as exc:
        raise ValueError(f"PermitRecord.{field_name} is not ISO-8601 parseable: {value!r} ({exc})") from exc


def _require_single_line_audit_field(value: str, field_name: str) -> None:
    """Reason must stay a one-line audit annotation (no body / no secrets discipline)."""
    if not isinstance(value, str):
        raise ValueError(f"PermitRecord.{field_name} must be a string (use '' for empty)")
    if "\n" in value or "\r" in value:
        raise ValueError(
            f"PermitRecord.{field_name} must be a single line (no newlines); "
            "multi-line bodies are rejected per the no-body / no-secrets "
            "discipline"
        )


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
        _require_machine_permit_id(self.permit_id)
        _require_identity_field(self.scope, "scope")
        _require_parseable_timestamp(self.expires_at, "expires_at")
        _require_identity_field(self.issued_to, "issued_to")
        _require_parseable_timestamp(self.issued_at, "issued_at")
        _require_identity_field(self.issued_by, "issued_by")
        _require_single_line_audit_field(self.reason, "reason")

    def to_dict(self) -> dict:
        """Return the JSON-serialisable shape (mirrors W268 reader).

        Load-bearing across modules, not local-only. This is the writer
        half of the on-disk permit round-trip paired with
        :func:`_permit_from_dict` (invoked by :func:`_write_permit`), AND
        the wire form consumed by ``cmd_permit`` JSON envelope fields.

        Dead-code review note: :func:`_write_permit` calls this as
        ``PermitRecord.to_dict(permit)`` so static dead-export scanners
        see a direct class-method edge. Keep the public method name
        stable: command callers still consume this wire shape through
        instance dispatch on :class:`PermitRecord` rows returned by
        :func:`issue_permit` / :func:`read_permit` / :func:`list_permits`.
        """
        return asdict(self)

    def is_expired_at(self, now: Optional[datetime] = None) -> bool:
        """Return True if ``expires_at`` has elapsed.

        Uses the lease store's shared wall-clock expiry helper so permit
        and lease timestamps keep identical ISO-8601 parsing semantics.
        Best-effort: a corrupt ``expires_at`` (shouldn't happen given the
        constructor validation, but readers may load older formats) is
        treated as not-expired so a bug in writers doesn't accidentally
        invalidate every permit on the next read.
        """
        return _is_wall_clock_expired_at(self.expires_at, now)


@dataclass(frozen=True)
class PermitRequest:
    """The caller-supplied content of a permit-to-be.

    The "what to issue" value object consumed by :func:`issue_permit` —
    the sibling of :class:`PermitRecord`, which is the persisted result
    (request fields + the store-generated ``permit_id`` / ``issued_at``).
    Deliberately unvalidated: :func:`issue_permit` constructs a
    ``PermitRecord`` from these fields, so ``PermitRecord.__post_init__``
    remains the single validation gate for permit content.
    """

    scope: str
    expires_at: str
    issued_to: str
    issued_by: str
    reason: str = ""


@dataclass(frozen=True)
class _PermitReadContext:
    """Shared context for fault-tolerant permit readers.

    Bundles the ``repo_root`` / ``warnings_out`` pair that every permit
    reader threads through its helper chain, removing the W593 data clump
    between ``list_permits``, ``load_permits_from_disk``, and their
    internal pipeline helpers.
    """

    repo_root: Path
    warnings_out: WarningsOut = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root))


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
    atomic_write_json(path, PermitRecord.to_dict(permit))
    return path


def read_permit(
    repo_root: Path,
    permit_id: str,
    *,
    warnings_out: WarningsOut = None,
) -> Optional[PermitRecord]:
    """Load a permit by id, or ``None`` if missing / unparseable.

    W595: mirrors the W448 ``read_lease`` plumb — when *warnings_out* is
    supplied, every silent-error site appends one structured closed-enum
    marker so callers can tell "permit not on disk" from "permit file is
    on disk but unreadable" from "JSON parsed but schema rejected". The
    ``None`` return on every drop path is PRESERVED — the None-return is
    the caller contract. ``warnings_out=None`` (default) preserves the
    pre-W595 silent-drop behaviour.

    The marker shape DIVERGES from W448's free-form ``read_lease`` format
    and mirrors W589's release-site closed-enum shape instead — consistent
    with W593b's per-file ``permit_corrupt:`` prefix already in
    ``list_permits``, so a caller threading the same bucket through any
    permits-read site sees one uniform marker vocabulary.

    Emitted kinds (closed enum):

      * ``permit_not_found:<permit_id>.json`` — the on-disk path does
        not exist. ``read_lease`` deliberately does NOT warn on this
        case; ``read_permit`` does, because a missing permit during a
        ``permit show`` lookup is an operational anomaly worth
        surfacing (caller typo / GC race / wrong repo root).
      * ``permit_read_failed:<permit_id>.json:<exc_class>:<detail>`` —
        ``Path.read_text`` raised ``OSError`` (typically
        ``PermissionError`` / ``IsADirectoryError`` / generic
        ``OSError``). The file is on disk but unreadable.
      * ``permit_corrupt:<permit_id>.json:JSONDecodeError`` — the
        bytes parsed as something other than JSON. Same prefix shape
        as ``list_permits`` W593b emits per-file.
      * ``permit_corrupt:<permit_id>.json:NotAJsonObject`` — JSON
        parsed cleanly but the top-level value was not a dict.
      * ``permit_corrupt:<permit_id>.json:SchemaInvalid`` — dict
        rejected by ``_permit_from_dict`` (missing required field /
        ``__post_init__`` ``ValueError``).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    path = _permit_path(repo_root, permit_id)
    if not path.exists():
        _emit(f"permit_not_found:{path.name}")
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        _emit(f"permit_read_failed:{path.name}:{type(exc).__name__}:{exc}")
        return None
    except json.JSONDecodeError:
        _emit(f"permit_corrupt:{path.name}:JSONDecodeError")
        return None
    if not isinstance(raw, dict):
        _emit(f"permit_corrupt:{path.name}:NotAJsonObject")
        return None
    record = _permit_from_dict(raw)
    if record is None:
        _emit(f"permit_corrupt:{path.name}:SchemaInvalid")
        return None
    return record


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
    except (KeyError, TypeError, ValueError) as exc:
        raw_keys = sorted(str(key) for key in raw.keys()) if isinstance(raw, dict) else [f"<{type(raw).__name__}>"]
        log.debug(
            "permit schema rejected while reconstructing PermitRecord (%s: %s); keys=%s",
            type(exc).__name__,
            exc,
            raw_keys,
        )
        return None


def _permit_record_or_warn(
    child: Path,
    emit: Callable[[str], None],
) -> Optional[PermitRecord]:
    """Parse one permit file; emit closed-enum warnings and return None on failure.

    This helper exists so :func:`list_permits` can be a resilience
    orchestrator without nesting JSON parsing, type guards, and schema
    validation inside its main loop.
    """
    if child.suffix != ".json" or not child.is_file():
        return None
    try:
        raw = json.loads(child.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        emit(f"permit_corrupt:{child.name}:{type(exc).__name__}")
        return None
    if not isinstance(raw, dict):
        return None
    return _permit_from_dict(raw)


def list_permits(
    repo_root: Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[PermitRecord]:
    """Enumerate every parseable permit under ``.roam/permits/``.

    Sorted newest first by ``issued_at`` (ISO timestamps sort lexically).
    Empty list when the directory does not exist (Pattern 2: never raise
    on missing-state).

    W593: when *warnings_out* is supplied, each silent-error site emits
    one structured kind marker so callers can tell "permits dir clean"
    from "permits dir is unreadable" from "one permit file is corrupt".
    The ``[]`` return on iterdir failure is PRESERVED -- the empty-return
    is the caller contract. The per-file ``continue`` semantic is also
    preserved (best-effort iteration). ``None`` (default) preserves the
    pre-W593 silent-drop behaviour. The closed-enum kinds diverge from
    W383's ``load_permits_from_disk`` free-form format intentionally --
    ``list_permits`` is the CLI-facing reader, marker prefixes let an
    operator grep / filter without parsing free-form text.

    Emitted kinds (closed enum):

      * ``permits_root_unreadable:<exc_class>:<detail>`` -- the
        ``.roam/permits/`` directory exists but ``iterdir()`` raised
        ``OSError``. The reader returns ``[]``.
      * ``permit_corrupt:<filename>.json:<exc_class>`` -- one permit
        file is unreadable / unparseable JSON. ``<exc_class>`` names
        the failure mode (``OSError`` / ``JSONDecodeError``). The
        loop ``continue``s past this file (best-effort iteration).
    """
    read_context = _PermitReadContext(repo_root, warnings_out)

    def _emit(kind: str) -> None:
        _append_permit_load_warning(read_context, kind)

    root = permits_root(read_context.repo_root)
    if not root.exists():
        return []
    try:
        children = sorted(root.iterdir())
    except OSError as exc:
        _emit(f"permits_root_unreadable:{type(exc).__name__}:{exc}")
        return []
    out = [record for child in children if (record := _permit_record_or_warn(child, _emit)) is not None]
    out.sort(key=lambda r: r.issued_at, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Validated bundle/replay reader (W383)
# ---------------------------------------------------------------------------


def _append_permit_load_warning(read_context: _PermitReadContext, message: str) -> None:
    """Disclose why the total permit reader dropped recoverable evidence."""
    if read_context.warnings_out is not None:
        read_context.warnings_out.append(message)


def _permit_children_preserving_empty_contract(read_context: _PermitReadContext) -> list[Path]:
    """Keep an unreadable permit directory in the reader's empty-state contract."""
    permits_dir = permits_root(read_context.repo_root)
    try:
        return sorted(permits_dir.iterdir())
    except OSError as exc:
        # W593c: route through the EXISTING ``warnings_out`` channel
        # (W383 already plumbed it for per-permit failures). Closed-enum
        # marker so callers can distinguish "permits dir unreadable"
        # from per-file W379/W380/W382 warnings without parsing the
        # free-form text. The ``[]`` return is PRESERVED -- the empty-
        # return is the caller contract; the marker just discloses WHY.
        _append_permit_load_warning(read_context,f"permits_dir_unreadable:{type(exc).__name__}:{exc}")
        return []


def _raw_permit_object_with_repair_warning(child: Path, read_context: _PermitReadContext) -> Optional[dict]:
    """Parse one permit file while naming repairable byte/object failures."""
    try:
        raw = json.loads(child.read_text(encoding="utf-8"))
    except OSError as exc:
        _append_permit_load_warning(read_context,
            f"permit file {child.name!s} skipped: malformed JSON ({type(exc).__name__}: {exc})",
        )
        return None
    except UnicodeDecodeError as exc:
        _append_permit_load_warning(read_context,
            f"permit file {child.name!s} skipped: malformed JSON ({type(exc).__name__}: {exc})",
        )
        return None
    except json.JSONDecodeError as exc:
        _append_permit_load_warning(read_context,
            f"permit file {child.name!s} skipped: malformed JSON ({type(exc).__name__}: {exc})",
        )
        return None
    if not isinstance(raw, dict):
        _append_permit_load_warning(read_context,
            f"permit file {child.name!s} skipped: top-level value is not a JSON object (got {type(raw).__name__})",
        )
        return None
    return raw


def _audit_ready_permit_or_warn(
    child: Path,
    seen_ids: dict[str, str],
    read_context: _PermitReadContext,
) -> Optional[dict]:
    """Accept only schema-valid, first-seen permits for audit consumers."""
    raw = _raw_permit_object_with_repair_warning(child, read_context)
    if raw is None:
        return None
    # W380: route through the validator so a permit dict that cannot
    # reconstruct a ``PermitRecord`` is dropped + warned.
    record = _permit_from_dict(raw)
    if record is None:
        raw_pid = raw.get("permit_id")
        id_phrase = f"permit_id={raw_pid!r}" if isinstance(raw_pid, str) and raw_pid else "permit_id=<missing>"
        _append_permit_load_warning(read_context,
            f"permit file {child.name!s} skipped: schema validation "
            f"failed ({id_phrase}); fields missing or invalid per "
            f"PermitRecord contract",
        )
        return None
    # W379: detect duplicate permit_id across files. Keep first-seen.
    pid = record.permit_id
    if pid in seen_ids:
        _append_permit_load_warning(read_context,
            f"duplicate permit_id={pid!r} found in {child.name!s}; "
            f"first occurrence was {seen_ids[pid]!s}; collector will "
            f"keep only the first AuthorityRef",
        )
        return None
    seen_ids[pid] = child.name
    return raw


def _audit_ready_permit_dicts_preserving_first_seen_ids(
    children: list[Path],
    read_context: _PermitReadContext,
) -> list[dict]:
    """Preserve first-seen audit evidence while filtering unreadable permits."""
    out: list[dict] = []
    seen_ids: dict[str, str] = {}  # permit_id -> first-seen file name
    for child in children:
        if child.suffix != ".json" or not child.is_file():
            continue
        raw = _audit_ready_permit_or_warn(child, seen_ids, read_context)
        if raw is not None:
            out.append(raw)
    return out


def load_permits_from_disk(
    repo_root: Optional[Path],
    *,
    warnings_out: WarningsOut = None,
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
    read_context = _PermitReadContext(repo_root, warnings_out)
    permits_dir = permits_root(read_context.repo_root)
    if not permits_dir.is_dir():
        return []

    children = _permit_children_preserving_empty_contract(read_context)
    return _audit_ready_permit_dicts_preserving_first_seen_ids(children, read_context)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def issue_permit(
    repo_root: Path,
    request: PermitRequest,
    *,
    issued_at: Optional[str] = None,
    permit_id: Optional[str] = None,
) -> tuple[PermitRecord, Path]:
    """Issue and persist a new permit. Returns ``(record, on_disk_path)``.

    Permit content (scope / expiry / identities / reason) arrives as a
    :class:`PermitRequest`; the store contributes ``permit_id`` and
    ``issued_at``.

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
            permit_id = _make_permit_id(ts, request.issued_to, request.scope)

    # Collision avoidance: if an id collides (two issuances at same μs
    # with same inputs), perturb the hash input with a counter. Mirrors
    # the lease store's pattern.
    counter = 0
    while _permit_path(repo_root, permit_id).exists():
        counter += 1
        permit_id = _make_permit_id(f"{ts}#{counter}", request.issued_to, request.scope)
        if counter > 1024:
            raise RuntimeError("could not allocate a unique permit_id after 1024 attempts")

    record = PermitRecord(
        permit_id=permit_id,
        scope=request.scope,
        expires_at=request.expires_at,
        issued_to=request.issued_to,
        issued_at=ts,
        issued_by=request.issued_by,
        reason=request.reason,
    )
    path = _write_permit(Path(repo_root), record)
    return record, path
