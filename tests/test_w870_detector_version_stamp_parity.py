"""Parity lint for per-detector version stamps (W870 / W940 Gate 1).

Three places in ``src/roam/commands/cmd_smells.py`` interact with smell-
detector version stamps and must stay coherent:

1. The composite ``SMELLS_DETECTOR_VERSION`` constant — the canonical
   fallback every detector inherits when it has no per-id constant.
2. Per-detector ``<NAME>_DETECTOR_VERSION`` constants — opt-in stamps
   that override the composite for one ``smell_id`` (W81 substrate
   discipline: every detector owns its version at the call-site).
3. The ``ALL_DETECTORS`` registry in ``src/roam/catalog/smells.py`` —
   the ground-truth list of registered ``smell_id`` strings.

This lint pins three invariants:

- Every registered ``smell_id`` has *some* version source (composite or
  per-id). No detector silently emits findings with a missing version
  field.
- Every per-id ``<NAME>_DETECTOR_VERSION`` string is canonical semver
  (``<major>.<minor>.<patch>``). Catches typos.
- Every per-id constant corresponds to a registered ``smell_id``. No
  orphan stamps left behind after a detector is removed.

W940 Gate 1: ``smell-detector P0 surface is a derived view, not a
hand-maintained table, and future detector additions require no
parallel edits``. This is the lint half of that gate (W871-bulk
handled the migration half).
"""

from __future__ import annotations

import re

import roam.commands.cmd_smells as cmd_smells_module
from roam.catalog.smells import ALL_DETECTORS

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_VERSION_SUFFIX = "_DETECTOR_VERSION"
_COMPOSITE_NAME = "SMELLS_DETECTOR_VERSION"


def _detector_version_constants() -> dict[str, str]:
    """Return ``{smell_id: version_string}`` for per-detector stamps.

    Converts ``BOOLEAN_PARAMETER_DETECTOR_VERSION`` -> ``boolean-parameter``
    via lowercase + underscore-to-hyphen. The composite
    ``SMELLS_DETECTOR_VERSION`` is excluded — it is the fallback, not a
    per-id stamp.
    """
    result: dict[str, str] = {}
    for name in dir(cmd_smells_module):
        if not name.endswith(_VERSION_SUFFIX):
            continue
        if name == _COMPOSITE_NAME:
            continue
        prefix = name[: -len(_VERSION_SUFFIX)]
        smell_id = prefix.lower().replace("_", "-")
        result[smell_id] = getattr(cmd_smells_module, name)
    return result


def test_every_detector_has_a_version_source() -> None:
    """Every registered smell_id has a version source — composite or per-id.

    The composite ``SMELLS_DETECTOR_VERSION`` is the canonical fallback
    that covers all detectors lacking a per-id stamp. Without it, any
    detector without a per-id constant would emit findings with no
    version field, breaking downstream version-aware diff.
    """
    composite = getattr(cmd_smells_module, _COMPOSITE_NAME, None)
    assert composite is not None, (
        f"{_COMPOSITE_NAME} composite missing from cmd_smells.py — "
        f"required as the fallback for detectors without a per-id stamp."
    )
    assert _VERSION_RE.match(composite), (
        f"{_COMPOSITE_NAME} = {composite!r} is not canonical semver (<major>.<minor>.<patch>)."
    )
    assert len(ALL_DETECTORS) > 0, "ALL_DETECTORS is empty — registry not loaded?"


def test_per_detector_version_constants_are_canonical() -> None:
    """Every ``<NAME>_DETECTOR_VERSION`` value matches semver ``<N>.<N>.<N>``."""
    constants = _detector_version_constants()
    invalid = sorted((smell_id, v) for smell_id, v in constants.items() if not _VERSION_RE.match(v))
    if invalid:
        rows = "\n".join(f"    {smell_id!r}: {v!r}" for smell_id, v in invalid)
        raise AssertionError(
            f"Non-canonical version strings in cmd_smells.py:\n{rows}\n"
            f"   Expected shape: <major>.<minor>.<patch> (e.g. '1.4.0')."
        )


def test_no_orphan_version_constants() -> None:
    """Every per-id version constant maps to a registered ``smell_id``.

    Orphan constants (kept after the detector was removed from
    ``ALL_DETECTORS``) are dead config and surface as a sparse-stamp
    drift bug.
    """
    registered = {smell_id for smell_id, _ in ALL_DETECTORS}
    constants = _detector_version_constants()
    orphans = sorted(set(constants) - registered)
    if orphans:
        raise AssertionError(
            f"Orphan <NAME>_DETECTOR_VERSION constants in cmd_smells.py: "
            f"{orphans}\n"
            f"   Each constant must correspond to a smell_id in ALL_DETECTORS "
            f"(src/roam/catalog/smells.py)."
        )
