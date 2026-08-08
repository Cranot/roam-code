"""`verification_complete` may not be true over a file the run never opened.

Commit b4dcf835 closed the case where report mode scored a whole-repo verdict
over a partial index: it now indexes tracked code files the index has never
seen before resolving targets. That repair enumerates candidates with
``discover_files``, which applies the indexer's 1 MB size cap -- so an
OVERSIZED tracked source file is never counted as missing, never triggers the
light reindex, and never reaches a check. It is simply absent from
``files_checked`` while the envelope keeps asserting full coverage.

Measured against the tree that shipped the b4dcf835 repair::

    ok.py             valid Python
    broken_big.py     1.4 MB, syntax error at line 200001, git add-ed

    $ roam index && roam --json verify --report
      verdict PASS, score 100, files_checked 1,
      verification_complete true, partial_success false,
      violation_count 0                                             rc 0
    $ python -c "ast.parse(open('broken_big.py').read())"
      SyntaxError: invalid syntax  (line 200001)
    $ sqlite3 .roam/index.db 'SELECT path FROM files'  ->  ['ok.py']

That docstring describes closing exactly this shape one variant over, which
is why the size-capped variant is worth a pin of its own rather than a note.

WHY THIS REFUSES WHERE `secrets` RECOVERS
-----------------------------------------
``secrets`` reads the oversized file itself, because a regex sweep does not
need the index. ``verify``'s checks are index-backed -- they run over symbol
rows, graph edges and metrics -- so there is nothing to recover: the file
genuinely cannot be verified while it exceeds the indexer's bound. The
honest answer is therefore to say so, which is the "say what was measured"
arm of the rule rather than the "measure what was claimed" arm.

The gap is scoped to CODE surfaces. A 2 MB CHANGELOG or JSON fixture is not
a verification gap, and measured on roam-code itself all three of its
oversized tracked files are non-code, so its own `verify --report` verdict
is unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.commands.cmd_verify import _report_scope_oversized_code
from roam.index.discovery import MAX_FILE_SIZE


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1"),
    )


def _repo(root: Path, big_name: str, big_body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "ok.py").write_text("def ok(x):\n    return x + 1\n", encoding="utf-8")
    (root / big_name).write_text(big_body, encoding="utf-8")
    assert (root / big_name).stat().st_size > MAX_FILE_SIZE
    _git(root, "add", "ok.py", big_name)
    _git(root, "commit", "-q", "-m", "fixture")
    built = _run(root, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    return root


#: 1.4 MB of valid padding with a genuine syntax error on the last line.
BROKEN_BIG = "x = 1\n" * 200_000 + "def bad(:\n    pass\n"
#: 1.4 MB of non-code. Large, tracked, and NOT a verification gap.
BIG_DOC = "filler line\n" * 200_000


@pytest.fixture()
def oversized_code_repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "code", "broken_big.py", BROKEN_BIG)


@pytest.fixture()
def oversized_doc_repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "doc", "NOTES.md", BIG_DOC)


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


def test_probe_names_the_oversized_code_file(oversized_code_repo: Path) -> None:
    assert _report_scope_oversized_code(oversized_code_repo) == ["broken_big.py"]


def test_probe_ignores_an_oversized_non_code_file(oversized_doc_repo: Path) -> None:
    """The scope control.

    Without it, every repository with a large CHANGELOG would start
    reporting an unverified file -- a signal that fires everywhere is one
    nobody reads.
    """
    assert _report_scope_oversized_code(oversized_doc_repo) == []


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_report_stops_claiming_complete_coverage(oversized_code_repo: Path) -> None:
    run = _run(oversized_code_repo, "--json", "verify", "--report")
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]

    assert summary["verification_complete"] is False, (
        f"`verify --report` claimed whole-repo coverage over a tracked file it never opened.\nsummary: {summary}"
    )
    assert summary["verdict"] != "PASS", summary
    assert summary["partial_success"] is True, summary
    assert "oversized_target_unverified" in summary.get("incomplete_reasons", []), summary


def test_the_unverified_file_is_named_not_merely_counted(oversized_code_repo: Path) -> None:
    """A count a reader cannot act on is barely better than silence."""
    run = _run(oversized_code_repo, "--json", "verify", "--report")
    payload = json.loads(run.stdout[run.stdout.find("{") :])
    gaps = [v for v in payload.get("violations", []) if v.get("category") == "verification"]
    assert any(v.get("file") == "broken_big.py" for v in gaps), gaps
    assert any("size cap" in (v.get("message") or "") for v in gaps), gaps


def test_a_repo_whose_oversized_file_is_not_code_still_passes(oversized_doc_repo: Path) -> None:
    """The must-not-fire control, end to end.

    roam-code's own three oversized tracked files are all non-code, so this
    is the case that decides whether the repo's own report verdict moves.
    """
    run = _run(oversized_doc_repo, "--json", "verify", "--report")
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]
    assert summary["verification_complete"] is True, summary
    assert "oversized_target_unverified" not in summary.get("incomplete_reasons", []), summary
