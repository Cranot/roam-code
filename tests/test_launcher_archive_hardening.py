"""Unsupported and damaged launcher archives stay unverified, without execution."""

from __future__ import annotations

import io
import struct
import sys
import zipfile

import pytest

from roam.runtime.launcher import inspect_launcher


def _archive(compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    output.write(b"MZ#!" + sys.executable.encode() + b"\n")
    with zipfile.ZipFile(output, "a", compression=compression) as archive:
        archive.writestr("__main__.py", "from roam.cli import cli\ncli()\n")
    return bytearray(output.getvalue())


def test_unsupported_zip_compression_is_unverified(tmp_path):
    data = _archive()
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<H", data, local + 8, 99)
    struct.pack_into("<H", data, central + 10, 99)
    path = tmp_path / "roam.exe"
    path.write_bytes(data)
    assert inspect_launcher(str(path), sys.executable)["state"] == "unverified"


@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_damaged_deflate_is_unverified(tmp_path, compression):
    data = _archive(compression)
    local = data.index(b"PK\x03\x04")
    name_length, extra_length = struct.unpack_from("<HH", data, local + 26)
    payload = local + 30 + name_length + extra_length
    data[payload + (4 if compression == zipfile.ZIP_LZMA else 0)] = 0xFF
    path = tmp_path / "roam.exe"
    path.write_bytes(data)
    assert inspect_launcher(str(path), sys.executable)["state"] == "unverified"


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_supported_archives_still_match(tmp_path, compression):
    path = tmp_path / "roam.exe"
    path.write_bytes(_archive(compression))
    assert inspect_launcher(str(path), sys.executable)["state"] == "matched"
