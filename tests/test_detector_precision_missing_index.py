"""Regression tripwire for missing-index, not a precision proof.

The labelled pair locks a paginated unindexed filter and its matching-index
suppression against refactors; it does not claim a precision number.
"""

from pathlib import Path

from roam.commands.cmd_missing_index import (
    _check_single_where,
    _parse_migration_indexes,
    _query_pattern_from_body,
)

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "missing-index"


def _findings(model_name: str, migration_name: str):
    root = FIXTURES
    source = (root / model_name).read_text(encoding="utf-8")
    pattern = _query_pattern_from_body(source, model_name, 1, "orders", "generic")
    indexes = _parse_migration_indexes(root, [migration_name])
    if pattern is None:
        return []
    return _check_single_where(pattern, pattern.where_cols[0], indexes[pattern.table], set())


def test_missing_index_tp_fires_and_matching_index_tn_is_clean():
    tp = _findings("tp_unindexed_filter.php", "tp_migration.php")
    assert tp and tp["pattern_type"] == "single_where"
    tn = _findings("tn_indexed_filter.php", "tn_migration.php")
    assert tn is None
