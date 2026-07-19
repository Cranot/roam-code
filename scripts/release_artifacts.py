#!/usr/bin/env python3
"""Bind release distributions and their CycloneDX SBOM to exact digests.

This script runs in the unprivileged build job.  It deliberately has no
network access and no publishing behavior: it validates the one-wheel /
one-sdist release set, binds both files into the SBOM, and writes the manifest
that the privileged publish job verifies before requesting an OIDC token.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import stat
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "roam.release-artifacts/v1"
PROJECT = "roam-code"
PUBLISH_PREDICATE = "https://docs.pypi.org/attestations/publish/v1"
RECOVERY_SCHEMA = "roam.pypi-recovery/v1"

_TAG_RE = re.compile(
    r"^v(?P<version>[0-9]+(?:\.[0-9]+)+"
    r"(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?)$"
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_PYPI_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_SBOM_BYTES = 64 * 1024 * 1024
_MAX_DISTRIBUTION_BYTES = 100 * 1024 * 1024
_MAX_ARCHIVE_METADATA_BYTES = 4 * 1024 * 1024
_MAX_SDIST_EXPANDED_BYTES = 512 * 1024 * 1024
_MAX_SDIST_MEMBERS = 100_000

_PROPERTY_NAMES = {
    "roam:release_manifest_schema",
    "roam:release_tag",
    "roam:release_commit",
    "roam:release_wheel_filename",
    "roam:release_wheel_sha256",
    "roam:release_wheel_provenance",
    "roam:release_sdist_filename",
    "roam:release_sdist_sha256",
    "roam:release_sdist_provenance",
    # Retained for consumers of the pre-manifest SBOM shape.
    "roam:published_wheel_filename",
    "roam:published_wheel_sha256",
}


class ReleaseArtifactError(ValueError):
    """A release artifact set or digest binding is invalid."""


class _DuplicateJsonKey(ValueError):
    """One JSON object repeated a key and therefore has ambiguous meaning."""


def _fail(message: str) -> None:
    raise ReleaseArtifactError(message)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _is_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validated_real_directory(path: Path, *, label: str) -> tuple[Path, os.stat_result]:
    """Return one existing directory whose path contains no symlink/reparse hop."""

    absolute = Path(os.path.abspath(path))
    try:
        resolved = absolute.resolve(strict=True)
        current = os.lstat(absolute)
    except OSError as exc:
        _fail(f"{label} directory is unavailable: {absolute}: {exc}")
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        _fail(f"{label} directory must not traverse a symlink or reparse point: {absolute}")
    if not stat.S_ISDIR(current.st_mode) or _is_reparse_point(current):
        _fail(f"{label} directory must be a real directory: {absolute}")
    return absolute, current


def _write_all(file_descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(file_descriptor, payload[offset:])
        if written <= 0:  # pragma: no cover - defensive OS contract guard
            _fail("atomic release write made no forward progress")
        offset += written


def _canonical_project_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validated_identity(tag: str, commit: str) -> str:
    match = _TAG_RE.fullmatch(tag)
    if match is None:
        _fail(f"tag must be a constrained PEP 440 release tag such as v13.9.0; got {tag!r}")
    if _COMMIT_RE.fullmatch(commit) is None:
        _fail(f"commit must be a lowercase 40-character SHA-1; got {commit!r}")
    return match.group("version")


def _validated_file(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular, non-symlink file: {path}")
    if _SAFE_FILENAME_RE.fullmatch(path.name) is None:
        _fail(f"{label} has an unsafe filename: {path.name!r}")


def _validated_only_file_in_directory(path: Path, *, label: str) -> None:
    _validated_file(path, label=label)
    entries = sorted(path.parent.iterdir(), key=lambda entry: entry.name)
    if entries != [path]:
        _fail(f"{label} directory must contain only {path.name!r}; found {[entry.name for entry in entries]}")


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare identity and mutation fields around one bounded file read."""

    return bool(
        _file_identity(left) == _file_identity(right)
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        # Windows can refresh ctime when a handle is opened or inspected by a
        # filesystem filter. Identity + mode + links + size + mtime remain the
        # stable cross-handle proof there; Linux release runners also bind ctime.
        and (os.name == "nt" or left.st_ctime_ns == right.st_ctime_ns)
    )


def _read_bounded_regular_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    """Read one stable, singly-linked file without following its leaf link."""

    _validated_real_directory(path.parent, label=f"{label} parent")
    try:
        before = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot inspect {label} at {path}: {exc}")
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or _is_reparse_point(before):
        _fail(f"{label} must be a regular, non-symlink file: {path}")
    if before.st_nlink != 1:
        _fail(f"{label} must not be hard-linked: {path}")
    if before.st_size > max_bytes:
        _fail(f"{label} exceeds the {max_bytes}-byte input limit: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _fail(f"cannot open {label} at {path}: {exc}")
    try:
        opened = os.fstat(descriptor)
        if not _same_file_state(before, opened):
            _fail(f"{label} changed before it could be read: {path}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                _fail(f"{label} exceeds the {max_bytes}-byte input limit: {path}")
        opened_after = os.fstat(descriptor)
        try:
            after = os.lstat(path)
        except OSError as exc:
            _fail(f"{label} changed while it was read: {path}: {exc}")
        if not _same_file_state(opened, opened_after) or not _same_file_state(before, after):
            _fail(f"{label} changed while it was read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _metadata_fields(raw: bytes, *, source: str) -> tuple[str, str]:
    metadata = BytesParser().parsebytes(raw)
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        _fail(f"{source} metadata must contain Name and Version fields")
    return str(name), str(version)


def _wheel_metadata(payload: bytes, *, filename: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_SDIST_MEMBERS:
                _fail(f"wheel contains more than {_MAX_SDIST_MEMBERS} archive members: {filename}")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                _fail(f"wheel contains duplicate archive member names: {filename}")
            members = [entry for entry in entries if entry.filename.endswith(".dist-info/METADATA")]
            if len(members) != 1:
                _fail(f"wheel must contain exactly one .dist-info/METADATA file; found {len(members)} in {filename}")
            metadata = members[0]
            if metadata.file_size > _MAX_ARCHIVE_METADATA_BYTES:
                _fail(f"wheel METADATA exceeds the {_MAX_ARCHIVE_METADATA_BYTES}-byte limit: {filename}")
            return _metadata_fields(archive.read(metadata), source=f"wheel {filename}")
    except (OSError, zipfile.BadZipFile) as exc:
        _fail(f"cannot read wheel {filename}: {exc}")


def _sdist_metadata(payload: bytes, *, filename: str) -> tuple[str, str]:
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            all_members = archive.getmembers()
            if len(all_members) > _MAX_SDIST_MEMBERS:
                _fail(f"sdist contains more than {_MAX_SDIST_MEMBERS} archive members: {filename}")
            members = [
                member
                for member in all_members
                if member.isfile()
                and PurePosixPath(member.name).name == "PKG-INFO"
                and len(PurePosixPath(member.name).parts) == 2
            ]
            if len(members) != 1:
                _fail(f"sdist must contain exactly one top-level PKG-INFO file; found {len(members)} in {filename}")
            if members[0].size > _MAX_ARCHIVE_METADATA_BYTES:
                _fail(f"sdist PKG-INFO exceeds the {_MAX_ARCHIVE_METADATA_BYTES}-byte limit: {filename}")
            extracted = archive.extractfile(members[0])
            if extracted is None:
                _fail(f"cannot read PKG-INFO from sdist {filename}")
            metadata = extracted.read(_MAX_ARCHIVE_METADATA_BYTES + 1)
            if len(metadata) > _MAX_ARCHIVE_METADATA_BYTES:
                _fail(f"sdist PKG-INFO exceeds the {_MAX_ARCHIVE_METADATA_BYTES}-byte limit: {filename}")
            return _metadata_fields(metadata, source=f"sdist {filename}")
    except (OSError, tarfile.TarError) as exc:
        _fail(f"cannot read sdist {filename}: {exc}")


def _artifact_record(path: Path, *, kind: str, version: str) -> dict[str, Any]:
    payload = _read_bounded_regular_file(
        path,
        label=f"{kind} distribution",
        max_bytes=_MAX_DISTRIBUTION_BYTES,
    )
    if kind == "wheel":
        name, artifact_version = _wheel_metadata(payload, filename=path.name)
    elif kind == "sdist":
        name, artifact_version = _sdist_metadata(payload, filename=path.name)
    else:  # pragma: no cover - internal closed enumeration
        _fail(f"unsupported distribution kind: {kind}")

    if _canonical_project_name(name) != PROJECT:
        _fail(f"{kind} project name {name!r} does not identify {PROJECT!r}")
    if artifact_version != version:
        _fail(f"{kind} version {artifact_version!r} does not match tag version {version!r}")

    return {
        "filename": path.name,
        "kind": kind,
        "provenance_url": (f"https://pypi.org/integrity/{PROJECT}/{version}/{path.name}/provenance"),
        "sha256": _sha256_bytes(payload),
        "size": len(payload),
    }


def _collect_distributions(dist_dir: Path, *, version: str) -> list[dict[str, Any]]:
    if not dist_dir.is_dir():
        _fail(f"distribution directory does not exist: {dist_dir}")

    entries = sorted(dist_dir.iterdir(), key=lambda path: path.name)
    wheels = [path for path in entries if path.name.endswith(".whl")]
    sdists = [path for path in entries if path.name.endswith(".tar.gz")]
    expected = set(wheels + sdists)
    unexpected = [path.name for path in entries if path not in expected]

    if unexpected:
        _fail(f"dist/ contains files outside the publish set: {unexpected}")
    if len(wheels) != 1 or len(sdists) != 1:
        _fail(
            "release requires exactly one wheel and one .tar.gz sdist; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    records = [
        _artifact_record(wheels[0], kind="wheel", version=version),
        _artifact_record(sdists[0], kind="sdist", version=version),
    ]
    return sorted(records, key=lambda record: record["filename"])


def _decode_json_object(payload: bytes, *, path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
        _fail(f"cannot read {label} JSON from {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object: {path}")
    return value


def _load_json_object_with_payload(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    payload = _read_bounded_regular_file(path, label=label, max_bytes=max_bytes)
    return _decode_json_object(payload, path=path, label=label), payload


def _load_json_object(path: Path, *, label: str, max_bytes: int) -> dict[str, Any]:
    value, _payload = _load_json_object_with_payload(path, label=label, max_bytes=max_bytes)
    return value


def _matching_components(document: dict[str, Any], *, version: str) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    components = document.get("components", [])
    if isinstance(components, list):
        candidates.extend(components)
    metadata = document.get("metadata")
    if isinstance(metadata, dict) and metadata.get("component") is not None:
        candidates.append(metadata["component"])

    matches = [
        component
        for component in candidates
        if isinstance(component, dict)
        and _canonical_project_name(str(component.get("name", ""))) == PROJECT
        and str(component.get("version", "")) == version
    ]
    if not matches:
        _fail(f"SBOM has no {PROJECT} {version} component to bind")
    return matches


def _artifact_by_kind(artifacts: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [artifact for artifact in artifacts if artifact.get("kind") == kind]
    if len(matches) != 1:
        _fail(f"manifest must contain exactly one {kind} record")
    return matches[0]


def _bind_sbom(
    sbom_path: Path,
    *,
    artifacts: list[dict[str, Any]],
    tag: str,
    commit: str,
    version: str,
) -> None:
    _validated_only_file_in_directory(sbom_path, label="SBOM")
    document = _load_json_object(sbom_path, label="SBOM", max_bytes=_MAX_SBOM_BYTES)
    wheel = _artifact_by_kind(artifacts, "wheel")
    sdist = _artifact_by_kind(artifacts, "sdist")

    # Preserve the historical component-level wheel hash while adding explicit
    # wheel and sdist bindings below.  A single CycloneDX component cannot carry
    # two SHA-256 values without making the archive identity ambiguous.
    for component in _matching_components(document, version=version):
        hashes = component.setdefault("hashes", [])
        if not isinstance(hashes, list):
            _fail("SBOM component hashes must be a JSON array")
        hashes[:] = [item for item in hashes if not isinstance(item, dict) or item.get("alg") != "SHA-256"]
        hashes.append({"alg": "SHA-256", "content": wheel["sha256"]})

    metadata = document.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        _fail("SBOM metadata must be a JSON object")
    properties = metadata.setdefault("properties", [])
    if not isinstance(properties, list) or any(not isinstance(item, dict) for item in properties):
        _fail("SBOM metadata.properties must be an array of JSON objects")

    properties[:] = [item for item in properties if item.get("name") not in _PROPERTY_NAMES]
    values = {
        "roam:release_manifest_schema": SCHEMA,
        "roam:release_tag": tag,
        "roam:release_commit": commit,
        "roam:release_wheel_filename": wheel["filename"],
        "roam:release_wheel_sha256": wheel["sha256"],
        "roam:release_wheel_provenance": wheel["provenance_url"],
        "roam:release_sdist_filename": sdist["filename"],
        "roam:release_sdist_sha256": sdist["sha256"],
        "roam:release_sdist_provenance": sdist["provenance_url"],
        "roam:published_wheel_filename": wheel["filename"],
        "roam:published_wheel_sha256": wheel["sha256"],
    }
    properties.extend({"name": name, "value": value} for name, value in sorted(values.items()))

    _write_json_atomic(sbom_path, document)


def _write_bytes_atomic(path: Path, payload: bytes, *, label: str) -> None:
    """Durably replace one file without following attacker-chosen links.

    POSIX uses a no-follow directory descriptor for creation, cleanup, and
    replacement, so renaming the parent cannot redirect the write.  Windows
    rejects symlink/reparse parents and rechecks the directory identity at the
    replacement boundary.  Both paths use a cryptographically random,
    exclusive temporary name; cleanup can therefore never unlink a pathname
    chosen by another writer.
    """

    if _SAFE_FILENAME_RE.fullmatch(path.name) is None:
        _fail(f"atomic {label} destination has an unsafe filename: {path.name!r}")
    parent, parent_stat = _validated_real_directory(path.parent, label=f"atomic {label} destination")
    destination = parent / path.name
    directory_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name: str | None = None

    try:
        if os.name != "nt":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(parent, directory_flags)
            if _file_identity(os.fstat(directory_fd)) != _file_identity(parent_stat):
                _fail(f"atomic {label} destination directory changed while it was opened")

        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        create_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        for _attempt in range(128):
            candidate = f".{path.name}.{secrets.token_hex(16)}.tmp"
            try:
                if directory_fd is None:
                    temporary_fd = os.open(parent / candidate, create_flags, 0o600)
                else:
                    temporary_fd = os.open(candidate, create_flags, 0o600, dir_fd=directory_fd)
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd is None or temporary_name is None:
            _fail(f"could not reserve a unique temporary release-{label.lower()} pathname")

        temporary_stat = os.fstat(temporary_fd)
        if not stat.S_ISREG(temporary_stat.st_mode):  # pragma: no cover - O_EXCL regular-file invariant
            _fail(f"atomic {label} temporary object is not a regular file")
        _write_all(temporary_fd, payload)
        if hasattr(os, "fchmod"):
            os.fchmod(temporary_fd, 0o644)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        try:
            if directory_fd is None:
                destination_stat = os.lstat(destination)
            else:
                destination_stat = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            destination_stat = None
        if destination_stat is not None and (
            not stat.S_ISREG(destination_stat.st_mode) or _is_reparse_point(destination_stat)
        ):
            _fail(f"atomic {label} destination must be absent or a regular, non-symlink file: {destination}")

        if directory_fd is None:
            current_parent = os.lstat(parent)
            if _file_identity(current_parent) != _file_identity(parent_stat) or _is_reparse_point(current_parent):
                _fail(f"atomic {label} destination directory changed before replacement")
            os.replace(parent / temporary_name, destination)
        else:
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        temporary_name = None
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        _fail(f"cannot atomically write release {label} {destination}: {exc}")
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name is not None:
            try:
                if directory_fd is None:
                    os.unlink(parent / temporary_name)
                else:
                    os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if directory_fd is not None:
            os.close(directory_fd)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_bytes_atomic(path, payload, label="JSON")


def _validated_sdist_member_name(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name:
        _fail(f"sdist contains an unsafe archive member name: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail(f"sdist contains an unsafe archive member name: {name!r}")
    if len(pure.parts) < 1:
        _fail(f"sdist contains an empty archive member name: {name!r}")
    return pure.parts


def canonicalize_sdist(*, sdist_path: Path, source_date_epoch: int) -> dict[str, Any]:
    """Normalize one built sdist into a deterministic, link-free tar.gz."""

    if not isinstance(source_date_epoch, int) or isinstance(source_date_epoch, bool):
        _fail("SOURCE_DATE_EPOCH must be an integer")
    if source_date_epoch < 0 or source_date_epoch > 0xFFFFFFFF:
        _fail("SOURCE_DATE_EPOCH must fit the gzip 32-bit timestamp field")
    if not sdist_path.name.endswith(".tar.gz"):
        _fail(f"sdist must use the .tar.gz extension: {sdist_path.name!r}")

    source_payload = _read_bounded_regular_file(
        sdist_path,
        label="sdist distribution",
        max_bytes=_MAX_DISTRIBUTION_BYTES,
    )
    normalized: list[tuple[str, bool, int, bytes]] = []
    seen: set[str] = set()
    roots: set[str] = set()
    expanded_size = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(source_payload), mode="r:gz") as source:
            members = source.getmembers()
            if not members or len(members) > _MAX_SDIST_MEMBERS:
                _fail(f"sdist must contain between 1 and {_MAX_SDIST_MEMBERS} archive members")
            for member in members:
                parts = _validated_sdist_member_name(member.name)
                roots.add(parts[0])
                logical_name = member.name.rstrip("/")
                if logical_name in seen:
                    _fail(f"sdist contains duplicate archive member {logical_name!r}")
                seen.add(logical_name)
                if not member.isdir() and not member.isfile():
                    _fail(f"sdist contains a link or special archive member: {member.name!r}")

                content = b""
                if member.isfile():
                    if member.size < 0 or member.size > _MAX_DISTRIBUTION_BYTES:
                        _fail(f"sdist member has an invalid or oversized payload: {member.name!r}")
                    expanded_size += member.size
                    if expanded_size > _MAX_SDIST_EXPANDED_BYTES:
                        _fail(f"sdist expanded payload exceeds the {_MAX_SDIST_EXPANDED_BYTES}-byte release limit")
                    extracted = source.extractfile(member)
                    if extracted is None:
                        _fail(f"cannot read sdist member: {member.name!r}")
                    content = extracted.read(member.size + 1)
                    if len(content) != member.size:
                        _fail(f"sdist member size differs from its tar header: {member.name!r}")

                canonical_name = logical_name + ("/" if member.isdir() else "")
                mode = 0o755 if member.isdir() or (member.mode & 0o111) else 0o644
                normalized.append((canonical_name, member.isdir(), mode, content))
    except (OSError, tarfile.TarError) as exc:
        _fail(f"cannot canonicalize sdist {sdist_path.name}: {exc}")

    if len(roots) != 1:
        _fail(f"sdist must contain exactly one top-level directory; found {sorted(roots)}")
    root = next(iter(roots))
    if root not in seen:
        _fail("sdist must explicitly contain its top-level directory entry")

    output = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=output,
        mtime=source_date_epoch,
    ) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as destination:
            for name, is_directory, mode, content in sorted(normalized, key=lambda item: item[0]):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE if is_directory else tarfile.REGTYPE
                member.mode = mode
                member.mtime = source_date_epoch
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.size = 0 if is_directory else len(content)
                destination.addfile(member, None if is_directory else io.BytesIO(content))

    payload = output.getvalue()
    if len(payload) > _MAX_DISTRIBUTION_BYTES:
        _fail(f"canonical sdist exceeds the {_MAX_DISTRIBUTION_BYTES}-byte release limit")
    _write_bytes_atomic(sdist_path, payload, label="sdist")
    verified = _read_bounded_regular_file(
        sdist_path,
        label="canonical sdist distribution",
        max_bytes=_MAX_DISTRIBUTION_BYTES,
    )
    if verified != payload:
        _fail("canonical sdist changed after atomic replacement")
    return {
        "filename": sdist_path.name,
        "sha256": _sha256_bytes(payload),
        "size": len(payload),
        "source_date_epoch": source_date_epoch,
    }


def prepare(*, dist_dir: Path, sbom_path: Path, manifest_path: Path, tag: str, commit: str) -> dict[str, Any]:
    """Bind the SBOM and write a manifest for the exact release files."""

    version = _validated_identity(tag, commit)
    artifacts = _collect_distributions(dist_dir, version=version)
    _bind_sbom(
        sbom_path,
        artifacts=artifacts,
        tag=tag,
        commit=commit,
        version=version,
    )
    _sbom_document, sbom_payload = _load_json_object_with_payload(
        sbom_path,
        label="SBOM",
        max_bytes=_MAX_SBOM_BYTES,
    )

    manifest = {
        "artifacts": artifacts,
        "commit": commit,
        "project": PROJECT,
        "publish_predicate": PUBLISH_PREDICATE,
        "sbom": {
            "filename": sbom_path.name,
            "sha256": _sha256_bytes(sbom_payload),
            "size": len(sbom_payload),
        },
        "schema": SCHEMA,
        "tag": tag,
        "version": version,
    }
    _write_json_atomic(manifest_path, manifest)
    verify(
        dist_dir=dist_dir,
        sbom_path=sbom_path,
        manifest_path=manifest_path,
        tag=tag,
        commit=commit,
    )
    return manifest


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    expected_keys = {
        "artifacts",
        "commit",
        "project",
        "publish_predicate",
        "sbom",
        "schema",
        "tag",
        "version",
    }
    if set(manifest) != expected_keys:
        _fail(f"manifest keys do not match the closed v1 schema: {sorted(manifest)}")
    if manifest.get("schema") != SCHEMA or manifest.get("project") != PROJECT:
        _fail("manifest schema or project identity is invalid")
    if manifest.get("publish_predicate") != PUBLISH_PREDICATE:
        _fail("manifest publish predicate is invalid")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        _fail("manifest must contain exactly two distribution records")
    expected_artifact_keys = {
        "filename",
        "kind",
        "provenance_url",
        "sha256",
        "size",
    }
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != expected_artifact_keys:
            _fail("manifest distribution record does not match the closed v1 schema")
        if artifact.get("kind") not in {"wheel", "sdist"}:
            _fail(f"manifest has unknown artifact kind: {artifact.get('kind')!r}")
        if _SAFE_FILENAME_RE.fullmatch(str(artifact.get("filename", ""))) is None:
            _fail("manifest contains an unsafe artifact filename")
        if _SHA256_RE.fullmatch(str(artifact.get("sha256", ""))) is None:
            _fail("manifest contains an invalid artifact SHA-256")
        if (
            not isinstance(artifact.get("size"), int)
            or isinstance(artifact["size"], bool)
            or artifact["size"] <= 0
            or artifact["size"] > _MAX_DISTRIBUTION_BYTES
        ):
            _fail("manifest contains an invalid artifact size")

    sbom = manifest.get("sbom")
    if not isinstance(sbom, dict) or set(sbom) != {"filename", "sha256", "size"}:
        _fail("manifest SBOM record does not match the closed v1 schema")
    if _SAFE_FILENAME_RE.fullmatch(str(sbom.get("filename", ""))) is None:
        _fail("manifest contains an unsafe SBOM filename")
    if _SHA256_RE.fullmatch(str(sbom.get("sha256", ""))) is None:
        _fail("manifest contains an invalid SBOM SHA-256")
    if (
        not isinstance(sbom.get("size"), int)
        or isinstance(sbom["size"], bool)
        or sbom["size"] <= 0
        or sbom["size"] > _MAX_SBOM_BYTES
    ):
        _fail("manifest contains an invalid SBOM size")


def _verified_manifest_distributions(
    *,
    dist_dir: Path,
    manifest_path: Path,
    tag: str,
    commit: str,
) -> dict[str, Any]:
    version = _validated_identity(tag, commit)
    manifest = _load_json_object(manifest_path, label="release manifest", max_bytes=_MAX_MANIFEST_BYTES)
    _validate_manifest_shape(manifest)
    if manifest["tag"] != tag or manifest["commit"] != commit:
        _fail("manifest tag or commit does not match the resolved release identity")
    if manifest["version"] != version or manifest["tag"] != f"v{version}":
        _fail("manifest version does not match its release tag")

    actual_artifacts = _collect_distributions(dist_dir, version=version)
    if manifest["artifacts"] != actual_artifacts:
        _fail("distribution filenames, metadata, sizes, or SHA-256 digests changed")
    return manifest


def _pypi_recovery_plan(
    *,
    manifest: dict[str, Any],
    http_status: int,
    response: dict[str, Any] | None,
) -> dict[str, Any]:
    """Accept only a byte-identical subset of the two manifested PyPI files."""

    expected = {artifact["filename"]: artifact for artifact in manifest["artifacts"]}
    existing: dict[str, dict[str, Any]] = {}
    if http_status == 404:
        if response is not None:
            _fail("a 404 PyPI recovery response must not be interpreted as JSON release state")
    elif http_status == 200:
        if not isinstance(response, dict):
            _fail("PyPI returned 200 without a JSON object")
        info = response.get("info")
        urls = response.get("urls")
        if not isinstance(info, dict) or info.get("version") != manifest["version"]:
            _fail("PyPI release identity does not match the manifest version")
        if not isinstance(urls, list):
            _fail("PyPI release JSON has no files array")

        for item in urls:
            if not isinstance(item, dict):
                _fail("PyPI release JSON contains a non-object file record")
            filename = item.get("filename")
            digests = item.get("digests")
            digest = digests.get("sha256") if isinstance(digests, dict) else None
            size = item.get("size")
            if (
                not isinstance(filename, str)
                or _SAFE_FILENAME_RE.fullmatch(filename) is None
                or _SHA256_RE.fullmatch(str(digest)) is None
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size <= 0
            ):
                _fail("PyPI release JSON contains an invalid filename, digest, or size")
            if filename in existing:
                _fail(f"PyPI release JSON duplicates filename {filename!r}")
            if filename not in expected:
                _fail(f"PyPI release contains an unmanifested file: {filename}")

            observed = {"filename": filename, "sha256": digest, "size": size}
            wanted = {
                "filename": filename,
                "sha256": expected[filename]["sha256"],
                "size": expected[filename]["size"],
            }
            if observed != wanted:
                _fail(f"PyPI file does not exactly match the release manifest: {filename}")
            existing[filename] = observed
    else:
        _fail(f"PyPI recovery requires a definitive HTTP 200 or 404; got {http_status}")

    existing_names = sorted(existing)
    missing_names = sorted(set(expected) - set(existing))
    return {
        "existing": existing_names,
        "missing": missing_names,
        "publish_required": bool(missing_names),
        "schema": RECOVERY_SCHEMA,
        "version": manifest["version"],
    }


def _create_fresh_directory(path: Path, *, label: str) -> Path:
    if _SAFE_FILENAME_RE.fullmatch(path.name) is None:
        _fail(f"{label} has an unsafe directory name: {path.name!r}")
    parent, parent_stat = _validated_real_directory(path.parent, label=label)
    absolute = parent / path.name
    directory_fd: int | None = None
    try:
        if os.name != "nt":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(parent, flags)
            if _file_identity(os.fstat(directory_fd)) != _file_identity(parent_stat):
                _fail(f"{label} parent directory changed while it was opened")
            os.mkdir(path.name, 0o700, dir_fd=directory_fd)
        else:
            if _file_identity(os.lstat(parent)) != _file_identity(parent_stat):
                _fail(f"{label} parent directory changed before creation")
            os.mkdir(absolute, 0o700)
    except FileExistsError:
        _fail(f"{label} must be a fresh directory: {absolute}")
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        _fail(f"cannot create {label} directory {absolute}: {exc}")
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    _validated_real_directory(absolute, label=label)
    return absolute


def _copy_distribution_exact(source: Path, destination: Path, *, expected: dict[str, Any]) -> None:
    """Copy one stable, bounded source snapshot into a fresh output file."""

    payload = _read_bounded_regular_file(
        source,
        label="recovery source distribution",
        max_bytes=_MAX_DISTRIBUTION_BYTES,
    )
    if _sha256_bytes(payload) != expected["sha256"] or len(payload) != expected["size"]:
        _fail(f"recovery source changed digest or size: {source.name}")
    output_parent, output_parent_stat = _validated_real_directory(
        destination.parent,
        label="recovery publish output",
    )
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    destination_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    destination_fd: int | None = None
    output_fd: int | None = None
    created = False
    try:
        if os.name != "nt":
            output_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
            output_flags |= getattr(os, "O_NOFOLLOW", 0)
            output_fd = os.open(output_parent, output_flags)
            if _file_identity(os.fstat(output_fd)) != _file_identity(output_parent_stat):
                _fail("recovery publish output changed while it was opened")
            destination_fd = os.open(destination.name, destination_flags, 0o600, dir_fd=output_fd)
        else:
            if _file_identity(os.lstat(output_parent)) != _file_identity(output_parent_stat):
                _fail("recovery publish output changed before file creation")
            destination_fd = os.open(output_parent / destination.name, destination_flags, 0o600)
        created = True

        _write_all(destination_fd, payload)
        if hasattr(os, "fchmod"):
            os.fchmod(destination_fd, 0o644)
        os.fsync(destination_fd)
        if output_fd is not None:
            os.fsync(output_fd)
    except FileExistsError:
        _fail(f"recovery publish output already contains {destination.name!r}")
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        _fail(f"cannot materialize recovery distribution {source.name}: {exc}")
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if created and sys.exc_info()[0] is not None:
            try:
                if output_fd is None:
                    os.unlink(output_parent / destination.name)
                else:
                    os.unlink(destination.name, dir_fd=output_fd)
            except FileNotFoundError:
                pass
        if output_fd is not None:
            os.close(output_fd)


def recover(
    *,
    dist_dir: Path,
    manifest_path: Path,
    pypi_response_path: Path,
    http_status: int,
    output_dir: Path,
    tag: str,
    commit: str,
) -> dict[str, Any]:
    """Materialize only files missing from an exact manifest subset on PyPI."""

    manifest = _verified_manifest_distributions(
        dist_dir=dist_dir,
        manifest_path=manifest_path,
        tag=tag,
        commit=commit,
    )
    response = (
        _load_json_object(
            pypi_response_path,
            label="PyPI recovery response",
            max_bytes=_MAX_PYPI_RESPONSE_BYTES,
        )
        if http_status == 200
        else None
    )
    plan = _pypi_recovery_plan(manifest=manifest, http_status=http_status, response=response)
    fresh_output = _create_fresh_directory(output_dir, label="recovery publish output")
    artifacts = {artifact["filename"]: artifact for artifact in manifest["artifacts"]}
    for filename in plan["missing"]:
        _copy_distribution_exact(
            dist_dir / filename,
            fresh_output / filename,
            expected=artifacts[filename],
        )

    entries = sorted(entry.name for entry in fresh_output.iterdir())
    if entries != plan["missing"]:
        _fail(f"recovery publish output differs from the missing manifest subset: {entries}")
    for filename in plan["missing"]:
        expected = artifacts[filename]
        actual = _artifact_record(
            fresh_output / filename,
            kind=expected["kind"],
            version=manifest["version"],
        )
        if actual != expected:
            _fail(f"recovery publish output changed after copy: {filename}")
    return plan


def _verify_sbom_bindings(document: dict[str, Any], *, manifest: dict[str, Any]) -> None:
    artifacts = manifest["artifacts"]
    wheel = _artifact_by_kind(artifacts, "wheel")
    sdist = _artifact_by_kind(artifacts, "sdist")
    components = _matching_components(document, version=manifest["version"])
    for component in components:
        hashes = component.get("hashes", [])
        sha256_hashes = [item for item in hashes if isinstance(item, dict) and item.get("alg") == "SHA-256"]
        if sha256_hashes != [{"alg": "SHA-256", "content": wheel["sha256"]}]:
            _fail("SBOM roam-code component is not bound to the wheel SHA-256")

    metadata = document.get("metadata")
    properties = metadata.get("properties") if isinstance(metadata, dict) else None
    if not isinstance(properties, list):
        _fail("SBOM metadata.properties is missing")

    expected = {
        "roam:release_manifest_schema": SCHEMA,
        "roam:release_tag": manifest["tag"],
        "roam:release_commit": manifest["commit"],
        "roam:release_wheel_filename": wheel["filename"],
        "roam:release_wheel_sha256": wheel["sha256"],
        "roam:release_wheel_provenance": wheel["provenance_url"],
        "roam:release_sdist_filename": sdist["filename"],
        "roam:release_sdist_sha256": sdist["sha256"],
        "roam:release_sdist_provenance": sdist["provenance_url"],
        "roam:published_wheel_filename": wheel["filename"],
        "roam:published_wheel_sha256": wheel["sha256"],
    }
    for name, value in expected.items():
        matches = [item.get("value") for item in properties if isinstance(item, dict) and item.get("name") == name]
        if matches != [value]:
            _fail(f"SBOM property {name!r} is missing, duplicated, or stale")


def verify(*, dist_dir: Path, sbom_path: Path, manifest_path: Path, tag: str, commit: str) -> dict[str, Any]:
    """Fail unless the manifest, distributions, and SBOM agree byte-for-byte."""

    manifest = _verified_manifest_distributions(
        dist_dir=dist_dir,
        manifest_path=manifest_path,
        tag=tag,
        commit=commit,
    )

    _validated_only_file_in_directory(sbom_path, label="SBOM")
    sbom_payload = _read_bounded_regular_file(
        sbom_path,
        label="SBOM",
        max_bytes=_MAX_SBOM_BYTES,
    )
    expected_sbom = manifest["sbom"]
    actual_sbom = {
        "filename": sbom_path.name,
        "sha256": _sha256_bytes(sbom_payload),
        "size": len(sbom_payload),
    }
    if expected_sbom != actual_sbom:
        _fail("SBOM filename, size, or SHA-256 digest changed")

    document = _decode_json_object(sbom_payload, path=sbom_path, label="SBOM")
    _verify_sbom_bindings(document, manifest=manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    canonicalize_parser = subparsers.add_parser("canonicalize-sdist")
    canonicalize_parser.add_argument("--sdist", type=Path, required=True)
    canonicalize_parser.add_argument("--source-date-epoch", type=int, required=True)
    for command in ("prepare", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--dist-dir", type=Path, required=True)
        subparser.add_argument("--sbom", type=Path, required=True)
        subparser.add_argument("--manifest", type=Path, required=True)
        subparser.add_argument("--tag", required=True)
        subparser.add_argument("--commit", required=True)

    recover_parser = subparsers.add_parser("recover")
    recover_parser.add_argument("--dist-dir", type=Path, required=True)
    recover_parser.add_argument("--manifest", type=Path, required=True)
    recover_parser.add_argument("--pypi-response", type=Path, required=True)
    recover_parser.add_argument("--http-status", type=int, choices=(200, 404), required=True)
    recover_parser.add_argument("--output-dir", type=Path, required=True)
    recover_parser.add_argument("--tag", required=True)
    recover_parser.add_argument("--commit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "canonicalize-sdist":
            result = canonicalize_sdist(
                sdist_path=args.sdist,
                source_date_epoch=args.source_date_epoch,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "recover":
            plan = recover(
                dist_dir=args.dist_dir,
                manifest_path=args.manifest,
                pypi_response_path=args.pypi_response,
                http_status=args.http_status,
                output_dir=args.output_dir,
                tag=args.tag,
                commit=args.commit,
            )
            print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
            return 0

        operation = prepare if args.command == "prepare" else verify
        manifest = operation(
            dist_dir=args.dist_dir,
            sbom_path=args.sbom,
            manifest_path=args.manifest,
            tag=args.tag,
            commit=args.commit,
        )
    except ReleaseArtifactError as exc:
        print(f"release artifact validation failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"release artifact validation passed: {manifest['tag']} "
        f"({len(manifest['artifacts'])} distributions, exact SBOM digest)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
