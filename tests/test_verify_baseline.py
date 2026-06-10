"""`roam verify --baseline-write` / `--new-only` — accept current debt once,
then surface only NEW findings.

This is the identity-scoped complement to --diff-only's position scoping: it
mutes pre-existing findings (e.g. roam-code's 387 broad-excepts) so the
auto-correct loop only sees debt the agent actually introduced. Fingerprints
are line-shift tolerant (keyed on the stripped code line, not the line number),
so editing elsewhere in a file does not unmute its baselined findings.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_verify import (
    _finding_fingerprint,
    _verify_baseline_path,
)

_ONE = """from __future__ import annotations


def helper_one():
    try:
        return 1
    except Exception:
        pass
"""

_ONE_SHIFTED = """from __future__ import annotations

# a
# b
# c


def helper_one():
    try:
        return 1
    except Exception:
        pass
"""

_TWO = (
    _ONE
    + """

def helper_two():
    try:
        return 2
    except Exception:
        pass
"""
)


def _index(proj):
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)


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


def test_fingerprint_is_line_shift_tolerant(tmp_path):
    (tmp_path / "m.py").write_text(_ONE, encoding="utf-8")
    v = {"category": "error_handling", "file": "m.py", "line": 7, "message": "broad `except Exception:`", "symbol": ""}
    fp1 = _finding_fingerprint(v, {}, tmp_path)
    # Same code line, different line number → same fingerprint.
    (tmp_path / "m.py").write_text(_ONE_SHIFTED, encoding="utf-8")
    v2 = dict(v, line=11)
    fp2 = _finding_fingerprint(v2, {}, tmp_path)
    assert fp1 == fp2, (fp1, fp2)


def test_baseline_write_then_new_only_mutes(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "m.py").write_text(_ONE, encoding="utf-8")
    _index(proj)

    env = _verify(proj, "m.py", "--checks", "error_handling", "--baseline-write")
    assert env["summary"]["verdict"] == "BASELINE_WRITTEN"
    assert env["summary"]["baseline_written"] >= 1
    assert _verify_baseline_path(proj).exists()

    env2 = _verify(proj, "m.py", "--checks", "error_handling", "--new-only")
    assert env2["summary"]["violation_count"] == 0, env2["violations"]
    assert env2["summary"]["baseline"] == "applied"
    assert env2["summary"]["baselined"] >= 1
    # A fully-baselined file is a PASS -- the verdict must reflect the surfaced
    # (post-filter) set, not the raw file's pre-baseline verdict.
    assert env2["summary"]["verdict"] == "PASS", env2["summary"]
    assert env2["summary"]["score"] == 100, env2["summary"]


def test_new_only_surfaces_new_finding(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "m.py").write_text(_ONE, encoding="utf-8")
    _index(proj)
    _verify(proj, "m.py", "--checks", "error_handling", "--baseline-write")

    # Add a brand-new broad-except in helper_two.
    (proj / "m.py").write_text(_TWO, encoding="utf-8")
    env = _verify(proj, "m.py", "--checks", "error_handling", "--new-only")
    assert env["summary"]["violation_count"] >= 1, env["summary"]
    # Every surfaced finding is in helper_two's region (the new code), not the
    # baselined helper_one.
    lines = (proj / "m.py").read_text().splitlines()
    two_start = next(i + 1 for i, ln in enumerate(lines) if "def helper_two" in ln)
    assert all(v["line"] >= two_start for v in env["violations"]), env["violations"]


def test_new_only_without_baseline_shows_all(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "m.py").write_text(_ONE, encoding="utf-8")
    _index(proj)
    env = _verify(proj, "m.py", "--checks", "error_handling", "--new-only")
    assert env["summary"]["baseline"] == "absent"
    assert env["summary"]["violation_count"] >= 1
