"""Tests for ``roam test-hermeticity`` — non-hermetic test detector.

Covers the W1287 detector. The fixture creates one cleanly hermetic
test file and one obviously non-hermetic test file; the detector must
catch the non-hermetic one with the correct closed-enum ``kind`` and
must NOT flag the hermetic one.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Fixture: a tiny project with one hermetic + one non-hermetic test file.
# ---------------------------------------------------------------------------


def _make_hermeticity_project(tmp_path: Path) -> Path:
    """Create a tiny project: src/foo.py + tests/test_hermetic.py + tests/test_leaky.py."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    # A trivial source file so the indexer has something to find.
    src = proj / "src"
    src.mkdir()
    (src / "foo.py").write_text(
        textwrap.dedent(
            """
            def add(a, b):
                return a + b
            """
        ),
        encoding="utf-8",
    )

    # tests/ — both files live under tests/ so file_role classifies them
    # as ROLE_TEST.
    tests = proj / "tests"
    tests.mkdir()

    # Hermetic test: only stdlib functional code, no clock / network / env.
    (tests / "test_hermetic.py").write_text(
        textwrap.dedent(
            """
            from foo import add

            def test_add_pure():
                assert add(1, 2) == 3

            def test_add_zero():
                assert add(0, 0) == 0
            """
        ),
        encoding="utf-8",
    )

    # Non-hermetic test: hits the network, the clock, random,
    # the filesystem, the env, and shells out — one of each closed-enum
    # kind so we can assert all six light up.
    (tests / "test_leaky.py").write_text(
        textwrap.dedent(
            """
            import os
            import random
            import socket
            import subprocess
            import time
            import tempfile
            from datetime import datetime
            from pathlib import Path

            import requests

            def test_network_get():
                # Real HTTP call -> non-hermetic.
                r = requests.get("https://example.com")
                assert r.status_code == 200

            def test_socket_open():
                s = socket.socket()
                s.close()

            def test_time_now():
                t = time.time()
                d = datetime.now()
                assert t > 0
                assert d.year > 0

            def test_random_unseeded():
                assert random.randint(1, 10) > 0
                assert random.choice([1, 2, 3]) in (1, 2, 3)

            def test_filesystem_dependent():
                home = Path.home()
                temp = tempfile.gettempdir()
                cwd = os.getcwd()
                assert home and temp and cwd

            def test_env_reads():
                v = os.environ["PATH"]
                u = os.environ.get("USER", "")
                w = os.getenv("HOME", "")
                assert v or u or w or True

            def test_shells_out():
                subprocess.run(["echo", "hi"], check=True)
            """
        ),
        encoding="utf-8",
    )

    # git init + commit so discovery picks the files up via git ls-files.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )
    return proj


@pytest.fixture
def hermeticity_project(tmp_path: Path):
    """Indexed project with one hermetic and one non-hermetic test file."""
    proj = _make_hermeticity_project(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed:\n{result.output}"
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests on the pure scanner — no DB / index dependency.
# ---------------------------------------------------------------------------


class TestScanner:
    def test_classify_call_recognises_network_requests(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file

        f = tmp_path / "test_x.py"
        f.write_text("import requests\nrequests.get('https://e.com')\n", encoding="utf-8")
        findings = _scan_test_file(str(f))
        assert any(x["kind"] == "network" for x in findings)
        assert all(x["file"] == str(f) for x in findings)

    def test_classify_call_recognises_time_clock(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file

        f = tmp_path / "test_x.py"
        f.write_text(
            "import time\nfrom datetime import datetime\n"
            "def test_a():\n    return time.time(), datetime.now()\n",
            encoding="utf-8",
        )
        findings = _scan_test_file(str(f))
        kinds = {x["kind"] for x in findings}
        assert "time" in kinds

    def test_random_seed_suppresses_random_findings(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file

        f = tmp_path / "test_x.py"
        f.write_text(
            "import random\nrandom.seed(42)\n"
            "def test_a():\n    return random.randint(1, 10)\n",
            encoding="utf-8",
        )
        findings = _scan_test_file(str(f))
        assert not any(x["kind"] == "random" for x in findings), (
            "random.seed(...) in the module should suppress random findings"
        )

    def test_monkeypatch_setenv_suppresses_env(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file

        f = tmp_path / "test_x.py"
        f.write_text(
            "import os\n"
            "def test_a(monkeypatch):\n"
            "    monkeypatch.setenv('FOO', 'bar')\n"
            "    return os.environ['FOO']\n",
            encoding="utf-8",
        )
        findings = _scan_test_file(str(f))
        assert not any(x["kind"] == "env" for x in findings), (
            "monkeypatch.setenv in the module should suppress env findings"
        )

    def test_freezegun_import_suppresses_time(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file

        f = tmp_path / "test_x.py"
        f.write_text(
            "import time\nimport freezegun\n"
            "def test_a():\n    return time.time()\n",
            encoding="utf-8",
        )
        findings = _scan_test_file(str(f))
        assert not any(x["kind"] == "time" for x in findings)

    def test_hermetic_file_yields_zero_findings(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file

        f = tmp_path / "test_pure.py"
        f.write_text(
            "def test_a():\n    assert 1 + 1 == 2\n"
            "def test_b():\n    xs = [1, 2, 3]\n    assert sum(xs) == 6\n",
            encoding="utf-8",
        )
        findings = _scan_test_file(str(f))
        assert findings == []


# ---------------------------------------------------------------------------
# CLI-level integration: run `roam test-hermeticity` on the fixture project.
# ---------------------------------------------------------------------------


class TestCommand:
    def test_json_envelope_shape(self, hermeticity_project):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "test-hermeticity"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        assert env["command"] == "test-hermeticity"
        summary = env["summary"]
        # The fixture has exactly 2 test files (hermetic + leaky).
        assert summary["total"] == 2
        assert summary["non_hermetic"] == 1
        assert summary["hermetic"] == 1
        # 50% hermeticity rate on a 1-of-2 split.
        assert summary["hermeticity_rate"] == 50.0
        assert "verdict" in summary and isinstance(summary["verdict"], str)

    def test_findings_cover_every_closed_enum_kind(self, hermeticity_project):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "test-hermeticity"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        kinds_found = {f["kind"] for f in env["findings"]}
        expected = {"network", "time", "random", "filesystem", "env", "subprocess"}
        missing = expected - kinds_found
        assert not missing, (
            f"non-hermetic fixture should light up every closed-enum kind; "
            f"missing: {missing}; got={kinds_found}"
        )

    def test_findings_only_target_the_leaky_file(self, hermeticity_project):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "test-hermeticity"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        leaky_findings = [f for f in env["findings"] if "test_leaky" in f["file"]]
        hermetic_findings = [
            f for f in env["findings"] if "test_hermetic" in f["file"]
        ]
        assert leaky_findings, "expected findings in test_leaky.py"
        assert not hermetic_findings, (
            f"hermetic test file should be clean; got: {hermetic_findings}"
        )

    def test_kind_counts_match_findings(self, hermeticity_project):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "test-hermeticity"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        kc = env["kind_counts"]
        # Closed-enum keys present, all ints.
        for k in ("network", "time", "random", "filesystem", "env", "subprocess"):
            assert k in kc
            assert isinstance(kc[k], int)
        assert sum(kc.values()) == len(env["findings"])

    def test_persist_writes_into_findings_registry(self, hermeticity_project):
        runner = CliRunner()
        # First persist.
        result = runner.invoke(
            cli, ["test-hermeticity", "--persist"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        # Then query the registry via `roam findings count`.
        count_result = runner.invoke(
            cli, ["--json", "findings", "count"], catch_exceptions=False
        )
        assert count_result.exit_code == 0, count_result.output
        env = json.loads(count_result.stdout if hasattr(count_result, "stdout") else count_result.output)
        counts = env.get("counts", {})
        assert counts.get("test-hermeticity", 0) > 0, (
            f"--persist should populate the central findings registry; got counts={counts}"
        )

    def test_ci_mode_exits_5_on_findings(self, hermeticity_project):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["test-hermeticity", "--ci"], catch_exceptions=False
        )
        # `--ci` exits 5 when any non-hermetic test is detected.
        assert result.exit_code == 5, (
            f"--ci should exit 5 when findings exist; got {result.exit_code}\n{result.output}"
        )
