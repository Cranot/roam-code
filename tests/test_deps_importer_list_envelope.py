"""The `deps` JSON envelope must carry the importer / import FILE LIST, not
just counts (2026-06-07).

Regression for the codex nav A/B q2 finding: `roam deps --json` returned ONLY
counts ("7 importers"), so an agent asking "what FILES import X" had to
shell/grep/SQL for the actual paths (+757% tokens in the A/B). Two root causes,
both fixed in cmd_deps.py:
  1. the default envelope stripped the list payloads entirely → now re-injects a
     BOUNDED preview (top-N paths) so the question is answered inline;
  2. the `--full` flag was DECLARED but never read (dead param) → now wired to
     `detail`, so `--full` returns the complete list as its help promises.

Coverage:
  - default `--json` carries `imported_by` as a list of {path, symbol_count}
  - default `--json` carries `imports` likewise
  - `--full` (previously a no-op for JSON) also returns the list
  - the preview is bounded (cap surfaced via summary.list_preview_capped_at)
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402


def _invoke(runner: CliRunner, cwd, *extra):
    """`roam --json deps <extra>` WITHOUT --detail (exercises the default path)."""
    from roam.cli import cli

    old = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, ["--json", "deps", *extra], catch_exceptions=False)
    finally:
        os.chdir(old)


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def deps_project(tmp_path, monkeypatch):
    """consumer.py imports helper.py → helper has 1 importer, consumer has 1 import."""
    proj = tmp_path / "deps_list_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "helper.py").write_text("def helper_fn():\n    return 'help'\n", encoding="utf-8")
    (src / "consumer.py").write_text(
        "from src.helper import helper_fn\n\ndef use_it():\n    return helper_fn()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def test_default_envelope_carries_importer_list(cli_runner, deps_project):
    """Default `roam deps --json` must include the importer FILE LIST (paths),
    not only the count — otherwise agents shell/SQL for it (nav A/B q2)."""
    result = _invoke(cli_runner, deps_project, "src/helper.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "deps"
    ib = data.get("imported_by")
    assert isinstance(ib, list) and ib, (
        f"default deps envelope must carry the imported_by LIST; got {ib!r}. keys={sorted(data.keys())!r}"
    )
    paths = {e["path"] for e in ib}
    assert any(p.endswith("consumer.py") for p in paths), (
        f"importer list must name the actual file (consumer.py); got {sorted(paths)!r}"
    )
    # Each entry is a {path, symbol_count} dict.
    for e in ib:
        assert set(e) >= {"path", "symbol_count"}


def test_default_envelope_carries_imports_list(cli_runner, deps_project):
    """The other direction: consumer.py's `imports` list names helper.py."""
    result = _invoke(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    im = data.get("imports")
    assert isinstance(im, list) and im, f"default envelope must carry imports LIST; got {im!r}"
    assert any(e["path"].endswith("helper.py") for e in im)


def test_full_flag_returns_list_not_dead(cli_runner, deps_project):
    """`--full` was a dead param (declared, never read). It must now return the
    complete list, matching its help text 'Show all results without truncation'."""
    from roam.cli import cli

    old = os.getcwd()
    try:
        os.chdir(str(deps_project))
        result = cli_runner.invoke(cli, ["--json", "deps", "src/helper.py", "--full"], catch_exceptions=False)
    finally:
        os.chdir(old)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    ib = data.get("imported_by")
    assert isinstance(ib, list) and any(e["path"].endswith("consumer.py") for e in ib), (
        f"--full must return the importer list (it was a no-op before); got {ib!r}"
    )


def test_bounded_list_preview_helper():
    """Unit-test the cap helper directly (no >cap-importer fixture needed)."""
    from roam.commands.cmd_deps import _bounded_list_preview

    items = [{"path": f"f{i}.py", "symbol_count": i, "id": i} for i in range(30)]

    # under cap → all returned, not capped
    prev, capped = _bounded_list_preview(items[:10], 25)
    assert len(prev) == 10 and capped is False

    # over cap → top-N returned, capped flag set
    prev, capped = _bounded_list_preview(items, 25)
    assert len(prev) == 25 and capped is True

    # exactly at cap → not capped
    prev, capped = _bounded_list_preview(items[:25], 25)
    assert len(prev) == 25 and capped is False

    # empty → empty, not capped
    assert _bounded_list_preview([], 25) == ([], False)

    # projection drops everything except path + symbol_count (no id/used_symbols leak)
    rich = [{"path": "x.py", "symbol_count": 3, "id": 99, "used_symbols": ["a", "b"]}]
    prev, _ = _bounded_list_preview(rich, 25)
    assert prev == [{"path": "x.py", "symbol_count": 3}]


def _find_any_answer_list(o) -> list[int]:
    """Recursively find non-trivial answer-lists (of dict/str) anywhere in the
    envelope — INCLUDING inside dicts (e.g. uses' `consumers.call`), which a
    top-level-only probe would miss."""
    found: list[int] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("_meta", "agent_contract"):
                    continue
                walk(v)
        elif isinstance(x, list) and x and isinstance(x[0], (dict, str)):
            found.append(len(x))

    walk(o)
    return found


@pytest.mark.parametrize(
    "verb,args",
    [
        ("deps", ["src/helper.py"]),  # "what files import X" → importer list
        ("uses", ["helper_fn"]),  # "who calls X" → caller list
    ],
)
def test_list_answer_commands_return_their_list(cli_runner, deps_project, verb, args):
    """REGRESSION GUARD generalizing the deps fix (2026-06-07): a command whose
    PRIMARY answer IS a list ("what files import X" / "who calls X") must carry
    that list in the DEFAULT --json envelope, not just counts. Counts-only forced
    agents to shell/SQL for the actual paths (codex nav A/B q2: +757% tokens
    re-deriving the importer list). This guards the whole class so a future
    strip_list_payloads tweak / envelope refactor can't silently drop a
    list-answer again. (audit 2026-06-07: deps/uses/impact/effects/closure/
    context/coupling all carry their list; only project-wide `dead` is counts-only
    by design.)"""
    from roam.cli import cli

    old = os.getcwd()
    try:
        os.chdir(str(deps_project))
        result = cli_runner.invoke(cli, ["--json", verb, *args], catch_exceptions=False)
    finally:
        os.chdir(old)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    lists = _find_any_answer_list(data)
    assert lists, (
        f"`roam {verb} {' '.join(args)}` default envelope carries NO answer-list — "
        f"counts-only regression (the q2 bug class). Lead the envelope with the list "
        f"the question asks for. keys={sorted(data.keys())!r}"
    )
