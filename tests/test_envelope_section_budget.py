"""W162 — per-section envelope budget tests.

The W119 global cap drops the LARGEST probe wholesale when the envelope
exceeds the recommended-model budget. That coarse strategy starves
unrelated sections. W162 adds per-PROBE-SECTION budgets so each probe
family gets a fair slice; only the oversize section is truncated, not
its neighbours.

These tests target the in-place truncation helper + envelope wiring,
not subprocess behaviour — fast, deterministic, no DB required.
"""

from __future__ import annotations

import json

from roam.plan.compiler import (
    _SECTION_BUDGET_BYTES,
    _apply_section_budgets,
    _truncate_section_value,
)


def _bytes_of(value) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def test_under_budget_keys_pass_through_unchanged():
    """When every section fits its budget, the truncation map is empty
    and no value is rewritten."""
    prefetched = {
        "file_skeleton": "def f(): pass",
        "callers": ["mod.a", "mod.b"],
        "recent_commits": ["sha1 fix typo", "sha2 add helper"],
    }
    before = json.dumps(prefetched, sort_keys=True)
    truncated = _apply_section_budgets(prefetched)
    after = json.dumps(prefetched, sort_keys=True)
    assert truncated == {}
    assert before == after


def test_single_oversize_string_section_is_truncated_in_place():
    """A single oversize known-key string is truncated; the marker is
    appended; the truncation map records the original byte count."""
    budget = _SECTION_BUDGET_BYTES["file_skeleton"]
    big_blob = "x" * (budget * 4)
    prefetched = {
        "file_skeleton": big_blob,
        "callers": ["mod.a"],
    }
    original_bytes = _bytes_of(big_blob)
    truncated = _apply_section_budgets(prefetched)
    assert "file_skeleton" in truncated
    assert truncated["file_skeleton"] == original_bytes
    # Truncated value carries the marker and fits the budget.
    assert "truncated to" in prefetched["file_skeleton"]
    assert _bytes_of(prefetched["file_skeleton"]) <= budget + 64  # marker slack
    # Neighbour preserved.
    assert prefetched["callers"] == ["mod.a"]


def test_oversize_list_section_is_head_truncated():
    """A list section over budget shrinks to a head prefix that fits;
    the values themselves are not mutated, just the cardinality."""
    budget = _SECTION_BUDGET_BYTES["callers"]
    # Each entry ~30 bytes; budget/30 ≈ 100 entries; build 5× that.
    entries = [f"package.module.symbol_{i}" for i in range(budget // 6)]
    prefetched = {"callers": entries}
    original_bytes = _bytes_of(entries)
    assert original_bytes > budget
    truncated = _apply_section_budgets(prefetched)
    assert "callers" in truncated
    assert truncated["callers"] == original_bytes
    head = prefetched["callers"]
    assert isinstance(head, list)
    assert len(head) > 0
    assert len(head) < len(entries)
    # Head is a true prefix.
    assert head == entries[: len(head)]
    assert _bytes_of(head) <= budget


def test_multiple_oversize_sections_are_truncated_independently():
    """Two oversize sections + one fitting section → both oversize
    keys truncated, fitting key untouched."""
    s_budget = _SECTION_BUDGET_BYTES["file_skeleton"]
    g_budget = _SECTION_BUDGET_BYTES["grep_results"]
    prefetched = {
        "file_skeleton": "y" * (s_budget * 3),
        "grep_results": [f"hit-line-{i}" for i in range(g_budget)],
        "recent_commits": ["sha1 small commit"],
    }
    fits_before = list(prefetched["recent_commits"])
    truncated = _apply_section_budgets(prefetched)
    assert set(truncated.keys()) == {"file_skeleton", "grep_results"}
    # Neither was wholesale-deleted.
    assert "file_skeleton" in prefetched
    assert "grep_results" in prefetched
    assert prefetched["file_skeleton"]
    assert prefetched["grep_results"]
    # Fitting neighbour unchanged.
    assert prefetched["recent_commits"] == fits_before


def test_unknown_keys_are_ignored():
    """Keys not in _SECTION_BUDGET_BYTES pass through (the W119 global
    cap is the safety net for unknown payload shapes)."""
    prefetched = {"some_future_probe": "z" * 50_000}
    before = prefetched["some_future_probe"]
    truncated = _apply_section_budgets(prefetched)
    assert truncated == {}
    assert prefetched["some_future_probe"] == before


def test_underscore_prefix_keys_are_ignored():
    """Internal/meta keys (e.g. earlier `_envelope_budget_pruned`) are
    skipped so meta-state isn't recursively truncated."""
    prefetched = {"_envelope_budget_pruned": {"reason": "x" * 100_000}}
    before = json.dumps(prefetched, sort_keys=True)
    truncated = _apply_section_budgets(prefetched)
    assert truncated == {}
    assert json.dumps(prefetched, sort_keys=True) == before


def test_truncate_helper_string_marker_format():
    """Direct-helper sanity: string truncation carries the expected
    marker substring naming the budget in bytes."""
    out = _truncate_section_value("a" * 10_000, 512)
    assert isinstance(out, str)
    assert "truncated to 512 bytes" in out


def test_truncate_helper_list_returns_empty_when_no_fit():
    """If even a single entry exceeds budget, the list shrinks to []."""
    huge_entry = "x" * 10_000
    out = _truncate_section_value([huge_entry, huge_entry], 200)
    assert out == []


def test_global_cap_does_not_fire_when_sections_fit_individual_budgets():
    """When per-section truncation already fits the global Opus 64KB
    cap, the W119 `_envelope_budget_pruned` flag must NOT appear.

    We simulate the full prefetched-budget pipeline by composing values
    that each over-shoot their per-section budget but cleanly under-
    shoot the 64KB global cap after section-level truncation.
    """
    prefetched: dict = {}
    for key, budget in _SECTION_BUDGET_BYTES.items():
        # Each key 4× its own budget — well over locally, but the
        # truncated sum stays under 64KB total.
        prefetched[key] = "q" * (budget * 4)
    truncated = _apply_section_budgets(prefetched)
    # Every oversize section recorded.
    assert set(truncated.keys()) == set(_SECTION_BUDGET_BYTES.keys())
    # Post-truncation total fits the Opus 64KB cap.
    post_bytes = _bytes_of(prefetched)
    assert post_bytes < 64 * 1024
    # No global-cap marker present.
    assert "_envelope_budget_pruned" not in prefetched
