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


def _indexed_project(tmp_path, *, helper_count=12, peer_outliers=()):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()  # isolate index root from any stray /tmp/.git (find_project_root stops here)
    functions = [f"def helper_{i}():\n    return {i}" for i in range(helper_count)]
    functions.extend(f"def {name}():\n    return 98" for name in peer_outliers)
    functions.append("def BadName():\n    return 99")
    body = "from __future__ import annotations\n\n\n" + "\n\n".join(functions) + "\n"
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
    # Ten conventional names plus two distinct outliers keep naming confidence
    # below the FAIL boundary, making this genuine suppressible advisory debt.
    proj = _indexed_project(tmp_path, helper_count=10, peer_outliers=("alsoBad",))

    # Baseline: the PascalCase `BadName` is flagged.
    env = _verify(proj)
    assert env["summary"]["violation_count"] >= 1
    bad_name = next(v for v in env["categories"]["naming"]["violations"] if v["symbol"] == "BadName")
    assert bad_name["severity"] == "WARN"
    assert "suppressed" not in env["summary"]

    # Acknowledge it via .roam-suppressions.yml → stops surfacing, but is COUNTED.
    (proj / ".roam-suppressions.yml").write_text(
        "suppressions:\n  - rule: naming\n    file: lib.py\n    reason: intended demo name\n", encoding="utf-8"
    )
    env2 = _verify(proj)
    syms = {v["symbol"] for v in env2["categories"]["naming"]["violations"]}
    assert "BadName" not in syms, "an acknowledged finding must not surface"
    assert env2["summary"].get("suppressed", 0) >= 1, env2["summary"]


def test_verify_refuses_to_suppress_fail_finding(tmp_path):
    proj = _indexed_project(tmp_path)
    (proj / ".roam-suppressions.yml").write_text(
        "suppressions:\n  - rule: naming\n    file: lib.py\n    reason: cannot acknowledge a gate failure\n",
        encoding="utf-8",
    )

    env = _verify(proj)
    bad_name = next(v for v in env["categories"]["naming"]["violations"] if v["symbol"] == "BadName")
    assert bad_name["severity"] == "FAIL"
    assert env["summary"].get("suppressed", 0) == 0
    assert env["summary"]["verdict"] == "FAIL"


def test_verify_refuses_to_suppress_hard_block(tmp_path):
    from roam.commands.cmd_verify import _apply_verify_suppressions

    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n  - rule: verification\n    file: lib.py\n    reason: cannot acknowledge incomplete verification\n",
        encoding="utf-8",
    )
    violation = {
        "category": "verification",
        "severity": "WARN",
        "file": "lib.py",
        "line": 1,
        "message": "verification incomplete",
        "hard_block": True,
    }
    categories = {"verification": {"violations": [violation]}}

    remaining, suppressed_count = _apply_verify_suppressions(tmp_path, categories, [violation])

    assert remaining == [violation]
    assert categories["verification"]["violations"] == [violation]
    assert suppressed_count == 0
