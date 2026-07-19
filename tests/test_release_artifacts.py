from __future__ import annotations

import gzip
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import release_artifacts as release

VERSION = "13.10.0"
TAG = f"v{VERSION}"
COMMIT = "a" * 40


def _metadata(version: str = VERSION) -> bytes:
    return (f"Metadata-Version: 2.4\nName: roam-code\nVersion: {version}\nLicense-Expression: Apache-2.0\n\n").encode()


def _write_wheel(path: Path, *, version: str = VERSION) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr(f"roam_code-{version}.dist-info/METADATA", _metadata(version))


def _write_sdist(path: Path, *, version: str = VERSION) -> None:
    payload = _metadata(version)
    info = tarfile.TarInfo(f"roam_code-{version}/PKG-INFO")
    info.size = len(payload)
    with tarfile.open(path, mode="w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))


def _write_sdist_variant(path: Path, *, timestamp: int, reverse: bool) -> None:
    root = f"roam_code-{VERSION}"
    members = [
        (f"{root}/PKG-INFO", _metadata(), 0o600),
        (f"{root}/README.txt", b"same source bytes\n", 0o755),
    ]
    if reverse:
        members.reverse()
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="source-name.tar", mode="wb", fileobj=raw, mtime=timestamp) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                root_info = tarfile.TarInfo(f"{root}/")
                root_info.type = tarfile.DIRTYPE
                root_info.mode = 0o700
                root_info.mtime = timestamp
                archive.addfile(root_info)
                for name, payload, mode in members:
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    info.mode = mode
                    info.uid = timestamp
                    info.gid = timestamp
                    info.uname = "builder"
                    info.gname = "builder"
                    info.mtime = timestamp
                    archive.addfile(info, io.BytesIO(payload))


def _write_sbom(path: Path, *, version: str = VERSION) -> None:
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "metadata": {},
                "components": [
                    {
                        "type": "application",
                        "name": "roam-code",
                        "version": version,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def release_files(tmp_path: Path) -> dict[str, Path]:
    dist = tmp_path / "dist"
    sbom_dir = tmp_path / "sbom"
    manifest_dir = tmp_path / "release"
    dist.mkdir()
    sbom_dir.mkdir()
    manifest_dir.mkdir()

    wheel = dist / f"roam_code-{VERSION}-py3-none-any.whl"
    sdist = dist / f"roam_code-{VERSION}.tar.gz"
    sbom = sbom_dir / f"roam-code-{VERSION}.cdx.json"
    manifest = manifest_dir / "release-manifest.json"
    _write_wheel(wheel)
    _write_sdist(sdist)
    _write_sbom(sbom)
    return {
        "dist": dist,
        "wheel": wheel,
        "sdist": sdist,
        "sbom": sbom,
        "manifest": manifest,
    }


def _prepare(paths: dict[str, Path]) -> dict[str, object]:
    return release.prepare(
        dist_dir=paths["dist"],
        sbom_path=paths["sbom"],
        manifest_path=paths["manifest"],
        tag=TAG,
        commit=COMMIT,
    )


def _verify(paths: dict[str, Path]) -> dict[str, object]:
    return release.verify(
        dist_dir=paths["dist"],
        sbom_path=paths["sbom"],
        manifest_path=paths["manifest"],
        tag=TAG,
        commit=COMMIT,
    )


def _pypi_response(manifest: dict[str, object], *filenames: str) -> dict[str, object]:
    artifacts = {
        item["filename"]: item
        for item in manifest["artifacts"]  # type: ignore[index,union-attr]
    }
    return {
        "info": {"version": manifest["version"]},
        "urls": [
            {
                "filename": filename,
                "digests": {"sha256": artifacts[filename]["sha256"]},
                "size": artifacts[filename]["size"],
            }
            for filename in filenames
        ],
    }


def _recover(
    paths: dict[str, Path],
    *,
    status: int,
    response: dict[str, object] | None,
) -> tuple[dict[str, object], Path]:
    response_path = paths["manifest"].parent / "pypi-response.json"
    if response is not None:
        response_path.write_text(json.dumps(response), encoding="utf-8")
    output = paths["manifest"].parent / "publish-dist"
    plan = release.recover(
        dist_dir=paths["dist"],
        manifest_path=paths["manifest"],
        pypi_response_path=response_path,
        http_status=status,
        output_dir=output,
        tag=TAG,
        commit=COMMIT,
    )
    return plan, output


def test_prepare_binds_wheel_sdist_sbom_and_provenance_urls(
    release_files: dict[str, Path],
) -> None:
    manifest = _prepare(release_files)
    assert _verify(release_files) == manifest

    artifacts = {item["kind"]: item for item in manifest["artifacts"]}
    assert set(artifacts) == {"wheel", "sdist"}
    for artifact in artifacts.values():
        path = release_files[artifact["kind"]]
        assert artifact["size"] == path.stat().st_size
        assert artifact["provenance_url"].endswith(f"/{artifact['filename']}/provenance")

    sbom = json.loads(release_files["sbom"].read_text(encoding="utf-8"))
    properties = {item["name"]: item["value"] for item in sbom["metadata"]["properties"]}
    assert properties["roam:release_wheel_sha256"] == artifacts["wheel"]["sha256"]
    assert properties["roam:release_sdist_sha256"] == artifacts["sdist"]["sha256"]
    assert properties["roam:release_wheel_provenance"] == artifacts["wheel"]["provenance_url"]
    assert properties["roam:release_sdist_provenance"] == artifacts["sdist"]["provenance_url"]
    assert sbom["components"][0]["hashes"] == [{"alg": "SHA-256", "content": artifacts["wheel"]["sha256"]}]


def test_prepare_is_byte_idempotent_despite_randomized_atomic_temp_names(
    release_files: dict[str, Path],
) -> None:
    first = _prepare(release_files)
    first_manifest = release_files["manifest"].read_bytes()
    first_sbom = release_files["sbom"].read_bytes()

    second = _prepare(release_files)

    assert second == first
    assert release_files["manifest"].read_bytes() == first_manifest
    assert release_files["sbom"].read_bytes() == first_sbom


def test_canonicalize_sdist_closes_order_metadata_and_gzip_drift(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / f"roam_code-{VERSION}.tar.gz"
    second = second_dir / first.name
    _write_sdist_variant(first, timestamp=1_700_000_001, reverse=False)
    _write_sdist_variant(second, timestamp=1_800_000_002, reverse=True)
    assert first.read_bytes() != second.read_bytes()

    epoch = 1_784_240_433
    first_result = release.canonicalize_sdist(sdist_path=first, source_date_epoch=epoch)
    second_result = release.canonicalize_sdist(sdist_path=second, source_date_epoch=epoch)

    assert first_result == second_result
    assert first.read_bytes() == second.read_bytes()
    canonical = first.read_bytes()
    assert int.from_bytes(canonical[4:8], "little") == epoch
    release.canonicalize_sdist(sdist_path=first, source_date_epoch=epoch)
    assert first.read_bytes() == canonical

    with tarfile.open(first, mode="r:gz") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == sorted(member.name for member in members)
    assert all(member.mtime == epoch for member in members)
    assert all(member.uid == 0 and member.gid == 0 for member in members)
    assert all(member.uname == "" and member.gname == "" for member in members)
    assert all(
        member.mode == (0o755 if member.isdir() or member.name.endswith("README.txt") else 0o644) for member in members
    )


def test_canonicalize_sdist_rejects_link_members(tmp_path: Path) -> None:
    path = tmp_path / f"roam_code-{VERSION}.tar.gz"
    root = f"roam_code-{VERSION}"
    with tarfile.open(path, mode="w:gz") as archive:
        root_info = tarfile.TarInfo(f"{root}/")
        root_info.type = tarfile.DIRTYPE
        archive.addfile(root_info)
        link = tarfile.TarInfo(f"{root}/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../victim"
        archive.addfile(link)

    with pytest.raises(release.ReleaseArtifactError, match="link or special"):
        release.canonicalize_sdist(sdist_path=path, source_date_epoch=1_784_240_433)


@pytest.mark.parametrize("target", ["wheel", "sdist", "sbom"])
def test_verify_rejects_any_post_manifest_digest_change(release_files: dict[str, Path], target: str) -> None:
    _prepare(release_files)
    with release_files[target].open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(release.ReleaseArtifactError, match="changed"):
        _verify(release_files)


def test_prepare_rejects_unmanifested_distribution(
    release_files: dict[str, Path],
) -> None:
    (release_files["dist"] / "existing.whl.publish.attestation").write_text("stale", encoding="utf-8")
    with pytest.raises(release.ReleaseArtifactError, match="outside the publish set"):
        _prepare(release_files)


def test_prepare_rejects_unmanifested_sbom_sibling(
    release_files: dict[str, Path],
) -> None:
    (release_files["sbom"].parent / "stale.cdx.json").write_text("{}", encoding="utf-8")
    with pytest.raises(release.ReleaseArtifactError, match="must contain only"):
        _prepare(release_files)


def test_prepare_rejects_tag_input_injection_before_writing_manifest(
    release_files: dict[str, Path],
) -> None:
    with pytest.raises(release.ReleaseArtifactError, match="constrained PEP 440"):
        release.prepare(
            dist_dir=release_files["dist"],
            sbom_path=release_files["sbom"],
            manifest_path=release_files["manifest"],
            tag="v13.9.0\nsha=attacker",
            commit=COMMIT,
        )
    assert not release_files["manifest"].exists()


def test_verify_rejects_duplicate_manifest_object_keys(
    release_files: dict[str, Path],
) -> None:
    _prepare(release_files)
    manifest = release_files["manifest"]
    raw = manifest.read_text(encoding="utf-8")
    manifest.write_text(raw.replace('  "schema":', '  "schema": "attacker",\n  "schema":', 1), encoding="utf-8")

    with pytest.raises(release.ReleaseArtifactError, match="duplicate JSON object key"):
        _verify(release_files)


def test_json_reader_rejects_oversized_and_hard_linked_inputs(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b'{"value":"12345"}')
    with pytest.raises(release.ReleaseArtifactError, match="input limit"):
        release._load_json_object(oversized, label="fixture", max_bytes=8)

    original = tmp_path / "original.json"
    linked = tmp_path / "linked.json"
    original.write_text("{}", encoding="utf-8")
    try:
        os.link(original, linked)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard links are unavailable: {exc}")
    with pytest.raises(release.ReleaseArtifactError, match="hard-linked"):
        release._load_json_object(linked, label="fixture", max_bytes=1024)


@pytest.mark.parametrize("target", ["wheel", "sdist"])
def test_prepare_rejects_hard_linked_distributions(
    release_files: dict[str, Path],
    target: str,
) -> None:
    artifact = release_files[target]
    external = artifact.parent.parent / f"external-{artifact.name}"
    artifact.replace(external)
    try:
        os.link(external, artifact)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(release.ReleaseArtifactError, match="hard-linked"):
        _prepare(release_files)


def test_prepare_rejects_sdist_version_drift(
    release_files: dict[str, Path],
) -> None:
    release_files["sdist"].unlink()
    _write_sdist(release_files["sdist"], version="13.8.0")
    with pytest.raises(release.ReleaseArtifactError, match="does not match tag version"):
        _prepare(release_files)


def test_recovery_materializes_only_the_missing_exact_subset(
    release_files: dict[str, Path],
) -> None:
    manifest = _prepare(release_files)
    wheel_name = next(item["filename"] for item in manifest["artifacts"] if item["kind"] == "wheel")
    sdist_name = next(item["filename"] for item in manifest["artifacts"] if item["kind"] == "sdist")

    plan, output = _recover(
        release_files,
        status=200,
        response=_pypi_response(manifest, wheel_name),
    )

    assert plan == {
        "existing": [wheel_name],
        "missing": [sdist_name],
        "publish_required": True,
        "schema": release.RECOVERY_SCHEMA,
        "version": VERSION,
    }
    assert [path.name for path in output.iterdir()] == [sdist_name]
    assert (output / sdist_name).read_bytes() == release_files["sdist"].read_bytes()


def test_recovery_404_materializes_both_manifested_files(
    release_files: dict[str, Path],
) -> None:
    manifest = _prepare(release_files)
    expected = sorted(item["filename"] for item in manifest["artifacts"])

    plan, output = _recover(release_files, status=404, response=None)

    assert plan["existing"] == []
    assert plan["missing"] == expected
    assert plan["publish_required"] is True
    assert sorted(path.name for path in output.iterdir()) == expected


def test_recovery_all_exact_files_is_a_safe_noop(
    release_files: dict[str, Path],
) -> None:
    manifest = _prepare(release_files)
    filenames = sorted(item["filename"] for item in manifest["artifacts"])

    plan, output = _recover(
        release_files,
        status=200,
        response=_pypi_response(manifest, *filenames),
    )

    assert plan["existing"] == filenames
    assert plan["missing"] == []
    assert plan["publish_required"] is False
    assert list(output.iterdir()) == []


def test_recovery_cli_emits_only_the_closed_plan_json(
    release_files: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _prepare(release_files)
    wheel_name = next(item["filename"] for item in manifest["artifacts"] if item["kind"] == "wheel")
    response_path = release_files["manifest"].parent / "pypi-response.json"
    response_path.write_text(json.dumps(_pypi_response(manifest, wheel_name)), encoding="utf-8")
    output = release_files["manifest"].parent / "publish-dist"

    result = release.main(
        [
            "recover",
            "--dist-dir",
            str(release_files["dist"]),
            "--manifest",
            str(release_files["manifest"]),
            "--pypi-response",
            str(response_path),
            "--http-status",
            "200",
            "--output-dir",
            str(output),
            "--tag",
            TAG,
            "--commit",
            COMMIT,
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    plan = json.loads(captured.out)
    assert set(plan) == {"existing", "missing", "publish_required", "schema", "version"}
    assert plan["existing"] == [wheel_name]
    assert plan["publish_required"] is True


@pytest.mark.parametrize("mutation", ["digest", "size", "extra", "duplicate"])
def test_recovery_rejects_every_non_subset_pypi_state_before_copying(
    release_files: dict[str, Path],
    mutation: str,
) -> None:
    manifest = _prepare(release_files)
    wheel_name = next(item["filename"] for item in manifest["artifacts"] if item["kind"] == "wheel")
    response = _pypi_response(manifest, wheel_name)
    record = response["urls"][0]  # type: ignore[index]
    if mutation == "digest":
        record["digests"]["sha256"] = "0" * 64  # type: ignore[index]
    elif mutation == "size":
        record["size"] += 1  # type: ignore[operator]
    elif mutation == "extra":
        record["filename"] = "unmanifested.whl"
    else:
        response["urls"].append(dict(record))  # type: ignore[union-attr]

    with pytest.raises(release.ReleaseArtifactError, match="PyPI"):
        _recover(release_files, status=200, response=response)
    assert not (release_files["manifest"].parent / "publish-dist").exists()


def test_recovery_rejects_duplicate_nested_pypi_json_keys(
    release_files: dict[str, Path],
) -> None:
    manifest = _prepare(release_files)
    wheel = next(item for item in manifest["artifacts"] if item["kind"] == "wheel")
    response_path = release_files["manifest"].parent / "pypi-response.json"
    response_path.write_text(
        json.dumps(
            {
                "info": {"version": manifest["version"]},
                "urls": [
                    {
                        "filename": wheel["filename"],
                        "digests": {"sha256": wheel["sha256"]},
                        "size": wheel["size"],
                    }
                ],
            }
        ).replace(
            f'"filename": "{wheel["filename"]}"',
            f'"filename": "attacker.whl", "filename": "{wheel["filename"]}"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(release.ReleaseArtifactError, match="duplicate JSON object key"):
        release.recover(
            dist_dir=release_files["dist"],
            manifest_path=release_files["manifest"],
            pypi_response_path=response_path,
            http_status=200,
            output_dir=release_files["manifest"].parent / "publish-dist",
            tag=TAG,
            commit=COMMIT,
        )


def test_recovery_requires_a_fresh_output_directory(
    release_files: dict[str, Path],
) -> None:
    _prepare(release_files)
    output = release_files["manifest"].parent / "publish-dist"
    output.mkdir()
    (output / "stale.whl").write_bytes(b"stale")

    with pytest.raises(release.ReleaseArtifactError, match="fresh directory"):
        _recover(release_files, status=404, response=None)
    assert (output / "stale.whl").read_bytes() == b"stale"


def test_atomic_json_write_uses_exclusive_random_name_without_following_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "release-manifest.json"
    victim = tmp_path / "victim.json"
    victim.write_text("preserve me", encoding="utf-8")
    collision = tmp_path / f".{destination.name}.{'1' * 32}.tmp"
    try:
        collision.symlink_to(victim)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    tokens = iter(("1" * 32, "2" * 32))
    monkeypatch.setattr(release.secrets, "token_hex", lambda _size: next(tokens))

    release._write_json_atomic(destination, {"safe": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"safe": True}
    assert victim.read_text(encoding="utf-8") == "preserve me"
    assert collision.is_symlink()
    assert not (tmp_path / f".{destination.name}.{'2' * 32}.tmp").exists()


def test_atomic_json_write_retries_an_existing_regular_temp_name_without_overwriting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "release-manifest.json"
    collision = tmp_path / f".{destination.name}.{'3' * 32}.tmp"
    collision.write_text("owned by another writer", encoding="utf-8")
    tokens = iter(("3" * 32, "4" * 32))
    monkeypatch.setattr(release.secrets, "token_hex", lambda _size: next(tokens))

    release._write_json_atomic(destination, {"safe": True})

    assert collision.read_text(encoding="utf-8") == "owned by another writer"
    assert json.loads(destination.read_text(encoding="utf-8")) == {"safe": True}
    assert not (tmp_path / f".{destination.name}.{'4' * 32}.tmp").exists()


def test_atomic_json_write_rejects_symlink_destination_without_touching_target(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "release-manifest.json"
    victim = tmp_path / "victim.json"
    victim.write_text("preserve me", encoding="utf-8")
    try:
        destination.symlink_to(victim)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(release.ReleaseArtifactError, match="absent or a regular, non-symlink"):
        release._write_json_atomic(destination, {"unsafe": False})
    assert victim.read_text(encoding="utf-8") == "preserve me"
    assert destination.is_symlink()


def test_atomic_json_write_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(release.ReleaseArtifactError, match="must not traverse"):
        release._write_json_atomic(linked_parent / "release-manifest.json", {"unsafe": False})
    assert list(real_parent.iterdir()) == []


def test_atomic_json_write_cleans_only_its_reserved_temp_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "release-manifest.json"
    token = "a" * 32
    monkeypatch.setattr(release.secrets, "token_hex", lambda _size: token)
    monkeypatch.setattr(release.os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("race")))

    with pytest.raises(release.ReleaseArtifactError, match="cannot atomically write"):
        release._write_json_atomic(destination, {"safe": True})
    assert not destination.exists()
    assert not (tmp_path / f".{destination.name}.{token}.tmp").exists()
