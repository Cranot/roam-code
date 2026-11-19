"""Symbol suppressions in one file must not discard each other on save."""

import pytest

from roam.commands.suppression import is_suppressed, load_suppressions, save_suppression


def test_save_keeps_distinct_symbol_suppressions(tmp_path):
    for symbol in ("A", "B"):
        save_suppression(
            tmp_path,
            rule="complexity",
            file="src/example.py",
            reason="Reviewed",
            status="safe",
            symbol=symbol,
            line=None,
        )

    rows = load_suppressions(tmp_path)
    assert {row.get("symbol") for row in rows} == {"A", "B"}
    for symbol in ("A", "B"):
        assert is_suppressed(
            rows,
            "complexity",
            "src/example.py",
            symbol=symbol,
        )
    assert not is_suppressed(rows, "complexity", "src/example.py", symbol="C")


@pytest.mark.parametrize("symbol,line", [("A", None), (None, 12)])
def test_save_still_deduplicates_same_target(tmp_path, symbol, line):
    for reason in ("Reviewed", "Reviewed again"):
        save_suppression(
            tmp_path,
            rule="complexity",
            file="src/example.py",
            reason=reason,
            status="safe",
            symbol=symbol,
            line=line,
        )

    rows = load_suppressions(tmp_path)
    assert len(rows) == 1
    assert is_suppressed(
        rows,
        "complexity",
        "src/example.py",
        symbol=symbol,
        line=line,
    )
