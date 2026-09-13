"""Exercise committed-byte staging with real Git in isolated temporary repos.

Git is the boundary under test, so these subprocesses are intentional. They use
only disposable local repositories, no network, publisher, credentials or clock.
"""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from scripts import stage_site


def git(root, *args, data=None):
    return subprocess.run(["git", *args], cwd=root, input=data, capture_output=True, check=True).stdout


def commit(root):
    paths = [".gitignore"]
    if (root / "templates").exists():
        paths.append("templates")
    git(root, "add", "--", *paths)
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Website fixture",
    )
    return git(root, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "site fixture"
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "core.autocrlf", "false")
    (root / ".gitignore").write_text("internal/\n.roam/\n.env*\n", encoding="utf-8")
    site = root / stage_site.SITE
    (site / ".well-known").mkdir(parents=True)
    (site / "index.html").write_bytes(b"<!doctype html>\n<p>Public site</p>\n")
    (site / ".well-known/security.txt").write_bytes(b"Contact: mailto:test@example.invalid\n")
    (site / "_headers").write_bytes(b"/*\n  X-Content-Type-Options: nosniff\n")
    return root, commit(root)


def test_stage_exports_only_committed_bytes_and_keeps_manifest_outside_upload(repo):
    root, sha = repo
    site = root / stage_site.SITE
    (site / ".roam").mkdir()
    (site / ".roam/local.json").write_bytes(b"local-only")
    (site / ".env").write_bytes(b"fixture-only")
    public = stage_site.stage(root, sha)
    assert public.is_relative_to(root / "internal/site-deploy")
    paths = sorted(p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file())
    assert paths == [".well-known/security.txt", "_headers", "index.html"]
    manifest = json.loads((public.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["commit"] == sha and manifest["file_count"] == 3
    assert sorted(manifest["files"]) == paths
    for relative in paths:
        blob = git(root, "show", f"{sha}:{stage_site.SITE}/{relative}")
        assert (public / relative).read_bytes() == blob
        assert manifest["files"][relative] == {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    assert (site / ".roam/local.json").read_bytes() == b"local-only"
    assert not git(root, "status", "--porcelain=v1", "--untracked-files=all")


@pytest.mark.parametrize("state", ["dirty", "staged", "hidden-untracked", "wrong-head", "invalid-id", "nested-root"])
def test_stage_refuses_unqualified_source_before_writing(repo, state):
    root, sha = repo
    if state in {"dirty", "staged"}:
        (root / stage_site.SITE / "index.html").write_bytes(b"changed")
        if state == "staged":
            git(root, "add", "--", stage_site.SITE)
    elif state == "hidden-untracked":
        git(root, "config", "status.showUntrackedFiles", "no")
        (root / "unknown.txt").write_bytes(b"not reviewed")
    elif state == "wrong-head":
        sha = "0" * 40
    elif state == "invalid-id":
        sha = "HEAD"
    selected_root = root / "templates" if state == "nested-root" else root
    with pytest.raises(ValueError):
        stage_site.stage(selected_root, sha)
    assert not (root / "internal/site-deploy").exists()


@pytest.mark.parametrize("attribute", ["export-ignore", "export-subst"])
def test_stage_refuses_archive_attribute_omission_or_transformation(repo, attribute):
    root, _ = repo
    site = root / stage_site.SITE
    (site / "index.html").write_text("$Format:%H$\n", encoding="utf-8")
    (site / ".gitattributes").write_text(f"index.html {attribute}\n", encoding="utf-8")
    sha = commit(root)
    with pytest.raises(ValueError, match="omitted|transformed"):
        stage_site.stage(root, sha)
    assert not (root / "internal/site-deploy").exists()


@pytest.mark.parametrize("private", [".roam/cache.json", ".env", "internal/note.md"])
def test_stage_refuses_accidentally_committed_private_files(repo, private):
    root, _ = repo
    path = root / stage_site.SITE / private
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    git(root, "add", "--force", "--", path.relative_to(root).as_posix())
    sha = commit(root)
    with pytest.raises(ValueError, match="Private"):
        stage_site.stage(root, sha)


def test_stage_refuses_git_symlink_without_following_a_local_target(repo):
    root, _ = repo
    oid = git(root, "hash-object", "-w", "--stdin", data=b"../../private.txt").decode().strip()
    # A Git link entry works on Windows without OS symlink privileges.
    git(root, "update-index", "--add", "--cacheinfo", f"120000,{oid},{stage_site.SITE}/link")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Link fixture",
    )
    git(root, "config", "core.symlinks", "false")
    (root / stage_site.SITE / "link").write_bytes(b"../../private.txt")
    sha = git(root, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(ValueError, match="regular committed"):
        stage_site.stage(root, sha)


def test_stage_refuses_empty_site(repo):
    root, _ = repo
    git(root, "rm", "-r", "--", stage_site.SITE)
    sha = commit(root)
    with pytest.raises(ValueError, match="No committed website"):
        stage_site.stage(root, sha)


def test_source_change_during_archive_refuses_export(repo, monkeypatch):
    root, sha = repo
    original = stage_site.git

    def changing_git(selected, *args):
        result = original(selected, *args)
        if args[0] == "archive":
            (root / stage_site.SITE / "index.html").write_bytes(b"concurrent edit")
        return result

    monkeypatch.setattr(stage_site, "git", changing_git)
    with pytest.raises(ValueError, match="working changes"):
        stage_site.stage(root, sha)
    assert not (root / "internal/site-deploy").exists()
