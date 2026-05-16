"""W994 + W995: extend the W987 Pattern 2 warnings_out plumbing.

Two silent-fallback drive-bys riding W987's warnings_out substrate:

* **W994** — ``_is_expired`` (and the parser) silently treated any
  unparseable ``expires`` value as "never expires". A user writing
  ``expires: tomorrow`` or ``expires: 2026-13-01`` (invalid month) was
  granted a permanent suppression with zero signal. Fix: surface an
  actionable warning while preserving the "treat as never-expires"
  semantic (W377-W382 permit-persist absorbed that contract; raising
  would regress them).
* **W995** — the parser comment at ``smells_suppress.py:80`` admitted
  "Malformed entries are silently skipped". Fix: surface every dropped
  row by 1-based index naming the missing required field, plus a
  total count when more than one row was dropped.

Discipline (per CLAUDE.md Pattern 2 + dev/CMD-ALERTS-PATTERN-2-CASE-STUDY-
2026-05-15.md):

* Backward compat: ``warnings_out=None`` preserves the pre-W994/W995
  silent behaviour byte-for-byte.
* No raising on incomplete user input.
* LAW 2 (imperative voice) + LAW 4 (concrete-noun terminal) on every
  new warning string.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.smells_suppress import (
    EXPIRES_FMT,
    _is_expired,
    _parse_smells_suppress_yaml,
    load_smells_suppressions,
    load_smells_suppressions_typed,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CONCRETE_NOUN_TERMINALS = frozenset(
    {
        # Mirrors a subset of _CONCRETE_NOUN_ANCHORS in tests/test_law4_lint.py
        # — only the terminals we expect to use in W994/W995 warnings. Kept
        # narrow on purpose: if someone reshapes a warning to end on a token
        # not in this list, the assertion fails and the developer must
        # consciously decide whether to widen the anchor set.
        "entries",
        "fields",
        "kinds",
    }
)


def _write_suppress(root: Path, body: str) -> None:
    (root / ".roam").mkdir(parents=True, exist_ok=True)
    (root / ".roam" / "smells.suppress.yml").write_text(body, encoding="utf-8")


def _terminal_token(s: str) -> str:
    """Return the last word after stripping trailing punctuation."""
    return s.rstrip(".").rsplit(" ", 1)[-1].strip(".,;:!?")


# ---------------------------------------------------------------------------
# W994 — unparseable ``expires`` surfaces an actionable warning
# ---------------------------------------------------------------------------


def test_unparseable_expires_warns_and_treats_as_never(tmp_path: Path) -> None:
    """An unparseable ``expires`` (e.g. ``tomorrow``) surfaces a warning AND
    the entry stays "never expires" (semantic preserved per W377-W382)."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub
    expires: tomorrow
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)

    # Backward compat: the entry IS still returned.
    assert len(entries) == 1
    assert entries[0]["expires"] == "tomorrow"

    # W994: exactly one warning fires for the unparseable expires.
    matching = [w for w in warnings if "expires" in w and "tomorrow" in w]
    assert len(matching) == 1, f"Expected exactly one warning about the unparseable expires, got: {warnings}"
    warning = matching[0]
    assert "never-expires" in warning, f"Warning must disclose the silent default explicitly, got: {warning!r}"


def test_unparseable_expires_invalid_month_also_warns(tmp_path: Path) -> None:
    """``2026-13-01`` (invalid month) is also unparseable and must warn —
    not just free-form strings but malformed dates too."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub
    expires: 2026-13-01
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)
    assert len(entries) == 1
    assert any("2026-13-01" in w and "expires" in w for w in warnings), (
        f"Invalid-month date must surface a warning, got: {warnings}"
    )


def test_canonical_expires_silent(tmp_path: Path) -> None:
    """Happy path: a canonical YYYY-MM-DD ``expires`` value emits zero
    expires-related warnings."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub
    expires: "2099-12-01"
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)
    assert len(entries) == 1
    assert all("expires" not in w for w in warnings), (
        f"Canonical expires must trigger no expires warning, got: {warnings}"
    )


def test_missing_expires_silent(tmp_path: Path) -> None:
    """An entry with no ``expires`` field at all triggers no warning —
    the W994 fix targets only present-but-unparseable values."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)
    assert len(entries) == 1
    assert all("expires" not in w for w in warnings), f"Missing expires must trigger no warning, got: {warnings}"


def test_expires_warning_text_actionable() -> None:
    """LAW 2 (imperative) + LAW 4 (concrete-noun terminal) on the W994
    warning text. Pinned so a future rewording stays compliant."""
    text = "suppressions:\n  - kind: shotgun-surgery\n    symbol: hub\n    expires: yesterday\n"
    warnings: list[str] = []
    parsed = _parse_smells_suppress_yaml(text, warnings_out=warnings, source_path="custom/path.yml")
    assert len(parsed) == 1

    matching = [w for w in warnings if "yesterday" in w]
    assert len(matching) == 1
    warning = matching[0]

    # LAW 2: imperative lead-in (Edit ...).
    assert warning.startswith("Edit "), f"W994 warning must lead with an imperative verb, got: {warning!r}"
    # The source path plumbs through.
    assert "custom/path.yml" in warning, f"Warning must name the offending file, got: {warning!r}"
    # The expected format constant is exposed in the message.
    assert EXPIRES_FMT in warning or "%Y-%m-%d" in warning, (
        f"Warning must reference the expected date format, got: {warning!r}"
    )
    # LAW 4: terminal token is a concrete-noun anchor.
    terminal = _terminal_token(warning)
    assert terminal in _CONCRETE_NOUN_TERMINALS, (
        f"LAW 4: terminal token {terminal!r} not in concrete-noun anchor "
        f"set {sorted(_CONCRETE_NOUN_TERMINALS)} (full: {warning!r})"
    )


def test_is_expired_warns_when_warnings_out_supplied() -> None:
    """``_is_expired`` itself accepts ``warnings_out`` so programmatic
    callers (tests, builders) that bypass the parser still get the
    silent-default surfacing."""
    entry = {
        "kind": "shotgun-surgery",
        "symbol": "hub",
        "expires": "never",
    }
    warnings: list[str] = []
    # Treat-as-never-expires semantic is preserved.
    assert _is_expired(entry, warnings_out=warnings) is False
    assert len(warnings) == 1
    assert "never" in warnings[0]
    assert "expires" in warnings[0]


def test_is_expired_silent_when_warnings_out_is_none() -> None:
    """``_is_expired`` stays silent without an accumulator — back-compat
    with every match-time call site in ``is_suppressed``."""
    entry = {"kind": "x", "symbol": "y", "expires": "nope"}
    # No warnings_out: must not raise, must return False (never-expires).
    assert _is_expired(entry) is False


def test_is_expired_canonical_silent_with_accumulator() -> None:
    """Even with a ``warnings_out`` accumulator, a canonical YYYY-MM-DD
    value emits nothing."""
    entry = {"kind": "x", "symbol": "y", "expires": "2099-12-01"}
    warnings: list[str] = []
    assert _is_expired(entry, warnings_out=warnings) is False
    assert warnings == []


# ---------------------------------------------------------------------------
# W995 — malformed-entry drops surface on warnings_out
# ---------------------------------------------------------------------------


def test_missing_kind_field_warns_and_drops(tmp_path: Path) -> None:
    """An entry missing ``kind`` is dropped AND surfaces an indexed warning."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - symbol: lonely
    reason: no kind here
  - kind: shotgun-surgery
    symbol: hub
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)

    # Only the valid entry survives.
    assert [e["symbol"] for e in entries] == ["hub"]

    # W995: one warning naming the dropped row's 1-based index + the
    # missing field 'kind'.
    matching = [w for w in warnings if "dropped" in w and "#1" in w]
    assert len(matching) == 1, f"Expected one dropped-row warning at index #1, got: {warnings}"
    warning = matching[0]
    assert "'kind'" in warning, f"Warning must name the missing field 'kind', got: {warning!r}"


def test_missing_symbol_field_warns_and_drops(tmp_path: Path) -> None:
    """An entry missing ``symbol`` is dropped AND surfaces an indexed warning."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: ok
  - kind: god-class
    reason: no symbol here
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)

    assert [e["symbol"] for e in entries] == ["ok"]
    matching = [w for w in warnings if "dropped" in w and "#2" in w]
    assert len(matching) == 1, f"Expected one dropped-row warning at index #2, got: {warnings}"
    assert "'symbol'" in matching[0]


def test_malformed_entry_count_in_envelope_warnings_out(tmp_path: Path) -> None:
    """Multiple dropped rows surface (a) one warning per row by index AND
    (b) a single roll-up count line so the envelope makes the magnitude
    obvious."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    # missing symbol
  - reason: nothing here
  - kind: god-class
    symbol: GodManager
  - symbol: orphan-three
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)

    # One valid entry survives.
    assert [e["symbol"] for e in entries] == ["GodManager"]

    # Three rows dropped: indices 1, 2, 4.
    dropped_indices = [w for w in warnings if "dropped" in w and "#" in w]
    # Each indexed dropped row is one line, plus the roll-up count line.
    per_row = [w for w in dropped_indices if "missing" in w]
    assert len(per_row) == 3, f"Expected three indexed dropped-row warnings, got: {warnings}"
    assert any("#1" in w for w in per_row)
    assert any("#2" in w for w in per_row)
    assert any("#4" in w for w in per_row)

    # Roll-up count line lists 3 dropped.
    roll_up = [w for w in warnings if "dropped 3" in w and "total" in w]
    assert len(roll_up) == 1, f"Expected one roll-up count line for >1 dropped rows, got: {warnings}"


def test_single_dropped_row_omits_roll_up(tmp_path: Path) -> None:
    """When only one row is dropped, the roll-up line is omitted — a
    single warning is signal enough; the count line would just repeat it."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - symbol: orphan
  - kind: shotgun-surgery
    symbol: hub
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)
    assert [e["symbol"] for e in entries] == ["hub"]
    roll_up = [w for w in warnings if "total" in w and "dropped" in w]
    assert roll_up == [], f"Single drop must omit the roll-up line, got: {warnings}"


def test_dropped_warning_text_is_actionable(tmp_path: Path) -> None:
    """LAW 2 (imperative) + LAW 4 (concrete-noun terminal) on every
    W995 warning string."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - symbol: orphan
""",
    )

    warnings: list[str] = []
    load_smells_suppressions(tmp_path, warnings_out=warnings)
    matching = [w for w in warnings if "dropped" in w]
    assert len(matching) == 1
    warning = matching[0]
    assert warning.startswith("Edit "), f"W995 warning must lead with an imperative verb, got: {warning!r}"
    terminal = _terminal_token(warning)
    assert terminal in _CONCRETE_NOUN_TERMINALS, (
        f"LAW 4: terminal token {terminal!r} not in concrete-noun anchor "
        f"set {sorted(_CONCRETE_NOUN_TERMINALS)} (full: {warning!r})"
    )


def test_drops_silent_when_warnings_out_is_none(tmp_path: Path) -> None:
    """Backward compat: without an accumulator, drops stay silent — every
    pre-W995 caller keeps its byte-identical behaviour."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - symbol: orphan
  - kind: shotgun-surgery
    symbol: hub
""",
    )
    # No warnings_out: must drop the malformed row + return the survivor.
    entries = load_smells_suppressions(tmp_path)
    assert [e["symbol"] for e in entries] == ["hub"]


def test_typed_loader_also_surfaces_drops_and_expires(tmp_path: Path) -> None:
    """The typed loader path used by cmd_smells delegates to the dict
    loader, so both W994 and W995 warnings reach the typed callers
    identically. Pin this so a future refactor of the typed path can't
    silently lose the W994/W995 surfacing."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - symbol: dropped-orphan
  - kind: shotgun-surgery
    symbol: hub
    expires: never
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions_typed(tmp_path, warnings_out=warnings)
    # The valid entry round-trips through KindSymbolSuppression.
    assert len(entries) == 1
    assert entries[0].symbol == "hub"
    # W994 + W995 warnings both reach the typed accumulator.
    assert any("dropped" in w for w in warnings), warnings
    assert any("expires" in w and "never" in w for w in warnings), warnings


# ---------------------------------------------------------------------------
# CLI integration — envelope surfacing through the smells command
# ---------------------------------------------------------------------------


def _build_smelly_project(tmp_path: Path) -> Path:
    """Minimal git + DB fixture mirroring test_w987_smells_pattern2.py."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "dummy.py").write_text("# dummy\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0,
            health_score REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS git_cochange (
            file_id_a INTEGER NOT NULL,
            file_id_b INTEGER NOT NULL,
            cochange_count INTEGER DEFAULT 0,
            PRIMARY KEY (file_id_a, file_id_b)
        );
    """)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/engine.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'process_everything', 'function', 10, 200, '(data, config, opts)')"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 75, 6)")
    conn.commit()
    conn.close()
    return tmp_path


def test_smells_cli_surfaces_drops_and_expires_on_envelope(tmp_path: Path) -> None:
    """End-to-end: a smells.suppress.yml with both a malformed entry and
    an unparseable expires surfaces both signals on the envelope's
    ``warnings_out`` array AND flips ``summary.partial_success=True``."""
    project = _build_smelly_project(tmp_path)
    (project / ".roam").mkdir(parents=True, exist_ok=True)
    (project / ".roam" / "smells.suppress.yml").write_text(
        """\
suppressions:
  - symbol: dropped-orphan
  - kind: shotgun-surgery
    symbol: process_everything
    expires: tomorrow
""",
        encoding="utf-8",
    )

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        # --detail bypasses ``strip_list_payloads`` so the
        # ``warnings_out`` list survives onto the JSON envelope, mirroring
        # the W987 test discipline. Without --detail, the envelope's list
        # fields are summarised away to keep the headline token-tight.
        result = runner.invoke(cli, ["--json", "smells", "--detail"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, (
        f"smells with W994/W995 fixture must not raise; got exit={result.exit_code}\nstdout={result.stdout!r}"
    )
    payload = json.loads(result.stdout)
    warnings = payload.get("warnings_out", [])
    assert isinstance(warnings, list)

    # Both signals must reach the envelope.
    assert any("dropped" in w and "#1" in w for w in warnings), (
        f"W995 dropped-row warning must reach the envelope, got: {warnings}"
    )
    assert any("expires" in w and "tomorrow" in w for w in warnings), (
        f"W994 expires warning must reach the envelope, got: {warnings}"
    )

    # Pattern 1 envelope discipline: partial_success flips True on any warning.
    summary = payload.get("summary", {})
    assert summary.get("partial_success") is True, (
        f"summary.partial_success must be True when warnings_out is non-empty, got summary={summary}"
    )
