"""A whole-repo report must not quietly report on part of the repo.

`--report` takes its targets FROM the index, so a tracked file the index has
never seen cannot become a target. Before this was closed, that produced the
worst possible shape: a repository containing a file that does not parse
reported PASS/100 with `verification_complete` true and `index_refresh.state`
"current" -- a false CLEAN carrying a false claim of currency. The non-report
path never had the hole, because it derives targets from the diff and treats a
missing index row as a changed file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

OK = "export function ok(): number { return 1 }\n"
BROKEN = "export function bad(): number {\n  return [\n}\n"


def _report(project: Path) -> dict:
    result = invoke_cli(CliRunner(), ["--json", "verify", "--report"], cwd=project)
    return json.loads(result.output)


def _make(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (project / "src" / "ok.ts").write_text(OK, encoding="utf-8")
    git_init(project)
    index_in_process(project)
    return project


def test_a_tracked_file_absent_from_the_index_is_still_reported_on(tmp_path, monkeypatch):
    project = _make(tmp_path)
    # Added AFTER the index was built -- exactly the case report mode could not
    # see, because its target list is read out of the index it is missing from.
    (project / "src" / "added_broken.ts").write_text(BROKEN, encoding="utf-8")
    monkeypatch.chdir(project)

    summary = _report(project)["summary"]

    assert summary["files_checked"] == 2, "the whole-repo report checked only part of the repo"
    assert not str(summary["verdict"]).upper().startswith("PASS"), (
        f"a file that does not parse was reported as clean: {summary['verdict']}"
    )


def test_the_repair_is_idempotent_and_changes_nothing_when_the_index_is_current(tmp_path, monkeypatch):
    # The must-not-churn pair: a covered repo must produce a byte-identical
    # verdict on a second run, or the repair is doing work it has no reason to.
    project = _make(tmp_path)
    (project / "src" / "added_broken.ts").write_text(BROKEN, encoding="utf-8")
    monkeypatch.chdir(project)

    first = _report(project)["summary"]
    second = _report(project)["summary"]

    for field in ("verdict", "score", "files_checked", "verification_complete"):
        assert first[field] == second[field], field


def test_a_repository_needing_no_repair_is_untouched(tmp_path, monkeypatch):
    # The other must-not-fire: nothing was added, so the coverage pass has
    # nothing to do and the clean repo still reports clean.
    project = _make(tmp_path)
    monkeypatch.chdir(project)

    summary = _report(project)["summary"]

    assert summary["files_checked"] == 1
    assert str(summary["verdict"]).upper().startswith("PASS")


def test_coverage_repair_reports_zero_when_it_cannot_enumerate(tmp_path, monkeypatch):
    # Best-effort by construction: if the coverage question cannot be answered
    # the repair must change nothing rather than invent a gap, so a repository
    # it cannot enumerate behaves exactly as it did before.
    from roam.commands import cmd_verify

    def _boom(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr("roam.index.discovery.discover_files", _boom)
    assert cmd_verify._repair_report_index_coverage(tmp_path) == 0
