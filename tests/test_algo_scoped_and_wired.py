"""`roam algo --path` scoping + its two default consumers.

The algorithm catalog was a rich island: 30 impact-ranked findings with
Current/Better/Tip/Fix on this repo, consumed by NOTHING by default (verify
only behind opt-in --deep, compiler never). Three seams wire it in:

1. ``roam algo --path <p>`` — scoped runs (~1.8s vs 18s whole-project),
   summary disclosure (`scoped_paths` / `scope_file_count`), misses warned.
2. Compiler: perf-shaped freeform tasks ("optimize X", "fix the n+1 in Y")
   embed the scoped findings via ``_probe_algo_findings``.
3. Verify: ``--auto`` implies the advisory ``--deep`` patterns sweep
   (ROAM_VERIFY_NO_DEEP=1 opts out).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

NPLUS1 = """\
import sqlite3


def load_items(conn, items):
    out = []
    for item in items:
        row = conn.execute("SELECT * FROM t WHERE id=?", (item,)).fetchone()
        out.append(row)
    return out


def join_names(names):
    s = ""
    for n in names:
        s += n + ","
    return s
"""

CLEAN = "def add(a, b):\n    return a + b\n"


def _repo(tmp_path: Path) -> Path:
    proj = tmp_path / "algo_repo"
    (proj / "src").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "loader.py").write_text(NPLUS1)
    (proj / "src" / "calc.py").write_text(CLEAN)
    git_init(proj)
    index_in_process(proj)
    return proj


def test_path_scopes_findings_to_named_file(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    runner = CliRunner()
    res = invoke_cli(runner, ["--json", "algo", "--path", "src/loader.py"], cwd=proj)
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    locs = [f.get("location") or "" for f in d.get("findings") or []]
    assert locs, "scoped run on an anti-pattern file must find something"
    assert all("loader.py" in loc for loc in locs), locs
    assert d["summary"]["scoped_paths"] == ["src/loader.py"]
    assert d["summary"]["scope_file_count"] == 1


def test_path_directory_prefix_matches(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    runner = CliRunner()
    res = invoke_cli(runner, ["--json", "algo", "--path", "src"], cwd=proj)
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["summary"]["scope_file_count"] == 2


def test_path_miss_is_disclosed_not_silent(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    runner = CliRunner()
    res = invoke_cli(runner, ["--json", "algo", "--path", "no/such/file.py"], cwd=proj)
    assert res.exit_code == 0, res.output
    assert "matched no indexed files" in res.output


def test_unscoped_summary_has_null_scope_fields(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    runner = CliRunner()
    res = invoke_cli(runner, ["--json", "algo"], cwd=proj)
    d = json.loads(res.output)
    assert d["summary"]["scoped_paths"] is None
    assert d["summary"]["scope_file_count"] is None


class TestCompilerAlgoProbe:
    def test_perf_shape_triggers(self):
        from roam.plan.compiler import _ALGO_PERF_RE

        for t in (
            "optimize the loop in cmd_fan.py",
            "fix the n+1 query in loader.py",
            "make load_items faster",
            "find algorithmic improvements in src/",
        ):
            assert _ALGO_PERF_RE.search(t), t

    def test_non_perf_shape_does_not_trigger(self):
        from roam.plan.compiler import _ALGO_PERF_RE

        for t in ("who calls load_items", "explain src/loader.py", "what changed recently"):
            assert not _ALGO_PERF_RE.search(t), t

    def test_probe_embeds_scoped_findings(self, tmp_path, monkeypatch):
        proj = _repo(tmp_path)
        monkeypatch.chdir(proj)
        from roam.plan.compiler import _probe_algo_findings

        facts = _probe_algo_findings("fix the n+1 query in src/loader.py", ["src/loader.py"], str(proj))
        assert facts.get("algo_findings"), facts
        assert all("loader.py" in (f.get("location") or "") for f in facts["algo_findings"])
        assert "do not re-derive" in facts["algo_findings_definition"]

    def test_probe_silent_on_non_perf_task(self, tmp_path, monkeypatch):
        proj = _repo(tmp_path)
        monkeypatch.chdir(proj)
        from roam.plan.compiler import _probe_algo_findings

        assert _probe_algo_findings("who calls load_items", ["src/loader.py"], str(proj)) == {}


class TestAutoImpliesDeep:
    def test_auto_runs_patterns_sweep(self, tmp_path, monkeypatch):
        proj = _repo(tmp_path)
        monkeypatch.chdir(proj)
        monkeypatch.delenv("ROAM_VERIFY_NO_DEEP", raising=False)
        runner = CliRunner()
        res = invoke_cli(runner, ["--json", "verify", "src/loader.py", "--auto"], cwd=proj)
        assert res.exit_code == 0, res.output
        d = json.loads(res.output)
        assert "patterns" in (d.get("categories") or {}), list((d.get("categories") or {}).keys())

    def test_env_opt_out(self, tmp_path, monkeypatch):
        proj = _repo(tmp_path)
        monkeypatch.chdir(proj)
        monkeypatch.setenv("ROAM_VERIFY_NO_DEEP", "1")
        runner = CliRunner()
        res = invoke_cli(runner, ["--json", "verify", "src/loader.py", "--auto"], cwd=proj)
        assert res.exit_code == 0, res.output
        d = json.loads(res.output)
        assert "patterns" not in (d.get("categories") or {})
