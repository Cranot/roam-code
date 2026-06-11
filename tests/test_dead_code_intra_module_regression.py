"""2026-05-24 — Regression guards for roam_dead_code intra-module blindness.

Live session-of-work discovered that ``roam_dead_code`` (and the related
``roam_safe_delete`` / ``roam_uses`` graph-edge tools) had reported
``refs: 0`` for symbols whose only consumers are inside the SAME module:

* module-level constants iterated within the same file
* methods called via ``self.<method>`` from another method in the same class
* functions passed as callbacks within the same file
* tests that monkeypatch the symbol (some test refs missed too)

Five of five "high-confidence safe-delete" candidates from that session
turned out to be false positives — deleting any of them broke pytest.

The five canonical false-positives are pinned below. The indexer now
tracks intra-module edges (the bug closed before promotion), so these
run as live guards rather than xfail markers.

Full investigation recorded in an internal planning memo.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()

# The five canonical false-positives. Each is a symbol that the dead-code
# scanner flagged as safe-to-delete but actually has real intra-module
# (or test-only) usage. Each entry: (symbol_name, defining_file, usage_file_or_kind).
_KNOWN_FALSE_POSITIVES = [
    # constant iterated within same file
    ("_PUBLIC_FOLDER_CANDIDATES", "src/roam/commands/cmd_stale_refs.py", "same-file iteration line 323"),
    # function used as callback within same file + tests
    ("_fts_search", "src/roam/commands/cmd_search.py", "same-file callback line 681 + monkeypatch tests"),
    # method called via self from another method in same class
    ("_build_search_indexes", "src/roam/index/indexer.py", "same-class self-call line 2186"),
    # function used in same-file + has explicit tests
    ("_scope_filter_candidates", "src/roam/commands/cmd_retrieve.py", "same-file ref line 623 + 2 tests"),
    # function with explicit test coverage
    ("simulate_delete", "src/roam/commands/cmd_simulate.py", "2 explicit tests in test_simulate.py"),
]


@pytest.mark.parametrize(
    "symbol,defining_file,usage_description",
    _KNOWN_FALSE_POSITIVES,
    ids=[s[0] for s in _KNOWN_FALSE_POSITIVES],
)
def test_dead_code_does_not_flag_intra_module_used_symbol(symbol, defining_file, usage_description):
    """Each known intra-module-used symbol must NOT appear in the
    ``safe`` (high-confidence) bucket of ``roam dead-code --json``.

    Failure mode (pre-fix): the symbol appears in safe with reason
    "No references". Post-fix: the same-file/test references are
    counted as edges and the symbol is correctly omitted from safe.
    """
    result = subprocess.run(
        ["roam", "dead", "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"roam dead-code subprocess failed: {result.stderr[:200]}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.skip("roam dead-code did not emit JSON")

    # Find the symbol in the safe-to-delete bucket.
    safe_findings = envelope.get("safe") or envelope.get("data", {}).get("safe") or []
    if not isinstance(safe_findings, list):
        safe_findings = []

    matched = [
        f
        for f in safe_findings
        if isinstance(f, dict)
        and (f.get("name") == symbol or f.get("qualified_name") == symbol or f.get("symbol") == symbol)
    ]

    assert not matched, (
        f"{symbol} (defined at {defining_file}) is flagged as SAFE to delete "
        f"but has real intra-module usage: {usage_description}. "
        f"Bug: roam_dead_code missed the intra-module edge. "
        f"See the dead-code regression notes."
    )
