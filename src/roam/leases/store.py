"""Disk-backed lease store for the Multi-Agent Lease System (R21).

Disk layout, per repo::

    .roam/
      leases/
        <lease_id>.json     # one JSON document per lease

A ``lease_id`` looks like ``lease_20260513_a3f9c2`` — a UTC date prefix
plus a short hash. The hash is derived from (acquired_at, agent,
subject) so callers can predict the id in tests by fixing those inputs.
This mirrors :mod:`roam.runs.ledger`'s ``run_id`` scheme so the two
substrates' identifiers feel consistent at a glance.

Lease lifecycle states::

    active    — currently held by an agent
    released  — explicitly released via :func:`release_lease`
    expired   — past ``expires_at`` and marked stale via
                :func:`gc_expired_leases` (or implicitly observed by a
                conflict check, which then *also* GCs)

Expiry-filtering design (W1067, asymmetric to permits):
Leases DO filter expired entries at read time. ``find_conflict`` skips
expired leases. ``list_leases`` defaults to ``include_expired=False``.
``gc_expired_leases`` marks them as stale. The semantic is live
conflict resolution: an expired lease no longer holds a subject so it
shouldn't block a new claim. Compare with :mod:`roam.permits.store`
which does NOT filter (audit-completeness — expired permits flow
through with ``extra["expired"]=True`` marker). See
``(internal memo)``.

Conflict detection
==================

Two leases conflict if their ``subject`` lists share any element (set
intersection non-empty). Algorithm complexity:

    O(N * |S|)

where ``N`` is the number of on-disk leases and ``|S|`` is the size of
the subject being claimed. Because subjects are typically a handful of
files (and N is bounded by the number of concurrently-active agents)
this is fine without any indexing. If lease counts ever cross 10k we
can swap the linear scan for a path-prefix index.

Substrate-only — no auto-enforcement
====================================

This module deliberately does NOT hook into command dispatch. The
substrate is for tooling; enforcement (warning, blocking, auto-claim)
is opt-in via callers. The seams documented for future integration are
in ``cmd_lease.py``'s module docstring.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from roam.atomic_io import atomic_write_json
from roam.output.formatter import WarningsOut

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEASES_DIR_NAME = ".roam"
LEASES_SUBDIR = "leases"

VALID_KINDS = {"files", "partition"}
VALID_STATES = {"active", "released", "expired"}

DEFAULT_TTL_SECONDS = 1800  # 30 minutes — matches the dogfood "edit session" cadence

# Lease ids look like ``lease_YYYYMMDD_<short-hash>``. Hash is 6+ hex chars.
LEASE_ID_RE = re.compile(r"^lease_\d{8}_[0-9a-f]{6,}$")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class Lease:
    """One agent's claim on a subject.

    Persisted to ``.roam/leases/<lease_id>.json`` via
    :func:`atomic_write_json`. The dataclass mirrors the on-disk shape
    plus a ``repo_root`` field that callers use to round-trip the lease
    back to disk (not persisted; computed from the file's directory).
    """

    lease_id: str
    agent: str
    subject_kind: str
    subject: list[str]
    ttl_seconds: int
    acquired_at: str
    expires_at: str
    state: str
    repo_root: Path = field(default_factory=Path)

    def to_dict(self) -> dict:
        """Return the JSON-serialisable wire shape (without ``repo_root``).

        Load-bearing across modules, not local-only. This is the writer
        half of the on-disk round-trip paired with :func:`_lease_from_dict`
        (invoked by :func:`_write_lease`), AND the wire form consumed by
        ``cmd_lease`` (lease / list / conflict JSON envelope fields) and
        ``cmd_pr_bundle`` (per-lease proof rows).

        Dead-code review note: :func:`_write_lease` calls this as
        ``Lease.to_dict(lease)`` so static dead-export scanners see a
        direct class-method edge. Keep the public method name stable:
        ``cmd_lease`` and ``cmd_pr_bundle`` still consume this wire shape
        through instance dispatch on :class:`Lease` rows returned by
        :func:`claim_lease` / :func:`list_leases` / :func:`find_conflict`.
        """
        d = asdict(self)
        d.pop("repo_root", None)
        # Path is not JSON serialisable; force list of plain strings for
        # the subject so downstream consumers see exactly what was claimed.
        d["subject"] = list(self.subject)
        return d

    def _is_expired_at(self, now: Optional[datetime] = None) -> bool:
        """Return True if this lease's wall-clock TTL has elapsed.

        Note: a lease can be ``state == "active"`` on-disk yet wall-clock
        expired. Readers should treat such leases as no-longer-conflicting
        even before :func:`gc_expired_leases` rewrites the state field.

        Exposed as ``is_expired_at`` below for compatibility with command
        callers while keeping the implementation private to this record.
        """
        if self.state in {"released", "expired"}:
            return True
        return _is_wall_clock_expired_at(self.expires_at, now)

    is_expired_at = _is_expired_at


@dataclass(frozen=True)
class LeaseClaimOptions:
    """Option cluster for :func:`claim_lease`."""

    kind: str = "files"
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    acquired_at: Optional[str] = None


@dataclass(frozen=True)
class LeaseListOptions:
    """Option cluster for :func:`list_leases`."""

    agent: Optional[str] = None
    include_expired: bool = False
    include_released: bool = True
    warnings_out: WarningsOut = None


@dataclass(frozen=True)
class _LeaseReadContext:
    """Shared context for fault-tolerant lease readers."""

    repo_root: Path
    warnings_out: WarningsOut = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root))


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def leases_root(repo_root: Path) -> Path:
    """Return the directory that holds all per-lease JSON documents."""
    return Path(repo_root) / LEASES_DIR_NAME / LEASES_SUBDIR


def _lease_path(repo_root: Path, lease_id: str) -> Path:
    return leases_root(repo_root) / f"{lease_id}.json"


# ---------------------------------------------------------------------------
# Timestamp + id helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp at microsecond precision (suffix ``Z``)."""
    return _utc_now().isoformat().replace("+00:00", "Z")


def _is_wall_clock_expired_at(expires_at: str, now: Optional[datetime] = None) -> bool:
    """Return True if an ISO-8601 expiry timestamp has elapsed.

    Corrupt timestamps are treated as not-expired so a writer bug does not
    accidentally free leases or invalidate permits on the next read.
    """
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= exp


def _make_lease_id(acquired_at: str, agent: str, subject: list[str]) -> str:
    """Deterministic-ish lease id derived from acquired_at + agent + subject.

    Date prefix sorts chronologically; hash suffix collapses the same
    inputs into 6 hex chars. Tests that fix the timestamp + agent +
    subject can predict the id on the first try.
    """
    try:
        dt = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
    except ValueError:
        dt = _utc_now()
    date_part = dt.strftime("%Y%m%d")

    # Normalise the subject for hashing so [a, b] and [b, a] produce
    # different ids only if order matters to the caller (it doesn't for
    # conflict detection but does for "this exact claim" uniqueness).
    subject_canonical = "|".join(sorted(subject))
    payload = f"{acquired_at}|{agent}|{subject_canonical}".encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:6]
    return f"lease_{date_part}_{digest}"


# ---------------------------------------------------------------------------
# Disk IO
# ---------------------------------------------------------------------------


def _write_lease(lease: Lease) -> None:
    """Persist *lease* to ``.roam/leases/<id>.json`` (atomic)."""
    path = _lease_path(lease.repo_root, lease.lease_id)
    atomic_write_json(path, Lease.to_dict(lease))


def _parse_lease_file(
    path: Path,
    read_context: _LeaseReadContext,
) -> Optional[Lease]:
    """Parse a single lease file and emit one warning per failure path.

    This helper exists to keep *directory iteration* and *per-file
    fault-tolerant parsing/diagnostics* separate. The conservation law
    here is tolerance versus diagnostic richness: callers want corrupt
    files skipped silently or with an actionable warning, but they do
    not want the iterator to own both concerns.
    """

    def _emit_warning(message: str) -> None:
        if read_context.warnings_out is not None:
            read_context.warnings_out.append(message)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _emit_warning(f"lease file {path.name!s} skipped: malformed JSON ({type(exc).__name__}: {exc})")
        return None
    if not isinstance(raw, dict):
        _emit_warning(
            f"lease file {path.name!s} skipped: top-level value is not a JSON object (got {type(raw).__name__})"
        )
        return None
    reasons: list[str] = []
    lease = _lease_from_dict(raw, read_context.repo_root, reason_out=reasons)
    if lease is None:
        raw_lid = raw.get("lease_id")
        id_phrase = f"lease_id={raw_lid!r}" if isinstance(raw_lid, str) and raw_lid else "lease_id=<missing>"
        reason_phrase = f"; {reasons[0]}" if reasons else ""
        _emit_warning(
            f"lease file {path.name!s} skipped: schema validation "
            f"failed ({id_phrase}{reason_phrase}); fields missing or "
            f"invalid per Lease contract"
        )
        return None
    return lease


def read_lease(
    repo_root: Path,
    lease_id: str,
    *,
    warnings_out: WarningsOut = None,
) -> Optional[Lease]:
    """Load a lease by id, or ``None`` if missing / unparseable.

    W448: when *warnings_out* is supplied, every drop path (malformed
    JSON / non-dict top-level / schema-invalid dict) appends one
    actionable warning naming the offending file + the closed-form
    reason — mirrors the format already emitted by :func:`_iter_leases`
    so callers that thread the same bucket get a consistent shape
    whether they read one lease by id or iterate the whole directory.
    A missing path is NOT a warning (the caller asked for a specific
    id that simply isn't on disk); ``None`` (default) preserves the
    pre-W448 silent-drop behaviour.
    """
    path = _lease_path(repo_root, lease_id)
    if not path.exists():
        return None
    return _parse_lease_file(path, _LeaseReadContext(repo_root, warnings_out))


def _lease_from_dict(
    raw: dict,
    repo_root: Path,
    *,
    reason_out: Optional[list[str]] = None,
) -> Optional[Lease]:
    """Build a :class:`Lease` from a parsed dict; ``None`` on shape error.

    Returns ``None`` when a required field is missing OR a typed coercion
    fails (``KeyError`` / ``TypeError`` / ``ValueError``). Fail-soft on
    purpose: one corrupt lease file must not crash lease iteration or a
    claim's conflict check. When *reason_out* is supplied, the concrete
    failure (``KeyError: 'agent'``) is appended so callers that thread a
    warnings bucket (W425/W448) can name WHICH field or coercion failed
    instead of emitting a generic schema-invalid line.
    """
    try:
        return Lease(
            lease_id=str(raw["lease_id"]),
            agent=str(raw["agent"]),
            subject_kind=str(raw["subject_kind"]),
            subject=[str(s) for s in raw.get("subject", [])],
            ttl_seconds=int(raw.get("ttl_seconds", DEFAULT_TTL_SECONDS)),
            acquired_at=str(raw["acquired_at"]),
            expires_at=str(raw["expires_at"]),
            state=str(raw.get("state", "active")),
            repo_root=Path(repo_root),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if reason_out is not None:
            reason_out.append(f"{type(exc).__name__}: {exc}")
        return None


def _iter_lease_files(repo_root: Path):
    """Yield every ``.json`` path under ``.roam/leases/``."""
    root = leases_root(repo_root)
    if not root.exists():
        return
    for child in sorted(root.iterdir()):
        if child.suffix == ".json" and child.is_file():
            yield child


def _iter_leases(read_context: _LeaseReadContext):
    """Yield every parseable :class:`Lease` under the repo.

    W425: when ``read_context.warnings_out`` is supplied, every dropped
    row (malformed JSON / non-dict top-level / schema-invalid dict)
    appends one actionable warning naming the offending file + the
    closed-form reason. ``None`` silently drops, preserving the pre-W425
    behavior for callers that don't care.
    """
    for path in _iter_lease_files(read_context.repo_root):
        lease = _parse_lease_file(path, read_context)
        if lease is not None:
            yield lease


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_conflict(repo_root: Path, subject: list[str]) -> Optional[Lease]:
    """Return an active lease whose subject intersects *subject*, else None.

    Wall-clock-expired leases (``is_expired_at(now)``) are skipped — they
    are treated as no-longer-conflicting even if their on-disk state has
    not yet been GC'd to ``"expired"``. This keeps fresh claims from
    being blocked by stale leases an agent forgot to release.

    Conflict semantics: two subjects conflict iff their string sets share
    any element. Callers normalise paths to a consistent form (forward
    slashes, repo-relative) before passing them in.
    """
    if not subject:
        return None
    target = set(subject)
    now = _utc_now()
    for lease in _iter_leases(_LeaseReadContext(repo_root)):
        if lease.state != "active":
            continue
        if lease.is_expired_at(now):
            continue
        if target.intersection(lease.subject):
            return lease
    return None


def _normalize_claim_options_for_legacy_callers(
    options: Optional[LeaseClaimOptions],
    overrides: dict[str, object],
) -> LeaseClaimOptions:
    """Return claim options while preserving the pre-options call surface.

    The conservation law here is API compatibility versus claim-path
    integrity: legacy keyword callers still work, while ``claim_lease``
    keeps the state-changing lease flow separate from boundary shims.
    """
    if options is None:
        claim_options = LeaseClaimOptions()
    elif isinstance(options, LeaseClaimOptions):
        claim_options = options
    else:
        raise TypeError("options must be a LeaseClaimOptions instance")
    if not overrides:
        return claim_options

    allowed = {"kind", "ttl_seconds", "acquired_at"}
    unexpected = sorted(set(overrides).difference(allowed))
    if unexpected:
        names = ", ".join(unexpected)
        raise TypeError(f"claim_lease() got unexpected option(s): {names}")
    return LeaseClaimOptions(
        kind=overrides.get("kind", claim_options.kind),
        ttl_seconds=overrides.get("ttl_seconds", claim_options.ttl_seconds),
        acquired_at=overrides.get("acquired_at", claim_options.acquired_at),
    )


def _allocate_lease_id_without_overwriting_existing_claim(
    repo_root: Path,
    acquired_at: str,
    agent: str,
    subject: list[str],
) -> str:
    """Return a lease id that preserves deterministic ids without clobbering.

    The conservation law here is deterministic lease ids versus disk
    uniqueness: tests and callers can predict the usual id, while a
    same-microsecond collision never overwrites an existing claim.
    """
    lease_id = _make_lease_id(acquired_at, agent, subject)
    counter = 0
    while _lease_path(repo_root, lease_id).exists():
        counter += 1
        lease_id = _make_lease_id(f"{acquired_at}#{counter}", agent, subject)
        if counter > 1024:
            raise RuntimeError("could not allocate a unique lease_id after 1024 attempts")
    return lease_id


def claim_lease(
    repo_root: Path,
    agent: str,
    subject: list[str],
    options: Optional[LeaseClaimOptions] = None,
    **overrides: object,
) -> tuple[Optional[Lease], Optional[Lease]]:
    """Try to claim a lease.

    Returns ``(claimed, conflict)``:

      * ``(Lease, None)`` — claim succeeded; ``claimed`` is the new
        lease record (already persisted to disk).
      * ``(None, Lease)`` — claim BLOCKED; ``conflict`` is the existing
        active lease whose subject overlaps. The caller's claim was NOT
        written to disk.

    ``options`` groups the lease kind, TTL, and optional deterministic
    timestamp. Existing callers may still pass ``kind=``, ``ttl_seconds=``,
    and ``acquired_at=``; those keyword overrides are normalized into a
    :class:`LeaseClaimOptions` instance before validation. ``acquired_at``
    is exposed mainly so tests can feed a deterministic timestamp;
    production callers leave it ``None``. The ``expires_at`` is computed
    from ``acquired_at + ttl_seconds``.

    Side-effect: before the conflict check, this function opportunistically
    GCs any wall-clock-expired leases so a stale lease never blocks a
    legitimate fresh claim. The GC is best-effort: an ``OSError`` during
    the sweep does NOT abort the claim — the failure is surfaced to stderr
    (so it is observable) and the claim proceeds.
    """
    claim_options = _normalize_claim_options_for_legacy_callers(options, overrides)
    kind = claim_options.kind
    ttl_seconds = claim_options.ttl_seconds
    acquired_at = claim_options.acquired_at

    if not agent:
        raise ValueError("agent must be a non-empty string")
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind {kind!r}; expected one of {sorted(VALID_KINDS)}")
    if not subject:
        raise ValueError("subject must be a non-empty list")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    # Best-effort GC pass — frees stale leases so they don't block this claim.
    try:
        gc_expired_leases(repo_root)
    except OSError as exc:
        # W746: narrowed from bare Exception. gc_expired_leases walks
        # the .roam/leases/ directory; only filesystem I/O failures are
        # realistic. Programmer-class errors (NameError / AttributeError)
        # now propagate per W531 — a broken GC must crash visibly rather
        # than silently leave stale leases in place forever.
        # Pattern-2 silent-fallback fix: surface the swallowed I/O failure
        # to stderr so a flaky GC sweep is observable (matching the
        # fire-and-forget idiom in memory/store.py's _warn_skipped_memory_line).
        # The claim itself still proceeds — the GC is best-effort per the
        # docstring, and a stale-lease sweep must never block a fresh claim.
        sys.stderr.write(f"[leases] pre-claim GC skipped ({type(exc).__name__}: {exc})\n")

    subject_list = [str(s) for s in subject]
    conflict = find_conflict(repo_root, subject_list)
    if conflict is not None:
        return None, conflict

    ts = acquired_at or _utc_now_iso()
    try:
        start_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        start_dt = _utc_now()
    expires = (start_dt + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
    lease_id = _allocate_lease_id_without_overwriting_existing_claim(repo_root, ts, agent, subject_list)

    lease = Lease(
        lease_id=lease_id,
        agent=agent,
        subject_kind=kind,
        subject=subject_list,
        ttl_seconds=ttl_seconds,
        acquired_at=ts,
        expires_at=expires,
        state="active",
        repo_root=Path(repo_root),
    )
    _write_lease(lease)
    return lease, None


def _load_lease_for_mutation(
    path: Path,
    read_context: _LeaseReadContext,
) -> tuple[Optional[Lease], Optional[str]]:
    """Load a lease for a state-changing operation.

    Returns ``(lease, None)`` on success, or ``(None, kind)`` on failure.
    The *kind* is a closed-form string consumed by :func:`release_lease`
    for its ``warnings_out`` bucket. This helper keeps the release path
    on the same ``_LeaseReadContext`` pattern used by :func:`_iter_leases`
    and :func:`read_lease`, while preserving release-specific error
    classification.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"lease_corrupt:{path.name}:{type(exc).__name__}"
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"lease_corrupt:{path.name}:{type(exc).__name__}"
    if not isinstance(raw, dict):
        return None, f"lease_corrupt:{path.name}:NotAJsonObject"
    lease = _lease_from_dict(raw, read_context.repo_root)
    if lease is None:
        return None, f"lease_corrupt:{path.name}:SchemaInvalid"
    return lease, None


def release_lease(
    repo_root: Path,
    lease_id: str,
    *,
    warnings_out: WarningsOut = None,
) -> bool:
    """Mark *lease_id* as ``state: released``. Returns True on success.

    Returns False if the lease does not exist. Releasing an already-
    released or already-expired lease is a no-op that returns True --
    idempotence makes "agent retries release on flaky network" safe.

    W589: when *warnings_out* is supplied, each silent-error site emits
    one structured kind string into the bucket so callers can tell
    "lease vanished" from "lease was already released" from "lease file
    is on disk but malformed". The release-site kinds DIVERGE from the
    W448 ``read_lease`` free-form format intentionally — release-call
    semantics are different (a missing lease IS a release-time anomaly
    worth disclosing, whereas a missing lease on a direct ``read_lease``
    lookup is just "not found"). ``None`` (default) preserves the
    pre-W589 silent behaviour.

    Emitted kinds (closed enum):

      * ``lease_not_found:<path>`` — the on-disk path does not exist.
      * ``lease_already_released:<lease_id>`` — the lease loaded clean
        but its ``state`` is already ``released``.
      * ``lease_corrupt:<path>:<exc_class>`` — the on-disk file is
        unreadable / unparseable / schema-invalid. ``<exc_class>``
        names the failure mode (``OSError`` / ``JSONDecodeError`` /
        ``NotAJsonObject`` / ``SchemaInvalid``).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    read_context = _LeaseReadContext(repo_root, warnings_out)
    path = _lease_path(read_context.repo_root, lease_id)
    if not path.exists():
        _emit(f"lease_not_found:{path.name}")
        return False

    # Re-parse here (instead of delegating to ``read_lease``) so the
    # release-site can identify WHICH failure mode fired and emit the
    # structured kind. ``read_lease`` returns ``None`` for all three
    # corrupt sub-cases without distinguishing them at the call site.
    lease, kind = _load_lease_for_mutation(path, read_context)
    if kind is not None:
        _emit(kind)
        return False

    if lease.state == "released":
        _emit(f"lease_already_released:{lease.lease_id}")
        return True
    lease.state = "released"
    _write_lease(lease)
    return True


def _visible_state_for_lease(
    lease: Lease,
    agent: Optional[str],
    include_expired: bool,
    include_released: bool,
    now: datetime,
) -> Optional[str]:
    """Return the wall-clock-effective state if *lease* should be listed.

    None means the lease is hidden by the active filters. Active leases
    that have crossed their expiry wall-clock are promoted to ``expired``
    so all readers see the same truth that :func:`find_conflict` sees.
    """
    if agent is not None and lease.agent != agent:
        return None
    effective_state = lease.state
    if effective_state == "active" and lease.is_expired_at(now):
        effective_state = "expired"
    if effective_state == "expired" and not include_expired:
        return None
    if effective_state == "released" and not include_released:
        return None
    return effective_state


def list_leases(
    repo_root: Path,
    options: Optional[LeaseListOptions] = None,
    **overrides: object,
) -> list[Lease]:
    """List leases for this repo, newest first.

    By default returns active + released leases but NOT expired ones
    (the default audience is an agent asking "what's still in play?").
    Set ``include_expired=True`` to see the full history.

    A lease is treated as wall-clock-expired even before its on-disk
    state has been rewritten — readers see the same truth that
    :func:`find_conflict` sees.

    W425: *warnings_out* is an optional list the reader appends one
    warning per dropped on-disk ``.roam/leases/*.json`` document to.
    Default ``None`` preserves the pre-W425 silent-drop behavior. The
    warning format mirrors :func:`roam.permits.store.load_permits_from_disk`
    (W379/W380/W382): each line names the offending file and the closed-
    form reason (malformed JSON / non-dict top-level / schema-invalid
    dict) so an operator can locate and repair the underlying lease
    without grepping.

    ``options`` groups the read-side filters. Existing callers may still
    pass ``agent=``, ``include_expired=``, ``include_released=``, and
    ``warnings_out=``; those keyword overrides are normalized into a
    :class:`LeaseListOptions` instance at the boundary.
    """
    if options is None:
        list_options = LeaseListOptions()
    elif isinstance(options, LeaseListOptions):
        list_options = options
    else:
        raise TypeError("options must be a LeaseListOptions instance")
    if overrides:
        allowed = {"agent", "include_expired", "include_released", "warnings_out"}
        unexpected = sorted(set(overrides).difference(allowed))
        if unexpected:
            names = ", ".join(unexpected)
            raise TypeError(f"list_leases() got unexpected option(s): {names}")
        list_options = LeaseListOptions(
            agent=overrides.get("agent", list_options.agent),
            include_expired=overrides.get("include_expired", list_options.include_expired),
            include_released=overrides.get("include_released", list_options.include_released),
            warnings_out=overrides.get("warnings_out", list_options.warnings_out),
        )

    agent = list_options.agent
    include_expired = list_options.include_expired
    include_released = list_options.include_released
    warnings_out = list_options.warnings_out

    now = _utc_now()
    out: list[Lease] = []
    for lease in _iter_leases(_LeaseReadContext(repo_root, warnings_out)):
        visible_state = _visible_state_for_lease(
            lease, agent, include_expired, include_released, now
        )
        if visible_state is None:
            continue
        # Surface the effective (wall-clock) state on the returned record
        # so callers don't have to recompute it.
        lease.state = visible_state
        out.append(lease)
    # Newest first by acquired_at (ISO timestamps sort lexically).
    out.sort(key=lambda lease_obj: lease_obj.acquired_at, reverse=True)
    return out


def gc_expired_leases(
    repo_root: Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[str]:
    """Mark every wall-clock-expired lease as ``state: expired``.

    Returns the list of lease_ids that transitioned from ``active`` to
    ``expired`` on this pass. Already-released and already-expired
    leases are skipped (they don't need rewriting).

    W592: when *warnings_out* is supplied, each silently-swallowed
    per-lease write failure emits one structured marker rather than a
    bare ``continue``. Before W592 a partial GC pass left stale lease
    files on disk with NO signal to the operator, making "GC ran clean"
    indistinguishable from "GC ran, hit 3 OSErrors, left 3 stale
    leases blocking future claims".

    The ``continue`` semantic is PRESERVED on purpose — a single I/O
    failure must NOT abort the whole sweep (best-effort contract). The
    marker just surfaces WHICH lease couldn't be cleaned so the caller
    can decide whether to retry, alert, or escalate.

    Emitted kind (closed enum):

      * ``lease_gc_failed:<lease_id>.json:<exc_class>:<detail>`` — the
        per-lease ``_write_lease`` raised ``OSError``. ``<exc_class>``
        names the failure mode (typically ``PermissionError`` /
        ``FileNotFoundError`` / ``OSError``); ``<detail>`` carries the
        exception's str().
    """
    now = _utc_now()
    transitioned: list[str] = []
    for lease in _iter_leases(_LeaseReadContext(repo_root, warnings_out)):
        if lease.state != "active":
            continue
        if not lease.is_expired_at(now):
            continue
        lease.state = "expired"
        try:
            _write_lease(lease)
        except OSError as exc:
            # A write failure shouldn't drop the whole pass — keep going.
            # W592: surface the per-failure marker so callers can see
            # WHICH expired lease couldn't be cleaned (Pattern-2 silent
            # fallback fix). ``continue`` is preserved — best-effort
            # sweep semantic is the contract.
            if warnings_out is not None:
                warnings_out.append(f"lease_gc_failed:{lease.lease_id}.json:{type(exc).__name__}:{exc}")
            continue
        transitioned.append(lease.lease_id)
    return transitioned
