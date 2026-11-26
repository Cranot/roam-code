"""The pre-push hook refuses a v-tag or a version change without a release receipt.

``scripts/prepush_check.py --release`` writes a receipt for the exact commit it
passed on; ``scripts/release_receipt_gate.py`` (pre-push step A3) refuses a
push that tags or re-versions a commit without one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._helpers.repo_root import repo_root

_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def _load(name: str, filename: str):
    path = repo_root() / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


gate = _load("roam_release_receipt_gate_test", "release_receipt_gate.py")


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in _GIT_ENV.items():
        monkeypatch.setenv(key, value)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr.decode("utf-8", errors="replace"))
    return proc.stdout.decode("utf-8").strip()


def _write_pyproject(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8")


def _repo(tmp_path: Path, version: str = "1.0.0") -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write_pyproject(repo, version)
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", "init")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit_version(repo: Path, version: str) -> str:
    _write_pyproject(repo, version)
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", f"version {version}")
    return _git(repo, "rev-parse", "HEAD")


def _updates(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "updates"
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


ZERO = "0" * 40


def test_tag_push_without_receipt_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, head = _repo(tmp_path)
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {head} refs/tags/v1.0.0 {ZERO}")

    assert gate.check_updates(repo, updates) == 1
    err = capsys.readouterr().err
    assert f"BLOCKED: {head}" in err
    assert "no release receipt" in err


def test_tag_push_with_receipt_passes(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    gate.write_receipt(repo, head, workers=2)
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {head} refs/tags/v1.0.0 {ZERO}")

    assert gate.check_updates(repo, updates) == 0


def test_annotated_tag_is_peeled_to_its_commit(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release")
    tag_oid = _git(repo, "rev-parse", "refs/tags/v1.0.0")
    assert tag_oid != head
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {tag_oid} refs/tags/v1.0.0 {ZERO}")

    assert gate.check_updates(repo, updates) == 1
    gate.write_receipt(repo, head, workers=2)
    assert gate.check_updates(repo, updates) == 0


def test_every_update_is_checked_and_tags_peel_to_their_own_commit(tmp_path: Path, capsys) -> None:
    repo, tagged = _repo(tmp_path)
    _git(repo, "tag", "-a", "release-candidate", "-m", "rc", tagged)
    tag_oid = _git(repo, "rev-parse", "refs/tags/release-candidate")
    head = _commit_version(repo, "1.0.0-later")
    gate.write_receipt(repo, head, workers=2)
    updates = _updates(
        tmp_path,
        f"refs/heads/topic {head} refs/heads/topic {ZERO}",
        f"refs/tags/release-candidate {tag_oid} refs/tags/v1.0.0 {ZERO}",
    )

    assert gate.check_updates(repo, updates) == 1
    assert f"BLOCKED: {tagged}" in capsys.readouterr().err
    gate.write_receipt(repo, tagged, workers=2)
    assert gate.check_updates(repo, updates) == 0


def test_receipt_for_another_commit_does_not_count(tmp_path: Path) -> None:
    repo, first = _repo(tmp_path)
    gate.write_receipt(repo, first, workers=2)
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", "notes")
    second = _git(repo, "rev-parse", "HEAD")
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {second} refs/tags/v1.0.0 {ZERO}")

    assert gate.check_updates(repo, updates) == 1


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda r: {**r, "rc": 1}, "records rc 1"),
        (lambda r: {**r, "rc": True}, "records rc True"),
        (lambda r: {**r, "rc": "0"}, "records rc '0'"),
        (lambda r: {**r, "rc": False}, "records rc False"),
        (lambda r: {**r, "rc": 0.0}, "records rc 0.0"),
        (lambda r: {**r, "sha": "f" * 40}, "names commit"),
        (lambda r: {**r, "tier": "fast"}, "names tier"),
        (lambda r: {**r, "schema": "other"}, "has schema"),
        (lambda r: [r], "not a JSON object"),
    ],
)
def test_invalid_receipt_is_refused(tmp_path: Path, mutate, expected: str, capsys) -> None:
    repo, head = _repo(tmp_path)
    path = gate.write_receipt(repo, head, workers=2)
    path.write_text(json.dumps(mutate(json.loads(path.read_text(encoding="utf-8")))), encoding="utf-8")
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {head} refs/tags/v1.0.0 {ZERO}")

    assert gate.check_updates(repo, updates) == 1
    assert expected in capsys.readouterr().err


def test_unparseable_and_oversized_receipts_are_refused(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    path = gate.write_receipt(repo, head, workers=2)
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {head} refs/tags/v1.0.0 {ZERO}")

    valid = path.read_text(encoding="utf-8")
    path.write_text("{not json", encoding="utf-8")
    assert gate.check_updates(repo, updates) == 1
    # A valid receipt padded past the cap: only the size check refuses it.
    path.write_text(valid + " " * gate.RECEIPT_MAX_BYTES, encoding="utf-8")
    assert "larger than" in gate.receipt_problem(repo, head)
    assert gate.check_updates(repo, updates) == 1


def _commit_pyproject_text(repo: Path, text: str, message: str) -> str:
    (repo / "pyproject.toml").write_text(text, encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_unreadable_pyproject_blob_is_unknown_not_pass(tmp_path: Path, capsys) -> None:
    repo, old = _repo(tmp_path, "1.0.0")
    new = _commit_version(repo, "1.0.1")
    blob = _git(repo, "rev-parse", f"{new}:pyproject.toml")
    loose = repo / ".git" / "objects" / blob[:2] / blob[2:]
    loose.chmod(0o644)  # Git writes loose objects read-only; Windows refuses to unlink those
    loose.unlink()
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 2
    assert "could not be read" in capsys.readouterr().err


def test_unchanged_unparseable_pyproject_does_not_block_other_changes(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    old = _commit_pyproject_text(repo, "project = [\n", "broken project file")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", "notes")
    new = _git(repo, "rev-parse", "HEAD")
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 0


def test_project_that_is_not_a_table_reads_as_no_version(tmp_path: Path) -> None:
    repo, old = _repo(tmp_path, "1.0.0")
    new = _commit_pyproject_text(repo, "project = []\n", "project is a list")
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 1
    gate.write_receipt(repo, new, workers=2)
    assert gate.check_updates(repo, updates) == 0


def test_pyproject_replaced_by_a_directory_counts_as_a_version_change(tmp_path: Path) -> None:
    repo, old = _repo(tmp_path, "1.0.0")
    _git(repo, "rm", "-q", "pyproject.toml")
    (repo / "pyproject.toml").mkdir()
    (repo / "pyproject.toml" / "README").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "pyproject.toml/README")
    _git(repo, "commit", "-q", "-m", "directory")
    new = _git(repo, "rev-parse", "HEAD")
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 1
    gate.write_receipt(repo, new, workers=2)
    assert gate.check_updates(repo, updates) == 0


def test_pyproject_as_a_symbolic_link_is_unknown_not_pass(tmp_path: Path, capsys) -> None:
    repo, old = _repo(tmp_path, "1.0.0")
    link = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=b"config=0", capture_output=True, check=True
        )
        .stdout.decode("ascii")
        .strip()
    )
    _git(repo, "update-index", "--cacheinfo", f"120000,{link},pyproject.toml")
    _git(repo, "commit", "-q", "-m", "link")
    new = _git(repo, "rev-parse", "HEAD")
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 2
    assert "not a regular file" in capsys.readouterr().err


def test_receipt_writers_use_private_temporary_names(tmp_path: Path, monkeypatch) -> None:
    repo, head = _repo(tmp_path)
    real = gate.tempfile.NamedTemporaryFile
    names: list[str] = []

    def recording(*args, **kwargs):
        stream = real(*args, **kwargs)
        names.append(stream.name)
        return stream

    monkeypatch.setattr(gate.tempfile, "NamedTemporaryFile", recording)
    path = gate.write_receipt(repo, head, workers=2)
    gate.write_receipt(repo, head, workers=2)

    assert len(names) == 2 and names[0] != names[1]
    assert gate.receipt_problem(repo, head) is None
    assert [p.name for p in gate.receipts_dir(repo).iterdir()] == [path.name]


def test_a_failed_receipt_write_leaves_no_temporary_file(tmp_path: Path, prepush) -> None:
    repo, head = _repo(tmp_path)
    directory = gate.receipts_dir(repo)
    (directory / f"{head}.json").mkdir(parents=True)  # the final replace cannot succeed

    assert prepush._finish_release_receipt(repo, (head, True), workers=2) is None
    assert [p.name for p in directory.iterdir()] == [f"{head}.json"]
    assert "not a regular file" in gate.receipt_problem(repo, head)


def test_repository_variables_do_not_redirect_the_gate(tmp_path: Path, monkeypatch) -> None:
    repo, head = _repo(tmp_path / "a", "1.0.0")
    other, other_head = _repo(tmp_path / "b", "2.0.0")
    assert other_head != head
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    assert gate.head_sha(repo) == head
    assert gate.receipts_dir(repo) == (repo / ".git").resolve() / gate.RECEIPT_DIR_NAME


def test_main_outside_a_repository_is_unknown_not_pass(tmp_path: Path, monkeypatch, capsys) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(outside)

    updates = tmp_path / "updates"
    updates.write_bytes(b"")  # a valid, empty update stream: only discovery can fail

    assert gate.main(["--updates-file", str(updates)]) == 2
    assert "cannot find the repository" in capsys.readouterr().err


def test_non_string_versions_are_unknown_not_equal(tmp_path: Path, capsys) -> None:
    repo, _ = _repo(tmp_path)
    old = _commit_pyproject_text(repo, '[project]\nname = "demo"\nversion = 1\n', "integer version")
    new = _commit_pyproject_text(repo, '[project]\nname = "demo"\nversion = 2\n', "integer version bumped")
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 2
    assert "non-string version" in capsys.readouterr().err


def test_release_run_outside_the_git_top_level_writes_no_receipt(tmp_path: Path, prepush) -> None:
    repo, _ = _repo(tmp_path)
    inner = repo / "export"
    inner.mkdir()

    assert prepush._release_receipt_start(inner) is None


def test_branch_update_that_changes_the_version_needs_a_receipt(tmp_path: Path) -> None:
    repo, old = _repo(tmp_path, "1.0.0")
    new = _commit_version(repo, "1.0.1")
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 1
    gate.write_receipt(repo, new, workers=2)
    assert gate.check_updates(repo, updates) == 0


def test_branch_update_that_keeps_the_version_passes(tmp_path: Path) -> None:
    repo, old = _repo(tmp_path, "1.0.0")
    # pyproject.toml changes, its version does not.
    new = _commit_pyproject_text(
        repo, '# a comment\n[project]\nname = "demo"\nversion = "1.0.0"\n', "comment in pyproject"
    )
    updates = _updates(tmp_path, f"refs/heads/main {new} refs/heads/main {old}")

    assert gate.check_updates(repo, updates) == 0


def test_deletions_new_branches_and_other_refs_need_no_receipt(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    updates = _updates(
        tmp_path,
        f"(delete) {ZERO} refs/tags/v1.0.0 {head}",
        f"refs/heads/topic {head} refs/heads/topic {ZERO}",
        f"refs/tags/nightly {head} refs/tags/nightly {ZERO}",
        f"refs/notes/x {head} refs/notes/x {ZERO}",
    )

    assert gate.check_updates(repo, updates) == 0


def test_unknown_remote_commit_is_unknown_not_pass(tmp_path: Path, capsys) -> None:
    repo, head = _repo(tmp_path)
    updates = _updates(tmp_path, f"refs/heads/main {head} refs/heads/main {'a' * 40}")

    assert gate.check_updates(repo, updates) == 2
    assert "git fetch" in capsys.readouterr().err


def test_malformed_updates_are_unknown_not_pass(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)

    assert gate.check_updates(repo, _updates(tmp_path, "not an update")) == 2


def test_receipt_from_a_linked_worktree_serves_the_main_worktree(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked), head)
    gate.write_receipt(linked, head, workers=2)
    updates = _updates(tmp_path, f"refs/tags/v1.0.0 {head} refs/tags/v1.0.0 {ZERO}")

    assert gate.receipts_dir(linked) == gate.receipts_dir(repo)
    assert gate.check_updates(repo, updates) == 0


# --- the writer in prepush_check.py --release --------------------------------


@pytest.fixture()
def prepush():
    return _load("roam_prepush_check_receipt_test", "prepush_check.py")


def test_release_run_on_a_clean_unmoved_head_writes_the_receipt(tmp_path: Path, prepush) -> None:
    repo, head = _repo(tmp_path)
    start = prepush._release_receipt_start(repo)

    path = prepush._finish_release_receipt(repo, start, workers=2)

    assert path == gate.receipts_dir(repo) / f"{head}.json"
    assert gate.receipt_problem(repo, head) is None


def test_release_run_on_a_dirty_tree_writes_no_receipt(tmp_path: Path, prepush) -> None:
    repo, head = _repo(tmp_path)
    (repo / "stray.py").write_text("x = 1\n", encoding="utf-8")
    start = prepush._release_receipt_start(repo)

    assert prepush._finish_release_receipt(repo, start, workers=2) is None
    assert gate.receipt_problem(repo, head) is not None


def test_release_run_with_a_tracked_edit_at_the_start_writes_no_receipt(tmp_path: Path, prepush) -> None:
    repo, head = _repo(tmp_path)
    (repo / "pyproject.toml").write_text("# edited\n", encoding="utf-8")
    start = prepush._release_receipt_start(repo)

    assert prepush._finish_release_receipt(repo, start, workers=2) is None
    assert gate.receipt_problem(repo, head) is not None


def test_release_run_with_a_tracked_edit_during_the_run_writes_no_receipt(tmp_path: Path, prepush) -> None:
    repo, head = _repo(tmp_path)
    start = prepush._release_receipt_start(repo)
    (repo / "pyproject.toml").write_text("# edited\n", encoding="utf-8")

    assert prepush._finish_release_receipt(repo, start, workers=2) is None
    assert gate.receipt_problem(repo, head) is not None


def test_release_run_whose_head_moved_writes_no_receipt(tmp_path: Path, prepush) -> None:
    repo, head = _repo(tmp_path)
    start = prepush._release_receipt_start(repo)
    moved = _commit_version(repo, "1.0.1")

    assert prepush._finish_release_receipt(repo, start, workers=2) is None
    assert gate.receipt_problem(repo, head) is not None
    assert gate.receipt_problem(repo, moved) is not None


def _stub_release_run(monkeypatch, prepush, failing: str | None):
    """Run ``main --release`` with every gate stubbed; the gate whose argv contains ``failing`` fails."""
    root = repo_root()
    monkeypatch.setattr(prepush, "repo_root", lambda: root)
    monkeypatch.setattr(prepush.shutil, "disk_usage", lambda _path: SimpleNamespace(free=1 << 40))
    events: list[str] = []
    envs: list[dict] = []

    def run(argv, **kwargs):
        events.append("gate")
        envs.append(kwargs.get("env") or {})
        rc = 1 if failing is not None and failing in " ".join(map(str, argv)) else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")

    monkeypatch.setattr(prepush.subprocess, "run", run)
    monkeypatch.setattr(prepush, "_release_receipt_start", lambda r: events.append("start") or ("f" * 40, True))
    monkeypatch.setattr(prepush, "_finish_release_receipt", lambda r, s, workers: events.append("finish"))
    rc = prepush.main(["--release", "--workers", "1"])
    return rc, events, envs


@pytest.mark.parametrize("failing", [None, "tests/", "linkcheck"])
def test_release_tier_writes_the_receipt_only_after_every_gate_passed(
    monkeypatch: pytest.MonkeyPatch, prepush, failing: str | None
) -> None:
    rc, events, _ = _stub_release_run(monkeypatch, prepush, failing)

    gates = [i for i, e in enumerate(events) if e == "gate"]
    assert events.index("start") < gates[0]
    if failing is None:
        assert rc == 0
        assert events[-1] == "finish" and events.count("finish") == 1
        assert gates[-1] < events.index("finish")
    else:
        assert rc == 1
        assert "finish" not in events


def test_gate_subprocesses_drop_an_inherited_pytest_addopts(monkeypatch: pytest.MonkeyPatch, prepush) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")

    _, _, envs = _stub_release_run(monkeypatch, prepush, None)

    gate_envs = [env for env in envs if env]
    assert gate_envs, "no gate subprocess received an environment"
    assert all("PYTEST_ADDOPTS" not in env for env in gate_envs)


# --- the hook itself ---------------------------------------------------------

_STUB = """\
from __future__ import annotations
import sys
sys.exit(0)
"""


def _hooked_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo, head = _repo(tmp_path)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    hooks = repo / ".test-hooks"
    hooks.mkdir()
    shutil.copyfile(repo_root() / ".githooks" / "pre-push", hooks / "pre-push")
    (hooks / "pre-push").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in ("secret_scan.py", "scan_internal_language.py", "prepush_check.py"):
        (scripts / name).write_text(_STUB, encoding="utf-8")
    for name in ("release_receipt_gate.py", "prepush_refs.py"):
        shutil.copyfile(repo_root() / "scripts" / name, scripts / name)
    return repo, remote, head


def _push(repo: Path, *refspecs: str) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHON": sys.executable}
    return subprocess.run(
        ["git", "push", "-q", "origin", *refspecs], cwd=repo, capture_output=True, env=env, check=False
    )


def test_hook_refuses_a_tag_push_without_receipt_and_allows_it_with_one(tmp_path: Path) -> None:
    repo, remote, head = _hooked_repo(tmp_path)
    _git(repo, "tag", "v1.0.0")

    refused = _push(repo, "refs/tags/v1.0.0")
    assert refused.returncode != 0
    assert b"BLOCKED: release receipt" in refused.stderr
    assert _git(remote, "show-ref", "--tags", check=False) == ""

    gate.write_receipt(repo, head, workers=2)
    allowed = _push(repo, "refs/tags/v1.0.0")
    assert allowed.returncode == 0, allowed.stderr.decode("utf-8", errors="replace")
    assert head in _git(remote, "show-ref", "--tags")


def test_hook_allows_an_ordinary_branch_push_without_receipt(tmp_path: Path) -> None:
    repo, remote, head = _hooked_repo(tmp_path)

    pushed = _push(repo, "HEAD:refs/heads/main")
    assert pushed.returncode == 0, pushed.stderr.decode("utf-8", errors="replace")
    assert _git(remote, "rev-parse", "refs/heads/main") == head

    # pyproject.toml changes, its version does not: no receipt needed.
    advanced = _commit_pyproject_text(
        repo, '# a comment\n[project]\nname = "demo"\nversion = "1.0.0"\n', "comment in pyproject"
    )
    pushed = _push(repo, "HEAD:refs/heads/main")
    assert pushed.returncode == 0, pushed.stderr.decode("utf-8", errors="replace")
    assert _git(remote, "rev-parse", "refs/heads/main") == advanced


def test_hook_refuses_a_version_change_on_an_existing_branch(tmp_path: Path) -> None:
    repo, remote, head = _hooked_repo(tmp_path)
    assert _push(repo, "HEAD:refs/heads/main").returncode == 0
    bumped = _commit_version(repo, "1.0.1")

    refused = _push(repo, "HEAD:refs/heads/main")
    assert refused.returncode != 0
    assert b"changes the version" in refused.stderr
    assert _git(remote, "rev-parse", "refs/heads/main") == head

    gate.write_receipt(repo, bumped, workers=2)
    assert _push(repo, "HEAD:refs/heads/main").returncode == 0
    assert _git(remote, "rev-parse", "refs/heads/main") == bumped
