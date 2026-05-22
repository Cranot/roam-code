"""Pattern-2 regression tests: silent-success / silent-fallback disclosure.

Covers three commands where a degraded code path previously emitted a
success / positive verdict indistinguishable from a fully-verified one:

1. ``compare`` -- one index predates the ``symbol_metrics`` schema. The
   old code silently swallowed the ``OperationalError`` and treated every
   file as complexity 0, fabricating an IMPROVED / REGRESSED verdict. The
   fix suppresses the complexity-delta section, stamps ``partial_success``
   and qualifies the verdict.
2. ``syntax-check`` -- files skipped (unsupported language, unreadable
   source, grammar-load failure, parse crash) were counted by an unused
   ``skipped`` variable. The fix surfaces ``files_skipped``, stamps
   ``partial_success`` and -- when every file was skipped -- the verdict
   no longer claims "clean".
3. ``verify`` -- a crashed tree-sitter parse used to ``continue`` after
   ``files_checked`` was already incremented, scoring 100 into the syntax
   category. The fix tracks ``parse_failures``, never credits a crashed
   file, surfaces INFO-level violations and discloses the degraded gate.

These are Pattern-2 fixes (CLAUDE.md "Six anti-patterns" #2). Each test
asserts the DEGRADED path now discloses; healthy-path coverage lives in
the existing per-command test files.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# FINDING 1 -- compare: index missing symbol_metrics
# ---------------------------------------------------------------------------


def _make_index(path: Path, *, with_symbol_metrics: bool) -> None:
    """Build a tiny index DB; optionally OMIT the symbol_metrics table.

    An index that predates the symbol_metrics schema simply has no such
    table -- the complexity query then raises sqlite3.OperationalError.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, qualified_name TEXT,
                              kind TEXT, file_id INTEGER, line_start INTEGER);
    """)
    if with_symbol_metrics:
        conn.execute("CREATE TABLE symbol_metrics (symbol_id INTEGER, cognitive_complexity INTEGER)")
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'a.py')")
    conn.commit()
    conn.close()


def _add_symbol(path: Path, sym_id: int, qname: str, *, with_symbol_metrics: bool, complexity: int = 5) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO symbols (id, qualified_name, kind, file_id, line_start) VALUES (?, ?, 'function', 1, ?)",
        (sym_id, qname, sym_id),
    )
    if with_symbol_metrics:
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) VALUES (?, ?)",
            (sym_id, complexity),
        )
    conn.commit()
    conn.close()


def test_compare_load_index_state_flags_missing_symbol_metrics(tmp_path) -> None:
    """_load_index_state records complexity_data_available=False when the
    symbol_metrics table is absent, instead of silently returning {}."""
    from roam.commands.cmd_compare import _load_index_state

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    _make_index(old_db, with_symbol_metrics=False)
    _make_index(new_db, with_symbol_metrics=True)
    _add_symbol(old_db, 1, "foo", with_symbol_metrics=False)
    _add_symbol(new_db, 1, "foo", with_symbol_metrics=True)

    old_state = _load_index_state(old_db)
    new_state = _load_index_state(new_db)

    assert old_state["complexity_data_available"] is False
    assert new_state["complexity_data_available"] is True


def test_compare_compute_delta_suppresses_fabricated_complexity(tmp_path) -> None:
    """_compute_delta must NOT fabricate complexity deltas when an index
    lacks complexity data -- the section is suppressed entirely."""
    from roam.commands.cmd_compare import _compute_delta, _load_index_state

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    _make_index(old_db, with_symbol_metrics=False)
    _make_index(new_db, with_symbol_metrics=True)
    # New index has a high-complexity symbol; old index has none recorded.
    # Pre-fix, the missing table would make every file complexity 0 and the
    # delta would look like a real REGRESSED jump.
    _add_symbol(old_db, 1, "foo", with_symbol_metrics=False)
    _add_symbol(new_db, 1, "foo", with_symbol_metrics=True, complexity=50)

    delta = _compute_delta(
        _load_index_state(old_db),
        _load_index_state(new_db),
        threshold=5,
    )

    assert delta["complexity_data_available"] is False
    # No fabricated deltas.
    assert delta["complexity_up"] == []
    assert delta["complexity_down"] == []


def test_compare_verdict_qualified_when_complexity_unavailable() -> None:
    """_verdict must NOT emit a bare IMPROVED/REGRESSED when complexity
    data was suppressed -- it qualifies the verdict instead."""
    from roam.commands.cmd_compare import _verdict

    delta = {
        "complexity_up": [],
        "complexity_down": [],
        "symbols_added": [{}, {}, {}],
        "symbols_removed": [{}],
        "complexity_data_available": False,
    }
    verdict = _verdict(delta)
    assert "complexity delta unavailable" in verdict
    assert "symbol_metrics" in verdict
    # Must not pretend the complexity dimension drove the verdict.
    assert verdict not in ("IMPROVED", "REGRESSED", "SIDEWAYS", "NO CHANGE")


def test_compare_cli_json_discloses_partial_success(tmp_path) -> None:
    """End-to-end: compare with one symbol_metrics-less index stamps
    partial_success and a complexity_unavailable_reason."""
    from roam.commands.cmd_compare import compare_cmd

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    _make_index(old_db, with_symbol_metrics=False)
    _make_index(new_db, with_symbol_metrics=True)
    _add_symbol(old_db, 1, "foo", with_symbol_metrics=False)
    _add_symbol(new_db, 1, "bar", with_symbol_metrics=True)

    runner = CliRunner()
    result = runner.invoke(compare_cmd, [str(old_db), str(new_db)], obj={"json": True})
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["complexity_data_available"] is False
    assert "complexity_unavailable_reason" in data
    # Phantom complexity counts must be zero.
    assert data["summary"]["complexity_regressions"] == 0
    assert data["summary"]["complexity_improvements"] == 0


def test_compare_cli_text_discloses_unavailable(tmp_path) -> None:
    """Text mode shows the complexity dimension as UNAVAILABLE."""
    from roam.commands.cmd_compare import compare_cmd

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    _make_index(old_db, with_symbol_metrics=False)
    _make_index(new_db, with_symbol_metrics=True)
    _add_symbol(old_db, 1, "foo", with_symbol_metrics=False)
    _add_symbol(new_db, 1, "bar", with_symbol_metrics=True)

    runner = CliRunner()
    result = runner.invoke(compare_cmd, [str(old_db), str(new_db)], obj={})
    assert result.exit_code == 0
    assert "UNAVAILABLE" in result.output
    assert "symbol_metrics" in result.output


def test_compare_healthy_path_keeps_complexity_section(tmp_path) -> None:
    """When BOTH indices have symbol_metrics, the healthy path is unchanged:
    no partial_success, complexity counts present, no disclosure noise."""
    from roam.commands.cmd_compare import compare_cmd

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    _make_index(old_db, with_symbol_metrics=True)
    _make_index(new_db, with_symbol_metrics=True)
    _add_symbol(old_db, 1, "foo", with_symbol_metrics=True)
    _add_symbol(new_db, 1, "foo", with_symbol_metrics=True)

    runner = CliRunner()
    result = runner.invoke(compare_cmd, [str(old_db), str(new_db)], obj={"json": True})
    assert result.exit_code == 0
    data = json.loads(result.output)
    # json_envelope() always stamps partial_success; on the healthy path it
    # must stay false and the compare-specific disclosure keys must be absent.
    assert data["summary"].get("partial_success") is False
    assert "complexity_data_available" not in data["summary"]
    assert "complexity_unavailable_reason" not in data


# ---------------------------------------------------------------------------
# FINDING 2 -- syntax-check: skipped files never disclosed
# ---------------------------------------------------------------------------


def _run_syntax_check(tmp_path, files: dict[str, str], json_mode: bool):
    """Invoke syntax-check on files written into a temp project dir."""
    import os

    from roam.cli import cli

    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    paths = []
    for rel, content in files.items():
        fp = proj / rel
        fp.write_text(content, encoding="utf-8")
        paths.append(rel)

    runner = CliRunner()
    args = (["--json"] if json_mode else []) + ["syntax-check", *paths]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def test_syntax_check_all_skipped_verdict_not_clean(tmp_path) -> None:
    """When EVERY target file is skipped (unsupported language), the verdict
    must NOT claim 'clean' -- it must say nothing was checked."""
    result = _run_syntax_check(
        tmp_path,
        {"data.csv": "a,b,c\n1,2,3\n", "more.csv": "x,y\n9,8\n"},
        json_mode=True,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["files_skipped"] == 2
    assert summary["partial_success"] is True
    assert summary["total_files"] == 0
    # Critically: the verdict must not lie about a clean check.
    assert "clean" not in summary["verdict"].lower()
    assert "nothing checked" in summary["verdict"].lower()


def test_syntax_check_partial_skip_discloses(tmp_path) -> None:
    """A mix of one parseable + one skipped file discloses files_skipped
    and partial_success while still reporting the clean parsed file."""
    result = _run_syntax_check(
        tmp_path,
        {"ok.py": "def f():\n    return 1\n", "data.csv": "a,b\n1,2\n"},
        json_mode=True,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["total_files"] == 1
    assert summary["files_skipped"] == 1
    assert summary["partial_success"] is True
    # The one real file was clean -- verdict discloses the skip.
    assert "skipped" in summary["verdict"].lower()


def test_syntax_check_partial_skip_text_shows_count(tmp_path) -> None:
    """Text mode also discloses the skipped count."""
    result = _run_syntax_check(
        tmp_path,
        {"ok.py": "def f():\n    return 1\n", "data.csv": "a,b\n1,2\n"},
        json_mode=False,
    )
    assert result.exit_code == 0
    assert "skipped" in result.output.lower()


def test_syntax_check_no_skips_healthy_envelope_clean(tmp_path) -> None:
    """Healthy path: no skipped files -> no files_skipped key, the verdict
    still says 'clean' and partial_success stays absent from the summary."""
    result = _run_syntax_check(
        tmp_path,
        {"ok.py": "def f():\n    return 1\n"},
        json_mode=True,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    # files_skipped is degraded-path-only -- keeps the healthy envelope stable.
    # json_envelope() always stamps partial_success; healthy path keeps it false.
    assert "files_skipped" not in summary
    assert summary.get("partial_success") is False
    assert summary["clean"] is True
    assert summary["verdict"] == "clean -- 1 files checked, 0 errors"


# ---------------------------------------------------------------------------
# FINDING 3 -- verify: crashed parse credited as a clean syntax check
# ---------------------------------------------------------------------------


def test_verify_check_syntax_crashed_parse_not_credited(tmp_path, monkeypatch) -> None:
    """_check_syntax must NOT count a file whose parse crashed as a clean
    checked file -- it tracks parse_failures and surfaces an INFO violation."""
    from roam.commands import cmd_verify

    # A fake DB connection yielding one changed file row.
    class _FakeConn:
        def execute(self, *_a, **_k):
            class _Cur:
                def fetchall(_self):
                    return [{"id": 1, "path": "boom.py", "language": "python"}]

            return _Cur()

    root = tmp_path
    (root / "boom.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    # Force parse_file to crash for every file.
    import roam.index.parser as parser_mod

    def _exploding_parse_file(*_a, **_k):
        raise RuntimeError("simulated tree-sitter crash")

    monkeypatch.setattr(parser_mod, "parse_file", _exploding_parse_file, raising=False)

    # batched_in is what cmd_verify actually calls -- patch it to return our row.
    monkeypatch.setattr(
        cmd_verify,
        "batched_in",
        lambda conn, sql, ids: [{"id": 1, "path": "boom.py", "language": "python"}],
    )

    result = cmd_verify._check_syntax(_FakeConn(), [1], root)

    # The crashed file must be disclosed, not silently scored 100-clean.
    assert result.get("parse_failures", 0) == 1
    assert any(
        v["category"] == "syntax" and v["severity"] == "INFO" and "boom.py" in v["message"]
        for v in result["violations"]
    ), result["violations"]


def test_verify_check_syntax_none_result_not_credited(tmp_path, monkeypatch) -> None:
    """A parser that returns (None, None, None) also counts as a parse
    failure -- the file was not verified."""
    from roam.commands import cmd_verify

    class _FakeConn:
        def execute(self, *_a, **_k):
            class _Cur:
                def fetchall(_self):
                    return []

            return _Cur()

    root = tmp_path
    (root / "ghost.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    import roam.index.parser as parser_mod

    monkeypatch.setattr(parser_mod, "parse_file", lambda *_a, **_k: (None, None, None), raising=False)
    monkeypatch.setattr(
        cmd_verify,
        "batched_in",
        lambda conn, sql, ids: [{"id": 1, "path": "ghost.py", "language": "python"}],
    )

    result = cmd_verify._check_syntax(_FakeConn(), [1], root)
    assert result.get("parse_failures", 0) == 1
    assert len(result["violations"]) == 1


def test_verify_check_syntax_import_error_marks_unavailable(monkeypatch) -> None:
    """When tree-sitter cannot be imported, the syntax category is marked
    available=False -- not silently scored as a perfect 100."""
    import builtins

    from roam.commands import cmd_verify

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "roam.index.parser":
            raise ImportError("simulated missing tree-sitter")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    monkeypatch.setattr(
        cmd_verify,
        "batched_in",
        lambda conn, sql, ids: [{"id": 1, "path": "x.py", "language": "python"}],
    )

    class _FakeConn:
        def execute(self, *_a, **_k):
            class _Cur:
                def fetchall(_self):
                    return []

            return _Cur()

    result = cmd_verify._check_syntax(_FakeConn(), [1], Path("."))
    assert result.get("available") is False
    assert "unavailable_reason" in result


def test_verify_check_syntax_healthy_path_unchanged(monkeypatch) -> None:
    """Healthy path: a clean parse with no crashes returns the same shape
    as before -- no parse_failures key, no available=False marker."""
    from roam.commands import cmd_verify

    # No file ids -> early return is the simplest healthy shape.
    result = cmd_verify._check_syntax(object(), [], Path("."))
    assert result == {"score": 100, "violations": []}
    assert "parse_failures" not in result
    assert "available" not in result
