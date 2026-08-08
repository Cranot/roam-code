"""`--fail-on-divergence` may not exit 0 over a corpus it never decoded.

``files_scanned`` was ``len(_discover_files(root))`` -- the paths rglob
yielded. The actual read happens inside ``extract_calcs_from_file``, whose
docstring says "``[]`` on any I/O or grammar miss", so the caller could not
tell an empty result from an unopened file. The opt-in CI gate then computed
its PASS over the empty set of files it decoded while publishing the count
of files it merely listed.

Measured against the tree that shipped, on two Python files each holding a
rounded money formula, with read access denied on both::

    $ python -c "Path('locked.py').read_bytes()"
      OSError: PermissionError [Errno 13] Permission denied
    $ roam calc-inventory <dir> --json --fail-on-divergence
      {"calculations": 0, "files_scanned": 2, "files_with_calcs": 0,
       "gate_failed": false, "partial_success": false,
       "verdict": "no calculations found"}                             rc 0
    $ roam calc-inventory <dir> --fail-on-divergence
      VERDICT: no calculations found                                   rc 0

Baseline with both files readable: 2 calculations across 2 files.
``--help`` calls ``--fail-on-divergence`` an "Opt-in CI gate" and discloses
no skip, cap or read-failure state.

WHY THIS GATE FAILS CLOSED ON AN INCOMPLETE READ, WHERE magic-numbers ONLY
DISCLOSES
-------------------------------------------------------------------------
A magic-number finding is a property of ONE file, so an unread file can
only add occurrences. A divergence is a property of the RELATION between
two formulas that share a field name: the file that was never decoded may
hold the second formula that makes the first one divergent. An unread file
can therefore RETRACT this command's answer, not merely add to it, which is
what makes the empty verdict uncertifiable rather than merely incomplete.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.index.calc_extract import (
    CALC_NO_LANGUAGE,
    CALC_READ,
    CALC_UNREADABLE,
    extract_calcs_from_file,
    extract_calcs_from_file_status,
)

MONEY_A = "def f(base, rate):\n    vat = round(base * rate / 100, 2)\n    return vat\n"
MONEY_B = "def g(base, rate):\n    total = round(base * rate / 100, 2)\n    return total\n"


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


@pytest.fixture()
def money_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.py").write_text(MONEY_A, encoding="utf-8")
    (root / "b.py").write_text(MONEY_B, encoding="utf-8")
    return root


def _deny_reads(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make ``read_bytes`` raise for the named files, on every platform.

    A real ACL/chmod would exercise the same ``OSError`` branch but the
    mechanics differ per platform while the branch does not -- and the
    branch is what is under test.
    """
    real_read_bytes = Path.read_bytes
    denied = set(names)

    def _fail(self: Path, *args, **kwargs):
        if self.name in denied:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", _fail)


# ---------------------------------------------------------------------------
# The extractor's outcomes
# ---------------------------------------------------------------------------


def test_unreadable_is_not_the_same_result_as_no_calculations(tmp_path: Path) -> None:
    holds_calcs = tmp_path / "a.py"
    holds_calcs.write_text(MONEY_A, encoding="utf-8")
    holds_none = tmp_path / "empty.py"
    holds_none.write_text("x = 1\n", encoding="utf-8")
    missing = tmp_path / "gone.py"

    found, status = extract_calcs_from_file_status(holds_calcs)
    assert status == CALC_READ and len(found) == 1

    none_found, none_status = extract_calcs_from_file_status(holds_none)
    assert none_status == CALC_READ and none_found == []

    absent, absent_status = extract_calcs_from_file_status(missing)
    assert absent == [] and absent_status == CALC_UNREADABLE, (
        "a file that could not be opened produced the same shape as one that held nothing"
    )


def test_unknown_language_is_its_own_state(tmp_path: Path) -> None:
    odd = tmp_path / "notes.unknownext"
    odd.write_text("total = round(1 * 2 / 100, 2)\n", encoding="utf-8")
    calcs, status = extract_calcs_from_file_status(odd)
    assert (calcs, status) == ([], CALC_NO_LANGUAGE)


def test_bare_extractor_keeps_its_list_signature(tmp_path: Path) -> None:
    holds_calcs = tmp_path / "a.py"
    holds_calcs.write_text(MONEY_A, encoding="utf-8")
    assert len(extract_calcs_from_file(holds_calcs)) == 1


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_gate_refuses_a_corpus_it_could_not_read(money_corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from roam.cli import cli

    _deny_reads(monkeypatch, "a.py", "b.py")
    result = CliRunner().invoke(cli, ["--json", "calc-inventory", str(money_corpus), "--fail-on-divergence"])

    assert result.exit_code != 0, (
        "`calc-inventory --fail-on-divergence` certified a corpus whose every "
        f"file failed to open.\noutput: {result.output[:600]!r}"
    )
    summary = json.loads(result.output[result.output.find("{") :])["summary"]
    assert summary["files_discovered"] == 2, summary
    assert summary["files_read"] == 0, summary
    assert summary["files_unreadable"] == 2, summary
    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert summary["gate_failed"] is True, summary
    assert summary["verdict"] != "no calculations found", summary


def test_text_channel_refuses_at_the_same_input(money_corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate decision used to sit BELOW `if not calcs: return`.

    Without this the text channel would exit 0 on the very refusal --json
    makes -- the channel split this repo has fixed twice before.
    """
    from click.testing import CliRunner

    from roam.cli import cli

    _deny_reads(monkeypatch, "a.py", "b.py")
    result = CliRunner().invoke(cli, ["calc-inventory", str(money_corpus), "--fail-on-divergence"])

    assert result.exit_code != 0, result.output[:600]
    assert "cannot certify" in result.output, result.output[:600]
    assert "unreadable" in result.output, result.output[:600]


def test_a_partially_read_corpus_is_still_uncertifiable(money_corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One of two denied is the harder case: findings exist AND coverage is short.

    The file that was not decoded may hold the second formula that makes the
    first divergent, so "no divergence" over a partial read is not an answer.
    """
    from click.testing import CliRunner

    from roam.cli import cli

    _deny_reads(monkeypatch, "b.py")
    result = CliRunner().invoke(cli, ["--json", "calc-inventory", str(money_corpus), "--fail-on-divergence"])

    summary = json.loads(result.output[result.output.find("{") :])["summary"]
    assert summary["files_read"] == 1, summary
    assert summary["files_discovered"] == 2, summary
    assert summary["calculations"] == 1, summary
    assert summary["scan_incomplete"] is True, summary
    assert result.exit_code != 0, result.output[:600]


def test_a_fully_read_corpus_still_passes(money_corpus: Path) -> None:
    """The must-not-fire control, in both channels.

    Without it the fix would make every ordinary run a refusal.
    """
    text = _run(money_corpus, "calc-inventory", str(money_corpus), "--fail-on-divergence")
    assert text.returncode == 0, text.stdout[:600] + text.stderr[:600]
    assert "cannot certify" not in text.stdout

    js = _run(money_corpus, "--json", "calc-inventory", str(money_corpus), "--fail-on-divergence")
    assert js.returncode == 0, js.stdout[:600]
    summary = json.loads(js.stdout[js.stdout.find("{") :])["summary"]
    assert summary["calculations"] == 2, summary
    assert summary["files_read"] == summary["files_discovered"] == 2, summary
    assert summary["scan_incomplete"] is False, summary
    assert summary["partial_success"] is False, summary
    assert summary["gate_failed"] is False, summary


def test_reporting_without_the_gate_flag_still_exits_zero(money_corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Narrowness control: the refusal belongs to the GATE, not the command.

    Without ``--fail-on-divergence`` nobody asked this run to certify
    anything, so an incomplete read is disclosed and reported, not refused.
    """
    from click.testing import CliRunner

    from roam.cli import cli

    _deny_reads(monkeypatch, "a.py", "b.py")
    result = CliRunner().invoke(cli, ["--json", "calc-inventory", str(money_corpus)])

    assert result.exit_code == 0, result.output[:600]
    summary = json.loads(result.output[result.output.find("{") :])["summary"]
    assert summary["scan_incomplete"] is True, summary
    assert summary["gate_failed"] is False, summary
