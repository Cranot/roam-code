"""Shared test helpers.

Modules in this package consolidate test-only constants and scanners
that previously sat duplicated across multiple ``tests/test_*.py``
files. Each consolidation mirrors the W506 / W518 lesson: one
canonical module per shared vocabulary, with a drift guard pinning
the import shape so future contributors can't silently re-introduce
the duplication.
"""

from __future__ import annotations
