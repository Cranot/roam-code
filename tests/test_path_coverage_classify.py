"""Unit tests for the pure path-coverage classifiers.

``cmd_path_coverage._classify_risk`` and ``_suggest_test_points`` are pure
functions with closed-enum vocabularies (W547 / W718 risk band canonical
names) and a greedy set-cover contract — they are surgical to unit-test
without standing up a SQLite fixture.

Existing coverage (``tests/test_path_coverage.py``) is integration-style:
it spins up an indexed project and asserts the JSON envelope shape. The
pure-function branch matrix below was uncovered until this file.

Coverage gap source: ``roam path-coverage --json`` reports 154 critical
+ 176 high untested paths on roam-code itself; ``_classify_risk`` is the
function that stamps every one of those bands. Wrong band -> every
SARIF/JSON consumer downstream is wrong; ``_suggest_test_points`` is
the W631-anchored greedy cover used to recommend the minimum-impact
test-insertion set.
"""

from __future__ import annotations

from roam.commands.cmd_path_coverage import (
    _DESTRUCTIVE_EFFECTS,
    _classify_risk,
    _suggest_test_points,
)

# ---------------------------------------------------------------------------
# _classify_risk — closed-enum band assignment
# ---------------------------------------------------------------------------


def test_classify_risk_critical_when_zero_tested_and_destructive_sink() -> None:
    """Zero tested nodes + writes_db sink -> ``critical``.

    Canonical W718 vocabulary: lowercase band names. The destructive
    effect set lives at module scope (``_DESTRUCTIVE_EFFECTS``) so this
    test pins both the band string and the effect-membership table.
    """
    # 3-node path: entry (1) -> mid (2) -> sink (3); sink writes DB.
    path = [1, 2, 3]
    tested: set[int] = set()  # nothing tested
    sink_effects = {3: "writes_db"}

    assert _classify_risk(path, tested, sink_effects) == "critical"
    # Destructive-effect vocabulary is closed (W718).
    assert "writes_db" in _DESTRUCTIVE_EFFECTS


def test_classify_risk_high_when_zero_tested_and_non_destructive_sink() -> None:
    """Zero tested nodes + non-destructive sink -> ``high``.

    network / filesystem effects are NOT in the destructive set; they
    still produce a ``high`` band because the path is entirely
    untested.
    """
    path = [10, 20, 30]
    tested: set[int] = set()
    # network effect — sensitive but not destructive per
    # _DESTRUCTIVE_EFFECTS.
    sink_effects = {30: "network"}

    assert _classify_risk(path, tested, sink_effects) == "high"
    # Documented invariant: only writes_db is destructive today.
    assert "network" not in _DESTRUCTIVE_EFFECTS
    assert "filesystem" not in _DESTRUCTIVE_EFFECTS


def test_classify_risk_medium_when_only_entry_tested() -> None:
    """Entry-only-tested path -> ``medium``.

    The W718 carve-out: when the FIRST node of the path is in
    ``tested_set`` but the rest is not, the path's *interior* has zero
    test edges. This is meaningfully different from a fully-untested
    path (``high``) AND from a fully-tested one (``low``); ``medium``
    is the documented compromise.
    """
    path = [100, 200, 300]
    tested = {100}  # entry only
    sink_effects = {300: "writes_db"}

    # Even with a writes_db sink, an entry-tested path stays ``medium``
    # — the destructive-sink branch only escalates when tested_count is
    # zero (per the source-order if/elif chain).
    assert _classify_risk(path, tested, sink_effects) == "medium"


def test_classify_risk_low_when_majority_tested() -> None:
    """Tested ratio >= 0.5 -> ``low``.

    Source contract (line 243-244): ``ratio = tested_count / total;
    if ratio >= 0.5: return 'low'``. Exactly-half is included because
    the comparison is ``>=`` not ``>``.
    """
    # 2 of 4 tested = ratio 0.5 (boundary).
    path = [1, 2, 3, 4]
    tested = {1, 2}
    # The entry-only carve-out only fires when tested_count == 1, so
    # tested_count == 2 falls through to the ratio check.
    assert _classify_risk(path, tested, {4: ""}) == "low"

    # 3 of 4 tested -> well above 0.5 -> ``low``.
    assert _classify_risk(path, {1, 2, 3}, {4: ""}) == "low"


def test_classify_risk_medium_when_tested_ratio_below_half_but_not_entry_only() -> None:
    """Tested ratio < 0.5 with a non-entry node tested -> ``medium``.

    Branch coverage: tested_count > 1 (so not the entry-only carve-out)
    AND ratio < 0.5 (so not ``low``). The fallback branch returns
    ``medium``.
    """
    # 2 of 5 tested, neither is the entry -> ratio 0.4 < 0.5.
    path = [1, 2, 3, 4, 5]
    tested = {3, 4}  # middle nodes tested, entry (1) is NOT
    assert _classify_risk(path, tested, {5: ""}) == "medium"


def test_classify_risk_band_vocabulary_is_lowercase_only() -> None:
    """W547 / W718 invariant: every returned band is lowercase.

    Pre-W718 the labels were UPPER-cased (CRITICAL / HIGH / MEDIUM /
    LOW). The source docstring pins the new canonical spelling — this
    test surfaces any future drift back to mixed case.
    """
    bands_seen = {
        _classify_risk([1, 2, 3], set(), {3: "writes_db"}),
        _classify_risk([1, 2, 3], set(), {3: "network"}),
        _classify_risk([1, 2, 3], {1}, {3: ""}),
        _classify_risk([1, 2, 3, 4], {1, 2}, {4: ""}),  # ratio 0.5 -> low
        _classify_risk([1, 2, 3, 4, 5], {3, 4}, {5: ""}),  # medium fallback
    }
    assert bands_seen == {"critical", "high", "medium", "low"}
    # Closed enum — no other bands ever emitted.
    for b in bands_seen:
        assert b == b.lower(), f"non-lowercase band leaked: {b}"


# ---------------------------------------------------------------------------
# _suggest_test_points — greedy set-cover correctness
# ---------------------------------------------------------------------------


def test_suggest_test_points_empty_paths_returns_empty_list() -> None:
    """Empty input -> empty output. Defensive entry-point check.

    The function short-circuits BEFORE building ``node_path_count``;
    this test pins that early-return branch so it cannot regress into
    a NameError on the empty path.
    """
    assert _suggest_test_points([], set()) == []
    assert _suggest_test_points([], {1, 2, 3}) == []


def test_suggest_test_points_picks_node_covering_most_paths_first() -> None:
    """Greedy cover: highest-frequency untested node ranks first.

    Three untested paths share node ``99`` — covering ``99`` covers all
    three. The function must surface ``99`` as the first suggestion
    with count ``3``.
    """
    # 3 paths, all share node 99.
    paths = [
        [1, 99, 10],
        [2, 99, 20],
        [3, 99, 30],
    ]
    tested: set[int] = set()
    suggestions = _suggest_test_points(paths, tested)
    # First pick covers all 3 paths.
    assert suggestions, "must surface at least one suggestion"
    first_node, first_count = suggestions[0]
    assert first_node == 99
    assert first_count == 3


def test_suggest_test_points_skips_already_tested_nodes() -> None:
    """Already-tested nodes are NOT proposed as test insertion points.

    The function filters them out at the ``node_path_count`` build step
    (``if nid not in tested_set``). Without this skip, the greedy cover
    would re-recommend nodes that already have test coverage.
    """
    paths = [
        [1, 50, 10],  # path 0
        [2, 50, 20],  # path 1
    ]
    tested = {50}  # the shared node is already tested
    suggestions = _suggest_test_points(paths, tested)
    chosen_nodes = {n for n, _ in suggestions}
    assert 50 not in chosen_nodes, "tested node leaked into suggestions"


def test_suggest_test_points_greedy_descends_by_coverage() -> None:
    """Across multiple picks, each suggestion covers <= the prior one.

    Standard greedy-cover monotonicity — the count never increases as
    we drain paths. Surfaces regressions in the per-iteration
    bookkeeping (the ``node_path_count`` decrement at the bottom of
    the loop).
    """
    # 4 paths arranged so 7 covers 3, then 8 covers 1 of the remainder.
    paths = [
        [1, 7, 10],
        [2, 7, 20],
        [3, 7, 30],
        [4, 8, 40],
    ]
    tested: set[int] = set()
    suggestions = _suggest_test_points(paths, tested)
    # Monotonic non-increasing count.
    counts = [c for _, c in suggestions]
    assert counts == sorted(counts, reverse=True), counts
    # The first pick covers the 3-path cluster.
    assert suggestions[0] == (7, 3)
