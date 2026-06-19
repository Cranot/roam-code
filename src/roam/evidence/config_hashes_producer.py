"""W1279 - producer-side wire-up for W1253 config-hash drift detection.

W1255-IMPL (in :mod:`roam.evidence.config_hashes`) defines the canonical
config-file paths AND the on-disk hashing primitive. It also stamps the
three hashes into ``RunMeta.extra`` at :func:`roam.runs.ledger.start_run`
time. W1253 (in :mod:`roam.evidence.collector`) accepts two new kwargs
(``packet_config_hashes`` + ``current_config_hashes``) and flips
``evidence_stale=True`` whenever the two diverge.

This module is the THIN producer-side glue that lifts the packet hashes
from a run's ``meta.json`` AND computes the current on-disk hashes,
giving every producer (pr-bundle emit, pr-replay collector, emit_vsa
attest paths) the same one-call entry point.

Single responsibility:

* :func:`lift_packet_hashes` reads ``meta.json`` via
  :func:`roam.runs.ledger.read_run_meta` and projects the three W1255
  ``extra`` keys into the dict shape :func:`collect_change_evidence`
  expects. Returns ``None`` when ``run_id`` is falsy or the meta file
  is unreadable (insufficient-data discipline mirrors W1234 - missing
  data is NOT a positive drift signal).
* :func:`current_hashes_or_none` is a thin wrapper around
  :func:`roam.evidence.config_hashes.stamp_all` that returns ``None``
  on filesystem errors instead of raising. Producers can pass it
  straight through to ``collect_change_evidence``.
* :func:`gather_hash_kwargs` composes both calls and returns a kwargs
  dict ready to splat into ``collect_change_evidence(**kwargs)``.

This module MUST NOT modify ``config_hashes.py`` (W1255-IMPL scope)
or ``collector.py`` (W1253 scope) or ``runs/ledger.py`` (W1255-IMPL
scope). It is pure read-side glue.

Forbidden:

* No new hashing logic - delegates to ``stamp_all``.
* No new ``meta.json`` parsing - delegates to ``read_run_meta``.
* No raising on missing inputs - every degraded path returns ``None``
  so the collector falls back to "no drift detected" cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roam.evidence.config_hashes import CANONICAL_PATHS, stamp_all


def lift_packet_hashes(
    repo_root: Path,
    run_id: str | None,
) -> dict[str, str] | None:
    """Lift the three W1255 hashes from ``RunMeta.extra``.

    Returns ``None`` (NOT an empty dict) when:

    * ``run_id`` is falsy / empty,
    * the meta file does not exist,
    * the meta file is unreadable / corrupt,
    * ``RunMeta.extra`` is missing all three keys.

    A ``None`` return tells the collector "no packet-side hash data
    available; do not flip evidence_stale on hash drift this packet" -
    the W1234 insufficient-data discipline. Returning an all-empty dict
    instead would also produce no drift (the collector skips empty
    strings) but ``None`` is more honest at the API boundary.

    When at least one key is present on extra, returns a dict with all
    three keys populated - missing keys default to ``""`` so the
    collector's empty-string-skip discipline still kicks in.
    """
    if not run_id:
        return None
    # Lazy import to avoid pulling the ledger module into evidence
    # initialisation - producers import this helper, the ledger module
    # pulls in HMAC + atomic_io + a Path-heavy startup cost.
    try:
        from roam.runs.ledger import read_run_meta
    except ImportError:  # pragma: no cover - defensive
        return None
    try:
        meta = read_run_meta(repo_root, run_id)
    except Exception:  # noqa: BLE001 - producer must not crash on bad meta
        return None
    if meta is None:
        return None
    extra = getattr(meta, "extra", None) or {}
    # No hash keys at all -> insufficient data, signal None.
    if not any(field in extra for field in CANONICAL_PATHS):
        return None
    out: dict[str, str] = {}
    for field in CANONICAL_PATHS:
        value = extra.get(field, "")
        # Coerce non-string values (forward-compat: someone stamps an int
        # by accident) to the empty string rather than raising. The
        # collector's hex-only contract will then treat the field as
        # absent.
        if not isinstance(value, str):
            value = ""
        out[field] = value
    return out


def current_hashes_or_none(repo_root: Path) -> dict[str, str] | None:
    """Compute the three current on-disk hashes; return ``None`` on error.

    Wraps :func:`roam.evidence.config_hashes.stamp_all` with an
    exception swallow so a transient filesystem failure (permissions,
    deleted-mid-read, etc.) degrades to "no drift detection" instead of
    aborting the producer. Returns the stamped dict directly when the
    on-disk read succeeds.
    """
    try:
        return stamp_all(repo_root)
    except Exception:  # noqa: BLE001 - producer must stay crash-safe
        return None


def gather_hash_kwargs(
    repo_root: Path,
    run_id: str | None,
) -> dict[str, Any]:
    """Compose both lifts into kwargs ready for ``collect_change_evidence``.

    Returns a dict that may contain ``packet_config_hashes`` and/or
    ``current_config_hashes`` keys (each ``Mapping[str, str] | None``).
    Splat into the collector::

        kwargs = gather_hash_kwargs(repo_root, run_id)
        packet, warnings = collect_change_evidence(..., **kwargs)

    The collector skips drift detection when either side is ``None`` -
    so missing run-meta gracefully degrades to "packet records on-disk
    hashes; no drift flag set" without any caller branching.
    """
    return {
        "packet_config_hashes": lift_packet_hashes(repo_root, run_id),
        "current_config_hashes": current_hashes_or_none(repo_root),
    }


__all__ = [
    "current_hashes_or_none",
    "gather_hash_kwargs",
    "lift_packet_hashes",
]
