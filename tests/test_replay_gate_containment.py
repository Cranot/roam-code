"""Security containment contracts for sibling-patch replay validation."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from roam.sibling_patch.replay_gate import run_replay_gate

_FIX = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def get(d, k):\n-    return d[k]\n+    return d.get(k)\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "replay-test",
        "GIT_AUTHOR_EMAIL": "replay-test@example.invalid",
        "GIT_COMMITTER_NAME": "replay-test",
        "GIT_COMMITTER_EMAIL": "replay-test@example.invalid",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
        env=env,
    )


def _init_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--allow-empty")


def _write_shell_script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def test_replay_uses_structured_argv_and_scrubs_ambient_environment(tmp_path, monkeypatch):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "mod.py").write_text("def get(d, k):\n    return d[k]\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "import os\n"
        "import pathlib\n"
        "import mod\n"
        "blocked = {\n"
        "    'AMBIENT_SECRET_SENTINEL', 'EXPLICIT_API_TOKEN', 'GITHUB_TOKEN',\n"
        "    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_TEMPLATE_DIR', 'PYTHONPATH', 'NODE_OPTIONS',\n"
        "}\n"
        "assert blocked.isdisjoint(os.environ)\n"
        "assert os.environ.get('SAFE_REPLAY_FLAG') == 'allowed'\n"
        "assert pathlib.Path(os.environ['HOME']).name == 'home'\n"
        "assert pathlib.Path(os.environ['TMP']).name == 'tmp'\n"
        "assert 'poisoned-home' not in os.environ['HOME']\n"
        "assert all(pathlib.Path(part).is_absolute() for part in os.environ.get('PATH', '').split(os.pathsep))\n"
        "assert mod.get({}, 'x') is None\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("AMBIENT_SECRET_SENTINEL", secret)
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "ambient-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "ambient-git-work-tree"))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "ambient-template"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "ambient-hooks"))
    monkeypatch.setenv("PYTHONPATH", str(repo))
    monkeypatch.setenv("NODE_OPTIONS", "--require=ambient-bootstrap.js")

    attestation = run_replay_gate(
        repo,
        _FIX,
        [sys.executable, "check.py"],
        timeout=30,
        env={
            "SAFE_REPLAY_FLAG": "allowed",
            "EXPLICIT_API_TOKEN": secret,
            "HOME": str(tmp_path / "poisoned-home"),
            "GIT_DIR": str(tmp_path / "explicit-git-dir"),
            "PYTHONPATH": str(repo),
            "NODE_OPTIONS": "--require=explicit-bootstrap.js",
        },
    )

    assert attestation.status == "green", attestation.detail
    serialized = json.dumps(attestation.to_dict())
    assert secret not in serialized
    assert "AMBIENT_SECRET_SENTINEL" not in serialized
    assert "EXPLICIT_API_TOKEN" not in serialized


def test_replay_rejects_shell_composition_in_string_command(tmp_path):
    marker = tmp_path / "shell-operator-ran"
    command_secret = "COMMAND-SECRET-SENTINEL-42"
    command = (
        f"{sys.executable} -c \"from pathlib import Path; Path(r'{marker}').touch()\" "
        f"--api-token {command_secret} && echo chained"
    )

    attestation = run_replay_gate(tmp_path, "patch", command)

    assert attestation.status == "error"
    assert "shell operators are unsupported" in attestation.detail
    assert command_secret not in json.dumps(attestation.to_dict())
    assert not marker.exists()


def test_replay_resolves_git_outside_the_repository(tmp_path, monkeypatch):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "mod.py").write_text("def get(d, k):\n    return d[k]\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "import mod\nassert mod.get({}, 'x') is None\n",
        encoding="utf-8",
    )
    _init_repo(repo)

    fake_bin = repo / "bin"
    fake_bin.mkdir()
    fake_marker = tmp_path / "repo-local-git-ran"
    fake_git = fake_bin / ("git.exe" if os.name == "nt" else "git")
    _write_shell_script(fake_git, f"printf invoked > {shlex.quote(fake_marker.as_posix())}\nexit 97")
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))

    attestation = run_replay_gate(repo, _FIX, [sys.executable, "check.py"], timeout=30)

    assert attestation.status == "green", attestation.detail
    assert not fake_marker.exists(), "replay executed a repository-local Git shim"


def test_replay_setup_does_not_execute_post_checkout_hook_or_smudge_filter(tmp_path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "mod.py").write_text("def get(d, k):\n    return d[k]\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "import mod\nassert mod.get({}, 'x') is None\n",
        encoding="utf-8",
    )
    (repo / ".gitattributes").write_text("* filter=replay_attack\n", encoding="utf-8")

    attacker_hooks = tmp_path / "attacker-hooks"
    attacker_hooks.mkdir()
    post_checkout = attacker_hooks / "post-checkout"
    smudge_filter = attacker_hooks / "smudge-filter"
    hook_marker = attacker_hooks / "post-checkout-ran"
    filter_marker = attacker_hooks / "smudge-filter-ran"
    _write_shell_script(
        post_checkout,
        'printf invoked > "$(dirname "$0")/post-checkout-ran"',
    )
    _write_shell_script(
        smudge_filter,
        'printf invoked > "$(dirname "$0")/smudge-filter-ran"\ncat',
    )

    _git(repo, "init")
    _git(repo, "config", "core.hooksPath", str(attacker_hooks))
    _git(repo, "config", "filter.replay_attack.smudge", shlex.quote(smudge_filter.as_posix()))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "config", "filter.replay_attack.required", "true")
    assert not hook_marker.exists()
    assert not filter_marker.exists()

    attestation = run_replay_gate(repo, _FIX, [sys.executable, "check.py"], timeout=30)

    assert attestation.status == "green", attestation.detail
    assert not hook_marker.exists(), "post-checkout executed while replay materialized a worktree"
    assert not filter_marker.exists(), "smudge filter executed while replay materialized a worktree"


def test_replay_pre_markers_cannot_carry_through_home_or_cache(tmp_path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    observation = tmp_path / "phase-runtimes.txt"
    (repo / "mod.py").write_text("def get(d, k):\n    return d[k]\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import mod\n"
        "home = Path(os.environ['HOME'])\n"
        "cache = Path(os.environ['XDG_CACHE_HOME'])\n"
        "home_marker = home / 'pre-state.marker'\n"
        "cache_marker = cache / 'pre-state.marker'\n"
        "try:\n"
        "    mod.get({}, 'x')\n"
        "except KeyError:\n"
        "    phase = 'pre'\n"
        "else:\n"
        "    phase = 'post'\n"
        "with Path(os.environ['OBSERVATION_FILE']).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(f'{phase}|{home}|{cache}\\n')\n"
        "if phase == 'pre':\n"
        "    assert not home_marker.exists() and not cache_marker.exists()\n"
        "    home_marker.write_text('seeded', encoding='utf-8')\n"
        "    cache_marker.write_text('seeded', encoding='utf-8')\n"
        "    raise SystemExit(23)\n"
        "if home_marker.exists() or cache_marker.exists():\n"
        "    raise SystemExit(31)\n",
        encoding="utf-8",
    )
    _init_repo(repo)

    attestation = run_replay_gate(
        repo,
        _FIX,
        [sys.executable, "check.py"],
        timeout=30,
        env={"OBSERVATION_FILE": str(observation)},
    )

    assert attestation.status == "green", attestation.detail
    records = [line.split("|", 2) for line in observation.read_text(encoding="utf-8").splitlines()]
    assert [record[0] for record in records] == ["pre", "post"]
    assert records[0][1] != records[1][1], "PRE and POST shared HOME"
    assert records[0][2] != records[1][2], "PRE and POST shared cache state"


def test_replay_pre_tracked_and_untracked_mutations_cannot_reach_post(tmp_path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "mod.py").write_text("def get(d, k):\n    return d[k]\n", encoding="utf-8")
    (repo / "state.txt").write_text("clean\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "from pathlib import Path\n"
        "import mod\n"
        "try:\n"
        "    mod.get({}, 'x')\n"
        "except KeyError:\n"
        "    Path('state.txt').write_text('poisoned\\n', encoding='utf-8')\n"
        "    Path('untracked.marker').write_text('seeded', encoding='utf-8')\n"
        "    raise SystemExit(41)\n"
        "if Path('state.txt').read_text(encoding='utf-8') != 'clean\\n':\n"
        "    raise SystemExit(42)\n"
        "if Path('untracked.marker').exists():\n"
        "    raise SystemExit(43)\n",
        encoding="utf-8",
    )
    _init_repo(repo)

    attestation = run_replay_gate(repo, _FIX, [sys.executable, "check.py"], timeout=30)

    assert attestation.status == "green", attestation.detail
    assert (repo / "state.txt").read_text(encoding="utf-8") == "clean\n"
    assert not (repo / "untracked.marker").exists()


def test_replay_timeout_terminates_descendant_process_tree(tmp_path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    marker = tmp_path / "descendant-escaped"
    child_code = (
        "import pathlib,sys,time; time.sleep(1.2); pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
    )
    (repo / "spawn_tree.py").write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"child_code = {child_code!r}\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', child_code, sys.argv[1]],\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        "    close_fds=True,\n"
        ")\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    _init_repo(repo)

    attestation = run_replay_gate(
        repo,
        "unused patch: validation times out before apply",
        [sys.executable, "spawn_tree.py", str(marker)],
        timeout=0.25,
    )

    assert attestation.status == "error"
    assert "timed out" in attestation.detail
    time.sleep(1.6)
    assert not marker.exists(), "a replay descendant survived the timeout"
    worktrees = _git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(worktrees) == 1
