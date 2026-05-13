"""Shared quality-metric helpers.

This package hosts metric computations that are canonical across multiple
commands. Each module here is the single source of truth for one metric
(per Pattern 3 of the dogfood SYNTHESIS — "Vocabulary mismatch across
commands. ... Either standardize the metric OR label every field with its
precise definition").

Current modules:

- ``ai_rot`` — Canonical AI rot score (8 weighted anti-pattern detectors).
  Owned-by: ``roam vibe-check``. Consumed by: ``roam dashboard``.
- ``cycles`` — Canonical dependency-cycle counts (total + actionable +
  informational). Owned-by: ``roam.graph.cycles.find_cycles``. Consumed
  by: ``roam health``, ``roam describe``, ``roam agent-export``.
- ``god_components`` — Canonical god-component count (degree-thresholded,
  utility-aware). Owned-by: ``cmd_health``'s algorithm. Consumed by:
  ``roam health``, ``roam fingerprint``, ``roam agent-export``.
- ``public_symbols`` — Canonical public-symbol counts under both
  inclusion criteria (``no_underscore_prefix`` and
  ``has_export_marker``). Owned-by: this module (the chasm is real, not
  a bug). Consumed by: ``roam api``, ``roam docs-coverage``.
"""

from __future__ import annotations
