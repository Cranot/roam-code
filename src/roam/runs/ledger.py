"""Per-agent-run event ledger (R20 substrate).

Disk layout, per repo::

    .roam/
      runs/
        <run_id>/
          meta.json       # run identity + start/end timestamps + status
          events.jsonl    # append-only event stream (one JSON object per line)

A ``run_id`` looks like ``run_20260513_a3f9c2`` -- a UTC date prefix plus
a short hash. The hash is derived from (started_at, agent, repo_root)
so callers can predict the id in tests by fixing those inputs.

This module is the SUBSTRATE for R20. It deliberately:
  - does NOT sign events (CGA wiring is a follow-up)
  - does NOT push to a server (local-only, ships with the repo)
  - does NOT mutate the SQLite index (lives entirely on the filesystem)

Higher-level features (``roam replay``, ``roam agent-score``,
``roam audit-trail``) consume this ledger; they do not need to know the
on-disk layout because they read through the API below.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNS_DIR_NAME = ".roam"
RUNS_SUBDIR = "runs"
META_FILE = "meta.json"
EVENTS_FILE = "events.jsonl"

VALID_STATUSES = {"in_progress", "completed", "failed", "abandoned"}

# Run ids look like ``run_YYYYMMDD_<short-hash>``. Hash is 6+ hex chars.
RUN_ID_RE = re.compile(r"^run_\d{8}_[0-9a-f]{6,}$")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class RunMeta:
    """Metadata for a single agent run.

    Mirrors the JSON shape persisted to ``meta.json``. Forward-compat
    extra fields are preserved on the disk side but not surfaced here --
    callers that need them can ``read_run_meta`` and inspect the raw
    dict.

    The ``mode`` field (W14.2 Synergy 4) records the active agent-mode
    at run-start time. It's optional so older meta.json files that
    pre-date this wiring still load cleanly via ``read_run_meta``.
    """

    run_id: str
    # Vocabulary note (W198): in ChangeEvidence + ActorRef this field is
    # named ``agent_id`` (id-suffixed). The run-ledger uses the
    # unsuffixed ``agent`` for historical reasons (RunMeta predates the
    # W182 agentic-assurance crosswalk vocabulary). The collector at
    # ``evidence/collector.py:_build_actor_refs`` maps
    # ``RunMeta.agent`` -> ``ActorRef(actor_kind="agent",
    # actor_id=<RunMeta.agent>)`` explicitly so the on-disk ledger
    # shape stays back-compat while the crosswalk surface stays
    # consistent.
    agent: str
    started_at: str
    ended_at: Optional[str] = None
    status: str = "in_progress"
    mode: Optional[str] = None
    # R20 phase 4 — quick-look integrity fingerprint stamped by end_run.
    # ``final_signature`` is the HMAC of the LAST event; ``event_count``
    # is the total at close time. A reader who only inspects meta.json
    # can detect "did the chain change since I last saw this run?" without
    # walking events.jsonl. Both are None for in-progress runs and for
    # legacy meta.json files that pre-date this wiring.
    final_signature: Optional[str] = None
    event_count: Optional[int] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Flatten ``extra`` into the top level so the on-disk shape stays
        # the documented one. Known fields take precedence.
        extras = d.pop("extra", {}) or {}
        merged = dict(extras)
        merged.update(d)
        # Drop ``mode`` entirely when it's None — keeps the on-disk shape
        # backward-compatible (older readers see no new field, newer
        # readers see ``mode`` only when populated).
        if merged.get("mode") is None:
            merged.pop("mode", None)
        # Same rule for final_signature / event_count: omit when unset so
        # callers reading older meta.json files don't see ``None``-valued
        # fields they have to defend against.
        if merged.get("final_signature") is None:
            merged.pop("final_signature", None)
        if merged.get("event_count") is None:
            merged.pop("event_count", None)
        return merged


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def runs_root(repo_root: Path) -> Path:
    """Return the directory that holds all per-run subdirectories."""
    return Path(repo_root) / RUNS_DIR_NAME / RUNS_SUBDIR


def run_dir(repo_root: Path, run_id: str) -> Path:
    """Return the directory for *run_id* (does not assert existence)."""
    return runs_root(repo_root) / run_id


def _meta_path(repo_root: Path, run_id: str) -> Path:
    return run_dir(repo_root, run_id) / META_FILE


def _events_path(repo_root: Path, run_id: str) -> Path:
    return run_dir(repo_root, run_id) / EVENTS_FILE


# ---------------------------------------------------------------------------
# Timestamp + id helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp at microsecond precision (suffix ``Z``).

    Microsecond resolution matters for two reasons:
      1. Within a single shell session an agent can plausibly start two
         runs in the same second; we want list_runs(newest-first) to
         order them correctly.
      2. The deterministic ``run_id`` hash derives from this timestamp;
         microsecond entropy keeps near-simultaneous starts from
         colliding on the same id.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_run_id(started_at: str, agent: str, repo_root: Path) -> str:
    """Deterministic-ish run id derived from start time + agent + repo.

    The date prefix is taken straight from ``started_at`` (YYYYMMDD) so
    listings sort chronologically. The hash suffix collapses the same
    inputs into 6 hex chars; collision probability across a single
    repo / single day is negligible, and tests can freeze the inputs to
    predict the id.
    """
    # Pull YYYYMMDD from the ISO timestamp -- safest is to parse rather
    # than slice (handles fractional seconds, +HH:MM offsets, ...).
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    date_part = dt.strftime("%Y%m%d")

    payload = f"{started_at}|{agent}|{Path(repo_root).resolve()}".encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:6]
    return f"run_{date_part}_{digest}"


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------


def start_run(repo_root: Path, agent: str, started_at: Optional[str] = None) -> RunMeta:
    """Create a new run directory and seed ``meta.json``.

    *started_at* is exposed mainly so tests can feed a deterministic
    timestamp; production callers leave it ``None`` and we use UTC now.

    Returns the freshly-created :class:`RunMeta`. The directory is
    guaranteed to exist after the call; ``events.jsonl`` is touched
    empty so callers can rely on its presence.

    W14.2 Synergy 4: the run's active mode (from
    :func:`roam.modes.policy.get_active_mode`) is stamped into
    ``meta.json``. When no ``.roam/active_mode`` file exists the
    function returns ``None`` and the field is omitted on-disk -- the
    "no opinion expressed" state is explicit. We DO NOT call
    ``resolve_mode`` here (it falls back to ``safe_edit`` even when
    nothing is configured); ``get_active_mode`` is the right hook
    because absence-of-file IS meaningful.
    """
    if not agent:
        raise ValueError("agent must be a non-empty string")
    started_at = started_at or _utc_now_iso()
    run_id = _make_run_id(started_at, agent, repo_root)
    rdir = run_dir(repo_root, run_id)
    # Collision-avoidance: if a run with the same id already exists (two
    # starts in the same UTC second from the same agent + repo), perturb
    # the hash input with a counter. Tests that pass an explicit
    # *started_at* still get a fully deterministic id on the first try.
    collision_counter = 0
    while rdir.exists():
        collision_counter += 1
        run_id = _make_run_id(f"{started_at}#{collision_counter}", agent, repo_root)
        rdir = run_dir(repo_root, run_id)
        if collision_counter > 1024:
            raise RuntimeError("could not allocate a unique run_id after 1024 attempts")
    rdir.mkdir(parents=True, exist_ok=True)

    # Touch events.jsonl so readers don't have to special-case its
    # absence. Use 'a' (append) so a pre-existing file is preserved --
    # important when run_id collides on a re-invocation in the same
    # millisecond (rare, but cheap to defend against).
    _events_path(repo_root, run_id).open("a", encoding="utf-8").close()

    # R20 phase 4 — materialise the per-repo HMAC key on first start_run.
    # Best-effort: if the filesystem refuses (permission error, etc.) we
    # let the run proceed; ``log_event`` will surface the absence by
    # writing events without signatures, and ``verify_chain`` will flag
    # them. Never block run creation on a signing-substrate failure.
    try:
        from roam.runs.signing import ensure_ledger_key

        ensure_ledger_key(Path(repo_root))
    except Exception:
        pass

    # W14.2 Synergy 4 — resolve active mode at start time. Best-effort:
    # mode subsystem failures must NEVER abort run creation.
    active_mode: Optional[str] = None
    try:
        from roam.modes.policy import get_active_mode

        active_mode = get_active_mode(Path(repo_root))
    except Exception:
        active_mode = None

    meta = RunMeta(
        run_id=run_id,
        agent=agent,
        started_at=started_at,
        ended_at=None,
        status="in_progress",
        mode=active_mode,
    )
    _write_meta(repo_root, meta)
    return meta


def log_event(repo_root: Path, run_id: str, **event_fields) -> int:
    """Append an event to ``events.jsonl`` for *run_id*. Returns the seq.

    Event shape is intentionally open-ended -- callers pass whatever
    fields are meaningful for the action. The substrate enforces only:

      - ``ts``  is added if absent (UTC now)
      - ``seq`` is assigned monotonically (1-indexed) based on current
        line count of ``events.jsonl``
      - ``signature`` is computed as the rolling HMAC over the previous
        signature concatenated with the canonical event JSON, so any
        mutation of a past event invalidates the chain from that point on

    The whole event is serialised on a single line so the JSONL stream
    stays grep-able and crash-safe.

    The signing chain (R20 phase 4) is **best-effort**: if the key file
    is unreadable or the previous signature can't be parsed, the event
    is still written (the ledger's append-only property is the
    higher-priority invariant) — but ``signature`` is omitted and
    :func:`verify_chain` will report the run as ``tampered`` at the
    first unsigned event. Callers that want a hard guarantee that
    signing succeeded should re-read the line they just wrote and check
    for ``signature`` in the parsed dict.
    """
    rdir = run_dir(repo_root, run_id)
    if not rdir.exists():
        raise FileNotFoundError(f"run {run_id} does not exist (run 'roam runs start' first)")

    events_path = _events_path(repo_root, run_id)
    # Count existing lines to assign the next seq. For substrate-scale
    # event volumes (hundreds-thousands per run) a full read is fine; if
    # this becomes a hotspot, persist the seq in meta.json.
    seq = _count_events(events_path) + 1

    event = dict(event_fields)
    event.setdefault("ts", _utc_now_iso())
    event["seq"] = seq

    # R20 phase 4 — rolling HMAC over (prev_sig || canonical_event_json).
    # Import lazily so a corrupt signing module never blocks the rest of
    # the ledger from working (signing is additive, not mandatory).
    try:
        from roam.runs.signing import (
            SEED_SIGNATURE,
            compute_event_signature,
            ensure_ledger_key,
        )

        key = ensure_ledger_key(Path(repo_root))
        prev_sig = _last_event_signature(events_path) or SEED_SIGNATURE
        event["signature"] = compute_event_signature(prev_sig, event, key)
    except Exception:
        # Best-effort: a missing key or filesystem error must not prevent
        # the event from being recorded. The verifier will flag it.
        pass

    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return seq


def _last_event_signature(events_path: Path) -> Optional[str]:
    """Return the ``signature`` of the most recent event, or ``None``.

    Used by :func:`log_event` to seed the HMAC chain. Reading only the
    final line keeps signing cost O(1) per append (rather than O(n)
    if we replayed the whole chain on every write). The trade-off: we
    trust the last stored signature without re-verifying it — a chain
    tampered between two ``log_event`` calls will produce a "valid"
    signature for the new event keyed off the corrupt previous one, but
    :func:`verify_chain` still catches it because the corrupt event's
    own stored signature won't match its recomputed value.
    """
    if not events_path.exists():
        return None
    last_line: Optional[str] = None
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                last_line = stripped
    if last_line is None:
        return None
    try:
        parsed = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    sig = parsed.get("signature") if isinstance(parsed, dict) else None
    return sig if isinstance(sig, str) else None


def end_run(
    repo_root: Path,
    run_id: str,
    status: str = "completed",
    ended_at: Optional[str] = None,
) -> RunMeta:
    """Stamp ``meta.json`` with ``ended_at`` + final ``status``.

    *status* must be one of :data:`VALID_STATUSES`. Calling ``end_run``
    on an already-ended run overwrites the status (so an agent can flip
    a run from ``completed`` to ``failed`` if a post-hoc check turns
    things red). The ``started_at`` field is preserved.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
    meta = read_run_meta(repo_root, run_id)
    if meta is None:
        raise FileNotFoundError(f"run {run_id} does not exist")
    meta.ended_at = ended_at or _utc_now_iso()
    meta.status = status

    # R20 phase 4 — stamp the final-signature fingerprint into meta.json
    # so callers can do a cheap integrity-changed-since-close check
    # without re-scanning the whole ledger. Best-effort: if the chain
    # is unsigned/legacy/empty, leave the fields off (the dataclass's
    # to_dict() drops None-valued ones, preserving the on-disk shape).
    try:
        events_path = _events_path(repo_root, run_id)
        meta.event_count = _count_events(events_path)
        meta.final_signature = _read_final_signature(events_path)
    except Exception:
        # Reading the ledger failed — leave the fields blank rather than
        # crashing the close. The run still ends cleanly.
        pass

    _write_meta(repo_root, meta)
    return meta


def _read_final_signature(events_path: Path) -> Optional[str]:
    """Return the ``signature`` of the very last event, or ``None``.

    Shared with the internal ``log_event`` chain-seed lookup; pulled out
    so ``end_run`` can stamp meta.json without re-implementing the same
    parse. Returns ``None`` when the ledger is empty or its last event
    is unsigned.
    """
    return _last_event_signature(events_path)


def _write_meta(repo_root: Path, meta: RunMeta) -> None:
    path = _meta_path(repo_root, meta.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = meta.to_dict()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.write("\n")


def _count_events(events_path: Path) -> int:
    """Cheap line count for a JSONL file. Blank lines do not count."""
    if not events_path.exists():
        return 0
    count = 0
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def read_run_meta(repo_root: Path, run_id: str) -> Optional[RunMeta]:
    """Load ``meta.json`` for *run_id*. Returns ``None`` if absent.

    Forward-compat fields (anything not on the dataclass) are preserved
    on the returned ``extra`` dict.
    """
    path = _meta_path(repo_root, run_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    known = {
        "run_id",
        "agent",
        "started_at",
        "ended_at",
        "status",
        "mode",
        "final_signature",
        "event_count",
    }
    kwargs = {k: raw.get(k) for k in known if k in raw}
    extras = {k: v for k, v in raw.items() if k not in known}
    try:
        meta = RunMeta(**kwargs)  # type: ignore[arg-type]
    except TypeError:
        return None
    meta.extra = extras
    return meta


def read_run_events(repo_root: Path, run_id: str) -> Iterator[dict]:
    """Stream events for *run_id* in seq order.

    Yields raw dicts (not dataclasses) since event shape is open-ended.
    Corrupt JSON lines are skipped silently -- the ledger should keep
    streaming even if one event got mangled. Order on disk is already
    seq order because writes are append-only.
    """
    path = _events_path(repo_root, run_id)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                yield raw


def list_runs(
    repo_root: Path,
    agent: Optional[str] = None,
    since: Optional[str] = None,
    status: Optional[str] = None,
) -> Iterator[RunMeta]:
    """Stream run metadata, newest first.

    Filters:
      - ``agent``: exact match on the ``agent`` field
      - ``since``: ISO-8601 string; runs with ``started_at >= since``
      - ``status``: one of :data:`VALID_STATUSES`

    Yields nothing if the runs directory does not exist -- callers must
    handle the "no runs yet" state explicitly.
    """
    root = runs_root(repo_root)
    if not root.exists():
        return
    metas: list[RunMeta] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = read_run_meta(repo_root, child.name)
        if meta is None:
            continue
        if agent is not None and meta.agent != agent:
            continue
        if status is not None and meta.status != status:
            continue
        if since is not None and meta.started_at < since:
            continue
        metas.append(meta)
    # Newest first -- ``started_at`` is an ISO timestamp so lexical sort
    # is chronological.
    metas.sort(key=lambda m: m.started_at, reverse=True)
    for m in metas:
        yield m


def latest_in_progress_run(repo_root: Path, agent: Optional[str] = None) -> Optional[RunMeta]:
    """Return the most-recent in-progress run, or ``None``.

    Used by ``roam runs log`` when ``--run-id`` is omitted: the caller
    almost always means "log to the run I just started in this shell".
    """
    for meta in list_runs(repo_root, agent=agent, status="in_progress"):
        return meta
    return None
