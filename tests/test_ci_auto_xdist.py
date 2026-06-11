"""Lock the CI auto-parallelism plugin (roam.testing.ci_xdist).

The CI matrix lane runs the suite sequentially from the workflow file; the
3.10 leg outgrew its job timeout three times (20 -> 30 -> 45 min). The
``-p``-loaded plugin injects ``-n auto --dist loadgroup`` on CI runners.
These tests pin (a) the full guard matrix of the pure decision function,
(b) that pyproject actually loads the plugin, and (c) end-to-end worker
spawn in a subprocess with CI set.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from roam.testing.ci_xdist import xdist_args_to_inject

INJECT = ["-n", "auto", "--dist", "loadgroup"]
CI = {"CI": "true"}


class TestGuardMatrix:
    def test_injects_on_plain_ci_run(self):
        assert xdist_args_to_inject(["tests/", "-x", "-q"], CI, True) == INJECT

    def test_no_ci_env_no_injection(self):
        assert xdist_args_to_inject(["tests/"], {}, True) == []

    def test_xdist_missing_no_injection(self):
        assert xdist_args_to_inject(["tests/"], CI, False) == []

    def test_opt_out_env(self):
        assert xdist_args_to_inject(["tests/"], {**CI, "ROAM_AUTO_XDIST": "0"}, True) == []

    def test_explicit_n_flag_wins(self):
        assert xdist_args_to_inject(["-n", "0", "tests/"], CI, True) == []
        assert xdist_args_to_inject(["-n2", "tests/"], CI, True) == []
        assert xdist_args_to_inject(["--numprocesses=4", "tests/"], CI, True) == []
        assert xdist_args_to_inject(["--numprocesses", "auto"], CI, True) == []

    def test_explicit_dist_wins(self):
        assert xdist_args_to_inject(["--dist", "load", "tests/"], CI, True) == []
        assert xdist_args_to_inject(["--dist=each", "tests/"], CI, True) == []

    def test_disabled_plugin_wins(self):
        assert xdist_args_to_inject(["-p", "no:xdist", "tests/"], CI, True) == []
        assert xdist_args_to_inject(["-pno:xdist", "tests/"], CI, True) == []

    def test_unrelated_flags_do_not_block(self):
        # -m "not slow" / -x / paths must not look like -n or --dist.
        assert xdist_args_to_inject(["tests/", "-x", "-m", "not slow"], CI, True) == INJECT


def test_pyproject_loads_the_plugin():
    """The plugin only works if pyproject's addopts carries the -p flag."""
    from tests._helpers.repo_root import repo_root

    text = (repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    assert "-p roam.testing.ci_xdist" in text


def test_end_to_end_worker_spawn(tmp_path):
    """With CI set, a real pytest subprocess must run tests on xdist workers."""
    (tmp_path / "test_probe.py").write_text(
        textwrap.dedent(
            """
            import os
            def test_on_worker():
                assert os.environ.get("PYTEST_XDIST_WORKER", "").startswith("gw")
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["CI"] = "true"
    env.pop("ROAM_AUTO_XDIST", None)
    env.pop("PYTEST_XDIST_WORKER", None)  # we may BE on a worker; child must not inherit confusion
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_probe.py"), "-q", "-p", "roam.testing.ci_xdist"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
