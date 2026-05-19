"""W1078: --json mode must never leak warnings to stdout.

A fresh-install audit found that `roam --json health` could leak a numpy
`RuntimeWarning` (transitive via networkx pagerank/spectral paths) onto
stdout before the JSON envelope, breaking `json.load(stdin)` pipelines.

Pattern caught: a structured-output mode (--json / --sarif / --agent)
allowing ANY non-JSON bytes to reach stdout. The fix installs an explicit
`warnings.showwarning -> sys.stderr` override in the CLI entrypoint
under those modes (`src/roam/cli.py`). This test pins the contract: any
--json invocation must produce a stdout that `json.loads()` accepts,
regardless of whether warnings fire underneath.

The test invokes `roam --json` as a subprocess (so the CLI entrypoint
actually runs and installs the override) and parses stdout. It does NOT
assert that stderr is empty — warnings going to stderr are correct
behaviour. The contract is one-sided: stdout under --json is parseable
JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root


def _run_roam_json(*args: str) -> tuple[int, str, str]:
    """Invoke `python -m roam --json <args>` and return (rc, stdout, stderr).

    Runs from the repo root so the indexed DB / config files resolve normally.
    """
    cmd = [sys.executable, "-m", "roam", "--json", *args]
    proc = subprocess.run(  # noqa: S603 — explicit args, no shell
        cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_root()),
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("health",),
        ("version",),
        ("surface",),
    ],
)
def test_json_stdout_is_parseable(args: tuple[str, ...]) -> None:
    """`roam --json <cmd>` stdout must be valid JSON, never warning-prefixed."""
    rc, stdout, _stderr = _run_roam_json(*args)
    # rc may be 0 or a gate-failure code (5); both are acceptable. The
    # invariant we pin is stdout-shape, not exit code.
    assert stdout.strip(), f"expected non-empty stdout for {args}; rc={rc}"
    # Hard invariant: the FIRST non-whitespace byte must start a JSON value.
    first = stdout.lstrip()[:1]
    assert first in "{[", f"stdout for {args} did not start with a JSON value; first 200 chars: {stdout[:200]!r}"
    # And the whole thing must round-trip through json.loads.
    try:
        json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"json.loads(stdout) failed for {args}: {exc}; first 200 chars: {stdout[:200]!r}")


def test_warning_redirect_installed_when_default_hook_present() -> None:
    """The CLI installs a custom showwarning under --json IFF no override is present.

    The override is deliberately conservative — it only displaces the stdlib
    default. Pre-installed handlers (pytest's warning capture, user shims)
    must be preserved so test machinery, recwarn fixtures, and downstream
    consumers keep working. Pattern caught: a future refactor making the
    install unconditional and clobbering pytest's hook.

    Verified by reseating the default before invoking, then asserting our
    hook took its place.
    """
    import warnings as _warnings

    from click.testing import CliRunner

    from roam.cli import cli

    original = _warnings.showwarning
    default = getattr(_warnings, "_showwarning_orig", None) or getattr(_warnings, "_showwarning_impl", None)
    if default is None:
        pytest.skip("no stdlib default showwarning attribute on this Python build")
    try:
        # Reseat the default so the CLI's "only-if-default" check fires.
        _warnings.showwarning = default
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "version"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        hook = _warnings.showwarning
        hook_name = getattr(hook, "__name__", "")
        assert hook_name == "_stderr_showwarning", (
            f"expected _stderr_showwarning override under --json mode; got {hook_name!r}"
        )
    finally:
        _warnings.showwarning = original


def test_warning_redirect_preserves_existing_hook() -> None:
    """When a custom showwarning is already installed, the CLI leaves it alone.

    Pattern caught: a future change making the override clobber pytest's
    warning-capture hook (which currently leads to a CliRunner.output mismatch
    in test_json_contracts and similar).
    """
    import warnings as _warnings

    from click.testing import CliRunner

    from roam.cli import cli

    original = _warnings.showwarning
    sentinel_called = []

    def _sentinel_hook(message, category, filename, lineno, file=None, line=None):
        sentinel_called.append((str(message), category.__name__))

    try:
        _warnings.showwarning = _sentinel_hook
        runner = CliRunner()
        runner.invoke(cli, ["--json", "version"], catch_exceptions=False)
        # The CLI must not have clobbered our hook.
        assert _warnings.showwarning is _sentinel_hook, (
            f"CLI replaced a pre-installed showwarning hook; now is {_warnings.showwarning!r}"
        )
    finally:
        _warnings.showwarning = original


def test_warning_redirect_writes_to_stderr_not_stdout(capsys) -> None:
    """The installed override routes warnings to stderr, never stdout.

    Invokes the CLI to install the hook (after re-seating the default so the
    install path fires), then fires a synthetic warning and inspects the
    captured streams. Pattern caught: a future change accidentally swapping
    `sys.stderr` for `sys.stdout` in the override body. We write to
    `sys.__stderr__` so the assertion uses capsys's terminal-stderr capture
    rather than capsys's redirected sys.stderr — that's the whole point of
    using `__stderr__` (W1078 lineage).
    """
    import warnings as _warnings

    from click.testing import CliRunner

    from roam.cli import cli

    original = _warnings.showwarning
    default = getattr(_warnings, "_showwarning_orig", None) or getattr(_warnings, "_showwarning_impl", None)
    if default is None:
        pytest.skip("no stdlib default showwarning attribute on this Python build")
    try:
        _warnings.showwarning = default
        runner = CliRunner()
        runner.invoke(cli, ["--json", "version"], catch_exceptions=False)
        # Confirm the override is installed before exercising it.
        hook = _warnings.showwarning
        assert getattr(hook, "__name__", "") == "_stderr_showwarning"
        # Read-and-discard everything captured up to this point.
        capsys.readouterr()
        hook(  # invoke directly — bypass any pytest warnings filter
            "synthetic-test-warning",
            RuntimeWarning,
            __file__,
            1,
        )
        captured = capsys.readouterr()
        assert "synthetic-test-warning" not in captured.out, f"warning leaked to stdout: {captured.out!r}"
    finally:
        _warnings.showwarning = original
