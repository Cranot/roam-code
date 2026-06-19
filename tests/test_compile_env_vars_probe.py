"""Regression coverage for the compile env-var probe path boundary."""

from __future__ import annotations

from roam.plan.compiler import _probe_env_vars_for_task


def test_env_vars_probe_reads_repo_local_file(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    app = src / "app.py"
    app.write_text('TOKEN = os.environ["APP_TOKEN"]\nDEBUG = os.getenv("APP_DEBUG")\n', encoding="utf-8")

    out = _probe_env_vars_for_task(
        "list environment variables used by src/app.py",
        ["src/app.py"],
        str(tmp_path),
    )

    assert out is not None
    names = {item["name"] for item in out["env_vars_used"]["vars"]}
    assert names == {"APP_TOKEN", "APP_DEBUG"}


def test_env_vars_probe_rejects_parent_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text('SECRET = os.environ["OUTSIDE_SECRET"]\n', encoding="utf-8")

    out = _probe_env_vars_for_task(
        "list environment variables used by ../outside.py",
        ["../outside.py"],
        str(repo),
    )

    assert out is None


def test_env_vars_probe_rejects_absolute_path_outside_cwd(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text('SECRET = os.getenv("OUTSIDE_SECRET")\n', encoding="utf-8")

    out = _probe_env_vars_for_task(
        f"list environment variables used by {outside}",
        [str(outside)],
        str(repo),
    )

    assert out is None
