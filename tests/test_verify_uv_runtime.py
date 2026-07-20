from __future__ import annotations

import subprocess

import pytest

from scripts import verify_uv_runtime


@pytest.mark.parametrize(
    ("banner", "expected"),
    [
        ("uv 0.11.29\n", "0.11.29"),
        ("uv 0.11.29 (901092ee1 2026-07-15 x86_64-unknown-linux-gnu)\n", "0.11.29"),
        ("uv 0.11.29 (901092ee1 2026-07-15 x86_64-pc-windows-msvc)\r\n", "0.11.29"),
    ],
)
def test_parse_uv_version_accepts_the_pinned_binary_banners(banner: str, expected: str) -> None:
    assert verify_uv_runtime.parse_uv_version(banner) == expected


@pytest.mark.parametrize(
    "banner",
    [
        "",
        "uv 0.11.290\nextra\n",
        "uv 0.11.29\n\n",
        "uv 0.11.29 unbounded-metadata\n",
        "evil 0.11.29\n",
        "uv 0.11.29\x00\n",
        "uv 0.11.29 (" + ("x" * 300) + ")\n",
    ],
)
def test_parse_uv_version_rejects_malformed_or_ambiguous_banners(banner: str) -> None:
    with pytest.raises(ValueError):
        verify_uv_runtime.parse_uv_version(banner)


def test_verify_uv_runtime_accepts_build_metadata_and_checks_without_a_shell(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="uv 0.11.29 (901092ee1 2026-07-15 x86_64-unknown-linux-gnu)\n",
            stderr="",
        )

    monkeypatch.setattr(verify_uv_runtime.subprocess, "run", fake_run)

    assert verify_uv_runtime.verify_uv_runtime() == "0.11.29"
    assert observed["argv"] == ["uv", "--version"]
    assert observed["stdin"] is subprocess.DEVNULL
    assert "shell" not in observed
    assert observed["timeout"] == 10


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "message"),
    [
        (7, "", "", "status 7"),
        (0, "uv 0.11.28\n", "", "does not match"),
        (0, "uv 0.11.29\n", "warning\n", "emitted stderr"),
    ],
)
def test_verify_uv_runtime_rejects_failed_wrong_or_noisy_processes(
    monkeypatch,
    returncode: int,
    stdout: str,
    stderr: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        verify_uv_runtime.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr),
    )

    with pytest.raises(RuntimeError, match=message):
        verify_uv_runtime.verify_uv_runtime()
