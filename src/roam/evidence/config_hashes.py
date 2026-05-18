"""W1255 - canonical config-file hashes for evidence_stale drift detection.

The W210 :class:`ChangeEvidence` scaffold added three "version + config
linking" fields (``rules_config_hash`` / ``constitution_hash`` /
``control_map_hash``) but never named the on-disk paths a producer
should hash. W1255 resolves the architectural fork (Cranot decision,
/loop session 2026-05-16) by establishing the canonical layout:

* ``.roam-rules.yml``         - project root, NEW canonical rules-config path
* ``.roam/constitution.yml``  - top-level, existing constitution path
* ``.roam/control-map.yml``   - top-level, NEW canonical control-mapping path

This module owns ONE responsibility: given a repo root, compute the
sha256 hash of each canonical config file. Missing files hash to the
empty string (insufficient-data discipline per W1234: missing data is
NOT a positive signal). This unblocks W1253 hash-drift detection - the
collector can stamp the active hashes on every packet and a verifier
can later compare those hashes against what the files contain at audit
time.

Determinism contract:

* ``compute_config_hash`` reads bytes directly and returns
  ``hashlib.sha256(...).hexdigest()`` - 64 lowercase hex chars.
* Missing file -> empty string ``""``. NOT ``None``, NOT a sentinel
  hash. Consumers that need to distinguish "absent" from "computed but
  empty" can check for the empty string.
* ``stamp_all`` returns a dict keyed by the W210 field name on
  :class:`roam.evidence.ChangeEvidence`. Stable ordering: rules config
  first, constitution second, control-map third (mirrors the order the
  fields appear in the dataclass).

W600 lineage disclosure ("Make fallback chains loud", agi-in-md
CP45/CP46/CP52/CP53). The empty-string return value alone cannot
distinguish "file deliberately absent (clean state)" from "file exists
but unreadable (operational anomaly)". Both ``compute_config_hash``
and ``stamp_all`` accept an optional ``warnings_out`` keyword that, when
supplied, receives closed-enum lineage markers:

* ``config_hash_<scope>_not_found:<rel_path>`` — informational. The
  file does not exist on disk. This is the COMMON, EXPECTED case (the
  three canonical paths are user-owned expectations; a repo without
  ``.roam/control-map.yml`` is not broken). Marker discipline mirrors
  W596 ``run_meta_not_found`` — distinguishes "absent" from
  "anomalously broken".
* ``config_hash_<scope>_read_failed:<rel_path>:<exc_class>:<detail>``
  — operational anomaly. The file exists but ``Path.read_bytes()``
  raised (permission denied, deleted mid-read, I/O error, etc.).
  Marker discipline mirrors W596 ``run_meta_read_failed``.

W978 first-hypothesis check: ``compute_config_hash`` is a RAW BYTE
HASH — ``hashlib.sha256(read_bytes()).hexdigest()``. There is no
parse step, so there is NO ``_corrupt:<exc_class>`` failure path. The
W596/W598 third marker (parse-corruption) is N/A here; the closed
enum has exactly 2 kinds per scope.

Scope short-names map from the W210 field name by stripping the
``_hash`` suffix: ``rules_config_hash`` → ``rules_config``,
``constitution_hash`` → ``constitution``, ``control_map_hash`` →
``control_map``. The mapping is stable: each W210 field gets exactly
one scope short-name, exposed via ``SCOPE_NAMES``.

The empty-string return is PRESERVED — every silent return path
remains byte-identical to pre-W600 behaviour. ``warnings_out=None``
(default) preserves the silent-empty contract; existing callers
(``runs/ledger.start_run`` + ``config_hashes_producer.current_hashes_or_none``)
need no changes.

Forbidden:

* This module MUST NOT create any of the three files. The canonical
  paths are EXPECTATIONS - users / projects own the files. The substrate
  only computes hashes when the files exist.
* No I/O beyond ``Path.read_bytes()`` on existing files.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Mapping

from roam.output.formatter import WarningsOut

# Canonical relative paths, keyed by the W210 ChangeEvidence field name
# that consumes the hash. The ordering matches the dataclass field
# order so stamp_all output reads naturally next to ChangeEvidence
# fields in logs and tests.
CANONICAL_PATHS: Mapping[str, str] = {
    "rules_config_hash": ".roam-rules.yml",
    "constitution_hash": ".roam/constitution.yml",
    "control_map_hash": ".roam/control-map.yml",
}

# Scope short-names for the W600 closed-enum warning markers. Derived
# from the W210 field by stripping the ``_hash`` suffix so the marker
# vocabulary aligns with the existing W210 vocabulary an operator
# already sees on RunMeta.extra / ChangeEvidence.
SCOPE_NAMES: Mapping[str, str] = {
    "rules_config_hash": "rules_config",
    "constitution_hash": "constitution",
    "control_map_hash": "control_map",
}


def compute_config_hash(
    root: pathlib.Path,
    rel_path: str,
    *,
    scope: str | None = None,
    warnings_out: WarningsOut = None,
) -> str:
    """Return sha256 hex digest of ``root / rel_path``, or ``""`` if absent.

    Missing-file semantics: insufficient-data discipline (W1234). We
    return the empty string so consumers can stamp the field without
    fabricating a hash that looks computed. Downstream verifiers
    distinguish "hash absent" from "hash mismatch" by checking for
    ``""`` explicitly.

    W600 lineage disclosure: when ``warnings_out`` is supplied, this
    function appends ONE closed-enum marker per call when the silent
    empty-string path is taken. The empty-string return value is
    preserved on every path. ``warnings_out=None`` (default) preserves
    pre-W600 silent behaviour. ``scope`` is the marker namespace —
    callers usually pass the W210 field name (e.g. ``rules_config``);
    when ``None``, ``rel_path`` is used verbatim so an ad-hoc caller
    still gets a usable marker.

    Closed-enum markers (exactly 2 kinds, per W978 first-hypothesis
    discipline — this is a raw-byte hash with no parse step):

    * ``config_hash_<scope>_not_found:<rel_path>`` — informational
      (file absent; intentional-absence case mirrors W597
      ``daemon_state_not_found`` discipline).
    * ``config_hash_<scope>_read_failed:<rel_path>:<exc_class>:<detail>``
      — operational anomaly (file exists but ``read_bytes()`` raised).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    scope_token = scope if scope is not None else rel_path
    abs_path = pathlib.Path(root) / rel_path
    if not abs_path.exists():
        _emit(f"config_hash_{scope_token}_not_found:{rel_path}")
        return ""
    try:
        payload = abs_path.read_bytes()
    except OSError as exc:
        _emit(f"config_hash_{scope_token}_read_failed:{rel_path}:{type(exc).__name__}:{exc}")
        return ""
    return hashlib.sha256(payload).hexdigest()


def stamp_all(
    root: pathlib.Path,
    *,
    warnings_out: WarningsOut = None,
) -> dict[str, str]:
    """Compute all three canonical config hashes for *root*.

    Returns a dict mapping the W210 :class:`ChangeEvidence` field name
    (``rules_config_hash`` / ``constitution_hash`` / ``control_map_hash``)
    to the sha256 hex digest (or ``""`` for absent files).

    The returned dict is freshly constructed on every call - callers
    may mutate it without affecting subsequent invocations.

    W600 lineage disclosure: when ``warnings_out`` is supplied, every
    silent empty-string path emits one closed-enum marker tagged with
    the W210-aligned scope short-name (``rules_config`` /
    ``constitution`` / ``control_map``). The dict values are unchanged
    from the pre-W600 contract — caller contract preserved.
    ``warnings_out=None`` (default) preserves silent behaviour for the
    two live callsites (``runs/ledger.start_run`` and
    ``config_hashes_producer.current_hashes_or_none``).
    """
    return {
        field: compute_config_hash(
            root,
            rel_path,
            scope=SCOPE_NAMES[field],
            warnings_out=warnings_out,
        )
        for field, rel_path in CANONICAL_PATHS.items()
    }


__all__ = [
    "CANONICAL_PATHS",
    "SCOPE_NAMES",
    "compute_config_hash",
    "stamp_all",
]
