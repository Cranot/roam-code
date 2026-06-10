"""`roam verify --changed-lines` — scope to EXPLICIT line ranges (the lines the
caller actually edited this turn) instead of git-diff-vs-HEAD.

Motivation (2026-06-04): a host auto-correct loop ran `verify --diff-only` on a
huge uncommitted tree and surfaced 79 PRE-EXISTING issues (0 from the actual edit),
because --diff-only baselines against HEAD. An editor/agent harness knows exactly
which lines it changed, so it can pass them explicitly and only those drive the
verdict. Pins the parser + the scoping behaviour.
"""

from __future__ import annotations

import json
import os
import subprocess

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_verify import _parse_changed_lines


def test_parse_changed_lines() -> None:
    r = _parse_changed_lines("src/a.py:1-5,src/a.py:10-12,src/b.py:7")
    assert r["src/a.py"] == set(range(1, 6)) | set(range(10, 13))
    assert r["src/b.py"] == {7}
    # empty / malformed segments skipped; reversed range normalized.
    assert _parse_changed_lines("") == {}
    assert _parse_changed_lines("nopaths,foo:,:5,foo:bar") == {}
    assert _parse_changed_lines("x.py:5-3") == {"x.py": {3, 4, 5}}


def _indexed_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    body = (
        "from __future__ import annotations\n\n\n"
        + "\n\n".join(f"def helper_{i}():\n    return {i}" for i in range(12))
        + "\n"
    )
    (proj / "lib.py").write_text(body, encoding="utf-8")
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)
    return proj


def _verify(proj, *args):
    runner = CliRunner()
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        res = runner.invoke(cli, ["--json", "verify", *args], env={"ROAM_COMPILE_VERIFY": "1"})
    finally:
        os.chdir(old)
    out = res.output
    return json.loads(out[out.index("{") :])


def test_changed_lines_scopes_to_explicit_ranges(tmp_path):
    proj = _indexed_project(tmp_path)
    # Append a PascalCase fn — a naming violation — at the end of lib.py.
    with open(proj / "lib.py", "a", encoding="utf-8") as fh:
        fh.write("\n\ndef BadName():\n    return 99\n")
    lines = (proj / "lib.py").read_text(encoding="utf-8").splitlines()
    bad_line = next(i + 1 for i, ln in enumerate(lines) if "def BadName" in ln)

    # Scope to lines 1-2 (NOT BadName's line) → the violation is hidden.
    env = _verify(proj, "lib.py", "--checks", "naming", "--changed-lines", "lib.py:1-2")
    assert env["summary"]["violation_count"] == 0, env["summary"]
    assert env["summary"].get("diff_scoped") is True

    # Scope to BadName's actual line → the violation is kept.
    env2 = _verify(proj, "lib.py", "--checks", "naming", "--changed-lines", f"lib.py:{bad_line}")
    syms = {v["symbol"] for v in env2["categories"]["naming"]["violations"]}
    assert "BadName" in syms, env2["categories"]["naming"]


def test_diff_only_keeps_python_syntax_errors_on_changed_file(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "lib.py").write_text("def good_name():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=proj, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "lib.py"], cwd=proj, check=True)
    subprocess.run(
        ["git", "-c", "user.email=verify@example.invalid", "-c", "user.name=Verify", "commit", "-m", "init"],
        cwd=proj,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)

    (proj / "lib.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    env = _verify(proj, "lib.py", "--checks", "syntax", "--diff-only")

    assert env["summary"]["verdict"] == "FAIL"
    assert env["summary"]["violation_count"] >= 1
    assert env["categories"]["syntax"]["violations"][0]["category"] == "syntax"
