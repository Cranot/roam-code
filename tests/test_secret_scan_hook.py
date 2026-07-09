"""Tests for the client-side secret scan used by the pre-push hook."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tests._helpers.repo_root import repo_root
from tests.conftest import git_commit, git_init

SCRIPT_PATH = repo_root() / "scripts" / "secret_scan.py"
SPEC = importlib.util.spec_from_file_location("secret_scan_test_module", SCRIPT_PATH)
assert SPEC is not None
secret_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(secret_scan)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_scan_commit_range_finds_secret(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("def main():\n    return 0\n")
    git_init(repo)

    secret = "AKIA" + "A" * 16
    app.write_text(f'value = "{secret}"\n')
    git_commit(repo, "add secret")

    findings = secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")

    assert findings, "expected the pushed commit to contain a secret finding"
    assert any(f["file"] == "app.py" for f in findings)
    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_scan_commit_range_clean_file_has_no_findings(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("def main():\n    return 0\n")
    git_init(repo)

    findings = secret_scan.scan_commit_range(repo, "HEAD")

    assert findings == []


def test_scan_commit_range_marker_comment_skips_allowlisted_line(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("def main():\n    return 0\n")
    git_init(repo)

    secret = "AKIA" + "A" * 16
    app.write_text(f'value = "{secret}"  # secretsallow\n')
    git_commit(repo, "allowlisted secret")

    findings = secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")

    assert findings == []
