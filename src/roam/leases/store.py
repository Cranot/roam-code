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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from roam.atomic_io import atomic_write_json

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
        """Return the JSON-serialisable shape (without ``repo_root``)."""
        d = asdict(self)
        d.pop("repo_root", None)
        # Path is not JSON serialisable; force list of plain strings for
        # the subject so downstream consumers see exactly what was claimed.
        d["subject"] = list(self.subject)
        return d

    def is_expired_at(self, now: Optional[datetime] = None) -> bool:
        """Return True if this lease's wall-clock TTL has elapsed.

        Note: a lease can be ``state == "active"`` on-disk yet wall-clock
        expired. Readers should treat such leases as no-longer-conflicting
        even before :func:`gc_expired_leases` rewrites the state field.
        """
        if self.state in {"released", "expired"}:
            return True
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            # A corrupt ``expires_at`` field is treated as still-active so a
            # bug in writers doesn't accidentally free everyone's leases.
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        return now >= exp


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
    atomic_write_json(path, lease.to_dict())


def read_lease(repo_root: Path, lease_id: str) -> Optional[Lease]:
    """Load a lease by id, or ``None`` if missing / unparseable."""
    path = _lease_path(repo_root, lease_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _lease_from_dict(raw, repo_root)


def _lease_from_dict(raw: dict, repo_root: Path) -> Optional[Lease]:
    """Build a :class:`Lease` from a parsed dict; ``None`` on shape error."""
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
    except (KeyError, TypeError, ValueError):
        return None


def _iter_lease_files(repo_root: Path):
    """Yield every ``.json`` path under ``.roam/leases/``."""
    root = leases_root(repo_root)
    if not root.exists():
        return
    for child in sorted(root.iterdir()):
        if child.suffix == ".json" and child.is_file():
            yield child


def _iter_leases(repo_root: Path):
    """Yield every parseable :class:`Lease` under the repo."""
    for path in _iter_lease_files(repo_root):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        lease = _lease_from_dict(raw, repo_root)
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
    for lease in _iter_leases(repo_root):
        if lease.state != "active":
            continue
        if lease.is_expired_at(now):
            continue
        if target.intersection(lease.subject):
            return lease
    return None


def claim_lease(
    repo_root: Path,
    agent: str,
    subject: list[str],
    *,
    kind: str = "files",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    acquired_at: Optional[str] = None,
) -> tuple[Optional[Lease], Optional[Lease]]:
    """Try to claim a lease.

    Returns ``(claimed, conflict)``:

      * ``(Lease, None)`` — claim succeeded; ``claimed`` is the new
        lease record (already persisted to disk).
      * ``(None, Lease)`` — claim BLOCKED; ``conflict`` is the existing
        active lease whose subject overlaps. The caller's claim was NOT
        written to disk.

    *acquired_at* is exposed mainly so tests can feed a deterministic
    timestamp; production callers leave it ``None``. The ``expires_at``
    is computed from ``acquired_at + ttl_seconds``.

    Side-effect: before the conflict check, this function opportunistically
    GCs any wall-clock-expired leases so a stale lease never blocks a
    legitimate fresh claim. The GC is best-effort (silently skips on
    I/O error).
    """
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
    except Exception:
        # GC failures must NEVER block a claim attempt.
        pass

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
    lease_id = _make_lease_id(ts, agent, subject_list)

    # Collision avoidance: if an id collides (two claims at same μs with
    # same agent + subject), perturb the hash input with a counter.
    counter = 0
    while _lease_path(repo_root, lease_id).exists():
        counter += 1
        lease_id = _make_lease_id(f"{ts}#{counter}", agent, subject_list)
        if counter > 1024:
            raise RuntimeError("could not allocate a unique lease_id after 1024 attempts")

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


def release_lease(repo_root: Path, lease_id: str) -> bool:
    """Mark *lease_id* as ``state: released``. Returns True on success.

    Returns False if the lease does not exist. Releasing an already-
    released or already-expired lease is a no-op that returns True --
    idempotence makes "agent retries release on flaky network" safe.
    """
    lease = read_lease(repo_root, lease_id)
    if lease is None:
        return False
    if lease.state == "released":
        return True
    lease.state = "released"
    _write_lease(lease)
    return True


def list_leases(
    repo_root: Path,
    *,
    agent: Optional[str] = None,
    include_expired: bool = False,
    include_released: bool = True,
) -> list[Lease]:
    """List leases for this repo, newest first.

    By default returns active + released leases but NOT expired ones
    (the default audience is an agent asking "what's still in play?").
    Set ``include_expired=True`` to see the full history.

    A lease is treated as wall-clock-expired even before its on-disk
    state has been rewritten — readers see the same truth that
    :func:`find_conflict` sees.
    """
    now = _utc_now()
    out: list[Lease] = []
    for lease in _iter_leases(repo_root):
        if agent is not None and lease.agent != agent:
            continue
        effective_state = lease.state
        if effective_state == "active" and lease.is_expired_at(now):
            effective_state = "expired"
        if effective_state == "expired" and not include_expired:
            continue
        if effective_state == "released" and not include_released:
            continue
        # Surface the effective (wall-clock) state on the returned record
        # so callers don't have to recompute it.
        lease.state = effective_state
        out.append(lease)
    # Newest first by acquired_at (ISO timestamps sort lexically).
    out.sort(key=lambda lease_obj: lease_obj.acquired_at, reverse=True)
    return out


def gc_expired_leases(repo_root: Path) -> list[str]:
    """Mark every wall-clock-expired lease as ``state: expired``.

    Returns the list of lease_ids that transitioned from ``active`` to
    ``expired`` on this pass. Already-released and already-expired
    leases are skipped (they don't need rewriting).
    """
    now = _utc_now()
    transitioned: list[str] = []
    for lease in _iter_leases(repo_root):
        if lease.state != "active":
            continue
        if not lease.is_expired_at(now):
            continue
        lease.state = "expired"
        try:
            _write_lease(lease)
        except OSError:
            # A write failure shouldn't drop the whole pass — keep going.
            continue
        transitioned.append(lease.lease_id)
    return transitioned
