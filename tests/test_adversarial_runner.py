"""W8.E — sanity tests for /usr/local/bin/roam-adversarial-check.

Loads the script under test as a module so we can mock subprocess.run
and verify TSV layout + exit-code logic without touching real `roam`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
from unittest import mock

import pytest

SCRIPT_PATH = pathlib.Path("/usr/local/bin/roam-adversarial-check")

pytestmark = pytest.mark.skipif(
    not SCRIPT_PATH.exists(),
    reason="adversarial-check script not installed",
)


def _load_runner() -> types.ModuleType:
    # The script has no .py suffix, so use SourceFileLoader directly.
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader("roam_adversarial_check", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture
def runner():
    return _load_runner()


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    corpus = tmp_path / "internal" / "benchmarks"
    corpus.mkdir(parents=True)
    (corpus / "adversarial_tasks.txt").write_text(
        "# header comment\n\nfix it\n# section header\ntrace the irony in docs\n",
        encoding="utf-8",
    )
    return tmp_path


def _mk_completed(returncode: int, stdout: str) -> mock.Mock:
    completed = mock.Mock()
    completed.returncode = returncode
    completed.stdout = stdout
    completed.stderr = ""
    return completed


def test_parse_tasks_skips_blanks_and_comments(runner, project):
    tasks = runner.parse_tasks(project / "internal" / "benchmarks" / "adversarial_tasks.txt")
    assert tasks == ["fix it", "trace the irony in docs"]


def test_run_pass_when_all_tasks_return_procedure(runner, project, tmp_path, monkeypatch):
    log = tmp_path / "adv.tsv"
    ok = _mk_completed(
        0,
        '{"summary": {"procedure": "freeform_explore", "verdict": "ok"}}',
    )
    monkeypatch.setattr(runner.subprocess, "run", mock.Mock(return_value=ok))
    rc = runner.main([str(project), "--log", str(log)])
    assert rc == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    # header + 2 data rows + summary comment
    assert len(lines) == 4
    assert lines[0].startswith("date\ttask_idx\ttask_prefix")
    # 8 tab-separated columns per data row
    assert lines[1].count("\t") == 7
    assert lines[2].count("\t") == 7
    assert lines[-1].startswith("# ")
    # Summary: passed=2 failed=0 crashed=0
    fields = lines[-1].split("\t")
    assert fields[1] == "2"
    assert fields[2] == "0"
    assert fields[3] == "0"


def test_run_crash_exit_5_when_subprocess_nonzero(runner, project, tmp_path, monkeypatch):
    log = tmp_path / "adv.tsv"
    bad = _mk_completed(
        2,
        '{"summary": {"verdict": "task_too_short"}}',
    )
    monkeypatch.setattr(runner.subprocess, "run", mock.Mock(return_value=bad))
    rc = runner.main([str(project), "--log", str(log)])
    assert rc == 5
    lines = log.read_text(encoding="utf-8").splitlines()
    # Both tasks should have crash_reason populated (missing_procedure
    # is checked before exit_<n>).
    data_rows = [ln for ln in lines if not ln.startswith("#") and not ln.startswith("date")]
    assert len(data_rows) == 2
    for row in data_rows:
        cols = row.split("\t")
        assert cols[3] == "2"  # exit_code
        assert cols[4] == "True"  # json_ok
        assert cols[5] == ""  # procedure empty
        assert cols[7] == "missing_procedure"


def test_run_crash_on_invalid_json(runner, project, tmp_path, monkeypatch):
    log = tmp_path / "adv.tsv"
    junk = _mk_completed(0, "not json at all")
    monkeypatch.setattr(runner.subprocess, "run", mock.Mock(return_value=junk))
    rc = runner.main([str(project), "--log", str(log)])
    assert rc == 5
    body = log.read_text(encoding="utf-8")
    assert "invalid_json" in body


def test_run_crash_on_timeout(runner, project, tmp_path, monkeypatch):
    log = tmp_path / "adv.tsv"

    def _raise_timeout(*_a, **_kw):
        raise runner.subprocess.TimeoutExpired(cmd=["roam"], timeout=15)

    monkeypatch.setattr(runner.subprocess, "run", _raise_timeout)
    rc = runner.main([str(project), "--log", str(log)])
    assert rc == 5
    body = log.read_text(encoding="utf-8")
    assert "timeout" in body


def test_missing_corpus_returns_2(runner, tmp_path):
    rc = runner.main([str(tmp_path), "--log", str(tmp_path / "out.tsv")])
    assert rc == 2


def test_task_prefix_strips_tabs_and_truncates(runner):
    assert runner.task_prefix("a" * 200) == "a" * 40
    assert "\t" not in runner.task_prefix("a\tb\tc\td" * 20)
