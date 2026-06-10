"""Tests for ``roam magic-numbers`` — AST scan for hardcoded numeric
constants.

The command is invoked via ``CliRunner`` against the command callable
directly (the command is not yet registered in ``cli._COMMANDS`` per the
add-task scope), which is the same pattern used by other ``test_cmd_*``
files that exercise commands in isolation.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_magic_numbers import (
    _TRIVIAL_VALUES,
    _extract_numeric_literals,
    _is_test_path,
    magic_numbers,
)

# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _make_project(tmp_path: Path) -> Path:
    """Three .py files seeded with known magic numbers.

    * ``a.py`` uses 3600 twice and 0.95 once.
    * ``b.py`` uses 3600 three times and 0.95 once and 1 (trivial) twice.
    * ``c.py`` uses 42 once (below threshold).
    * ``tests/test_x.py`` uses 3600 five times — MUST be skipped.
    * ``v.py`` defines ``__version__ = 7`` — the 7 MUST be skipped.
    """
    root = tmp_path / "proj"
    _write(
        root / "src" / "a.py",
        """
        TIMEOUT_A = 3600
        TIMEOUT_B = 3600
        THRESHOLD = 0.95
    """,
    )
    _write(
        root / "src" / "b.py",
        """
        def f():
            x = 3600
            y = 3600
            z = 3600
            t = 0.95
            return 1 + 1
    """,
    )
    _write(
        root / "src" / "c.py",
        """
        ANSWER = 42
    """,
    )
    # Test file — must be skipped entirely.
    _write(
        root / "tests" / "test_x.py",
        """
        T = 3600
        U = 3600
        V = 3600
        W = 3600
        X = 3600
    """,
    )
    # __version__ literal — must NOT be counted.
    _write(
        root / "src" / "v.py",
        """
        __version__ = 7
        OTHER = 99
    """,
    )
    return root / "src"


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


def test_is_test_path_detects_tests_dir():
    assert _is_test_path(Path("foo/tests/bar.py")) is True
    assert _is_test_path(Path("tests/test_x.py")) is True
    assert _is_test_path(Path("src/foo/bar.py")) is False


def test_is_test_path_detects_test_prefix():
    assert _is_test_path(Path("foo/test_bar.py")) is True
    assert _is_test_path(Path("foo/notatest.py")) is False


def test_trivial_values_default_set():
    assert _TRIVIAL_VALUES == frozenset({0, 1, -1, 2})


def test_extract_numeric_literals_filters_bools(tmp_path):
    src_path = tmp_path / "x.py"
    src_path.write_text("a = True\nb = False\nc = 7\nd = 3.14\n", encoding="utf-8")
    import ast as _ast

    tree = _ast.parse(src_path.read_text())
    lines = src_path.read_text().splitlines()
    out = _extract_numeric_literals(tree, lines, str(src_path))
    values = sorted(v for v, _, _ in out)
    # True/False are bool instances and must be excluded.
    assert 7 in values
    assert 3.14 in values
    assert True not in values
    assert False not in values


# ---------------------------------------------------------------------------
# CLI behaviour tests
# ---------------------------------------------------------------------------


def _invoke_json(runner: CliRunner, *args: str):
    """Invoke the command with ``ctx.obj['json'] = True`` so the JSON
    branch is exercised. The command reads ``ctx.obj.get('json')`` and
    the public ``roam --json <cmd>`` flag also sets that key — we
    replicate it directly here."""
    return runner.invoke(magic_numbers, list(args), obj={"json": True})


def test_finds_repeated_magic_numbers_default_threshold(tmp_path):
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, str(src))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    findings = payload["findings"]
    values = {f["value"]: f["occurrences"] for f in findings}

    # 3600 appears: 2 in a.py + 3 in b.py = 5 (tests file skipped).
    assert values.get(3600) == 5
    # 0.95 appears: 1 in a.py + 1 in b.py = 2 (>= threshold 2).
    assert values.get(0.95) == 2
    # 42 appears once (< threshold 2) — must NOT be flagged.
    assert 42 not in values
    # 1 is trivial and skipped (the two 1s in b.py and the implicit 1 in
    # the AST do not appear).
    assert 1 not in values
    # __version__ = 7 is suppressed.
    assert 7 not in values
    # 99 appears once — below threshold.
    assert 99 not in values


def test_top_finding_appears_first(tmp_path):
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, str(src))
    payload = json.loads(result.output)
    findings = payload["findings"]
    assert findings, "expected at least one finding"
    # Sorted DESC by occurrences — 3600 (5) before 0.95 (2).
    assert findings[0]["value"] == 3600
    assert findings[0]["occurrences"] == 5


def test_sites_include_file_line_snippet(tmp_path):
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, str(src))
    payload = json.loads(result.output)
    top = next(f for f in payload["findings"] if f["value"] == 3600)
    site = top["sites"][0]
    assert "file" in site and "line" in site and "context_snippet" in site
    assert "3600" in site["context_snippet"]
    assert site["line"] >= 1


def test_envelope_summary_fields(tmp_path):
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, str(src))
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert "verdict" in summary
    assert summary["threshold_used"] == 2
    assert summary["include_trivial"] is False
    assert summary["files_scanned"] == payload["files_scanned"]
    # files_scanned excludes the tests/ dir (4 non-test .py files: a, b, c, v)
    assert payload["files_scanned"] == 4
    assert payload["threshold_used"] == 2


def test_threshold_filter_raises_floor(tmp_path):
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, "--threshold", "3", str(src))
    payload = json.loads(result.output)
    values = {f["value"]: f["occurrences"] for f in payload["findings"]}
    # 3600 (5) survives threshold 3; 0.95 (2) drops out.
    assert values.get(3600) == 5
    assert 0.95 not in values


def test_include_trivial_flips_zero_and_one_back_on(tmp_path):
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, "--include-trivial", "--threshold", "2", str(src))
    payload = json.loads(result.output)
    values = {f["value"]: f["occurrences"] for f in payload["findings"]}
    # b.py has `return 1 + 1` (two 1s) — with --include-trivial they
    # cross the threshold.
    assert values.get(1, 0) >= 2


def test_agent_contract_facts_law4_anchored(tmp_path):
    """Facts must terminate on tokens in ``concrete_plural_terminals``."""
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, str(src))
    payload = json.loads(result.output)
    facts = payload["agent_contract"]["facts"]
    # The terminal token of each fact (last whitespace-split, punctuation
    # stripped) should be a plural concrete noun. Sample checks:
    terminals = [f.split()[-1].rstrip(".,!?") for f in facts]
    # `magic numbers across N files`, `N files scanned`, `threshold N occurrences`
    assert "files" in terminals or "scanned" in terminals
    assert "occurrences" in terminals or "sites" in terminals or "files" in terminals


def test_missing_path_exits_with_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        magic_numbers,
        [str(tmp_path / "does-not-exist")],
        obj={"json": True},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["partial_success"] is False
    assert "path not found" in payload["summary"]["verdict"]


def test_text_output_includes_verdict_line(tmp_path):
    """Non-JSON mode emits a leading VERDICT: line."""
    src = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(magic_numbers, [str(src)], obj={"json": False})
    assert result.exit_code == 0
    assert result.output.splitlines()[0].startswith("VERDICT:")
