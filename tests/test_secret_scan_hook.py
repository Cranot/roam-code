"""Tests for the client-side secret scan used by the pre-push hook."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root
from tests.conftest import git_commit, git_init

SCRIPT_PATH = repo_root() / "scripts" / "secret_scan.py"
SPEC = importlib.util.spec_from_file_location("secret_scan_test_module", SCRIPT_PATH)
assert SPEC is not None
secret_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(secret_scan)

INTERNAL_SCRIPT_PATH = repo_root() / "scripts" / "scan_internal_language.py"
INTERNAL_SPEC = importlib.util.spec_from_file_location("internal_language_scan_test_module", INTERNAL_SCRIPT_PATH)
assert INTERNAL_SPEC is not None
internal_scan = importlib.util.module_from_spec(INTERNAL_SPEC)
assert INTERNAL_SPEC.loader is not None
INTERNAL_SPEC.loader.exec_module(internal_scan)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=check)


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


def test_scan_commit_range_handles_utf8_blob_without_locale_decoding(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("def main():\n    return 0\n", encoding="utf-8")
    git_init(repo)

    secret = "AKIA" + "B" * 16
    app.write_text(f'# Unicode: ✓ and ∈\nvalue = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "add utf8 secret")

    findings = secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")

    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_scan_commit_range_handles_non_utf8_blob_without_skipping_secret(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("def main():\n    return 0\n", encoding="utf-8")
    git_init(repo)

    secret = ("AKIA" + "C" * 16).encode("ascii")
    app.write_bytes(b'# invalid byte: \xff\nvalue = "' + secret + b'"\n')
    git_commit(repo, "add non-utf8 secret")

    findings = secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")

    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_history_scanners_decode_bom_marked_utf16_blob(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.txt"
    app.write_text("clean\n", encoding="utf-8")
    git_init(repo)
    secret = "AKIA" + "W" * 16
    marker = "Pass " + "159" + " — " + "private UTF-16 note"
    app.write_bytes(f"{secret}\n{marker}\n".encode("utf-16"))
    git_commit(repo, "publish UTF-16 blob")
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    secret_findings = secret_scan._scan_commits(repo, [commit])
    internal_findings = internal_scan._collect_commit_blob_hits(str(repo), [commit])

    assert any(finding["pattern_name"] == "AWS Access Key" for finding in secret_findings)
    assert any(finding[1] == "Pass NN session marker" for finding in internal_findings)


def test_scan_commit_range_recurses_into_nested_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    nested = repo / "src" / "package"
    nested.mkdir(parents=True)
    app = nested / "app.py"
    app.write_text("def main():\n    return 0\n", encoding="utf-8")
    git_init(repo)

    secret = "AKIA" + "D" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "add nested secret")

    findings = secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")

    assert any(f["file"] == "src/package/app.py" for f in findings)
    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_uncommitted_path_allowlist_cannot_hide_committed_secret(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    generated = repo / "generated"
    generated.mkdir()
    app = generated / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)

    secret = "AKIA" + "J" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "add generated secret")
    (repo / ".secretsallow").write_text("generated/*\n", encoding="utf-8")

    findings = secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")

    assert any(f["file"] == "generated/app.py" for f in findings)


def test_committed_path_allowlist_skips_matching_committed_blob(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    generated = repo / "generated"
    generated.mkdir()
    app = generated / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)

    secret = "AKIA" + "K" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    (repo / ".secretsallow").write_text("generated/*\n", encoding="utf-8")
    git_commit(repo, "allow generated fixture")

    assert secret_scan.scan_commit_range(repo, "HEAD~1..HEAD") == []


def test_scan_commit_range_scans_merge_resolution_content(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'base'\n", encoding="utf-8")
    git_init(repo)
    base_branch = _git(repo, "branch", "--show-current").stdout.decode("ascii").strip()

    _git(repo, "checkout", "-b", "feature")
    app.write_text("value = 'feature'\n", encoding="utf-8")
    git_commit(repo, "feature change")
    _git(repo, "checkout", base_branch)
    app.write_text("value = 'main'\n", encoding="utf-8")
    git_commit(repo, "main change")
    merge = _git(repo, "merge", "feature", "--no-commit", "--no-ff", check=False)
    assert merge.returncode != 0, "test setup must create a merge conflict"

    secret = "AKIA" + "E" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "resolve merge")

    findings = secret_scan.scan_commit_range(repo, "HEAD^1..HEAD")

    assert any(f["file"] == "app.py" for f in findings)
    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_history_scanners_include_type_change_blobs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)

    secret = "AKIA" + "T" * 16
    marker = "Pass " + "987" + " — " + "private type-change note"
    payload = f"{secret}\n{marker}\n".encode()
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=payload,
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    _git(repo, "update-index", "--cacheinfo", "120000", blob, "app.py")
    _git(repo, "commit", "-m", "publish type change")
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    secret_findings = secret_scan._scan_commits(repo, [commit])
    internal_findings = internal_scan._collect_commit_blob_hits(str(repo), [commit])

    assert any(finding["pattern_name"] == "AWS Access Key" for finding in secret_findings)
    assert any(finding[0] == f"{commit[:7]}:app.py" for finding in internal_findings)
    assert any(finding[1] == "Pass NN session marker" for finding in internal_findings)


def test_commit_object_scans_ignore_git_log_output_encoding(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    secret = "AKIA" + "U" * 16
    marker = "Pass " + "852" + " — " + "private encoded-output note"
    app.write_text("value = 'changed'\n", encoding="utf-8")
    git_commit(repo, f"{secret}\n\n{marker}")
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    _git(repo, "config", "i18n.logOutputEncoding", "UTF-16LE")

    secret_findings = secret_scan._scan_commits(repo, [commit])
    internal_findings = internal_scan._collect_commit_message_hits_for_commits(str(repo), [commit])

    assert any(finding["pattern_name"] == "AWS Access Key" for finding in secret_findings)
    assert any(finding[1] == "Pass NN session marker" for finding in internal_findings)


def test_literal_backslash_path_cannot_alias_history_whitelist(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    git_init(repo)
    marker = "Pass " + "963" + " — " + "private backslash-path note"
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=(marker + "\n").encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    alias_path = "tests\\test_no_internal_language.py"
    tree = (
        subprocess.run(
            ["git", "mktree", "-z"],
            cwd=repo,
            input=f"100644 blob {blob}\t{alias_path}\0".encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    parent = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    commit = (
        _git(repo, "commit-tree", tree, "-p", parent, "-m", "publish backslash path").stdout.decode("ascii").strip()
    )

    findings = internal_scan._collect_commit_blob_hits(str(repo), [commit])

    assert any(finding[1] == "Pass NN session marker" for finding in findings)


def test_published_path_names_are_scanned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    secret = "AKIA" + "N" * 16
    internal_name = "union" + "-web"
    rel_path = f"{internal_name}-{secret}.txt"
    (repo / rel_path).write_text("clean\n", encoding="utf-8")
    git_init(repo)
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    secret_findings = secret_scan._scan_commits(repo, [commit])
    internal_findings = internal_scan._collect_commit_blob_hits(str(repo), [commit])

    assert any("published path name" in finding["file"] for finding in secret_findings)
    assert all(secret not in finding["file"] for finding in secret_findings)
    assert secret not in secret_scan._format_findings(secret_findings)
    assert any("published path name" in finding[0] for finding in internal_findings)
    assert any(finding[1] == "internal-project-codename" for finding in internal_findings)


def test_commit_author_metadata_is_scanned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    app.write_text("value = 'changed'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    secret = "AKIA" + "M" * 16
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "union" + "-web"
    env["GIT_AUTHOR_EMAIL"] = f"{secret}@example.com"
    subprocess.run(
        ["git", "commit", "-m", "clean metadata test"],
        cwd=repo,
        capture_output=True,
        env=env,
        check=True,
    )
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    secret_findings = secret_scan._scan_commits(repo, [commit])
    internal_findings = internal_scan._collect_commit_message_hits_for_commits(str(repo), [commit])

    assert any(finding["pattern_name"] == "AWS Access Key" for finding in secret_findings)
    assert any(finding[1] == "internal-project-codename" for finding in internal_findings)


def test_pre_push_new_ref_scans_all_reachable_history(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    secret = "AKIA" + "F" * 16
    (repo / "app.py").write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_init(repo)
    head = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(f"refs/heads/topic {head} refs/heads/topic {'0' * len(head)}\n", encoding="utf-8")

    findings = secret_scan.scan_pre_push_updates(repo, updates)

    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_pre_push_new_ref_does_not_trust_local_remote_tracking_refs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    historical_secret = "AKIA" + "H" * 16
    marker = "Pass " + "741" + " — " + "private tracked-ref note"
    (repo / "app.py").write_text(f'value = "{historical_secret}"\n# {marker}\n', encoding="utf-8")
    git_init(repo)
    published_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", published_oid)
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/tags/v1 {published_oid} refs/tags/v1 {'0' * len(published_oid)}\n",
        encoding="utf-8",
    )

    findings = secret_scan.scan_pre_push_updates(repo, updates)
    internal_findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(finding["commit"] == published_oid for finding in findings)
    assert any(finding["pattern_name"] == "AWS Access Key" for finding in findings)
    assert any(finding[0] == f"{published_oid[:7]}:app.py" for finding in internal_findings)
    assert any(finding[1] == "Pass NN session marker" for finding in internal_findings)


def test_new_ref_excludes_history_advertised_by_exact_destination(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    historical_secret = "AKIA" + "B" * 16
    marker = "Pass " + "753" + " — " + "already published note"
    (repo / "app.py").write_text(f"{historical_secret}\n{marker}\n", encoding="utf-8")
    git_init(repo)
    published_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    remote = tmp_path / "destination.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "push", "--no-verify", str(remote), "HEAD:refs/heads/main")
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/tags/v1 {published_oid} refs/tags/v1 {'0' * len(published_oid)}\n",
        encoding="utf-8",
    )

    secret_findings = secret_scan.scan_pre_push_updates(repo, updates, remote_url=str(remote))
    internal_findings = internal_scan._collect_pre_push_history_hits(
        str(repo),
        str(updates),
        remote_url=str(remote),
    )

    assert secret_findings == []
    assert internal_findings == []


@pytest.mark.parametrize(
    "ref_raw",
    [
        b"refs/heads/main\textra",
        b"refs/heads/main\x00evil",
        b"refs/heads/../evil",
        b"refs/heads/has space",
        b"refs/",
    ],
)
def test_malformed_destination_ref_advertisement_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref_raw: bytes,
) -> None:
    oid = b"a" * 40

    def fake_git_bytes(*args: object, **kwargs: object) -> bytes:
        return oid + b"\t" + ref_raw + b"\n"

    monkeypatch.setattr(secret_scan._prepush_refs, "_git_bytes", fake_git_bytes)

    with pytest.raises(secret_scan._prepush_refs.PrePushGitError, match="destination"):
        secret_scan._prepush_refs.authoritative_remote_commits(tmp_path, "destination")


def test_pre_push_new_ref_scans_unadvertised_commit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    published_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", published_oid)
    secret = "AKIA" + "I" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "add unpublished secret")
    local_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/heads/topic {local_oid} refs/heads/topic {'0' * len(local_oid)}\n",
        encoding="utf-8",
    )

    findings = secret_scan.scan_pre_push_updates(repo, updates)

    assert {finding["commit"] for finding in findings} == {local_oid}


def test_pre_push_existing_ref_scans_only_introduced_commits(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    remote_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    secret = "AKIA" + "G" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "add pushed secret")
    local_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n",
        encoding="utf-8",
    )

    findings = secret_scan.scan_pre_push_updates(repo, updates)

    assert {finding["commit"] for finding in findings} == {local_oid}


def test_pre_push_scans_credentials_in_commit_messages(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    remote_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    app.write_text("value = 'changed'\n", encoding="utf-8")
    secret = "AKIA" + "L" * 16
    git_commit(repo, f"publish credential {secret}")
    local_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n",
        encoding="utf-8",
    )

    findings = secret_scan.scan_pre_push_updates(repo, updates)

    assert any(f["file"] == f"{local_oid[:7]} (commit object)" for f in findings)
    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_pre_push_scans_annotated_tag_messages(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    commit_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", commit_oid)
    secret = "AKIA" + "M" * 16
    _git(repo, "tag", "-a", "v1", "-m", f"release credential {secret}")
    tag_oid = _git(repo, "rev-parse", "v1").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/tags/v1 {tag_oid} refs/tags/v1 {'0' * len(tag_oid)}\n",
        encoding="utf-8",
    )

    secret_findings = secret_scan.scan_pre_push_updates(repo, updates)

    assert any(f["file"] == f"{tag_oid[:7]} (annotated tag object)" for f in secret_findings)
    assert any(f["pattern_name"] == "AWS Access Key" for f in secret_findings)

    marker = "Pass " + "789" + " — " + "private tag note"
    _git(repo, "tag", "-a", "v2", "-m", marker)
    tag_oid_2 = _git(repo, "rev-parse", "v2").stdout.decode("ascii").strip()
    updates.write_text(
        f"refs/tags/v2 {tag_oid_2} refs/tags/v2 {'0' * len(tag_oid_2)}\n",
        encoding="utf-8",
    )
    internal_findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(f[0] == f"{tag_oid_2[:7]} (annotated tag object)" for f in internal_findings)
    assert any(f[1] == "Pass NN session marker" for f in internal_findings)


def test_pre_push_scans_annotated_tag_tagger_metadata(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    secret = "AKIA" + "P" * 16
    env = os.environ.copy()
    env["GIT_COMMITTER_NAME"] = "union" + "-web"
    env["GIT_COMMITTER_EMAIL"] = f"{secret}@example.com"
    subprocess.run(
        ["git", "tag", "-a", "v1", "-m", "clean tag message"],
        cwd=repo,
        capture_output=True,
        env=env,
        check=True,
    )
    tag_oid = _git(repo, "rev-parse", "v1").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/tags/v1 {tag_oid} refs/tags/v1 {'0' * len(tag_oid)}\n",
        encoding="utf-8",
    )

    secret_findings = secret_scan.scan_pre_push_updates(repo, updates)
    internal_findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(finding["file"] == f"{tag_oid[:7]} (annotated tag object)" for finding in secret_findings)
    assert any(finding["pattern_name"] == "AWS Access Key" for finding in secret_findings)
    assert any(finding[1] == "internal-project-codename" for finding in internal_findings)


def test_pre_push_scans_published_ref_names(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    secret = "AKIA" + "V" * 16
    remote_ref = f"refs/heads/{'union' + '-web'}-{secret}#secretsallow"
    updates = tmp_path / "updates"
    updates.write_text(
        f"HEAD {commit} {remote_ref} {'0' * len(commit)}\n",
        encoding="utf-8",
    )

    secret_findings = secret_scan.scan_pre_push_updates(repo, updates)
    internal_findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any("published ref name" in finding["file"] for finding in secret_findings)
    assert all(secret not in finding["file"] for finding in secret_findings)
    assert secret not in secret_scan._format_findings(secret_findings)
    assert any("published ref name" in finding[0] for finding in internal_findings)
    assert any(finding[1] == "internal-project-codename" for finding in internal_findings)


def test_pre_push_scans_direct_blob_and_tree_refs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "base.txt").write_text("clean\n", encoding="utf-8")
    git_init(repo)
    secret = "AKIA" + "N" * 16
    # Write the direct blob through subprocess input so it is not present as a
    # tracked fixture.
    blob_oid = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=f'value = "{secret}"\n'.encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/tags/blob {blob_oid} refs/tags/blob {'0' * len(blob_oid)}\n",
        encoding="utf-8",
    )

    blob_findings = secret_scan.scan_pre_push_updates(repo, updates)

    assert any(f["file"] == f"{blob_oid[:7]} (direct pushed blob)" for f in blob_findings)

    internal_dir = repo / "internal"
    internal_dir.mkdir()
    marker = "Pass " + "321" + " — " + "private tree note"
    (internal_dir / "private.toml").write_text(f'note = "{marker}"\n', encoding="utf-8")
    _git(repo, "add", "internal/private.toml")
    tree_oid = _git(repo, "write-tree").stdout.decode("ascii").strip()
    updates.write_text(
        f"refs/tags/tree {tree_oid} refs/tags/tree {'0' * len(tree_oid)}\n",
        encoding="utf-8",
    )

    tree_findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(f[0] == "internal/private.toml" for f in tree_findings)
    assert any(f[1] == "Pass NN session marker" for f in tree_findings)


def test_metadata_names_cannot_use_content_suppression_marker(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    secret = "AKIA" + "Y" * 16
    rel_path = f"{secret}#secretsallow"
    (repo / rel_path).write_text("clean\n", encoding="utf-8")
    git_init(repo)
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    findings = secret_scan._scan_commits(repo, [commit])

    assert any("published path name" in finding["file"] for finding in findings)
    assert secret not in secret_scan._format_findings(findings)


def test_direct_tree_gitlink_path_names_are_scanned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    secret = "AKIA" + "Z" * 16
    internal_name = "union" + "-web"
    path = f"{internal_name}-{secret}"
    tree = (
        subprocess.run(
            ["git", "mktree", "-z"],
            cwd=repo,
            input=f"160000 commit {commit}\t{path}\0".encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/tags/tree {tree} refs/tags/tree {'0' * len(tree)}\n",
        encoding="utf-8",
    )

    secret_findings = secret_scan.scan_pre_push_updates(repo, updates)
    internal_findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any("published path name" in finding["file"] for finding in secret_findings)
    assert any(finding[1] == "internal-project-codename" for finding in internal_findings)


def test_pre_push_deletion_has_no_commits_to_scan(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    remote_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"(delete) {'0' * len(remote_oid)} refs/heads/topic {remote_oid}\n",
        encoding="utf-8",
    )

    assert secret_scan.scan_pre_push_updates(repo, updates) == []


def test_pre_push_malformed_update_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    updates = tmp_path / "updates"
    updates.write_text("not a valid update\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="invalid pre-push updates"):
        secret_scan.scan_pre_push_updates(repo, updates)


def test_hook_uses_captured_authoritative_ref_updates() -> None:
    hook = (repo_root() / ".githooks" / "pre-push").read_text(encoding="utf-8")

    assert 'cat >"$UPDATES_FILE"' in hook
    assert hook.count('--pre-push-updates "$UPDATES_FILE"') == 2
    assert "@{upstream}" not in hook


def test_first_push_hook_forwards_git_ref_update_to_both_scanners(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    head = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))

    hooks = repo / ".test-hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text((repo_root() / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))

    scripts = repo / "scripts"
    scripts.mkdir()
    log = tmp_path / "hook-calls.jsonl"
    stub = """\
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

record = {"script": Path(sys.argv[0]).name, "args": sys.argv[1:]}
if "--pre-push-updates" in sys.argv:
    update_path = Path(sys.argv[sys.argv.index("--pre-push-updates") + 1])
    record["updates"] = update_path.read_text(encoding="utf-8")
with Path(os.environ["HOOK_CALL_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\\n")
"""
    for name in ("scan_internal_language.py", "secret_scan.py", "prepush_check.py"):
        (scripts / name).write_text(stub, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    env["HOOK_CALL_LOG"] = str(log)
    pushed = subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/topic"],
        cwd=repo,
        capture_output=True,
        env=env,
        check=False,
    )

    assert pushed.returncode == 0, pushed.stderr.decode("utf-8", errors="replace")
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    update_records = [record for record in records if "--pre-push-updates" in record["args"]]
    assert [record["script"] for record in update_records] == [
        "secret_scan.py",
        "scan_internal_language.py",
    ]
    assert len({record["updates"] for record in update_records}) == 1
    assert all(record["args"][-2:] == ["--remote-url", str(remote)] for record in update_records)
    local_ref, local_oid, remote_ref, remote_oid = update_records[0]["updates"].split()
    branch = _git(repo, "branch", "--show-current").stdout.decode("ascii").strip()
    assert local_ref in {"HEAD", f"refs/heads/{branch}"}
    assert local_oid == head
    assert remote_ref == "refs/heads/topic"
    assert remote_oid == "0" * len(head)
    assert records[-1] == {"args": ["--fast"], "script": "prepush_check.py"}


def test_hook_rejects_non_python_override(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))

    hooks = repo / ".test-hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text((repo_root() / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    _git(repo, "remote", "add", "origin", str(remote))

    env = os.environ.copy()
    env["PYTHON"] = "echo"
    pushed = subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/topic"],
        cwd=repo,
        capture_output=True,
        env=env,
        check=False,
    )

    assert pushed.returncode != 0
    assert b"does not resolve to a working Python interpreter" in pushed.stderr
    assert _git(remote, "show-ref", "--verify", "refs/heads/topic", check=False).returncode != 0


def test_internal_language_update_scan_reads_nested_committed_blobs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    nested = repo / "src" / "package"
    nested.mkdir(parents=True)
    app = nested / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    remote_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    marker = "Pass " + "123" + " — " + "private note"
    app.write_text(f"# {marker}\nvalue = 'clean'\n", encoding="utf-8")
    git_commit(repo, "add nested note")
    local_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n",
        encoding="utf-8",
    )

    findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(finding[0] == f"{local_oid[:7]}:src/package/app.py" for finding in findings)
    assert any(finding[1] == "Pass NN session marker" for finding in findings)


@pytest.mark.parametrize("rel_path", ["pyproject.toml", "internal/private.md", "notes"])
def test_internal_language_history_scans_all_publication_paths(tmp_path: Path, rel_path: str) -> None:
    repo = _make_repo(tmp_path)
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("clean\n", encoding="utf-8")
    git_init(repo)
    remote_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    marker = "Pass " + "654" + " — " + "private history note"
    target.write_text(marker + "\n", encoding="utf-8")
    git_commit(repo, "change publication path")
    local_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n",
        encoding="utf-8",
    )

    findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(finding[0] == f"{local_oid[:7]}:{rel_path}" for finding in findings)


def test_git_replacement_objects_cannot_hide_committed_content(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'base'\n", encoding="utf-8")
    git_init(repo)
    secret = "AKIA" + "P" * 16
    app.write_text(f'value = "{secret}"\n', encoding="utf-8")
    git_commit(repo, "secret commit")
    secret_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    app.write_text("value = 'clean replacement'\n", encoding="utf-8")
    git_commit(repo, "clean commit")
    clean_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    _git(repo, "replace", secret_oid, clean_oid)

    findings = secret_scan._scan_commits(repo, [secret_oid])

    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


@pytest.mark.parametrize(
    "line",
    [
        'secretsAllowedKey = "{}"',
        'example_client_live_key = "{}"',
        'fallback = os.environ.get("KEY", "{}")',
        'value = "{}"  # secretsallow later',
    ],
)
def test_line_context_cannot_exempt_a_real_matched_credential(line: str) -> None:
    secret = "AKIA" + "Q" * 16

    findings = secret_scan._scan_text("app.py", line.format(secret), commit="a" * 40)

    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


def test_placeholder_match_cannot_hide_later_real_credential() -> None:
    real_secret = "AKIA" + "R" * 16
    line = f'x="AKIAIOSFODNN7EXAMPLE"; y="{real_secret}"'

    findings = secret_scan._scan_text("app.py", line, commit="a" * 40)

    aws_findings = [finding for finding in findings if finding["pattern_name"] == "AWS Access Key"]
    assert len(aws_findings) == 1
    assert aws_findings[0]["matched_text"] == "AKIA...RRRR"


@pytest.mark.parametrize(
    "placeholder",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "AKIAIOSFODNN7TESTDAT",
        "AKIAIOSFODNN7DOCEXAM",
    ],
)
def test_exact_aws_placeholders_are_not_findings(placeholder: str) -> None:
    findings = secret_scan._scan_text(
        "fixture.py",
        f'aws_key = "{placeholder}"',
        commit="a" * 40,
    )

    assert findings == []


@pytest.mark.parametrize("fragment", ["XXX", "DUMMY", "SAMPLE"])
def test_placeholder_substring_cannot_exempt_credential(fragment: str) -> None:
    body = "AA" + fragment + "R" * (14 - len(fragment))
    secret = "AKIA" + body

    findings = secret_scan._scan_text("app.py", secret, commit="a" * 40)

    assert any(finding["pattern_name"] == "AWS Access Key" for finding in findings)


def test_internal_language_update_scan_reads_commit_messages(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("value = 'clean'\n", encoding="utf-8")
    git_init(repo)
    remote_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    app.write_text("value = 'changed'\n", encoding="utf-8")
    marker = "Pass " + "456" + " — " + "private note"
    git_commit(repo, marker)
    local_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    updates = tmp_path / "updates"
    updates.write_text(
        f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n",
        encoding="utf-8",
    )

    findings = internal_scan._collect_pre_push_history_hits(str(repo), str(updates))

    assert any(finding[0] == f"{local_oid[:7]} (commit object)" for finding in findings)
    assert any(finding[1] == "Pass NN session marker" for finding in findings)


def test_scan_commit_range_fails_closed_when_git_show_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("def main():\n    return 0\n", encoding="utf-8")
    git_init(repo)
    app.write_text("def main():\n    return 1\n", encoding="utf-8")
    git_commit(repo, "change app")

    real_run = secret_scan.subprocess.run

    def fail_git_show(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if args[:3] == ["git", "--no-replace-objects", "show"] and "-s" not in args:
            return subprocess.CompletedProcess(args, 23, stdout=b"", stderr=b"simulated read failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(secret_scan.subprocess, "run", fail_git_show)

    with pytest.raises(SystemExit, match=r"read blob .* failed \(git exit 23\)"):
        secret_scan.scan_commit_range(repo, "HEAD~1..HEAD")


def test_git_capture_is_binary_and_locale_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=b"payload", stderr=b"")

    monkeypatch.setattr(secret_scan.subprocess, "run", fake_run)

    assert secret_scan._git_bytes(tmp_path, ["status"], operation="test git") == b"payload"
    assert calls == [{"cwd": tmp_path, "capture_output": True, "check": False}]


# ---------------------------------------------------------------------------
# Value-shape precision: name-vs-value discrimination (ported from
# compile-code's scripts/secret_scan.py, commit ee7896e)
# ---------------------------------------------------------------------------


def test_screaming_snake_identifier_value_is_not_flagged() -> None:
    """A bare SCREAMING_SNAKE_CASE value is the NAME of a secret (an env var
    or a secret to read at runtime), not a credential value."""
    findings = secret_scan._scan_text(
        "app.py",
        "SECRET = 'RELEASE_GUARD_READ_TOKEN'",
        commit="a" * 40,
    )
    assert findings == []


def test_screaming_snake_rule_does_not_swallow_a_real_looking_secret() -> None:
    """The identifier-value discrimination must be narrow: a value with
    mixed case and digits (i.e. actual entropy, not just an identifier
    shape) still has to fire Generic Secret Assignment."""
    findings = secret_scan._scan_text(
        "app.py",
        "API_SECRET = 'aB3xQ7zRt9LmZp2w'",
        commit="a" * 40,
    )
    assert any(f["pattern_name"] == "Generic Secret Assignment" for f in findings)


def test_screaming_snake_rule_does_not_weaken_vendor_patterns() -> None:
    """AWS Access Key IDs are themselves canonically all-uppercase-and-
    digits -- the identifier-value discrimination is scoped to the generic
    assignment patterns only and must not exempt a vendor-shaped credential
    just because it happens to look like a constant name."""
    secret = "AKIA" + "Q" * 16
    findings = secret_scan._scan_text("app.py", f"AWS_KEY = '{secret}'", commit="a" * 40)
    assert any(f["pattern_name"] == "AWS Access Key" for f in findings)


@pytest.mark.parametrize("wrapped", ["{secret}", "${secret}", "%(secret)s"])
def test_unresolved_template_placeholder_value_is_not_flagged(wrapped: str) -> None:
    """An un-interpolated f-string/format/shell placeholder is template
    syntax, not a literal value -- e.g. a parametrized test's own
    assignment-fixture line, which must not read as the credential it
    is a template FOR."""
    findings = secret_scan._scan_text("app.py", f"API_KEY = '{wrapped}'", commit="a" * 40)
    assert findings == []


def test_own_test_corpus_predicate_is_one_file_not_a_directory() -> None:
    """A path allowlist, not a directory rule: only these exact files are
    exempt as the scanner's own fixture corpus, so the exemption cannot
    quietly grow into "tests/ is exempt" (which would reopen the coverage
    gap this scanner exists to close)."""
    assert secret_scan._is_own_test_corpus("tests/test_secrets_v2.py")
    assert secret_scan._is_own_test_corpus("tests\\test_secrets_v2.py")
    assert secret_scan._is_own_test_corpus("tests/test_secrets_ai_provider_keys.py")
    assert not secret_scan._is_own_test_corpus("tests/test_secrets_v2_other.py")
    assert not secret_scan._is_own_test_corpus("tests/test_secret_scan_hook.py")
    assert not secret_scan._is_own_test_corpus("scripts/secret_scan.py")


def test_legacy_fixture_exemption_predicate_is_one_file_not_a_directory() -> None:
    """Same one-file-not-a-directory discipline for the (separate, own-
    reasoned) legacy exemption bucket: these are ordinary tests, not the
    scanner's own corpus, so they must not be foldable into
    ``_OWN_TEST_CORPUS_FILES`` and must not widen to their directory."""
    assert secret_scan._is_legacy_fixture_exemption("tests/test_hooks_claude_setup.py")
    assert secret_scan._is_legacy_fixture_exemption("tests/test_evidence_pr_replay.py")
    assert not secret_scan._is_legacy_fixture_exemption("tests/test_hooks_claude_setup_other.py")
    assert not secret_scan._is_own_test_corpus("tests/test_hooks_claude_setup.py")
    assert not secret_scan._is_legacy_fixture_exemption("tests/test_secrets_v2.py")


def test_own_test_corpus_file_is_exempt_across_its_whole_history(tmp_path: Path) -> None:
    """Reproduces the real shape this fix closes: a commit adds a
    credential-shaped fixture line to the scanner's own test corpus
    unmarked, and a LATER commit adds a ``# secretsallow`` marker at the
    tip. Because the range scan reads every commit's own full blob, the
    tip-side marker does not retroactively clean the earlier commit --
    only the path exemption (``_OWN_TEST_CORPUS_FILES``) does that."""
    repo = _make_repo(tmp_path)
    fixture = repo / "tests" / "test_secrets_v2.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("# corpus\n", encoding="utf-8")
    git_init(repo)

    secret = "AKIA" + "Z" * 16
    fixture.write_text(f"TEST_KEY = '{secret}'\n", encoding="utf-8")
    git_commit(repo, "add fixture corpus (unmarked)")
    unmarked_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    fixture.write_text(f"TEST_KEY = '{secret}'  # secretsallow\n", encoding="utf-8")
    git_commit(repo, "mark the corpus fixture")

    findings = secret_scan.scan_commit_range(repo, f"{unmarked_oid}~1..HEAD")

    assert findings == []


# ---------------------------------------------------------------------------
# Drift guard: first-party source must stay clean under roam's own gate
# ---------------------------------------------------------------------------


def _first_party_python_sources() -> list[Path]:
    """Git-tracked ``.py`` files under ``src/`` and ``scripts/``."""
    root = repo_root()
    listed = subprocess.run(
        ["git", "ls-files", "src", "scripts"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [root / rel for rel in listed if rel.endswith(".py")]


def test_first_party_source_has_no_credential_shaped_literals() -> None:
    """``roam secrets --fail-on-found`` must stay green on our own source.

    CI went red on 2026-07-23 because ``scripts/secret_scan.py`` declared two
    AWS-key-shaped placeholder constants as bare literals and roam's own
    detector flagged its allowlist -- the scanner reading its own training data.

    ``src/`` and ``scripts/`` are deliberately NOT covered by cmd_secrets'
    test/fixture/docs path suppression, and ``.roam-suppressions.yml`` is read
    by cmd_triage/cmd_verify/SARIF but NOT by cmd_secrets -- so there is no
    suppression escape hatch for first-party source. When a credential-shaped
    literal is genuinely required there, use the concatenation idiom
    (``"AKIA" + "..."``) already used throughout this module.
    """
    from roam.commands.cmd_secrets import _is_test_or_doc_path, scan_file

    root = repo_root()
    findings: list[dict] = []
    for path in _first_party_python_sources():
        rel = path.relative_to(root).as_posix()
        if _is_test_or_doc_path(rel):
            continue
        findings.extend(scan_file(str(path)))

    assert not findings, "credential-shaped literals in first-party source:\n" + json.dumps(
        [{"file": f["file"], "line": f["line"], "pattern": f["pattern_name"]} for f in findings],
        indent=2,
    )
