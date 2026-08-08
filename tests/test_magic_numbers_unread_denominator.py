"""A file that was never opened may not be counted as a file that was clean.

``files_scanned`` was ``len(_discover_files(root))`` -- the count of paths
rglob YIELDED -- and was never decremented by a file the scanner could not
open or could not parse. ``_scan_python_file`` returned ``[]`` on ``OSError``
and ``[]`` on ``SyntaxError``, so "unreadable", "unparseable" and "genuinely
clean" arrived at the aggregator as the identical empty list. The
denominator admitted the files the numerator could never see.

Measured against the tree that shipped, on two syntactically broken Python
files each containing a repeated numeric literal::

    $ printf 'def a(:\\n    return 86400 + 86400\\n' > broken1.py
    $ printf 'class B(\\n    x = 3600 * 3600\\n'     > broken2.py
    $ roam magic-numbers <dir> --threshold 1
      VERDICT: 0 magic numbers across 2 files scanned
      scanned: 2 files (path=...)                                     rc 0

Control, identical literals with valid syntax::

    $ roam magic-numbers <dir> --threshold 1
      VERDICT: 2 magic numbers across 2 files (top: `3600` in 2 sites)

``roam magic-numbers --help`` disclosed nothing about skipped, unreadable or
unparseable inputs.

The shape adopted here is the one this repo already ships in
``cmd_article_12_check``: separate ``unreadable`` / ``unparseable``
counters, a named gap string, and coverage travelling WITH the verdict.

WHAT IS NOT COVERED HERE
------------------------
``magic-numbers`` has no gate flag, so there is no exit code to change and
none is invented. The defect is a false CLEAN in a report a human reads and
an agent parses; the fix is disclosure, not refusal. If a gate is ever added
to this command it must consult ``summary.scan_incomplete``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.commands.cmd_magic_numbers import (
    _READ,
    _UNPARSEABLE,
    _UNREADABLE,
    _scan_python_file,
    _scan_python_file_status,
)

#: Syntactically broken, and each holds a literal repeated twice -- so a
#: scanner that COULD read them would report findings, not silence.
BROKEN_1 = "def a(:\n    return 86400 + 86400\n"
BROKEN_2 = "class B(\n    x = 3600 * 3600\n"

#: The same literals, valid syntax. This pair is what proves the corpus is
#: not simply devoid of magic numbers.
FIXED_1 = "def a():\n    return 86400 + 86400\n"
FIXED_2 = "class B:\n    x = 3600 * 3600\n"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1"),
    )


def _corpus(root: Path, first: str, second: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "one.py").write_text(first, encoding="utf-8")
    (root / "two.py").write_text(second, encoding="utf-8")
    return root


@pytest.fixture()
def broken_corpus(tmp_path: Path) -> Path:
    return _corpus(tmp_path / "broken", BROKEN_1, BROKEN_2)


@pytest.fixture()
def clean_corpus(tmp_path: Path) -> Path:
    return _corpus(tmp_path / "fixed", FIXED_1, FIXED_2)


# ---------------------------------------------------------------------------
# The scanner's three outcomes
# ---------------------------------------------------------------------------


def test_unparseable_is_not_the_same_result_as_clean(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text(BROKEN_1, encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text("x = 1\n", encoding="utf-8")

    broken_occ, broken_status = _scan_python_file_status(broken, include_trivial=False)
    clean_occ, clean_status = _scan_python_file_status(clean, include_trivial=False)

    assert broken_occ == clean_occ == []
    assert broken_status == _UNPARSEABLE
    assert clean_status == _READ, "a file that WAS read and held nothing must not look like a failure"


def test_unreadable_is_not_the_same_result_as_clean(tmp_path: Path) -> None:
    """A path that cannot be opened at all.

    A directory is used rather than a chmod/icacls dance because the
    permission mechanics differ per platform while ``OSError`` does not --
    and ``OSError`` is the branch under test.
    """
    not_a_file = tmp_path / "package.py"
    not_a_file.mkdir()

    occurrences, status = _scan_python_file_status(not_a_file, include_trivial=False)
    assert occurrences == []
    assert status == _UNREADABLE


def test_bare_scanner_keeps_its_list_signature(tmp_path: Path) -> None:
    """The back-compat projection the existing tests import."""
    clean = tmp_path / "clean.py"
    clean.write_text("x = 86400\n", encoding="utf-8")
    assert [v for v, _ln, _sn in _scan_python_file(clean, include_trivial=False)] == [86400]


# ---------------------------------------------------------------------------
# The reported denominator
# ---------------------------------------------------------------------------


def test_verdict_names_the_files_it_actually_read(broken_corpus: Path) -> None:
    run = _run(broken_corpus, "magic-numbers", str(broken_corpus), "--threshold", "1")
    assert run.returncode == 0, run.stdout[:600] + run.stderr[:600]
    assert "0 of 2 files read" in run.stdout, run.stdout[:600]
    assert "2 files scanned" not in run.stdout, (
        "the empty verdict still claims a denominator the scanner never read.\nstdout: " + run.stdout[:600]
    )
    assert "unparseable" in run.stdout, run.stdout[:600]


def test_envelope_publishes_the_gap(broken_corpus: Path) -> None:
    run = _run(broken_corpus, "--json", "magic-numbers", str(broken_corpus), "--threshold", "1")
    payload = json.loads(run.stdout[run.stdout.find("{") :])
    summary = payload["summary"]
    assert summary["files_discovered"] == 2, summary
    assert summary["files_read"] == 0, summary
    assert summary["files_unparseable"] == 2, summary
    assert summary["files_unreadable"] == 0, summary
    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert len(payload["unparseable_files"]) == 2, payload["unparseable_files"]
    assert any("0 of 2 files read" in f for f in payload["agent_contract"]["facts"]), payload["agent_contract"]


def test_the_same_literals_are_found_when_the_files_parse(clean_corpus: Path) -> None:
    """The positive control: this corpus is NOT devoid of magic numbers."""
    run = _run(clean_corpus, "magic-numbers", str(clean_corpus), "--threshold", "1")
    assert run.returncode == 0, run.stdout[:600] + run.stderr[:600]
    assert "2 magic numbers" in run.stdout, run.stdout[:600]
    assert "2 of 2 files read" in run.stdout, run.stdout[:600]


def test_a_fully_read_corpus_is_not_marked_partial(tmp_path: Path) -> None:
    """The must-not-fire control.

    A genuinely clean, genuinely parseable corpus must keep reporting a
    clean result with ``partial_success: false``. If this fails the fix has
    turned every ordinary run into a degraded one.
    """
    root = _corpus(tmp_path / "trivial", "x = 1\n", "y = 0\n")
    run = _run(root, "--json", "magic-numbers", str(root), "--threshold", "1")
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]
    assert summary["findings_count"] == 0, summary
    assert summary["files_read"] == summary["files_discovered"] == 2, summary
    assert summary["scan_incomplete"] is False, summary
    assert summary["partial_success"] is False, summary
    assert summary["verdict"] == "0 magic numbers across 2 files scanned", summary


def test_unreadable_files_reach_the_envelope_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The OSError leg, driven through the command rather than the scanner.

    ``read_text`` is made to fail for one specific file so the aggregator's
    unreadable branch is exercised without depending on POSIX ``chmod`` or
    Windows ``icacls`` behaving the same way.
    """
    root = _corpus(tmp_path / "mixed", FIXED_1, FIXED_2)
    real_read_text = Path.read_text

    def _fail_for_two(self: Path, *args, **kwargs):
        if self.name == "two.py":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _fail_for_two)

    from click.testing import CliRunner

    from roam.cli import cli

    result = CliRunner().invoke(cli, ["--json", "magic-numbers", str(root), "--threshold", "1"])
    assert result.exit_code == 0, result.output[:600]
    payload = json.loads(result.output[result.output.find("{") :])
    summary = payload["summary"]
    assert summary["files_discovered"] == 2, summary
    assert summary["files_read"] == 1, summary
    assert summary["files_unreadable"] == 1, summary
    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert [Path(p).name for p in payload["unreadable_files"]] == ["two.py"], payload["unreadable_files"]
