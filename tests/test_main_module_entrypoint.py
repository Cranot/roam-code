"""Tests for src/roam/__main__.py — the `python -m roam` entrypoint.

The module is tiny but load-bearing: it wires `from roam.cli import cli` to the
`python -m roam` invocation and installs the graceful-Ctrl-C handler. Its three
observable behaviors:

1. It calls `roam.cli.cli()` when run as a module.
2. A `KeyboardInterrupt` raised by `cli()` is converted to a clean exit 130 with
   an "Interrupted" notice on stderr — NOT a traceback.
3. Any other outcome (normal return, `SystemExit`, other exceptions) is passed
   through unchanged — only `KeyboardInterrupt` is special-cased.

The handler is exercised via `runpy.run_module` with `roam.cli.cli` monkeypatched,
so the exact `try/except KeyboardInterrupt` branch runs deterministically (a real
SIGINT race would be flaky). A subprocess smoke test covers the real wiring.
"""

from __future__ import annotations

import runpy
import subprocess
import sys

import pytest

import roam.cli


def _run_main():
    """Execute roam/__main__.py with run_name='__main__', as `python -m roam` does."""
    return runpy.run_module("roam", run_name="__main__", alter_sys=True)


class TestKeyboardInterruptHandling:
    def test_keyboard_interrupt_exits_130(self, monkeypatch):
        def fake_cli():
            raise KeyboardInterrupt

        monkeypatch.setattr(roam.cli, "cli", fake_cli)
        with pytest.raises(SystemExit) as exc:
            _run_main()
        assert exc.value.code == 130

    def test_keyboard_interrupt_writes_notice_to_stderr(self, monkeypatch, capsys):
        def fake_cli():
            raise KeyboardInterrupt

        monkeypatch.setattr(roam.cli, "cli", fake_cli)
        with pytest.raises(SystemExit):
            _run_main()
        captured = capsys.readouterr()
        # The notice goes to stderr, not stdout.
        assert "Interrupted (Ctrl-C)" in captured.err
        assert "rerun the command" in captured.err
        assert captured.out == ""

    def test_keyboard_interrupt_notice_starts_with_newline(self, monkeypatch, capsys):
        def fake_cli():
            raise KeyboardInterrupt

        monkeypatch.setattr(roam.cli, "cli", fake_cli)
        with pytest.raises(SystemExit):
            _run_main()
        # Leading "\n" separates the notice from any partial in-progress output.
        assert capsys.readouterr().err.startswith("\n")


class TestPassThroughBehavior:
    def test_normal_return_does_not_exit(self, monkeypatch, capsys):
        called = []

        def fake_cli():
            called.append(True)

        monkeypatch.setattr(roam.cli, "cli", fake_cli)
        # A clean return must NOT be converted into a SystemExit, and must emit
        # no Ctrl-C notice.
        _run_main()
        assert called == [True]
        assert "Interrupted" not in capsys.readouterr().err

    @pytest.mark.parametrize("code", [0, 2, 5])
    def test_system_exit_propagates_unchanged(self, monkeypatch, code):
        def fake_cli():
            raise SystemExit(code)

        monkeypatch.setattr(roam.cli, "cli", fake_cli)
        # Click exits via SystemExit on success and on usage errors; the wrapper
        # must not rewrite those codes to 130.
        with pytest.raises(SystemExit) as exc:
            _run_main()
        assert exc.value.code == code

    def test_other_exceptions_are_not_swallowed(self, monkeypatch):
        sentinel = RuntimeError("boom")

        def fake_cli():
            raise sentinel

        monkeypatch.setattr(roam.cli, "cli", fake_cli)
        # Only KeyboardInterrupt is special-cased; everything else surfaces so
        # real failures are not masked as a graceful exit.
        with pytest.raises(RuntimeError) as exc:
            _run_main()
        assert exc.value is sentinel


class TestRealEntrypointWiring:
    def test_python_dash_m_roam_help_exits_zero(self):
        # End-to-end: confirms `from roam.cli import cli` resolves and the module
        # is runnable as a real subprocess (no import-time crash).
        result = subprocess.run(
            [sys.executable, "-m", "roam", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Usage" in result.stdout
