"""W711: error-branch coverage for ``roam mcp --card``.

W695 added the happy-path smoke for ``roam mcp --card`` (the handler at
``src/roam/mcp_server.py:9576-9607``). W711 extends that coverage to the
three error branches the handler exposes:

1. **Missing card file** — neither the bundled ``src/roam/mcp-server-card.json``
   nor the dev-checkout fallback under ``docs/site/.well-known/`` exists.
2. **Read raises OSError** — the file exists per ``is_file()`` but
   ``read_text()`` fails (permission error, disk gone, etc.).
3. **Malformed JSON bytes** — the handler is a passthrough today (it doesn't
   parse), so this case documents that the bytes are echoed verbatim. This
   pins a known Pattern-1-family gap: the handler does NOT validate the
   payload before emission. A future fix should add JSON parse + structured
   error envelope here.

Pattern 1 family note: the current handler emits a plain stderr line
(``"error: server card file not found"``) and ``SystemExit(1)`` on the
missing-file branch. That is NOT the canonical Pattern-1 envelope shape
(``isError: true`` inside the result, ``error_code``, ``hint``, etc.).
These tests assert today's behavior so a future canonicalization PR has
a baseline to update. See CLAUDE.md "Pattern-1 family" for the canonical
shape.
"""

from __future__ import annotations

from click.testing import CliRunner

from roam.mcp_server import mcp_cmd

# ---------------------------------------------------------------------------
# Branch 1: missing card file (all fallback paths absent)
# ---------------------------------------------------------------------------


def test_card_missing_file_exits_with_error_message(monkeypatch):
    """When the bundled card file and both dev-checkout fallback paths are
    absent, ``--card`` writes a human-readable error to stderr and exits
    non-zero. Documents the current (pre-canonical) error branch.
    """
    # Force every Path.is_file() check inside the --card handler to report
    # False, simulating the post-install case where the package data
    # somehow got stripped (W554 / W664 territory).
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

    runner = CliRunner()
    result = runner.invoke(mcp_cmd, ["--card"], standalone_mode=False)

    # Click surfaces ``SystemExit(1)`` as exit_code == 1 under
    # ``standalone_mode=False`` (the SystemExit is captured into
    # ``result.exception``).
    assert result.exit_code != 0, f"expected non-zero exit, got {result.exit_code}: {result.output!r}"
    # The current handler emits to stderr via ``click.echo(err=True)``.
    # CliRunner merges streams in this Click version, so the warning
    # surfaces in either ``.output`` or ``.stderr``.
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "error" in combined.lower(), f"expected human-readable 'error' marker in output: {combined!r}"
    assert "card" in combined.lower() or "not found" in combined.lower(), (
        f"expected actionable hint about the missing card: {combined!r}"
    )
    # Pattern-1 hygiene: the error string must be human-readable, NEVER a
    # raw traceback dump. Asserts the handler doesn't leak Python frames.
    assert "Traceback" not in combined, f"error must not leak a raw traceback: {combined!r}"


# ---------------------------------------------------------------------------
# Branch 2: importer raises OSError (file exists, read fails)
# ---------------------------------------------------------------------------


def test_card_read_oserror_propagates_or_handled(monkeypatch):
    """If ``read_text()`` raises (permission denied, ENOSPC, disk gone),
    the handler today has no try/except — the OSError leaks. This test
    pins that behavior so a future Pattern-1 canonicalization PR can
    flip the assertion to expect a structured envelope instead.
    """
    # Make the bundled path "exist" so we hit the read_text() call.
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)

    def boom(self, *args, **kwargs):
        raise OSError(13, "permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", boom)

    runner = CliRunner()
    result = runner.invoke(mcp_cmd, ["--card"], standalone_mode=False)

    # Current handler: OSError propagates out as result.exception.
    # Future canonical handler: would emit Pattern-1 envelope + non-zero exit.
    # Either way, the invocation must not succeed silently.
    assert result.exit_code != 0, (
        f"OSError on read must fail the invocation, got exit {result.exit_code} with output {result.output!r}"
    )
    # If it raised, the exception should be the OSError (or a wrapper
    # carrying its message). The output, if any, must not be valid JSON
    # passing for a server card.
    if result.exception is not None:
        # Today's behavior: raw OSError leaks.
        assert isinstance(result.exception, (OSError, SystemExit)), (
            f"unexpected exception type: {type(result.exception).__name__}"
        )


# ---------------------------------------------------------------------------
# Branch 3: malformed JSON bytes (passthrough — documents current gap)
# ---------------------------------------------------------------------------


def test_card_malformed_json_is_passthrough_today(monkeypatch):
    """The handler reads file bytes and echoes them without JSON.parse().
    A corrupted card therefore prints corrupted bytes and exits 0.

    This test pins the current (Pattern-1-gap) behavior. A canonical fix
    should JSON-parse the bytes before emission and produce a structured
    error envelope on parse failure. When that lands, flip this test to
    assert exit_code != 0 and ``isError: true`` in the output envelope.
    """
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)

    bad_bytes = "{not valid json at all,,,}"
    monkeypatch.setattr("pathlib.Path.read_text", lambda self, **k: bad_bytes)

    runner = CliRunner()
    result = runner.invoke(mcp_cmd, ["--card"], standalone_mode=False)

    # Current behavior: passthrough — exit 0, malformed bytes echoed.
    # This is the documented gap. Asserting exit_code == 0 makes the
    # regression visible if the handler later starts validating.
    assert result.exit_code == 0, (
        f"current handler is a passthrough; if this now fails, the "
        f"handler started validating JSON — flip the test to assert the "
        f"Pattern-1 envelope shape. Got exit {result.exit_code}, "
        f"output {result.output!r}"
    )
    assert bad_bytes.strip() in result.output, f"expected raw bytes echo, got {result.output!r}"


# ---------------------------------------------------------------------------
# Branch 4: Pattern-1 always-emit discipline check (informational)
# ---------------------------------------------------------------------------


def test_card_missing_emits_no_raw_traceback(monkeypatch):
    """Pattern-1 hygiene: even on the failure path, the handler must not
    crash with a bare Python traceback reaching the user. The missing-file
    branch uses ``click.echo(err=True) + SystemExit(1)`` which is the
    minimum acceptable shape until a structured envelope lands.
    """
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

    runner = CliRunner()
    result = runner.invoke(mcp_cmd, ["--card"], standalone_mode=False)

    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    # SystemExit is fine; an uncaught Exception is not.
    if result.exception is not None:
        assert isinstance(result.exception, SystemExit), (
            f"missing-file branch should raise SystemExit, not {type(result.exception).__name__}: {result.exception!r}"
        )
    # No Python frame leakage in user-facing output.
    assert 'File "' not in combined, f"traceback frame leaked into output: {combined!r}"
