"""Envelope cache for ``roam pr-analyze`` (D5 split out of cmd_pr_analyze).

A cache hit short-circuits the heavy work — pr-prep + AI scoring + rules
matching — when the inputs that affect the analysis haven't changed.
Inputs hashed: diff text, rules-file content (mtime-independent), block
threshold, language override, and the cache schema version.

The schema version is bumped when the bundle shape changes so older
cached envelopes don't surface as stale renders.
"""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path

from roam.output.formatter import WarningsOut

DEFAULT_CACHE_DIR = Path(".roam") / "pr-analyze-cache"
CACHE_VERSION = 1  # bump when the envelope shape changes


def _cache_key(diff_text: str, rules_path: Path, block_threshold: int, language_override: str | None) -> str:
    """Derive a stable cache key from inputs that affect the analysis."""
    h = hashlib.sha256()
    h.update(f"v={CACHE_VERSION}\n".encode())
    h.update(b"diff=")
    h.update((diff_text or "").encode("utf-8"))
    h.update(b"\nrules=")
    if rules_path.exists():
        try:
            h.update(rules_path.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    h.update(f"\nthreshold={block_threshold}\n".encode())
    h.update(f"lang={language_override or ''}".encode())
    return h.hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _load_cache(
    cache_dir: Path,
    key: str,
    *,
    warnings_out: WarningsOut = None,
) -> dict | None:
    """Return cached envelope or None on miss / read error.

    W598: mirrors the W595 ``read_permit`` / W596 ``read_run_meta`` /
    W597 ``daemon_state`` plumb — when *warnings_out* is supplied, every
    silent-error site appends one structured closed-enum marker so
    callers can tell "cache file not on disk" (legitimate cold-cache
    sentinel — does NOT warn, mirrors W597's ``daemon_running`` missing
    PID-file discipline) from "cache file on disk but unreadable" from
    "JSON parsed but top-level not a dict". The ``None`` return on
    every drop path is PRESERVED — the None-return is the caller
    contract (it's how ``_try_cache_envelope`` projects cache-miss).
    ``warnings_out=None`` (default) preserves the pre-W598 silent-drop
    behaviour.

    Marker shape mirrors W595's ``read_permit`` / W596's
    ``read_run_meta`` / W597's ``daemon_state`` closed-enum vocabulary
    with a ``pr_analyze_cache_`` prefix so a caller threading the same
    bucket through multiple substrate read sites sees one uniform
    marker vocabulary.

    Intentional-absence decision (W978 + "Make fallback chains loud"):
    a missing cache file is the documented cold-cache sentinel — the
    common, expected path on first invocation. Warning here would train
    operators to ignore real warnings. The behaviour mirrors W597's
    ``daemon_running`` missing-pidfile discipline (legitimate "not
    running" → no warning) rather than W596's ``read_run_meta``
    missing-meta.json discipline (an operational anomaly worth
    surfacing). Schema-version mismatch is folded into ``_cache_key``
    so a version bump produces a different filename — there's no
    SchemaVersionMismatch path to surface at read time.

    Emitted kinds (closed enum):

      * ``pr_analyze_cache_read_failed:<path>:<exc_class>:<detail>`` —
        ``Path.read_text`` raised ``OSError`` (typically
        ``PermissionError`` / ``IsADirectoryError`` / generic
        ``OSError``). The cache file is on disk but unreadable.
      * ``pr_analyze_cache_corrupt:<path>:JSONDecodeError`` — the
        bytes parsed as something other than JSON.
      * ``pr_analyze_cache_corrupt:<path>:NotAJsonObject`` — JSON
        parsed cleanly but the top-level value was not a dict (the
        downstream ``_try_cache_envelope`` callsite indexes
        ``cached["cache_hit"]`` and ``cached.get("summary")``, so a
        non-dict cached payload is cache poisoning, not cold cache).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    p = _cache_path(cache_dir, key)
    if not p.exists():
        # Legitimate cold-cache sentinel — do NOT warn (mirrors W597's
        # ``daemon_running`` missing-pidfile discipline).
        return None
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        _emit(f"pr_analyze_cache_read_failed:{p}:{type(exc).__name__}:{exc}")
        return None
    except _json.JSONDecodeError:
        _emit(f"pr_analyze_cache_corrupt:{p}:JSONDecodeError")
        return None
    if not isinstance(raw, dict):
        _emit(f"pr_analyze_cache_corrupt:{p}:NotAJsonObject")
        return None
    return raw


def _save_cache(cache_dir: Path, key: str, bundle: dict) -> None:
    """Persist envelope to the cache. Best-effort; failures are silent."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(cache_dir, key).write_text(_json.dumps(bundle, indent=2), encoding="utf-8")
    except OSError:
        pass
