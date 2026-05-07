"""Regression-FP corpus harness.

Loads every ``*.json`` file from ``tests/regression_fp_fixtures/`` and
parametrises one test per entry. Each fixture entry asserts that a
specific user-reported false-positive pattern stays suppressed. Adding
a new pattern is a one-file edit (no Python).

The four supported helpers map onto the small, side-effect-free
classifier functions inside ``roam.catalog.detectors``:

* ``in_memory_call`` — verify a call name resolves the same way the
  N+1/I/O detector resolves it. Optional ``framework`` field activates
  one of the bundled framework profiles for the assertion only.
* ``depth_guard_regex`` — verify the bounded-recursion regex matches
  (or doesn't) for a given snippet.
* ``dev_only_block`` — verify the DEV-gate detector classifies a body
  as dev-stripped (or not).
* ``call_awaited`` — verify the await heuristic catches a call.

If you need to assert on something the existing helpers can't express
yet, add the helper to ``_DISPATCH`` rather than special-casing in
the test body — keeps the corpus declarative and lets non-coders
contribute fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from roam.catalog.detectors import (
    _has_batch_iteration,
    _io_is_known_in_memory_call,
    _is_call_awaited_in_snippet,
    _is_dev_only_block,
    set_active_framework_profile,
)
from roam.commands.cmd_auth_gaps import _ancestor_has_constructor_auth
from roam.commands.cmd_migration_safety import _extract_arg, _has_try_catch_idempotency
from roam.commands.cmd_over_fetch import _BODY_SHAPING_PATTERNS

_FIXTURE_DIR = Path(__file__).parent / "regression_fp_fixtures"

# Mirror of the bounded-recursion regex from detectors.py so the corpus
# can declare expectations without importing a private inline. If the
# detector tightens its pattern, this constant must move with it.
_DEPTH_GUARD_PATTERN = re.compile(
    r"\b(?:depth|level|budget|remaining|hops|recursion_count|recursionDepth)\b"
    r"\s*(?:>|>=|<|<=)\s*(?:\d+|maxDepth|max_depth|MAX_DEPTH|max_recursion|MAX_RECURSION|0)\s*\)?\s*"
    r"\s*[:{]?\s*(?:return|raise|throw|break)"
    r"|--?\s*\b(?:depth|budget|remaining|hops)\b\s*[<>]=?\s*\d+\s*\)?\s*[:{]?\s*"
    r"(?:return|raise|throw|break)"
)


def _check_in_memory_call(entry: dict) -> bool:
    framework = entry.get("framework")
    if framework:
        try:
            set_active_framework_profile(framework)
            return _io_is_known_in_memory_call(entry["input"])
        finally:
            set_active_framework_profile(None)
    return _io_is_known_in_memory_call(entry["input"])


def _check_depth_guard(entry: dict) -> bool:
    return bool(_DEPTH_GUARD_PATTERN.search(entry["input"]))


def _check_dev_only_block(entry: dict) -> bool:
    return _is_dev_only_block(entry["input"])


def _check_call_awaited(entry: dict) -> bool:
    payload = entry["input"]
    return _is_call_awaited_in_snippet(payload["call"], payload["snippet"])


def _check_extract_arg_after(entry: dict) -> str:
    """E4 — extract_arg with after_token returns the table name correctly.

    Input shape: ``{"line": "...", "after_token": "create("}``. Expected
    is the table-name string the helper should return; the harness uses
    string equality (rather than the bool path) for this helper alone.
    """
    payload = entry["input"]
    return _extract_arg(payload["line"], after_token=payload.get("after_token"))


def _check_try_catch_idempotency(entry: dict) -> bool:
    return _has_try_catch_idempotency(entry["input"])


def _check_ancestor_constructor_auth(entry: dict) -> bool:
    """E2 — given the child source + a {parent_class: parent_source} map,
    the inheritance walker must find auth middleware in the parent.
    """
    payload = entry["input"]
    return _ancestor_has_constructor_auth(payload["source"], payload.get("class_source_map") or {})


def _check_body_shaping(entry: dict) -> bool:
    """E5 — does any of the over-fetch body-shaping patterns match the input?"""
    return any(p.search(entry["input"]) for p in _BODY_SHAPING_PATTERNS)


def _check_batch_iteration(entry: dict) -> bool:
    """DF2 — chunked iteration suppresses N+1."""
    return _has_batch_iteration(entry["input"])


_DISPATCH = {
    "in_memory_call": _check_in_memory_call,
    "depth_guard_regex": _check_depth_guard,
    "dev_only_block": _check_dev_only_block,
    "call_awaited": _check_call_awaited,
    "extract_arg_after": _check_extract_arg_after,
    "try_catch_idempotency": _check_try_catch_idempotency,
    "ancestor_constructor_auth": _check_ancestor_constructor_auth,
    "body_shaping": _check_body_shaping,
    "batch_iteration": _check_batch_iteration,
}


def _load_corpus() -> list[tuple[str, str, dict]]:
    """Yield (fixture_file, entry_name, entry_dict) for every loaded entry."""
    out: list[tuple[str, str, dict]] = []
    if not _FIXTURE_DIR.exists():
        return out
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("entries", []):
            out.append((path.name, entry["name"], entry))
    return out


_CORPUS = _load_corpus()


def test_corpus_has_entries():
    """Smoke-check: the corpus must not be empty (would otherwise silently pass)."""
    assert len(_CORPUS) > 0, f"no fixture entries discovered in {_FIXTURE_DIR}"


@pytest.mark.parametrize(
    ("fixture_file", "entry_name", "entry"),
    _CORPUS,
    ids=[f"{f}::{n}" for f, n, _ in _CORPUS],
)
def test_regression_fp_fixture(fixture_file: str, entry_name: str, entry: dict):
    helper = entry.get("helper")
    if helper not in _DISPATCH:
        pytest.fail(f"{fixture_file}::{entry_name} uses unknown helper '{helper}'. Supported: {sorted(_DISPATCH)}.")
    expected = entry["expect"]
    actual = _DISPATCH[helper](entry)
    assert actual == expected, (
        f"{fixture_file}::{entry_name} regressed:\n"
        f"  description: {entry.get('description', '')}\n"
        f"  helper: {helper}\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}"
    )
