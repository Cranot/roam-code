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


def _load_cache(cache_dir: Path, key: str) -> dict | None:
    """Return cached envelope or None on miss / read error."""
    p = _cache_path(cache_dir, key)
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return None


def _save_cache(cache_dir: Path, key: str, bundle: dict) -> None:
    """Persist envelope to the cache. Best-effort; failures are silent."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(cache_dir, key).write_text(_json.dumps(bundle, indent=2), encoding="utf-8")
    except OSError:
        pass
