"""Python builds without pidfd wrappers retain kernel-bound containment."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from roam.commands import cmd_service_report as mod

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd backend")


def _hide_stdlib_wrappers(monkeypatch):
    monkeypatch.setattr(os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", None, raising=False)


def test_stdlib_wrappers_absent_still_open_and_signal_owned_identity(monkeypatch):
    _hide_stdlib_wrappers(monkeypatch)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    identity = None
    try:
        identity = mod._linux_open_pidfd_identity(proc.pid)
        assert identity is not None and identity[0] == proc.pid
        assert os.get_inheritable(identity[2]) is False
        mod._linux_pidfd_send_signal(identity[2], signal.SIGTERM)
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert mod._linux_pidfd_is_alive(identity[2]) is False
    finally:
        if identity is not None:
            os.close(identity[2])
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_stdlib_wrappers_absent_still_produce_verified_boundary_receipt(tmp_path, monkeypatch):
    _hide_stdlib_wrappers(monkeypatch)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(mod.__file__).resolve().parents[2])
    proc = mod._start_component_process(
        [sys.executable, "-c", "print('contained', flush=True)"], cwd=str(tmp_path), env=env
    )
    out, err, state, cleaned, error = mod._capture_component_output(proc, timeout_seconds=5)
    assert (out, err, state, cleaned, error) == (b"contained\n", b"", "completed", True, None)


def test_native_wrappers_remain_preferred(monkeypatch):
    opener = lambda *_args: 123
    sender = lambda *_args: None
    monkeypatch.setattr(os, "pidfd_open", opener, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", sender, raising=False)

    def unavailable():
        raise AssertionError("native wrappers must not load libc")

    monkeypatch.setattr(mod, "_linux_libc_pidfd_api", unavailable)
    assert mod._linux_pidfd_opener() is opener
    assert mod._linux_pidfd_sender() is sender


@pytest.mark.parametrize("missing", ["open", "signal", "both"])
def test_missing_both_backends_refuses_before_launch(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(os, "pidfd_open", None if missing != "signal" else lambda *_: 123, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", None if missing != "open" else lambda *_: None, raising=False)
    monkeypatch.setattr(mod, "_linux_libc_pidfd_api", lambda: None)

    def forbidden_launch(*_args, **_kwargs):
        raise AssertionError("unavailable identity APIs must not launch a target")

    monkeypatch.setattr(subprocess, "Popen", forbidden_launch)
    with pytest.raises(RuntimeError, match="identity tracking is unavailable"):
        mod._start_component_process([sys.executable, "-c", "pass"], cwd=str(tmp_path), env=os.environ.copy())


def test_libc_preserves_kernel_errno(monkeypatch):
    _hide_stdlib_wrappers(monkeypatch)
    sender = mod._linux_pidfd_sender()
    assert sender is not None
    with pytest.raises(OSError) as caught:
        sender(-1, signal.SIGTERM, None, 0)
    assert caught.value.errno == errno.EBADF


def test_kernel_unavailable_never_falls_back_to_numeric_pid(monkeypatch):
    def unavailable(*_args):
        raise OSError(errno.ENOSYS, "pidfd unavailable")

    monkeypatch.setattr(mod, "_linux_pidfd_opener", lambda: unavailable)
    with pytest.raises(RuntimeError, match="process identity is unavailable") as caught:
        mod._linux_open_pidfd_identity(os.getpid())
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.ENOSYS
