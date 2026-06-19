"""Tests for ``roam index-export`` / ``roam index-import``.

The bundle format must be:

* Self-verifying (manifest sha256 of index.db must match)
* Schema-aware (refuses cross-version mismatch with a warning)
* HEAD-aware (warns if bundle exported at a different git HEAD)
* Refusal-by-default (won't overwrite existing .roam/index.db without --force)
* Round-trippable (export → import → identical SHA256)
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_index_bundle import (
    BUNDLE_FORMAT_VERSION,
    _build_manifest,
    _read_schema_version,
    _sha256_file,
    _verify_bundle,
)
from tests.conftest import make_src_project as _make_project

_FIXTURE = {
    "auth.py": """
        class UserSession:
            def refresh(self):
                return self.token

        def handle_login(user):
            return UserSession()
    """,
    "billing.py": """
        class Invoice:
            def total(self):
                return self.amount
    """,
}


@pytest.fixture
def indexed_project(tmp_path: Path):
    """A small repo with an indexed `.roam/index.db`."""
    proj = _make_project(tmp_path, _FIXTURE)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


class TestSha256File:
    def test_known_content(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello roam-code")
        h = _sha256_file(f)
        assert h == hashlib.sha256(b"hello roam-code").hexdigest()
        assert len(h) == 64

    def test_streaming_large_file(self, tmp_path):
        """Files larger than the 1MB chunk are still hashed correctly."""
        f = tmp_path / "big.bin"
        # 3MB so we exercise multiple chunks
        payload = b"X" * (3 * 1024 * 1024)
        f.write_bytes(payload)
        assert _sha256_file(f) == hashlib.sha256(payload).hexdigest()


class TestBuildManifest:
    def test_required_fields(self, indexed_project):
        index_path = Path(".roam/index.db")
        manifest = _build_manifest(index_path, Path.cwd())
        assert manifest["roam_version"]
        assert manifest["bundle_format_version"] == BUNDLE_FORMAT_VERSION
        assert manifest["index_db"]["name"] == "index.db"
        assert manifest["index_db"]["size_bytes"] == index_path.stat().st_size
        assert len(manifest["index_db"]["sha256"]) == 64
        assert "T" in manifest["exported_at"]  # ISO 8601 round-trip
        # schema_version is read from PRAGMA user_version — should be ≥ 0
        assert manifest["schema_version"] is None or manifest["schema_version"] >= 0


class TestReadSchemaVersion:
    def test_real_index(self, indexed_project):
        version = _read_schema_version(Path(".roam/index.db"))
        assert version is None or version >= 0

    def test_missing_file_returns_none(self, tmp_path):
        assert _read_schema_version(tmp_path / "nope.db") is None

    def test_malformed_sqlite_returns_none(self, tmp_path):
        bad_db = tmp_path / "bad.db"
        bad_db.write_text("not a sqlite database", encoding="utf-8")

        assert _read_schema_version(bad_db) is None

    def test_unexpected_errors_propagate(self, tmp_path, monkeypatch):
        index_path = tmp_path / "index.db"
        index_path.write_bytes(b"placeholder")

        def broken_connection(*args, **kwargs):
            raise RuntimeError("programmer bug")

        monkeypatch.setitem(_read_schema_version.__globals__, "get_connection", broken_connection)

        with pytest.raises(RuntimeError, match="programmer bug"):
            _read_schema_version(index_path)


# ---------------------------------------------------------------------------
# Round-trip: export → verify → import
# ---------------------------------------------------------------------------


class TestExportImportRoundTrip:
    def test_export_then_import_round_trip(self, indexed_project, tmp_path):
        """Export → wipe → import; the imported DB must match the bundle's
        manifest sha256 (export's snapshot of the DB).
        """
        runner = CliRunner()
        bundle = tmp_path / "out" / "trip.tar.gz"

        # 1. Export — manifest captures the sha256 at export time.
        result = runner.invoke(cli, ["index-export", str(bundle)])
        assert result.exit_code == 0, result.output
        assert bundle.exists()
        assert "VERDICT: Exported" in result.output
        # Pull the bundled manifest's sha256 — this is the authoritative
        # answer (some indexer paths touch the DB on every open, so reading
        # the SHA before vs. after export isn't a stable comparison).
        with tarfile.open(bundle, mode="r:*") as tar:
            mfh = tar.extractfile(tar.getmember("manifest.json"))
            assert mfh is not None
            bundled_sha = json.loads(mfh.read())["index_db"]["sha256"]

        # 2. Wipe the local index, then import
        Path(".roam/index.db").unlink()
        result = runner.invoke(cli, ["index-import", str(bundle)])
        assert result.exit_code == 0, result.output
        assert "VERDICT: Imported" in result.output

        # 3. The imported DB must match the manifest's recorded sha256.
        new_sha = _sha256_file(Path(".roam/index.db"))
        assert new_sha == bundled_sha, f"imported DB sha differs from bundled manifest sha: {new_sha} != {bundled_sha}"

    def test_export_emits_proper_manifest_inside_bundle(self, indexed_project, tmp_path):
        runner = CliRunner()
        bundle = tmp_path / "withmanifest.tar.gz"
        runner.invoke(cli, ["index-export", str(bundle)])
        with tarfile.open(bundle, mode="r:*") as tar:
            names = sorted(tar.getnames())
            assert "manifest.json" in names
            assert "index.db" in names
            mfh = tar.extractfile(tar.getmember("manifest.json"))
            assert mfh is not None
            manifest = json.loads(mfh.read())
            assert manifest["bundle_format_version"] == BUNDLE_FORMAT_VERSION
            assert "exported_at" in manifest
            assert manifest["index_db"]["name"] == "index.db"

    def test_default_extension_appended(self, indexed_project, tmp_path):
        """`roam index-export <name>` (no extension) writes `<name>.tar.gz`."""
        runner = CliRunner()
        bare = tmp_path / "bare-bundle"
        result = runner.invoke(cli, ["index-export", str(bare)])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "bare-bundle.tar.gz").exists()


# ---------------------------------------------------------------------------
# Verification & failure cases
# ---------------------------------------------------------------------------


class TestVerifyBundle:
    def _make_bundle(self, indexed_project, tmp_path) -> Path:
        runner = CliRunner()
        bundle = tmp_path / "v.tar.gz"
        runner.invoke(cli, ["index-export", str(bundle)])
        return bundle

    def test_clean_bundle_verifies(self, indexed_project, tmp_path):
        bundle = self._make_bundle(indexed_project, tmp_path)
        manifest = _verify_bundle(bundle)
        assert manifest["index_db"]["sha256"]

    def test_missing_bundle_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            _verify_bundle(tmp_path / "nope.tar.gz")

    def test_tampered_index_db_rejected(self, indexed_project, tmp_path):
        """Re-pack the bundle with a different index.db and the verifier
        must reject the size-or-sha mismatch.
        """
        bundle = self._make_bundle(indexed_project, tmp_path)
        tampered = tmp_path / "tampered.tar.gz"
        # Re-pack with a fake index.db.
        with tarfile.open(bundle, mode="r:*") as r:
            mfh = r.extractfile(r.getmember("manifest.json"))
            assert mfh is not None
            manifest_bytes = mfh.read()
        with tarfile.open(tampered, mode="w:gz") as w:
            # Write the unchanged manifest …
            mpath = tmp_path / "manifest.json"
            mpath.write_bytes(manifest_bytes)
            w.add(mpath, arcname="manifest.json")
            # … but a deliberately wrong index.db (different bytes).
            fake = tmp_path / "fake.db"
            fake.write_bytes(b"NOT A REAL SQLITE FILE")
            w.add(fake, arcname="index.db")
        with pytest.raises(ValueError, match="size mismatch|sha256 mismatch"):
            _verify_bundle(tampered)

    def test_missing_member_rejected(self, tmp_path):
        bad = tmp_path / "bad.tar.gz"
        with tarfile.open(bad, mode="w:gz") as w:
            stub = tmp_path / "stub.txt"
            stub.write_text("hi")
            w.add(stub, arcname="stub.txt")
        with pytest.raises(ValueError, match="missing required members"):
            _verify_bundle(bad)


class TestImportRefusal:
    def test_import_refuses_to_overwrite(self, indexed_project, tmp_path):
        runner = CliRunner()
        bundle = tmp_path / "x.tar.gz"
        runner.invoke(cli, ["index-export", str(bundle)])
        # .roam/index.db still exists; import without --force must refuse.
        result = runner.invoke(cli, ["index-import", str(bundle)])
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_force_flag_overwrites(self, indexed_project, tmp_path):
        runner = CliRunner()
        bundle = tmp_path / "x.tar.gz"
        runner.invoke(cli, ["index-export", str(bundle)])
        result = runner.invoke(cli, ["index-import", str(bundle), "--force"])
        assert result.exit_code == 0, result.output
        assert "VERDICT: Imported" in result.output

    def test_corrupted_bundle_clean_error(self, indexed_project, tmp_path):
        bad = tmp_path / "broken.tar.gz"
        bad.write_bytes(b"not a real tarball, just a poke")
        Path(".roam/index.db").unlink()
        runner = CliRunner()
        result = runner.invoke(cli, ["index-import", str(bad)])
        assert result.exit_code != 0
        assert "verification failed" in result.output or "Error" in result.output


class TestJSONEnvelope:
    def test_export_json_envelope(self, indexed_project, tmp_path):
        runner = CliRunner()
        bundle = tmp_path / "j.tar.gz"
        result = runner.invoke(cli, ["--json", "index-export", str(bundle)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "index-export"
        assert data["summary"]["verdict"].startswith("Wrote bundle")
        assert data["summary"]["bundle_path"] == str(bundle)
        assert data["summary"]["size_bytes"] > 0
        assert data["manifest"]["index_db"]["sha256"]

    def test_import_json_envelope(self, indexed_project, tmp_path):
        runner = CliRunner()
        bundle = tmp_path / "j2.tar.gz"
        runner.invoke(cli, ["index-export", str(bundle)])
        result = runner.invoke(cli, ["--json", "index-import", str(bundle), "--force"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "index-import"
        assert "Imported" in data["summary"]["verdict"]
        assert data["summary"]["extracted_to"] == ".roam/index.db" or data["summary"]["extracted_to"].replace(
            "\\", "/"
        ).endswith(".roam/index.db")
