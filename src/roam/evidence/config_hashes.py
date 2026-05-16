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

# Canonical relative paths, keyed by the W210 ChangeEvidence field name
# that consumes the hash. The ordering matches the dataclass field
# order so stamp_all output reads naturally next to ChangeEvidence
# fields in logs and tests.
CANONICAL_PATHS: Mapping[str, str] = {
    "rules_config_hash": ".roam-rules.yml",
    "constitution_hash": ".roam/constitution.yml",
    "control_map_hash": ".roam/control-map.yml",
}


def compute_config_hash(root: pathlib.Path, rel_path: str) -> str:
    """Return sha256 hex digest of ``root / rel_path``, or ``""`` if absent.

    Missing-file semantics: insufficient-data discipline (W1234). We
    return the empty string so consumers can stamp the field without
    fabricating a hash that looks computed. Downstream verifiers
    distinguish "hash absent" from "hash mismatch" by checking for
    ``""`` explicitly.
    """
    abs_path = pathlib.Path(root) / rel_path
    if not abs_path.exists():
        return ""
    return hashlib.sha256(abs_path.read_bytes()).hexdigest()


def stamp_all(root: pathlib.Path) -> dict[str, str]:
    """Compute all three canonical config hashes for *root*.

    Returns a dict mapping the W210 :class:`ChangeEvidence` field name
    (``rules_config_hash`` / ``constitution_hash`` / ``control_map_hash``)
    to the sha256 hex digest (or ``""`` for absent files).

    The returned dict is freshly constructed on every call - callers
    may mutate it without affecting subsequent invocations.
    """
    return {field: compute_config_hash(root, rel_path) for field, rel_path in CANONICAL_PATHS.items()}


__all__ = [
    "CANONICAL_PATHS",
    "compute_config_hash",
    "stamp_all",
]
