"""Read supported Python launcher bindings without executing PATH entries.

This is provenance metadata, not authentication of executable code. Unknown
wrappers remain unverified. In particular, directory proximity proves nothing.
"""

from __future__ import annotations

import io
import os
import re
import struct
import zipfile
from pathlib import Path


def _uv_python_path(data: bytes) -> str | None:
    """Read uv's UTF-8 UV_PYTHON_PATH RCDATA from bounded PE directories.

    Format source: astral-sh/uv, crates/uv-trampoline/src/bounce.rs.
    No native library is loaded and no code from the file is executed.
    """

    def u16(offset):
        return struct.unpack_from("<H", data, offset)[0]

    def u32(offset):
        return struct.unpack_from("<I", data, offset)[0]

    pe = u32(0x3C)
    if data[pe : pe + 4] != b"PE\0\0":
        return None
    optional = pe + 24
    magic = u16(optional)
    if magic not in (0x10B, 0x20B):
        return None
    directory = optional + (96 if magic == 0x10B else 112)
    sections = optional + u16(pe + 20)

    def offset_for(rva):
        for i in range(min(u16(pe + 6), 96)):
            section = sections + i * 40
            size, address, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
            if address <= rva < address + min(size, raw_size):
                return raw + rva - address
        raise ValueError("resource address outside sections")

    base = offset_for(u32(directory + 16))

    def entries(relative):
        node = base + relative
        count = u16(node + 12) + u16(node + 14)
        if count > 256:
            raise ValueError("resource directory too large")
        for i in range(count):
            name, value = struct.unpack_from("<II", data, node + 16 + i * 8)
            if name & 0x80000000:
                at = base + (name & 0x7FFFFFFF)
                length = u16(at)
                if length > 256:
                    raise ValueError("resource name too large")
                name = data[at + 2 : at + 2 + length * 2].decode("utf-16-le")
            yield name, value

    for kind, branch in entries(0):
        if kind != 10 or not branch & 0x80000000:
            continue
        for name, languages in entries(branch & 0x7FFFFFFF):
            if name != "UV_PYTHON_PATH" or not languages & 0x80000000:
                continue
            values = list(entries(languages & 0x7FFFFFFF))
            if len(values) != 1 or values[0][1] & 0x80000000:
                return None
            resource = base + values[0][1]
            size = u32(resource + 4)
            at = offset_for(u32(resource))
            if not 0 < size <= 32768 or at + size > len(data):
                return None
            return data[at : at + size].decode("utf-8")
    return None


def _shebang(script: str) -> str | None:
    if script.startswith("#!/bin/sh\n'''exec' "):
        match = re.match(r"#!/bin/sh\n'''exec' (.+) \"\$0\" \"\$@\"\n' '''", script)
        line = match[1] if match else ""
    elif script.startswith("#!"):
        line = script.splitlines()[0][2:].strip()
    else:
        return None
    # Keep Windows backslashes and spaces inside quoted interpreter paths.
    match = re.fullmatch(r'"([^"\r\n]+)"|([^\s"\r\n]+)', line)
    return (match[1] or match[2]) if match else None


def inspect_launcher(on_path: str | None, interpreter: str) -> dict:
    """Return matched/different_interpreter/unverified/missing with evidence."""
    result = {"path": on_path, "state": "unverified", "evidence": "unsupported launcher"}
    if not on_path:
        return {**result, "state": "missing", "evidence": "roam is not on PATH"}
    try:
        path = Path(on_path)
        if not path.is_file():
            return {**result, "evidence": "launcher is not a regular file"}
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            return {**result, "evidence": "launcher exceeds inspection limit"}
        binding = None
        if data.startswith(b"MZ"):
            try:
                binding = _uv_python_path(data)
            except (struct.error, ValueError, UnicodeError):
                binding = None
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                main = archive.getinfo("__main__.py")
                if main.file_size > 65536:
                    return result
                try:
                    script = archive.read(main).decode("utf-8")
                except Exception:  # noqa: BLE001 — untrusted compressed payload; no execution or success fallback
                    return {**result, "evidence": "launcher archive payload could not be decoded"}
                if binding is None:
                    # distlib appends a shebang immediately before its zip.
                    prefix = data[: min(info.header_offset for info in archive.infolist())]
                    _, marker, tail = prefix.rpartition(b"#!")
                    if marker:
                        binding = _shebang((marker + tail).decode("utf-8"))
            evidence = "embedded Python binding"
        else:
            script = data.decode("utf-8")
            binding = _shebang(script)
            evidence = "script interpreter binding"
        if "from roam.cli import cli" not in script or not binding or "\0" in binding:
            return result
        target = Path(binding)
        if not target.is_absolute():
            # uv permits relative bindings, interpreted from the launcher path.
            if data.startswith(b"MZ"):
                target = path.parent / target
            else:
                return result
        # Resolve directory aliases but retain the executable name. Resolving
        # POSIX venv Python symlinks would merge distinct project environments.
        actual = os.path.normcase(str(target.parent.resolve() / target.name))
        expected_path = Path(interpreter)
        expected = os.path.normcase(str(expected_path.parent.resolve() / expected_path.name))
        state = "matched" if actual == expected else "different_interpreter"
        return {**result, "state": state, "interpreter": str(target), "evidence": evidence}
    except (
        OSError,
        RuntimeError,
        ValueError,
        UnicodeError,
        KeyError,
        EOFError,
        zipfile.BadZipFile,
        struct.error,
    ):
        return {**result, "evidence": "launcher binding could not be read"}
