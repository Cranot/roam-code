"""Strict parser for Git pre-push ref-update records.

Git supplies one update per line on the pre-push hook's standard input::

    <local-ref> <local-oid> <remote-ref> <remote-oid>

The OIDs, rather than the checked-out branch or its configured upstream, are
the authoritative description of what the push will publish.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class PrePushUpdateError(ValueError):
    """Raised when Git's pre-push update stream is malformed."""


class PrePushGitError(RuntimeError):
    """Raised when exact pushed-object inspection cannot complete."""


@dataclass(frozen=True)
class RefUpdate:
    local_ref: str
    local_oid: str
    remote_ref: str
    remote_oid: str

    @property
    def is_deletion(self) -> bool:
        return not self.local_oid.strip("0")

    @property
    def is_new_remote_ref(self) -> bool:
        return not self.remote_oid.strip("0")

    @property
    def revision_spec(self) -> str | None:
        """Return the complete commit revision spec introduced by this update."""
        if self.is_deletion:
            return None
        if self.is_new_remote_ref:
            # Scan all history reachable from a newly published ref. Excluding
            # local remote-tracking refs would make completeness depend on
            # their freshness, so the safe first-push behavior is intentionally
            # exhaustive.
            return self.local_oid
        return f"{self.remote_oid}..{self.local_oid}"


@dataclass(frozen=True)
class PublishedTextObject:
    oid: str
    kind: str
    path: str
    data: bytes


def parse_pre_push_updates(text: str) -> list[RefUpdate]:
    """Parse and validate a complete pre-push update stream."""
    updates: list[RefUpdate] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line:
            raise PrePushUpdateError(f"empty pre-push update at line {line_no}")
        fields = raw_line.split(" ")
        if len(fields) != 4 or any(not field for field in fields):
            raise PrePushUpdateError(f"malformed pre-push update at line {line_no}")
        local_ref, local_oid, remote_ref, remote_oid = fields
        local_oid = local_oid.lower()
        remote_oid = remote_oid.lower()
        if not _OID_RE.fullmatch(local_oid) or not _OID_RE.fullmatch(remote_oid):
            raise PrePushUpdateError(f"invalid object ID at line {line_no}")
        if len(local_oid) != len(remote_oid):
            raise PrePushUpdateError(f"mixed object ID algorithms at line {line_no}")
        updates.append(RefUpdate(local_ref, local_oid, remote_ref, remote_oid))
    return updates


def load_pre_push_updates(path: Path) -> list[RefUpdate]:
    """Read a captured update stream as strict UTF-8 and parse it."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PrePushUpdateError(f"cannot read pre-push updates: {exc}") from exc
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PrePushUpdateError("pre-push updates are not valid UTF-8") from exc
    return parse_pre_push_updates(text)


def unique_revision_specs(updates: list[RefUpdate]) -> list[str]:
    """Return non-deletion revision specs in stable first-seen order."""
    specs: list[str] = []
    seen: set[str] = set()
    for update in updates:
        spec = update.revision_spec
        if spec is None or spec in seen:
            continue
        seen.add(spec)
        specs.append(spec)
    return specs


def unique_revision_args(
    updates: list[RefUpdate],
    *,
    published_commits: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    """Return stable rev-list argv groups for every commit introduced.

    New refs are deliberately exhaustive. Local remote-tracking refs are
    mutable cache state, not authoritative evidence about the destination of
    the current push, so they can never be used to exclude reachable history.
    """
    groups: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for update in updates:
        spec = update.revision_spec
        if spec is None:
            continue
        if update.is_new_remote_ref and published_commits:
            args = (spec, "--not", *published_commits)
        else:
            args = (spec,)
        if args in seen:
            continue
        seen.add(args)
        groups.append(args)
    return groups


def _is_valid_full_ref_name(ref_name: str) -> bool:
    """Apply Git's full ref-name grammar without spawning per advertised ref."""
    if not ref_name.startswith("refs/") or ref_name.endswith("/"):
        return False
    if "//" in ref_name or ".." in ref_name or "@{" in ref_name:
        return False
    if any(ord(char) <= 0x20 or ord(char) == 0x7F or char in "~^:?*[\\" for char in ref_name):
        return False
    components = ref_name.split("/")
    return all(
        component and not component.startswith(".") and not component.endswith(".") and not component.endswith(".lock")
        for component in components
    )


def authoritative_remote_commits(repo_root: Path, remote_url: str) -> tuple[str, ...]:
    """Return locally available commits advertised by the exact destination.

    The pre-push hook's second argument is the destination Git is using for
    this push. Querying that URL avoids trusting mutable local tracking refs.
    Advertised objects absent from the local object database are ignored
    because they cannot be used as local revision exclusions.
    """
    if not remote_url or "\x00" in remote_url or "\n" in remote_url or "\r" in remote_url:
        raise PrePushUpdateError("invalid remote URL")
    raw = _git_bytes(
        repo_root,
        ["ls-remote", "--refs", "--", remote_url],
        operation="read destination remote advertisement",
    )
    commits: list[str] = []
    seen: set[str] = set()
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        if raw_line.count(b"\t") != 1:
            raise PrePushGitError(f"malformed destination advertisement at line {line_no}")
        oid_raw, separator, ref_raw = raw_line.partition(b"\t")
        if not separator:
            raise PrePushGitError(f"malformed destination advertisement at line {line_no}")
        try:
            oid = oid_raw.decode("ascii", errors="strict").lower()
            ref_name = ref_raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PrePushGitError(f"undecodable destination advertisement at line {line_no}") from exc
        if not _OID_RE.fullmatch(oid):
            raise PrePushGitError(f"invalid destination object ID at line {line_no}")
        if not _is_valid_full_ref_name(ref_name):
            raise PrePushGitError(f"invalid destination ref name at line {line_no}")
        proc = subprocess.run(
            ["git", "--no-replace-objects", "rev-parse", "--verify", f"{oid}^{{commit}}"],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            continue
        try:
            commit = proc.stdout.decode("ascii", errors="strict").strip().lower()
        except UnicodeDecodeError as exc:
            raise PrePushGitError(f"undecodable peeled destination object at line {line_no}") from exc
        if not _OID_RE.fullmatch(commit):
            raise PrePushGitError(f"invalid peeled destination object at line {line_no}")
        if commit not in seen:
            seen.add(commit)
            commits.append(commit)
    return tuple(commits)


def _git_bytes(repo_root: Path, args: list[str], *, operation: str) -> bytes:
    proc = subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise PrePushGitError(f"{operation} failed (git exit {proc.returncode}){suffix}")
    return proc.stdout


def _object_type(repo_root: Path, oid: str) -> str:
    raw = _git_bytes(repo_root, ["cat-file", "-t", oid], operation=f"resolve object type {oid}")
    try:
        object_type = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise PrePushGitError(f"object type for {oid} is not ASCII") from exc
    if object_type not in {"blob", "commit", "tag", "tree"}:
        raise PrePushGitError(f"unsupported pushed object type for {oid}: {object_type}")
    return object_type


def read_commit_object(repo_root: Path, oid: str) -> bytes:
    """Read an immutable commit object without Git's display transcoding."""
    return _git_bytes(repo_root, ["cat-file", "commit", oid], operation=f"read commit object {oid}")


def _parse_tag(repo_root: Path, oid: str) -> tuple[str, str, bytes]:
    raw = _git_bytes(repo_root, ["cat-file", "tag", oid], operation=f"read tag object {oid}")
    header, separator, message = raw.partition(b"\n\n")
    if not separator:
        raise PrePushGitError(f"tag object {oid} has no header terminator")
    object_lines = [line for line in header.splitlines() if line.startswith(b"object ")]
    type_lines = [line for line in header.splitlines() if line.startswith(b"type ")]
    if len(object_lines) != 1 or len(type_lines) != 1:
        raise PrePushGitError(f"tag object {oid} has malformed target headers")
    try:
        target_oid = object_lines[0][len(b"object ") :].decode("ascii", errors="strict").lower()
        declared_type = type_lines[0][len(b"type ") :].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise PrePushGitError(f"tag object {oid} has non-ASCII target headers") from exc
    if not _OID_RE.fullmatch(target_oid):
        raise PrePushGitError(f"tag object {oid} has an invalid target object ID")
    actual_type = _object_type(repo_root, target_oid)
    if declared_type != actual_type:
        raise PrePushGitError(f"tag object {oid} declares {declared_type} but targets {actual_type}")
    return target_oid, actual_type, raw


def _tree_entries(repo_root: Path, tree_oid: str) -> list[tuple[str, str, bytes | None]]:
    raw = _git_bytes(
        repo_root,
        ["ls-tree", "-r", "-z", "--full-tree", tree_oid],
        operation=f"list pushed tree {tree_oid}",
    )
    entries: list[tuple[str, str, bytes | None]] = []
    for entry in (part for part in raw.split(b"\0") if part):
        header, separator, path_raw = entry.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3:
            raise PrePushGitError(f"tree {tree_oid} has malformed ls-tree output")
        _mode, object_type_raw, object_oid_raw = fields
        try:
            object_type = object_type_raw.decode("ascii", errors="strict")
            object_oid = object_oid_raw.decode("ascii", errors="strict").lower()
            path = path_raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PrePushGitError(f"tree {tree_oid} has undecodable entries") from exc
        if not _OID_RE.fullmatch(object_oid):
            raise PrePushGitError(f"tree {tree_oid} has an invalid object ID")
        if object_type == "commit":
            # Gitlinks publish an object ID, not the external repository's
            # object content. Their path names are still published text.
            entries.append((object_oid, path, None))
            continue
        if object_type != "blob":
            raise PrePushGitError(f"tree {tree_oid} has unexpected recursive entry type {object_type}")
        data = _git_bytes(repo_root, ["cat-file", "blob", object_oid], operation=f"read tree blob {object_oid}")
        entries.append((object_oid, path, data))
    return entries


def collect_direct_published_texts(repo_root: Path, updates: list[RefUpdate]) -> list[PublishedTextObject]:
    """Collect names, tag objects, and direct blob/tree content from pushed tips.

    Commit objects and changed path names are handled by revision traversal.
    This function covers other publishable text that ``rev-list`` does not
    expose directly.
    """
    results: list[PublishedTextObject] = []
    emitted: set[tuple[str, str, str]] = set()
    visited: set[str] = set()

    def emit(oid: str, kind: str, path: str, data: bytes) -> None:
        key = (oid, kind, path)
        if key not in emitted:
            emitted.add(key)
            results.append(PublishedTextObject(oid=oid, kind=kind, path=path, data=data))

    def visit(oid: str) -> None:
        if oid in visited:
            return
        visited.add(oid)
        object_type = _object_type(repo_root, oid)
        if object_type == "commit":
            return
        if object_type == "tag":
            target_oid, _target_type, raw = _parse_tag(repo_root, oid)
            emit(oid, "tag_object", f"{oid[:7]} (annotated tag object)", raw)
            visit(target_oid)
            return
        if object_type == "blob":
            data = _git_bytes(repo_root, ["cat-file", "blob", oid], operation=f"read pushed blob {oid}")
            emit(oid, "blob", f"{oid[:7]} (direct pushed blob)", data)
            return
        if object_type == "tree":
            for entry_oid, path, data in _tree_entries(repo_root, oid):
                emit(entry_oid, "path_name", f"{path} (published path name)", path.encode("utf-8"))
                if data is not None:
                    emit(entry_oid, "blob", path, data)
            return
        raise PrePushGitError(f"unsupported pushed object type for {oid}: {object_type}")

    for update in updates:
        if not update.is_deletion:
            emit(
                update.local_oid,
                "ref_name",
                f"{update.remote_ref} (published ref name)",
                update.remote_ref.encode("utf-8"),
            )
            visit(update.local_oid)
    return results
