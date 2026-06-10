"""Tests for `roam cycles` — the import/call cycle (SCC) command.

Sibling of `roam clusters` / `roam layers`; the focused view of the cycle
analysis `roam health` bundles.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    index_in_process,
    invoke_cli,
    parse_json_output,
)


def test_cycles_finds_cross_file_cycle(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "cyc"
    proj.mkdir()
    # Anchor project-root detection here so find_project_root can't walk up to a
    # polluted /tmp ancestor (lesson from the brief-test /tmp pollution dig).
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("from b import foo\n\n\ndef bar():\n    return foo()\n")
    (proj / "b.py").write_text("from a import bar\n\n\ndef foo():\n    return bar()\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out

    result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="cycles")
    assert data["command"] == "cycles"
    assert data["summary"]["cycle_count"] >= 1
    assert data["summary"]["actionable_count"] >= 1  # 2 distinct non-test files


def test_cycles_clean_repo_reports_none(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "clean"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("def foo():\n    return 1\n")
    (proj / "b.py").write_text("def bar():\n    return 2\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out

    result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
    assert result.exit_code == 0
    data = parse_json_output(result, command="cycles")
    assert data["summary"]["cycle_count"] == 0
