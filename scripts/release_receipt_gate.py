"""Refuse a release push whose exact commit has not passed the release tier.

``python scripts/prepush_check.py --release`` writes a receipt for HEAD when
every gate passed on a clean tree whose HEAD did not move during the run. The
receipt lives in the repository's common Git directory, so every linked
worktree of the repository sees it::

    <git-common-dir>/roam-release-receipts/<commit sha>.json

The pre-push hook hands this script the ref updates Git is about to publish
(``--updates-file``). A push needs a receipt for a commit when it

* creates or moves a ``refs/tags/v*`` tag (the tag's commit, annotated tags
  peeled), because ``.github/workflows/publish.yml`` publishes on that tag; or
* moves an existing ``refs/heads/*`` branch so that ``[project].version`` in
  ``pyproject.toml`` differs between the remote's old commit and the new one
  (the new commit).

Deletions, new branches and other refs need no receipt. A receipt is keyed to
the exact commit, so a commit made after the release run has none.

Exit codes: 0 when every required receipt holds, 1 when one is missing or
invalid, 2 when the check cannot complete (malformed updates, Git failure).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

RECEIPT_DIR_NAME = "roam-release-receipts"
RECEIPT_SCHEMA = "roam-release-receipt/1"
RECEIPT_MAX_BYTES = 100_000
_GIT_TIMEOUT_S = 30
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
# Variables that point Git at another repository, index or object store. The
# hook clears them already; a direct `prepush_check.py --release` run may not.
_REPO_LOCAL_GIT_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)


def _load_prepush_refs_module():
    module_path = Path(__file__).resolve().parent / "prepush_refs.py"
    spec = importlib.util.spec_from_file_location("roam_prepush_refs", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_prepush_refs = _load_prepush_refs_module()


class ReceiptCheckError(RuntimeError):
    """Raised when the receipt check cannot complete."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), *args],
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
            env={k: v for k, v in os.environ.items() if k not in _REPO_LOCAL_GIT_VARS},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReceiptCheckError(f"git {' '.join(args)} could not run: {exc}") from exc


def _git_text(root: Path, *args: str, path: bool = False) -> str:
    """Return Git's output; a ``path`` keeps its spaces and loses only the final newline."""
    proc = _git(root, *args)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ReceiptCheckError(f"git {' '.join(args)} failed: {detail}")
    out = proc.stdout
    if isinstance(out, bytes):
        out = os.fsdecode(out) if path else out.decode("utf-8", errors="strict")
    return out.removesuffix("\n") if path else out.strip()


def receipts_dir(root: Path) -> Path:
    raw = _git_text(root, "rev-parse", "--git-common-dir", path=True)
    if not raw:
        raise ReceiptCheckError("git did not name the common Git directory")
    common = Path(raw)
    if not common.is_absolute():
        common = root / common
    return common.resolve() / RECEIPT_DIR_NAME


def _full_oid(value: str, what: str) -> str:
    if not _OID_RE.fullmatch(value):
        raise ReceiptCheckError(f"{what} is not a full commit id: {value!r}")
    return value


def toplevel(root: Path) -> Path:
    return Path(_git_text(root, "rev-parse", "--show-toplevel", path=True)).resolve()


def head_sha(root: Path) -> str:
    return _full_oid(_git_text(root, "rev-parse", "--verify", "HEAD^{commit}"), "HEAD")


def tree_is_clean(root: Path, *, include_untracked: bool) -> bool:
    mode = "--untracked-files=normal" if include_untracked else "--untracked-files=no"
    return _git_text(root, "status", "--porcelain", "--ignore-submodules=none", mode) == ""


def write_receipt(root: Path, sha: str, *, workers: int) -> Path:
    """Record that the release tier passed on ``sha``."""
    _full_oid(sha, "receipt commit")
    directory = receipts_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sha}.json"
    record = {
        "schema": RECEIPT_SCHEMA,
        "sha": sha,
        "tier": "release",
        "rc": 0,
        "workers": workers,
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # A private temporary name per writer: two worktrees may finish a release
    # run on the same commit at once.
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=f".{sha}.", suffix=".tmp", delete=False
    ) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        except BaseException:
            stream.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def receipt_problem(root: Path, sha: str) -> str | None:
    """Return why ``sha`` has no valid receipt, or None when it has one."""
    path = receipts_dir(root) / f"{sha}.json"
    try:
        st = path.stat()
    except FileNotFoundError:
        return f"no release receipt at {path}"
    except OSError as exc:
        return f"release receipt unreadable at {path}: {exc}"
    if not stat.S_ISREG(st.st_mode):
        return f"release receipt at {path} is not a regular file"
    try:
        with path.open("rb") as stream:
            raw = stream.read(RECEIPT_MAX_BYTES + 1)
    except OSError as exc:
        return f"release receipt unreadable at {path}: {exc}"
    if len(raw) > RECEIPT_MAX_BYTES:
        return f"release receipt at {path} is larger than {RECEIPT_MAX_BYTES} bytes"
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"release receipt at {path} is not valid JSON: {exc}"
    if not isinstance(record, dict):
        return f"release receipt at {path} is not a JSON object"
    if record.get("schema") != RECEIPT_SCHEMA:
        return f"release receipt at {path} has schema {record.get('schema')!r}, expected {RECEIPT_SCHEMA!r}"
    if record.get("sha") != sha:
        return f"release receipt at {path} names commit {record.get('sha')!r}, not {sha}"
    if record.get("tier") != "release":
        return f"release receipt at {path} names tier {record.get('tier')!r}, not 'release'"
    rc = record.get("rc")
    if type(rc) is not int or rc != 0:
        return f"release receipt at {path} records rc {rc!r}, not 0"
    return None


def _peeled_commit(root: Path, oid: str) -> str:
    return _full_oid(_git_text(root, "rev-parse", "--verify", f"{oid}^{{commit}}"), oid)


def _pyproject_blob(root: Path, commit: str) -> str | None:
    """Return the blob id of ``pyproject.toml`` at ``commit``, or None when it has none.

    A directory at that path counts as no project file. A symbolic link is
    refused: the blob Git stores is the link text, not the file it names.
    """
    listing = _git_text(root, "ls-tree", "-z", commit, "--", "pyproject.toml").rstrip("\0")
    if not listing:
        return None
    meta, _, _name = listing.partition("\t")
    mode, kind, oid = meta.split(" ")
    if kind == "tree":
        return None
    if kind != "blob" or mode not in ("100644", "100755"):
        raise ReceiptCheckError(f"pyproject.toml at {commit} is not a regular file (mode {mode})")
    return oid


def _project_version(root: Path, blob: str | None, commit: str) -> str | None:
    if blob is None:
        return None
    proc = _git(root, "cat-file", "blob", blob)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ReceiptCheckError(f"pyproject.toml at {commit} could not be read: {detail}")
    try:
        data = tomllib.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReceiptCheckError(f"pyproject.toml at {commit} does not parse: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    version = project.get("version")
    if version is not None and not isinstance(version, str):
        raise ReceiptCheckError(f"pyproject.toml at {commit} has a non-string version {version!r}")
    return version


def required_receipts(root: Path, updates: list) -> list[tuple[str, str]]:
    """Return ``(commit, reason)`` for every commit this push needs a receipt for."""
    required: list[tuple[str, str]] = []
    seen: set[str] = set()

    def need(commit: str, reason: str) -> None:
        if commit not in seen:
            seen.add(commit)
            required.append((commit, reason))

    for update in updates:
        if update.is_deletion:
            continue
        ref = update.remote_ref
        if ref.startswith("refs/tags/v"):
            commit = _peeled_commit(root, update.local_oid)
            need(commit, f"tag {ref} points at it")
        elif ref.startswith("refs/heads/") and not update.is_new_remote_ref:
            new_commit = _peeled_commit(root, update.local_oid)
            try:
                old_commit = _peeled_commit(root, update.remote_oid)
            except ReceiptCheckError as exc:
                raise ReceiptCheckError(
                    f"the remote's current {ref} ({update.remote_oid}) is not in this repository, so a "
                    f"version change cannot be ruled out; run `git fetch` and push again ({exc})"
                ) from exc
            old_blob = _pyproject_blob(root, old_commit)
            new_blob = _pyproject_blob(root, new_commit)
            if old_blob == new_blob:
                continue
            old_version = _project_version(root, old_blob, old_commit)
            new_version = _project_version(root, new_blob, new_commit)
            if old_version != new_version:
                need(new_commit, f"{ref} changes the version from {old_version!r} to {new_version!r}")
    return required


def check_updates(root: Path, updates_path: Path) -> int:
    try:
        updates = _prepush_refs.load_pre_push_updates(updates_path)
        required = required_receipts(root, updates)
        problems = [(commit, reason, receipt_problem(root, commit)) for commit, reason in required]
    except (_prepush_refs.PrePushUpdateError, ReceiptCheckError) as exc:
        print(f"[release-receipt] UNKNOWN: the release-receipt check could not complete: {exc}", file=sys.stderr)
        return 2
    missing = [(commit, reason, problem) for commit, reason, problem in problems if problem is not None]
    for commit, reason, _ in problems:
        if all(commit != m[0] for m in missing):
            print(f"[release-receipt] ok: {commit} ({reason}) passed the release tier")
    if not missing:
        return 0
    for commit, reason, problem in missing:
        print(f"[release-receipt] BLOCKED: {commit} ({reason}): {problem}", file=sys.stderr)
    print(
        "[release-receipt] On a clean tree whose branch HEAD is that exact commit (with its upstream set), run "
        "`python scripts/prepush_check.py --release`, and push again once it passes.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--updates-file", type=Path, required=True, help="the pre-push ref updates Git supplied")
    args = parser.parse_args(argv)
    try:
        root = toplevel(Path.cwd())
    except ReceiptCheckError as exc:
        print(f"[release-receipt] UNKNOWN: cannot find the repository: {exc}", file=sys.stderr)
        return 2
    return check_updates(root, args.updates_file)


if __name__ == "__main__":
    sys.exit(main())
