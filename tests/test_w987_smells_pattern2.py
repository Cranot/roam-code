"""W987: apply the W983 Pattern 2 case-study playbook to ``cmd_smells.py``.

Two silent-fallback surfaces pre-W987:

* ``--kind`` filter on the CLI did not exist yet; once added, any closed-set
  validation that hard-fails would break backward compat with CI scripts
  pinning a fixed kind argument. The W987 shape is Click-multiple + warn +
  drop, surfacing the typo on ``warnings_out`` rather than raising.
* ``load_smells_suppressions`` accepted arbitrary ``kind:`` strings in
  ``.roam/smells.suppress.yml``. A typo (e.g. ``kind: shotgun-survey``) sat
  in the file matching zero findings forever, no signal to the user.

Discipline (per CLAUDE.md Pattern 2 + dev/CMD-ALERTS-PATTERN-2-CASE-STUDY-
2026-05-15.md): preserve happy-path behaviour, surface the silent state as
an actionable warning, NEVER raise on incomplete user input (backward
compat). Pattern 1 plumbs ``warnings_out`` through the call chain; Pattern
2 introduces the closed-set vocabulary anchored on
``roam.catalog.registry.kind_to_confidence``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.catalog.registry import kind_to_confidence
from roam.cli import cli
from roam.commands.cmd_smells import _registered_smell_kinds
from roam.commands.smells_suppress import (
    _parse_smells_suppress_yaml,
    load_smells_suppressions,
    load_smells_suppressions_typed,
)


# Minimal DB + git fixture mirroring tests/test_smells.py _make_db /
# _git_init / _populate_brain_method. Inlined here so this test file
# stays self-contained — adding an import dependency on the giant
# test_smells.py harness would couple us to its evolution.
def _build_smelly_project(tmp_path: Path) -> Path:
    """Return a tmp project root with a git init + a one-row brain-method DB.

    Mirrors the long-standing in-tree fixture: brain-method on
    ``process_everything`` at src/engine.py:10. The CLI test only needs one
    detectable smell to exercise the --kind filter path; the precise
    detector that fires is incidental.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True
    )
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
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
        "VALUES (1, 75, 6)"
    )
    conn.commit()
    conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_suppress(root: Path, body: str) -> None:
    (root / ".roam").mkdir(parents=True, exist_ok=True)
    (root / ".roam" / "smells.suppress.yml").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pattern 2 — closed-set vocabulary anchored on the registry
# ---------------------------------------------------------------------------


def test_registered_smell_kinds_matches_registry() -> None:
    """W987 four-anchor sanity: the cmd_smells helper returns exactly the
    registered set from ``roam.catalog.registry``. Pin so a refactor that
    accidentally narrows the helper (e.g. drops rollup ids) surfaces here.
    """
    helper = _registered_smell_kinds()
    canonical = frozenset(kind_to_confidence().keys())
    assert helper == canonical, (
        f"_registered_smell_kinds drift: helper={sorted(helper)} "
        f"vs canonical={sorted(canonical)}"
    )
    # At least the known core detectors must be present (defensive
    # against an empty registry slipping through).
    assert "shotgun-surgery" in helper
    assert "god-class" in helper
    # Rollup id (W647) must also be in the set since suppression entries
    # and --kind filters can legitimately target it.
    assert "temporal-coupling-cluster" in helper


# ---------------------------------------------------------------------------
# Pattern 1 — warnings_out plumb-through on the suppression loader
# ---------------------------------------------------------------------------


def test_suppression_loader_warns_on_unknown_kind(tmp_path: Path) -> None:
    """W987 Pattern 1 + 2: ``load_smells_suppressions`` appends a warning
    for an unknown ``kind:`` value and still returns the entry (backward
    compat with the pre-W987 silent-keep behaviour)."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: not-a-real-detector
    symbol: foo
    reason: typo
  - kind: shotgun-surgery
    symbol: get_extractor
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)

    # Backward compat: the unknown-kind entry IS still returned.
    assert len(entries) == 2
    assert entries[0]["kind"] == "not-a-real-detector"
    assert entries[1]["kind"] == "shotgun-surgery"

    # Pattern 1: exactly one warning fires (the unknown kind).
    assert len(warnings) == 1, (
        f"Expected exactly one warning for the unknown kind, got: {warnings}"
    )
    warning = warnings[0]
    # LAW 2 (imperative) + LAW 4 (concrete-noun anchored terminal).
    assert warning.startswith("Edit "), (
        f"Warning must lead with an imperative verb, got: {warning!r}"
    )
    assert "not-a-real-detector" in warning, (
        f"Warning must name the offending kind, got: {warning!r}"
    )
    assert "symbol='foo'" in warning, (
        f"Warning must name the suppression symbol for disambiguation, "
        f"got: {warning!r}"
    )
    # Concrete-noun anchor: ends on 'kinds' (in _CONCRETE_NOUN_ANCHORS).
    last_token = warning.rstrip(".").rsplit(" ", 1)[-1].strip(".,")
    assert last_token == "kinds", (
        f"LAW 4: warning must terminate on a concrete-noun anchor "
        f"(expected 'kinds'), got terminal token: {last_token!r} "
        f"(full warning: {warning!r})"
    )


def test_suppression_loader_silent_when_warnings_out_is_none(tmp_path: Path) -> None:
    """W987 backward compat: when *warnings_out* is omitted (or ``None``)
    the loader stays silent — library callers without an envelope to
    populate get the pre-W987 behaviour. The entry is still returned."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: typo-kind
    symbol: anywhere
""",
    )

    # No warnings_out — must not raise, must return the entry.
    entries = load_smells_suppressions(tmp_path)
    assert len(entries) == 1
    assert entries[0]["kind"] == "typo-kind"


def test_suppression_loader_typed_path_also_warns(tmp_path: Path) -> None:
    """W987 + W737: the typed loader (used by cmd_smells via
    ``load_smells_suppressions_typed``) plumbs ``warnings_out`` through
    the dict path identically."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: definitely-not-a-detector
    symbol: thing
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions_typed(tmp_path, warnings_out=warnings)
    assert len(entries) == 1
    assert entries[0].kind == "definitely-not-a-detector"
    assert len(warnings) == 1
    assert "definitely-not-a-detector" in warnings[0]


def test_suppression_loader_valid_kinds_emit_no_warning(tmp_path: Path) -> None:
    """W987 happy path: every well-formed entry referencing a registered
    smell id passes silently."""
    _write_suppress(
        tmp_path,
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: get_extractor
  - kind: god-class
    symbol: GodManager
  - kind: temporal-coupling-cluster
    symbol: some_cluster
""",
    )

    warnings: list[str] = []
    entries = load_smells_suppressions(tmp_path, warnings_out=warnings)
    assert len(entries) == 3
    assert warnings == [], (
        f"Valid kinds must not trigger any warning, got: {warnings}"
    )


def test_parse_smells_suppress_yaml_direct_invocation_warns() -> None:
    """W987 belt-and-braces: the parser, called directly, also surfaces
    unknown-kind warnings on ``warnings_out``. Library consumers that
    bypass the path-aware loader get the same signal."""
    text = (
        "suppressions:\n"
        "  - kind: madeup-smell\n"
        "    symbol: foo\n"
    )
    warnings: list[str] = []
    parsed = _parse_smells_suppress_yaml(
        text,
        warnings_out=warnings,
        source_path="custom/path.yml",
    )

    assert len(parsed) == 1
    assert parsed[0]["kind"] == "madeup-smell"
    assert len(warnings) == 1
    assert "madeup-smell" in warnings[0]
    # ``source_path`` plumbs into the warning so the user can locate the
    # offending file even when the loader was invoked outside the standard
    # ``.roam/`` lookup.
    assert "custom/path.yml" in warnings[0]


# ---------------------------------------------------------------------------
# CLI integration — --kind filter validation + envelope surfacing
# ---------------------------------------------------------------------------


def test_smells_cli_unknown_kind_warns_not_raises(tmp_path):
    """W987 Pattern 2: ``--kind nonsense`` does NOT raise; it warns and
    drops the value from the filter set. The envelope carries the warning
    on ``warnings_out`` AND flips ``summary.partial_success=True``.
    """
    project = _build_smelly_project(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        result = runner.invoke(
            cli,
            ["--json", "smells", "--kind", "totally-not-a-real-kind", "--detail"],
        )
    finally:
        os.chdir(old_cwd)

    # Backward compat: the command exits cleanly even with a typo.
    assert result.exit_code == 0, (
        f"--kind with unknown value must not raise; got exit={result.exit_code}\n"
        f"stdout={result.stdout!r}"
    )

    payload = json.loads(result.stdout)

    # Envelope surfaces the warning on ``warnings_out``.
    assert "warnings_out" in payload, (
        f"Envelope must carry the ``warnings_out`` field, got keys: {sorted(payload.keys())}"
    )
    warnings = payload["warnings_out"]
    assert isinstance(warnings, list)
    assert len(warnings) >= 1, (
        f"Expected at least one warning for the unknown --kind, got: {warnings}"
    )
    assert any("totally-not-a-real-kind" in w for w in warnings), (
        f"Warning must name the offending --kind value, got: {warnings}"
    )

    # Pattern 1 envelope discipline: partial_success flips True on any warning.
    summary = payload.get("summary", {})
    assert summary.get("partial_success") is True, (
        f"summary.partial_success must be True when warnings_out is non-empty, "
        f"got summary={summary}"
    )


def test_smells_cli_valid_kind_no_warning(tmp_path):
    """W987 happy path: a registered ``--kind`` value triggers no warning
    and ``summary.partial_success`` stays absent (or False).
    """
    project = _build_smelly_project(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        result = runner.invoke(cli, ["--json", "smells", "--kind", "long-params"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, (
        f"Valid --kind run failed: exit={result.exit_code} stdout={result.stdout!r}"
    )
    payload = json.loads(result.stdout)

    warnings = payload.get("warnings_out", [])
    # The CLI may carry unrelated warnings from other paths, but no
    # warning text should reference the (valid) kind we passed.
    assert not any("long-params" in w and "unknown" in w.lower() for w in warnings), (
        f"Valid --kind value must not trigger an unknown-kind warning, got: {warnings}"
    )

    summary = payload.get("summary", {})
    # If partial_success is set, it must not be due to our --kind filter.
    # We don't strictly assert partial_success absence because other
    # silent-fallback paths in the broader command could flip it; the
    # contract here is "this command run did not surface a --kind warning".
    if summary.get("partial_success") is True:
        # Re-assert no warning text named our kind.
        assert all("long-params" not in w for w in warnings)
