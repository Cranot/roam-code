"""Stage only committed public site bytes for the existing Pages deployment.

Run from the repository root with the already reviewed commit ID. Retain the
export and manifest under ignored internal/site-deploy/ for release evidence.
This helper performs no network calls and does not approve or deploy a release.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path, PurePosixPath

SITE = "templates/distribution/landing-page"


def git(root: Path, *args: str) -> bytes:
    command = ["git"]
    if args and args[0] == "archive":
        # Archive honors checkout conversions. Override host line-ending
        # preferences only for export; status must retain the user's normal
        # worktree interpretation. Attribute transformations still fail the
        # blob check below, and no persistent Git configuration is changed.
        command.extend(["-c", "core.autocrlf=false", "-c", "core.eol=lf"])
    return subprocess.run([*command, *args], cwd=root, check=True, capture_output=True, timeout=60).stdout


def require_candidate(root: Path, commit: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
        raise ValueError("Supply the full reviewed commit ID")
    if git(root, "rev-parse", "--verify", "HEAD").decode().strip() != commit:
        raise ValueError("HEAD changed; qualify the current candidate before deployment")
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Commit or preserve working changes before staging the production site")


def stage(root: Path, commit: str) -> Path:
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("Run from the repository root")
    require_candidate(root, commit)
    objects = {}
    for entry in git(root, "ls-tree", "-r", "-z", commit, "--", SITE).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        relative = PurePosixPath(path).relative_to(SITE)
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError(f"Only regular committed site files may ship: {path}")
        if any(part in {".", "..", ".roam", ".git", "internal"} or part.startswith(".env") for part in relative.parts):
            raise ValueError(f"Private or unsafe site path: {path}")
        if "\\" in path or ":" in path:
            raise ValueError(f"Nonportable site path: {path}")
        objects[path] = oid
    if not objects or f"{SITE}/index.html" not in objects:
        raise ValueError("No committed website index; an empty export is not a deployment")
    algorithm = git(root, "rev-parse", "--show-object-format").decode().strip()
    if algorithm not in {"sha1", "sha256"}:
        raise ValueError("Unsupported Git object format")
    archive = git(root, "archive", "--format=zip", commit, "--", SITE)
    contents = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            if member.filename not in objects or member.filename in contents:
                raise ValueError("Archive contains an unexpected or duplicate file")
            data = bundle.read(member)
            identity = hashlib.new(algorithm, f"blob {len(data)}\0".encode() + data).hexdigest()
            if identity != objects[member.filename]:
                raise ValueError(f"Archive transformed committed bytes: {member.filename}")
            contents[member.filename] = data
    if set(contents) != set(objects):
        raise ValueError("Archive omitted committed site files; inspect export-ignore attributes")
    # Recheck before writing; never silently export a different or dirty HEAD.
    require_candidate(root, commit)
    evidence = root / "internal/site-deploy" / f"{commit[:12]}-{uuid.uuid4().hex}"
    if not evidence.resolve().is_relative_to(root / "internal"):
        raise ValueError("Deployment evidence must stay within internal/")
    public = evidence / "public"
    public.mkdir(parents=True, exist_ok=False)
    manifest = {"commit": commit, "file_count": len(contents), "files": {}}
    for name, data in sorted(contents.items()):
        relative = PurePosixPath(name).relative_to(SITE).as_posix()
        target = public / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest["files"][relative] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    require_candidate(root, commit)
    return public


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: python scripts/stage_site.py FULL_REVIEWED_COMMIT_ID")
        public = stage(Path.cwd(), sys.argv[1])
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Site staging refused: {exc}", file=sys.stderr)
        return 1
    print(public.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
