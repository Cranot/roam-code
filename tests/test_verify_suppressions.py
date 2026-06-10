"""`roam verify` honors `.roam-suppressions.yml` (rule=category, file, optional
line): an INTENDED finding can be ACKNOWLEDGED so it stops re-surfacing — keeping
the auto-correct dogfood signal sharp on genuinely-NEW debt. Transparent: the
suppressed count is reported in `summary.suppressed`, never silently hidden.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli


def _indexed_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()  # isolate index root from any stray /tmp/.git (find_project_root stops here)
    body = (
        "from __future__ import annotations\n\n\n"
        + "\n\n".join(f"def helper_{i}():\n    return {i}" for i in range(12))
        + "\n\n\ndef BadName():\n    return 99\n"
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


def _verify(proj):
    runner = CliRunner()
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        res = runner.invoke(cli, ["--json", "verify", "lib.py", "--checks", "naming"], env={"ROAM_COMPILE_VERIFY": "1"})
    finally:
        os.chdir(old)
    out = res.output
    return json.loads(out[out.index("{") :])


def test_verify_honors_suppression(tmp_path):
    proj = _indexed_project(tmp_path)

    # Baseline: the PascalCase `BadName` is flagged.
    env = _verify(proj)
    assert env["summary"]["violation_count"] >= 1
    assert "BadName" in {v["symbol"] for v in env["categories"]["naming"]["violations"]}
    assert "suppressed" not in env["summary"]

    # Acknowledge it via .roam-suppressions.yml → stops surfacing, but is COUNTED.
    (proj / ".roam-suppressions.yml").write_text(
        "suppressions:\n  - rule: naming\n    file: lib.py\n    reason: intended demo name\n", encoding="utf-8"
    )
    env2 = _verify(proj)
    syms = {v["symbol"] for v in env2["categories"]["naming"]["violations"]}
    assert "BadName" not in syms, "an acknowledged finding must not surface"
    assert env2["summary"].get("suppressed", 0) >= 1, env2["summary"]
