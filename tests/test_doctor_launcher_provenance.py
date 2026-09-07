"""Read launcher bindings instead of inferring them from directory layout."""

from __future__ import annotations

import io
import struct
import sys
import zipfile

import pytest

from roam.commands import cmd_doctor

BODY = "import sys\nfrom roam.cli import cli\nsys.exit(cli())\n"


def uv_pe(interpreter):
    """Minimal PE resource directories matching uv's documented binding."""
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x86, 1)  # one section
    struct.pack_into("<H", data, 0x94, 224)  # PE32 optional header
    struct.pack_into("<H", data, 0x98, 0x10B)
    struct.pack_into("<II", data, 0x98 + 112, 0x1000, 512)  # resources
    struct.pack_into("<IIII", data, 0x178 + 8, 512, 0x1000, 512, 512)
    base = 512
    struct.pack_into("<H", data, base + 14, 1)
    struct.pack_into("<II", data, base + 16, 10, 0x80000020)
    struct.pack_into("<H", data, base + 32 + 12, 1)
    struct.pack_into("<II", data, base + 48, 0x80000080, 0x80000040)
    struct.pack_into("<H", data, base + 64 + 14, 1)
    struct.pack_into("<II", data, base + 80, 1033, 96)
    encoded = str(interpreter).encode()
    struct.pack_into("<II", data, base + 96, 0x1100, len(encoded))
    name = "UV_PYTHON_PATH"
    struct.pack_into("<H", data, base + 128, len(name))
    data[base + 130 : base + 130 + len(name) * 2] = name.encode("utf-16-le")
    data[base + 256 : base + 256 + len(encoded)] = encoded
    return bytes(data)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    interpreter = tmp_path / "environment/python"
    interpreter.parent.mkdir()
    interpreter.touch()
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f'python-version: ["{sys.version_info[0]}.{sys.version_info[1]}"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cmd_doctor.sys, "executable", str(interpreter))
    return tmp_path, interpreter


@pytest.mark.parametrize("style", ["posix", "quoted", "distlib", "uv", "shell"])
def test_redirected_launcher_binding_is_accepted(environment, monkeypatch, style):
    root, interpreter = environment
    launcher = root / "bin/roam"
    launcher.parent.mkdir()
    command = str(interpreter)
    if style == "quoted":
        command = f'"{interpreter}"'
    content = f"#!{command}\n{BODY}".encode()
    if style == "shell":
        content = (f"#!/bin/sh\n'''exec' \"{interpreter}\" \"$0\" \"$@\"\n' '''\n" + BODY).encode()
    if style in {"distlib", "uv"}:
        buffer = io.BytesIO()
        # Model the two supported launcher metadata layouts; never execute it.
        buffer.write(b"MZ" + f"#!{command}\n".encode() if style == "distlib" else uv_pe(interpreter))
        with zipfile.ZipFile(buffer, "a") as archive:
            archive.writestr("__main__.py", content)
        content = buffer.getvalue()
    launcher.write_bytes(content)
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: str(launcher))
    result = cmd_doctor._check_ci_environment_parity()
    assert result["passed"] is True, result
    assert result["launcher"]["state"] == "matched"


def test_same_directory_is_not_proof_of_binding(environment, monkeypatch):
    root, interpreter = environment
    launcher = interpreter.parent / "roam"
    launcher.write_text(f"#!{root / 'other/python'}\n{BODY}", encoding="utf-8")
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: str(launcher))
    result = cmd_doctor._check_ci_environment_parity()
    assert result["passed"] is False
    assert result["launcher"]["state"] == "different_interpreter"


@pytest.mark.parametrize("kind", ["unknown", "missing", "env", "unreadable"])
def test_unverifiable_launcher_does_not_claim_a_match(environment, monkeypatch, kind):
    root, interpreter = environment
    launcher = interpreter.parent / "roam"
    if kind == "unknown":
        launcher.write_text("unknown wrapper\n", encoding="utf-8")
    elif kind == "env":
        launcher.write_text("#!/usr/bin/env python\n" + BODY, encoding="utf-8")
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: None if kind == "missing" else str(launcher))
    result = cmd_doctor._check_ci_environment_parity()
    assert result["passed"] is False
    assert result["launcher"]["state"] in {"unverified", "missing"}
    assert "not the one" not in result["detail"]


def test_uv_binding_cannot_be_overridden_by_zip_shebang(environment, monkeypatch):
    root, interpreter = environment
    launcher = root / "roam.exe"
    buffer = io.BytesIO()
    buffer.write(uv_pe(root / "other/python"))
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr("__main__.py", f"#!{interpreter}\n{BODY}")
    launcher.write_bytes(buffer.getvalue())
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: str(launcher))
    result = cmd_doctor._check_ci_environment_parity()
    assert result["launcher"]["state"] == "different_interpreter"


@pytest.mark.parametrize(
    "data",
    [b"MZ", b"MZ" + b"\xff" * 256, b"#!\xff\n", b"x" * (1024 * 1024 + 1)],
    ids=["short_pe", "bad_pe", "bad_encoding", "oversized"],
)
def test_malformed_or_large_launchers_remain_unverified(environment, monkeypatch, data):
    root, interpreter = environment
    launcher = root / "roam"
    launcher.write_bytes(data)
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: str(launcher))
    result = cmd_doctor._check_ci_environment_parity()
    assert result["passed"] is False
    assert result["launcher"]["state"] == "unverified"


def test_non_file_launcher_is_rejected_before_content_reads(environment, monkeypatch):
    from pathlib import Path

    root, interpreter = environment
    launcher = root / "not-a-file"
    launcher.mkdir()
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path != launcher, "non-regular launcher content must not be opened"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: str(launcher))
    result = cmd_doctor._check_ci_environment_parity()
    assert result["launcher"]["state"] == "unverified"
