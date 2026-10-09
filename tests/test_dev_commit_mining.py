"""Exercise the local owner's environment boundary through the mining CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root


@pytest.mark.parametrize("configured", [False, True])
def test_commit_mining_owner_environment(tmp_path, configured):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    email = "fixture" + "@" + "example.org" if configured else "44682693+Cranot@users.noreply.github.com"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.pop("ROAM_COMMIT_MINING_OWNER_EMAILS", None)
    env.update(
        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL=email
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "fixture"],
        check=True,
        env=env,
    )
    if configured:
        env["ROAM_COMMIT_MINING_OWNER_EMAILS"] = " , " + email.upper() + " , "
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root() / "dev" / "commit_mining.py"),
            "--repo",
            str(repo),
            "--output-dir",
            str(output),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    corpus = json.loads((output / "corpus.json").read_text())
    assert corpus["commits_processed"] == 1
    assert corpus["owner_authored_commits_mined"] == int(configured)
    assert corpus["owner_email_identities"] == ([email.casefold()] if configured else [])
