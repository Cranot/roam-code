"""Tests for the fast-startup MCP wrapper (``roam.commands.cmd_mcp``).

Regression guard for the private MCP startup-timeout eval finding:
``roam mcp`` booted in ~38 s on a 22 MB index because it ran a
synchronous full reindex before ``mcp.run()``. The wrapper in
``cmd_mcp`` swaps that for a fast mtime-only ``check_stale`` so the
server boots in under a second on the common case (index fresh) and
emits a stderr warning on the rare case (index stale) rather than
blocking past Claude Code's 30 s connect timeout.

We deliberately avoid the full subprocess to keep the suite robust on
machines without ``fastmcp`` installed; instead we monkey-patch the
underlying ``mcp_server.mcp_cmd`` and assert on the args the wrapper
forwards.
"""

from __future__ import annotations

import sys
import time

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Module-cache hygiene
# ---------------------------------------------------------------------------
#
# Several tests below ``sys.modules.pop("roam.mcp_server", None)`` to force a
# cold import. monkeypatch does NOT track raw ``sys.modules`` pops, so without
# restoration the module stays evicted — and the NEXT ``import roam.mcp_server``
# anywhere builds a SECOND module object. Any other test file that did a
# top-level ``from roam.mcp_server import X`` then holds a reference to the
# orphaned first copy while monkeypatching the second, so its stubs silently
# miss. This bit tests/test_validate_plan.py under xdist (the 3 monkeypatching
# tests flaked when a popper ran earlier on the same worker). Snapshot and
# restore the canonical module objects around every test here so the cache
# stays single-copy.
@pytest.fixture(autouse=True)
def _preserve_module_cache():
    names = ("roam.mcp_server", "roam.commands.cmd_mcp")
    saved = {name: sys.modules.get(name) for name in names}
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Import-cost regression guards
# ---------------------------------------------------------------------------


def test_cmd_mcp_module_import_is_cheap():
    """Importing ``roam.commands.cmd_mcp`` must NOT eagerly drag in the
    8600-line ``roam.mcp_server`` module. If someone re-adds a
    ``from roam.mcp_server import ...`` at the top of ``cmd_mcp.py``
    this test catches it before the regression ships.
    """
    # Drop both modules from the cache so we measure a true cold import.
    sys.modules.pop("roam.commands.cmd_mcp", None)
    sys.modules.pop("roam.mcp_server", None)

    t0 = time.perf_counter()
    import roam.commands.cmd_mcp  # noqa: F401 — import for timing only

    elapsed = time.perf_counter() - t0

    # 1.0 s is generous: a healthy import is well under 200 ms even on
    # cold-cache Windows runners. The fail-fast threshold is meant to
    # catch a 5-10 s ``from roam.mcp_server import mcp_cmd`` regression,
    # not to flake on slow CI hardware.
    assert elapsed < 1.0, f"cmd_mcp import took {elapsed:.3f}s (expected < 1.0s)"

    # And the heavy module must NOT be loaded as a side effect.
    assert "roam.mcp_server" not in sys.modules, (
        "Importing roam.commands.cmd_mcp pulled in roam.mcp_server — "
        "this defeats the fast-startup wrapper. Move the import inside "
        "the function body."
    )


def test_cmd_mcp_help_does_not_load_mcp_server():
    """``roam mcp --help`` must not import ``roam.mcp_server``. Click
    only invokes the function body when a real command runs; ``--help``
    just renders the option list, so the lazy import inside the body
    should never fire.
    """
    sys.modules.pop("roam.commands.cmd_mcp", None)
    sys.modules.pop("roam.mcp_server", None)

    from roam.commands.cmd_mcp import mcp

    runner = CliRunner()
    result = runner.invoke(mcp, ["--help"])
    assert result.exit_code == 0
    assert "roam mcp" in result.output
    assert "--no-auto-index" in result.output
    assert "roam.mcp_server" not in sys.modules, "Rendering --help should not import roam.mcp_server."


# ---------------------------------------------------------------------------
# Wrapper-behaviour tests (does it forward correctly?)
# ---------------------------------------------------------------------------


class _MCPServerStub:
    """Stand-in for the ``roam.mcp_server`` module.

    Records the args the wrapper forwards so we can assert on them. The
    real module is too heavy (and may not be importable without the
    ``[mcp]`` extra) to exercise in unit tests.
    """

    def __init__(self):
        self.calls = []

    def mcp_cmd(self):  # placeholder — Click won't actually call this
        raise AssertionError("Click should call .callback, not the command itself")

    def __call__(self):  # pragma: no cover — defensive
        raise AssertionError("not callable")


@pytest.fixture
def stub_mcp_server(monkeypatch):
    """Replace ``roam.mcp_server.mcp_cmd`` with a recording stub."""
    import roam.mcp_server as mod

    captured = {}

    def fake_callback(transport, host, port, no_auto_index, list_tools, list_tools_json, compat_profile, card):
        captured.update(
            transport=transport,
            host=host,
            port=port,
            no_auto_index=no_auto_index,
            list_tools=list_tools,
            list_tools_json=list_tools_json,
            compat_profile=compat_profile,
            card=card,
        )
        return None

    # Patch ``.callback`` rather than replacing the whole Click command;
    # the wrapper reads ``_real_mcp_cmd.callback`` and our patch must
    # survive that attribute lookup.
    original = mod.mcp_cmd.callback
    monkeypatch.setattr(mod.mcp_cmd, "callback", fake_callback)
    yield captured
    monkeypatch.setattr(mod.mcp_cmd, "callback", original)


def test_default_invocation_forces_no_auto_index(stub_mcp_server, monkeypatch):
    """The whole point of the wrapper: the default server-start path
    must not trigger the 36 s ``_ensure_fresh_index`` reindex. The
    wrapper replaces it with a fast ``check_stale`` and forwards with
    ``no_auto_index=True``.
    """
    # Pretend the index is fresh so no stderr warning is emitted.
    monkeypatch.setattr(
        "roam.commands.stale_index.check_stale",
        lambda sensitivity="medium": (False, None),
    )

    from roam.commands.cmd_mcp import mcp

    runner = CliRunner()
    result = runner.invoke(mcp, [], standalone_mode=False)

    assert result.exit_code == 0, result.output
    assert stub_mcp_server["no_auto_index"] is True
    assert stub_mcp_server["transport"] == "stdio"
    assert stub_mcp_server["card"] is False


def test_stale_index_emits_warning_but_still_boots(stub_mcp_server, monkeypatch):
    """When ``check_stale`` returns True, the wrapper writes a stderr
    warning and still forwards with ``no_auto_index=True`` — the server
    boots fast and the agent gets a visible affordance instead of a
    30 s connect-timeout failure.
    """
    monkeypatch.setattr(
        "roam.commands.stale_index.check_stale",
        lambda sensitivity="medium": (True, "index mtime 99h old"),
    )

    from roam.commands.cmd_mcp import mcp

    # CliRunner versions differ on stderr separation. Try the modern
    # constructor first (Click 8.2+ merges streams by default and you
    # set ``stderr=True`` only on .invoke). Fall back to legacy if not
    # available.
    runner = CliRunner()
    try:
        result = runner.invoke(mcp, [], standalone_mode=False)
        combined_output = result.output
    except TypeError:  # pragma: no cover — defensive
        result = runner.invoke(mcp, [], standalone_mode=False)
        combined_output = result.output

    assert result.exit_code == 0, combined_output
    assert stub_mcp_server["no_auto_index"] is True
    # Warning lands on stderr; CliRunner merges stderr into output by
    # default in this Click version. Either way, the warning text
    # should be present.
    assert "stale" in combined_output.lower(), combined_output
    assert "roam index" in combined_output, combined_output


def test_explicit_no_auto_index_skips_check(stub_mcp_server, monkeypatch):
    """If the user passes ``--no-auto-index`` we trust them and skip the
    fast check entirely. ``check_stale`` should not be called.
    """
    called = {"count": 0}

    def fake_check(sensitivity="medium"):
        called["count"] += 1
        return False, None

    monkeypatch.setattr("roam.commands.stale_index.check_stale", fake_check)

    from roam.commands.cmd_mcp import mcp

    runner = CliRunner()
    result = runner.invoke(mcp, ["--no-auto-index"], standalone_mode=False)

    assert result.exit_code == 0, result.output
    assert stub_mcp_server["no_auto_index"] is True
    assert called["count"] == 0, "check_stale should not run when --no-auto-index is set"


@pytest.mark.parametrize(
    "info_flag,attr",
    [
        (["--card"], "card"),
        (["--list-tools"], "list_tools"),
        (["--list-tools-json"], "list_tools_json"),
        (["--compat-profile", "claude"], "compat_profile"),
    ],
)
def test_info_modes_bypass_freshness_check(info_flag, attr, stub_mcp_server, monkeypatch):
    """Info-only modes don't need a fresh index — and the underlying
    ``mcp_cmd`` returns before the freshness check anyway. The wrapper
    must not call ``check_stale`` and must forward verbatim.
    """
    called = {"count": 0}

    def fake_check(sensitivity="medium"):
        called["count"] += 1
        return True, "fake stale"

    monkeypatch.setattr("roam.commands.stale_index.check_stale", fake_check)

    from roam.commands.cmd_mcp import mcp

    runner = CliRunner()
    result = runner.invoke(mcp, info_flag, standalone_mode=False)

    assert result.exit_code == 0, result.output
    assert called["count"] == 0, "info modes should not run check_stale"
    # The info flag itself must be forwarded as True (or the right value).
    forwarded = stub_mcp_server[attr]
    if attr == "compat_profile":
        assert forwarded == "claude"
    else:
        assert forwarded is True


def test_check_stale_filesystem_error_is_safe(stub_mcp_server, monkeypatch):
    """A defensive guard: if ``check_stale`` itself hits an expected
    filesystem error reading the DB, the wrapper still boots the server.
    It MUST NOT propagate the exception — that would be worse than the
    original 36 s reindex.
    """

    def boom(sensitivity="medium"):
        raise PermissionError("disk gone")

    monkeypatch.setattr("roam.commands.stale_index.check_stale", boom)

    from roam.commands.cmd_mcp import mcp

    runner = CliRunner()
    result = runner.invoke(mcp, [], standalone_mode=False)

    assert result.exit_code == 0, result.output
    # Even on the exception path we still skip the heavy reindex.
    assert stub_mcp_server["no_auto_index"] is True


def test_check_stale_unexpected_error_propagates(stub_mcp_server, monkeypatch):
    """Programmer-class ``check_stale`` failures must not be swallowed.

    The wrapper catches expected import/filesystem failures only. A broad
    ``except Exception`` here would hide real bugs and still boot the server.
    """

    def boom(sensitivity="medium"):
        raise RuntimeError("programmer bug")

    monkeypatch.setattr("roam.commands.stale_index.check_stale", boom)

    from roam.commands.cmd_mcp import mcp

    runner = CliRunner()
    with pytest.raises(RuntimeError, match="programmer bug"):
        runner.invoke(mcp, [], standalone_mode=False, catch_exceptions=False)

    assert stub_mcp_server == {}


# ---------------------------------------------------------------------------
# Subprocess-level smoke (optional, slow on cold cache)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cmd_mcp_help_is_fast_subprocess(tmp_path):
    """End-to-end: ``python -m roam mcp --help`` must complete under
    2 s. Marked slow because it's I/O-bound and flaky on shared CI.
    """
    import subprocess

    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "roam", "mcp", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    elapsed = time.perf_counter() - t0

    assert result.returncode == 0, result.stderr
    assert "roam mcp" in result.stdout
    assert elapsed < 2.0, f"roam mcp --help took {elapsed:.2f}s (regression — should be <2s)"
