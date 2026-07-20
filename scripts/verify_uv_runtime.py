#!/usr/bin/env python3
"""Fail closed unless the active uv binary has the release-pinned version."""

from __future__ import annotations

import re
import subprocess
import sys

EXPECTED_UV_VERSION = "0.11.29"
_MAX_OUTPUT_CHARS = 512
_UV_VERSION_OUTPUT = re.compile(r"uv (?P<version>[0-9]+[.][0-9]+[.][0-9]+)(?: [(][ -~]{1,256}[)])?")


def parse_uv_version(output: str) -> str:
    """Return the semantic version from one bounded, single-line uv banner."""
    if not output or len(output) > _MAX_OUTPUT_CHARS:
        raise ValueError("uv --version emitted empty or oversized output")
    if output.endswith("\r\n"):
        banner = output[:-2]
    elif output.endswith("\n"):
        banner = output[:-1]
    else:
        banner = output
    if not banner or "\r" in banner or "\n" in banner:
        raise ValueError("uv --version must emit exactly one line")
    match = _UV_VERSION_OUTPUT.fullmatch(banner)
    if match is None:
        raise ValueError(f"uv --version emitted an unexpected banner: {banner!r}")
    return match.group("version")


def verify_uv_runtime() -> str:
    """Execute uv without a shell and prove its semantic version token."""
    completed = subprocess.run(
        ["uv", "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"uv --version exited with status {completed.returncode}")
    if completed.stderr:
        bounded = completed.stderr[:_MAX_OUTPUT_CHARS]
        raise RuntimeError(f"uv --version emitted stderr: {bounded!r}")
    actual = parse_uv_version(completed.stdout)
    if actual != EXPECTED_UV_VERSION:
        raise RuntimeError(f"uv version {actual} does not match the release pin {EXPECTED_UV_VERSION}")
    return actual


def main() -> int:
    if len(sys.argv) != 1:
        print("usage: verify_uv_runtime.py", file=sys.stderr)
        return 2
    try:
        actual = verify_uv_runtime()
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"uv runtime verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"verified uv {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
