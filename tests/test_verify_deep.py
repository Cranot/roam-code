"""`roam verify --deep` — surface algorithm/idiom anti-patterns (the "vast
detector collection") SCOPED to the changed files, as an advisory `patterns`
category, on top of the standard checks.

Design pins:
1. Default verify (no --deep) is UNCHANGED — no `patterns` category.
2. --deep adds a `patterns` category with the changed files' anti-patterns.
3. The idiom sweep is SCOPED (set_idiom_scope) so it stays fast post-edit.
4. `patterns` is advisory — it does NOT flip a PASS verdict to FAIL.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli

_SRC = """from __future__ import annotations

import re


def helper_a():
    return 1


def mutable_default(x=[]):   # py-mutable-default-arg anti-pattern
    x.append(1)
    return x
"""


def _indexed(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "m.py").write_text(_SRC, encoding="utf-8")
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)
    return proj


def _verify(proj, *args):
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        res = CliRunner().invoke(cli, ["--json", "verify", "m.py", *args], env={"ROAM_COMPILE_VERIFY": "1"})
    finally:
        os.chdir(old)
    return json.loads(res.output[res.output.index("{") :])


def test_scope_unit():
    """set_idiom_scope restricts _python_files to the given ids (and resets)."""
    from roam.catalog.python_idioms import _python_files, set_idiom_scope
    from roam.db.connection import open_db

    try:
        # Empty scope → no python files visible; None → all.
        set_idiom_scope(set())
        with open_db(readonly=True) as conn:
            assert _python_files(conn) == []
    finally:
        set_idiom_scope(None)


def test_default_verify_has_no_patterns_category(tmp_path):
    env = _verify(_indexed(tmp_path), "--checks", "naming")
    assert "patterns" not in env["categories"], env["categories"].keys()


def test_deep_surfaces_scoped_idiom_patterns(tmp_path):
    env = _verify(_indexed(tmp_path), "--checks", "naming", "--deep")
    cats = env["categories"]
    assert "patterns" in cats, cats.keys()
    msgs = " ".join(v.get("message", "") for v in cats["patterns"]["violations"])
    assert "py-mutable-default-arg" in msgs, cats["patterns"]
    # Advisory: a clean naming check + only advisory patterns stays PASS.
    assert env["summary"]["verdict"].startswith("PASS"), env["summary"]


def test_deep_advisory_patterns_do_not_gate_diff_only_verdict(tmp_path):
    """REGRESSION: a `--deep` advisory `patterns` finding sitting ON a changed
    line must NOT flip the verdict. The diff-only / suppression recompute paths
    re-score from the surviving violation set; before the fix they counted the
    advisory patterns finding, turning a clean PASS/100 into WARN/95. The
    `--compute_composite` (non-diff) path was already correct (patterns has no
    category weight), so this guards the recompute path specifically.

    `--changed-lines` implies diff-only behaviour without a git baseline, so we
    scope to the lines bearing the mutable-default anti-pattern (def at L10).
    """
    env = _verify(_indexed(tmp_path), "--checks", "naming", "--deep", "--changed-lines", "m.py:1-15")
    s = env["summary"]
    # The advisory finding is still SURFACED (transparency) ...
    pviols = env["categories"].get("patterns", {}).get("violations", [])
    assert any("py-mutable-default-arg" in v.get("message", "") for v in pviols), env["categories"]
    # ... but it does NOT gate: clean naming + advisory-only stays PASS/100.
    assert s["verdict"].startswith("PASS"), s
    assert s["score"] == 100, s
