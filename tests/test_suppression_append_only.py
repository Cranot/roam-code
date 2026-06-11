"""save_suppression must APPEND, never rewrite — data-loss regression.

Dogfood incident (2026-06-10, external Vue/PHP repo): five hand-appended
entries in ``.roam-suppressions.yml`` vanished between verify runs. Root
cause: ``save_suppression`` round-tripped the file through the typed
loader (which drops rows it can't coerce — unknown status values,
unquoted reasons, foreign keys) and rewrote the whole file from that
lossy view. A fully-unparseable file loaded as ``[]`` and was REPLACED
by the single new entry.

These tests pin the append-only contract: existing bytes are preserved
verbatim; only the new entry is added at the tail.
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.suppression import is_suppressed, load_suppressions, save_suppression

MALFORMED_TAIL = """suppressions:
  - rule: naming
    file: app/Services/Sync.php
    line: 76
    reason: PSR-12 camelCase is the real convention
    status: safe
  - rule: complexity
    file: src/composables/useDriver.ts
    reason: retry loop floors at 19: irreducible
    status: fp
"""


def test_save_preserves_entries_the_loader_cannot_parse(tmp_path: Path) -> None:
    """Hand-added rows with foreign status / unquoted colons survive a save."""
    cfg = tmp_path / ".roam-suppressions.yml"
    cfg.write_text(MALFORMED_TAIL, encoding="utf-8")

    save_suppression(tmp_path, "imports", "app/Models/User.php", "vendor facade", "safe")

    text = cfg.read_text(encoding="utf-8")
    # The hand-added tail entry (status: fp — not a valid status, so the
    # typed loader drops it) must still be on disk.
    assert "useDriver.ts" in text
    assert "status: fp" in text
    # The well-formed first entry survives byte-identical.
    assert "app/Services/Sync.php" in text
    # And the new entry landed.
    assert "app/Models/User.php" in text


def test_save_on_unparseable_file_does_not_wipe_it(tmp_path: Path) -> None:
    """A file the YAML loader rejects entirely must not be replaced."""
    cfg = tmp_path / ".roam-suppressions.yml"
    garbage = "suppressions:\n\t- rule: naming\n  broken: [unclosed\n"
    cfg.write_text(garbage, encoding="utf-8")

    save_suppression(tmp_path, "naming", "a.py", "test", "safe")

    text = cfg.read_text(encoding="utf-8")
    assert "broken: [unclosed" in text  # original bytes preserved
    assert "a.py" in text  # new entry appended


def test_save_creates_fresh_file_with_root_key(tmp_path: Path) -> None:
    save_suppression(tmp_path, "naming", "a.py", "test", "safe", line=10)
    rows = load_suppressions(tmp_path)
    assert len(rows) == 1
    assert is_suppressed(rows, "naming", "a.py", 10)


def test_save_appends_parseable_entry_roundtrip(tmp_path: Path) -> None:
    """Append onto a well-formed file: both old and new rows load and match."""
    save_suppression(tmp_path, "naming", "a.py", "first", "safe", line=10)
    save_suppression(tmp_path, "complexity", "b.py", "second", "wont-fix")

    rows = load_suppressions(tmp_path)
    assert len(rows) == 2
    assert is_suppressed(rows, "naming", "a.py", 10)
    assert is_suppressed(rows, "complexity", "b.py", 99)  # no line = whole file


def test_save_onto_comment_only_file_adds_root_key(tmp_path: Path) -> None:
    cfg = tmp_path / ".roam-suppressions.yml"
    cfg.write_text("# notes only, no root key yet\n", encoding="utf-8")

    save_suppression(tmp_path, "naming", "a.py", "test", "safe")

    text = cfg.read_text(encoding="utf-8")
    assert text.startswith("# notes only")
    rows = load_suppressions(tmp_path)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Symbol-keyed + line-tolerant matching (dogfood finding #4)
# ---------------------------------------------------------------------------


def test_symbol_keyed_suppression_survives_line_drift(tmp_path: Path) -> None:
    save_suppression(tmp_path, "naming", "a.php", "PSR-12", "safe", symbol="syncFromCursor")
    rows = load_suppressions(tmp_path)
    # Same symbol reported at ANY line still matches.
    assert is_suppressed(rows, "naming", "a.php", 76, symbol="syncFromCursor")
    assert is_suppressed(rows, "naming", "a.php", 9999, symbol="syncFromCursor")
    # A different symbol at the same lines does not.
    assert not is_suppressed(rows, "naming", "a.php", 76, symbol="otherFn")


def test_line_keyed_suppression_matches_within_tolerance(tmp_path: Path) -> None:
    save_suppression(tmp_path, "complexity", "b.php", "retry loop", "acknowledged", line=99)
    rows = load_suppressions(tmp_path)
    # Refactor shifted the function 99 -> 102 (observed drift): still matches.
    assert is_suppressed(rows, "complexity", "b.php", 102)
    assert is_suppressed(rows, "complexity", "b.php", 96)
    # Beyond the tolerance window: no match.
    assert not is_suppressed(rows, "complexity", "b.php", 110)
