"""Detector version stamps for drift detection (Audit A6).

The detector registry in :mod:`roam.catalog.detectors` is function-based —
each detector is a ``(task_id, way_id, detect_fn)`` tuple rather than a
subclass of a common ABC. That makes the "stamp VERSION on the class"
pattern used by :class:`roam.bridges.base.LanguageBridge` and
:class:`roam.languages.base.LanguageExtractor` a poor fit.

This module is the alternative: a flat mapping from ``task_id`` to a
SemVer-flavoured version string. When a detector's matching predicate
changes meaningfully (e.g. the nested-lookup tightening in migration 51
that added the ``loop_eq_with_dependent_write`` signal), bump the entry
here. The manifest captures the full map under ``component_versions``
so downstream consumers can spot that a finding emitted on day N was
produced by a different predicate than the one running on day N+1.

Bumping discipline (same as VERSION on the ABCs):

* Initial release of a detector lands at ``"1.0.0"``.
* Bump the patch when the predicate changes but the finding semantics
  are stable (e.g. tightening to reduce false-positives).
* Bump the minor when the detector starts emitting findings on cases it
  previously missed (recall improvement).
* Bump the major when the finding *shape* changes — new evidence keys,
  different ``confidence`` calibration, renamed ``detected_way``.

Detectors not listed default to :data:`DEFAULT_VERSION` (``"1.0.0"``).
"""

from __future__ import annotations

DEFAULT_VERSION: str = "1.0.0"

# Per-detector version overrides, keyed by ``task_id`` (the first element
# of each registry tuple). Most detectors are still at ``1.0.0`` so the
# explicit map is small — listing every detector here would be noise.
# Bumping a value below makes the change visible to the manifest writer
# and any future ``roam doctor`` drift check.
DETECTOR_VERSION_OVERRIDES: dict[str, str] = {
    # nested-lookup tightened in migration 51 (added the
    # loop_eq_with_dependent_write predicate to cut a ~85% FP rate on
    # PHP streaming-CSV / matrix-render code). Pre-W21 indexes built
    # with the looser predicate still carry the old findings; consumers
    # comparing manifests can spot the bump and trigger re-detection.
    "nested-lookup": "1.1.0",
}


def detector_version(task_id: str) -> str:
    """Return the version string for the detector keyed by *task_id*.

    Falls back to :data:`DEFAULT_VERSION` when no override is registered.
    """
    return DETECTOR_VERSION_OVERRIDES.get(task_id, DEFAULT_VERSION)


def all_detector_versions(task_ids: list[str]) -> dict[str, str]:
    """Return ``{task_id: version}`` for every id in *task_ids*.

    Used by the manifest writer to capture the full version map at
    index time. Caller is responsible for supplying the list of
    currently-registered task ids — keeping the enumeration *out* of
    this module avoids importing :mod:`roam.catalog.detectors` (and
    transitively :mod:`roam.catalog.python_idioms`, the plugin
    registry, …) just to write the manifest.
    """
    return {task_id: detector_version(task_id) for task_id in task_ids}
