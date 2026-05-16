"""W325 -- MCP Pattern-1 Variant B pass-through tests.

W315's audit (``(internal memo)``)
identified the root cause: ``_run_roam_inprocess`` and
``_run_roam_subprocess`` both treated ``{0, EXIT_GATE_FAILURE}`` as the
only "success" codes. Exit codes 1 (advisory failure) and 6
(``EXIT_PARTIAL``) fell to the error path, where ``error_text = output``
buried valid JSON stdout into the ``error`` field as a raw string.

W325 seals this with a single chokepoint:
``_maybe_pass_through_structured_json`` -- if stdout parsed cleanly as
JSON, pass it through annotated with ``_meta.cli_exit_code`` +
``isError: True`` rather than wrapping it in a generic error envelope.

These tests pin:

* ``roam_doctor`` -- exit 1 + structured JSON now reaches the consumer
* ``roam_stale_refs`` -- ``EXIT_PARTIAL`` (6) + structured JSON pass-through
* ``roam_test_scaffold`` -- ``symbol_not_found`` + ``SystemExit(1)``
  pass-through (W327 instance of Variant B)
* non-JSON stderr falls through to the classic error envelope
* the helper preserves a pre-existing ``_meta.cli_exit_code``
* the helper preserves a pre-existing ``isError``
* ``_SUCCESS_EXIT_CODES`` is at module scope and BOTH ``_run_roam_*``
  read it
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A tmp dir with no ``.roam/`` -- triggers doctor exit 1 path."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    monkeypatch.delenv("ROAM_MCP_DISABLE_COLD_START_GUARD", raising=False)
    # Drop any cached result-cache state so we hit the real CLI path.
    from roam.mcp_server import _ROAM_RESULT_CACHE

    _ROAM_RESULT_CACHE.clear()
    return tmp_path


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    """A tmp dir with a real built index -- so symbol-lookup commands
    actually run end-to-end. Tiny Python file so indexing is fast.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    monkeypatch.delenv("ROAM_MCP_DISABLE_COLD_START_GUARD", raising=False)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.py").write_text(
        "def greet(name: str) -> str:\n    return f'hi {name}'\n",
        encoding="utf-8",
    )
    # Initialise a git repo so discovery can pick the file up.
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=False)
    # Build the index in-process.
    from click.testing import CliRunner

    from roam.cli import cli as _cli
    from roam.mcp_server import _ROAM_RESULT_CACHE

    _ROAM_RESULT_CACHE.clear()
    runner = CliRunner()
    runner.invoke(_cli, ["--json", "init"], catch_exceptions=True)
    return tmp_path


def _run_maybe_async(fn, /, **kwargs):
    """Invoke ``fn(**kwargs)``, awaiting if it's a coroutine."""
    result = fn(**kwargs)
    if inspect.iscoroutine(result):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError("loop already running")
            return loop.run_until_complete(result)
        except RuntimeError:
            return asyncio.run(result)
    return result


# ---------------------------------------------------------------------------
# End-to-end: real CLI commands round-trip through the MCP wrapper
# ---------------------------------------------------------------------------


class TestPassThroughEndToEnd:
    """Exercise the full ``_run_roam_*`` path on real CLI commands that
    exit non-zero but emit valid JSON. Each test asserts the structured
    diagnostic now reaches the consumer instead of being buried under a
    generic error envelope.
    """

    def test_doctor_exit_1_with_json_passes_through(self, fresh_project):
        """``roam_doctor`` on a project with no ``.roam/`` exits 1
        because the "Index exists" advisory check fails. Pre-W325 the
        MCP wrapper turned that into a USAGE_ERROR envelope; post-W325
        the structured diagnostic is surfaced annotated with
        ``_meta.cli_exit_code == 1`` and ``isError: True``.
        """
        from roam.mcp_server import roam_doctor

        result = _run_maybe_async(roam_doctor, root=str(fresh_project))

        assert isinstance(result, dict)
        # Pre-W325 this was an error_code=USAGE_ERROR envelope; post-W325
        # the structured diagnostic survives.
        assert result.get("error_code") != "USAGE_ERROR"
        # Structured envelope reaches consumer -- summary.verdict or
        # checks[] is present (doctor emits at least one).
        summary = result.get("summary") or {}
        assert "verdict" in summary or "checks" in result, (
            f"doctor envelope did not pass through: keys={list(result.keys())}"
        )
        # W325 annotations -- exit code preserved, isError set.
        # Doctor exits with a non-success code on a fresh project (the
        # specific code -- 1 advisory vs 2 usage -- depends on which
        # advisory checks tripped). The pass-through MUST surface
        # whatever code came out, and it MUST NOT be a success code (0
        # or 5).
        meta = result.get("_meta") or {}
        cli_exit = meta.get("cli_exit_code")
        assert cli_exit is not None, f"cli_exit_code annotation missing: meta={meta}"
        assert cli_exit not in (0, 5), f"doctor on fresh project should exit non-success: cli_exit_code={cli_exit}"
        assert result.get("isError") is True

    def test_stale_refs_exit_6_with_json_passes_through(self, indexed_project):
        """``roam stale-refs`` exits with ``EXIT_PARTIAL`` (6) when it
        finds broken references but completes its scan. Build a minimal
        indexed project with a markdown link to a nonexistent file so
        the command finds at least one broken reference.
        """
        # Add a broken-link markdown doc to the fixture.
        (indexed_project / "README.md").write_text(
            "See [missing](docs/does-not-exist.md) for details.\n",
            encoding="utf-8",
        )

        from roam.mcp_server import _ROAM_RESULT_CACHE, _run_roam_inprocess

        _ROAM_RESULT_CACHE.clear()

        # Call the raw wrapper directly so we don't depend on
        # ``roam_stale_refs`` signature details. ``--json`` is auto-added.
        result = _run_roam_inprocess(["stale-refs", "--limit", "20"])

        assert isinstance(result, dict)
        # Either the structured stale-refs envelope (pass-through) or
        # ``no_data`` if the working tree on this build has zero broken
        # refs -- both are valid; the *anti-pattern* we are blocking is
        # an INVALID_JSON / USAGE_ERROR envelope.
        if result.get("error_code"):
            assert result["error_code"] not in {
                "USAGE_ERROR",
                "COMMAND_FAILED",
                "INVALID_JSON",
            }, f"stale-refs response should not be an error envelope: {result}"
        # If exit was non-zero, the pass-through must have annotated it.
        meta = result.get("_meta") or {}
        cli_exit = meta.get("cli_exit_code")
        if cli_exit is not None:
            assert cli_exit in (1, 6), f"stale-refs cli_exit_code unexpected: {cli_exit}"
            assert result.get("isError") is True

    def test_test_scaffold_unknown_symbol_passes_through(self, indexed_project):
        """W327: ``cmd_test_scaffold`` emits ``symbol_not_found`` JSON
        then ``SystemExit(1)`` when the symbol is unknown. The CLI emits
        structured output AND exits non-zero -- the canonical Variant B
        pattern. Pre-W325 this envelope was buried; post-W325 it passes
        through.
        """
        from roam.mcp_server import _ROAM_RESULT_CACHE, _run_roam_inprocess

        _ROAM_RESULT_CACHE.clear()

        result = _run_roam_inprocess(["test-scaffold", "nonexistent_symbol_xyz_w325"])

        assert isinstance(result, dict)
        # Pass-through annotation -- exit 1 surfaced + isError set.
        meta = result.get("_meta") or {}
        assert meta.get("cli_exit_code") == 1, (
            f"test-scaffold did not pass through: keys={list(result.keys())}, meta={meta}"
        )
        assert result.get("isError") is True
        # The underlying symbol_not_found envelope structure survives --
        # at minimum the dict has more than the raw error fields, i.e.
        # we did not collapse to ``{error, error_code, hint}``.
        assert result.keys() - {
            "error",
            "error_code",
            "hint",
            "exit_code",
            "command",
            "isError",
            "_meta",
            "retryable",
            "suggested_action",
            "doc_link",
            "severity",
        }, f"only generic error fields present, no structured envelope: {result}"

    def test_pytest_fixtures_unknown_symbol_passes_through_mcp(self, indexed_project):
        """W354: confirm the W327 CLI-side fix flows through the MCP
        wrapper end-to-end. ``roam pytest-fixtures <unknown>`` exits 0
        with a structured envelope (Pattern-2 always-emit), so the
        wrapper's normal success path -- NOT the W325 chokepoint --
        must preserve the envelope intact. Pinning the round-trip
        guards against the wrapper ever stripping or wrapping the
        zero-exit envelope by mistake.
        """
        from roam.mcp_server import _ROAM_RESULT_CACHE, _run_roam_inprocess

        _ROAM_RESULT_CACHE.clear()

        result = _run_roam_inprocess(["pytest-fixtures", "fake_symbol_does_not_exist_w354"])

        assert isinstance(result, dict)
        # Exit 0 is in _SUCCESS_EXIT_CODES, so the W325 pass-through
        # MUST NOT have fired. Classic-success path delivers the
        # envelope without the cli_exit_code annotation.
        meta = result.get("_meta") or {}
        assert "cli_exit_code" not in meta, f"exit 0 must not trigger the W325 chokepoint annotation: meta={meta}"
        # NOT an error envelope -- isError must not be set on success.
        assert result.get("isError") is not True, (
            f"pytest-fixtures unknown-symbol round-trip wrongly tagged as error: {result}"
        )
        assert result.get("error_code") is None, (
            f"pytest-fixtures unknown-symbol must not produce error envelope: {result}"
        )
        # W327 envelope fields survive the round-trip.
        assert result.get("command") == "pytest-fixtures"
        assert result.get("target") == "fake_symbol_does_not_exist_w354"
        assert result.get("fixtures") == []
        assert result.get("chain") == []
        summary = result.get("summary") or {}
        assert summary.get("resolved") is False
        assert summary.get("count") == 0
        assert summary.get("symbol") == "fake_symbol_does_not_exist_w354"
        assert "fake_symbol_does_not_exist_w354" in summary.get("verdict", "")

    def test_non_json_output_falls_through_to_error_envelope(self):
        """Non-JSON stderr from a failed command MUST still get the
        classic error envelope (existing behavior preserved). The
        pass-through is conservative: it only fires when stdout parses
        as a JSON object.
        """
        from roam.mcp_server import _ROAM_RESULT_CACHE, _run_roam_inprocess

        _ROAM_RESULT_CACHE.clear()

        # An unknown subcommand exits with USAGE error and prints Click's
        # plaintext "No such command" message -- not JSON.
        result = _run_roam_inprocess(["this-command-does-not-exist-xyz-w325"])

        assert isinstance(result, dict)
        assert result.get("isError") is True
        # Classic error envelope path: error_code present and a hint.
        assert "error_code" in result
        # The pass-through did NOT fire -- ``_meta.cli_exit_code`` is
        # specific to the pass-through annotation. (The classic envelope
        # uses ``exit_code`` at top level, not nested under ``_meta``.)
        meta = result.get("_meta") or {}
        assert "cli_exit_code" not in meta, f"pass-through must not fire on non-JSON output: meta={meta}, full={result}"


# ---------------------------------------------------------------------------
# Unit tests: the pure helper
# ---------------------------------------------------------------------------


class TestHelperPureFunction:
    """Pure-function unit tests for
    ``_maybe_pass_through_structured_json``. No subprocess, no DB --
    just the JSON parsing + annotation logic.
    """

    def test_helper_preserves_existing_meta(self):
        """If the inner JSON already carries
        ``_meta.cli_exit_code``, the helper MUST NOT overwrite it. This
        protects producers that intentionally annotate their own
        envelope (e.g. recipes that fan out and stamp the inner exit
        code themselves).
        """
        from roam.mcp_server import _maybe_pass_through_structured_json

        inner = {
            "summary": {"verdict": "preserved"},
            "_meta": {"cli_exit_code": 42, "custom": "keep"},
        }
        out = _maybe_pass_through_structured_json(json.dumps(inner), 1)

        assert isinstance(out, dict)
        assert out["_meta"]["cli_exit_code"] == 42, "helper overwrote existing cli_exit_code"
        assert out["_meta"]["custom"] == "keep"

    def test_helper_preserves_existing_isError(self):
        """If the inner JSON already sets ``isError`` (including
        ``False``), the helper MUST NOT overwrite it.
        """
        from roam.mcp_server import _maybe_pass_through_structured_json

        inner = {"summary": {"verdict": "deliberate"}, "isError": False}
        out = _maybe_pass_through_structured_json(json.dumps(inner), 1)

        assert isinstance(out, dict)
        assert out["isError"] is False, "helper overwrote existing isError"
        # cli_exit_code still annotated -- it was absent on the inner.
        assert out["_meta"]["cli_exit_code"] == 1

    def test_helper_sets_meta_and_iserror_by_default(self):
        """Plain JSON dict with neither ``_meta`` nor ``isError`` gets
        both stamped.
        """
        from roam.mcp_server import _maybe_pass_through_structured_json

        inner = {"summary": {"verdict": "fresh"}}
        out = _maybe_pass_through_structured_json(json.dumps(inner), 6)

        assert isinstance(out, dict)
        assert out["isError"] is True
        assert out["_meta"]["cli_exit_code"] == 6

    def test_helper_returns_none_on_empty_input(self):
        from roam.mcp_server import _maybe_pass_through_structured_json

        assert _maybe_pass_through_structured_json("", 1) is None
        assert _maybe_pass_through_structured_json("   \n  ", 1) is None

    def test_helper_returns_none_on_non_json(self):
        from roam.mcp_server import _maybe_pass_through_structured_json

        # Stack trace / plaintext stderr -- must not be misinterpreted.
        assert _maybe_pass_through_structured_json("Traceback (most recent call last):", 1) is None
        assert _maybe_pass_through_structured_json("Error: no such command 'foo'", 2) is None

    def test_helper_returns_none_on_top_level_array(self):
        """Top-level JSON arrays are NOT envelopes -- the helper must
        decline so the existing error-envelope path can wrap structure
        around them.
        """
        from roam.mcp_server import _maybe_pass_through_structured_json

        assert _maybe_pass_through_structured_json("[1, 2, 3]", 1) is None

    def test_helper_returns_none_on_malformed_json(self):
        from roam.mcp_server import _maybe_pass_through_structured_json

        assert _maybe_pass_through_structured_json("{not json", 1) is None


# ---------------------------------------------------------------------------
# Drift guard: ``_SUCCESS_EXIT_CODES`` is module-scope, shared by both
# ---------------------------------------------------------------------------


class TestSuccessExitCodesConstant:
    """Drift guard: hoisting ``_success_codes`` out of the two
    ``_run_roam_*`` functions to module scope is a single-source-of-
    truth fix. Future exit-code additions must edit one place.
    """

    def test_success_codes_constant_is_module_scope(self):
        """``_SUCCESS_EXIT_CODES`` is a frozenset accessible at module
        level. Frozenset guarantees immutability + O(1) membership.
        """
        from roam import mcp_server

        assert hasattr(mcp_server, "_SUCCESS_EXIT_CODES"), "_SUCCESS_EXIT_CODES must be module-scope (W325 hoist)"
        assert isinstance(mcp_server._SUCCESS_EXIT_CODES, frozenset)
        # Currently {0, 5} -- exit 0 (success) + exit 5 (gate failure).
        # Gate failure produces valid JSON so it's treated as success
        # for parsing.
        assert 0 in mcp_server._SUCCESS_EXIT_CODES
        assert 5 in mcp_server._SUCCESS_EXIT_CODES
        # Exit 1 (advisory failure) MUST NOT be in the success set --
        # the W325 pass-through covers it via a separate path.
        assert 1 not in mcp_server._SUCCESS_EXIT_CODES
        assert 6 not in mcp_server._SUCCESS_EXIT_CODES

    def test_both_run_roam_wrappers_reference_module_constant(self):
        """Inspect the source of ``_run_roam_inprocess`` and
        ``_run_roam_subprocess`` -- both must reference
        ``_SUCCESS_EXIT_CODES`` (not duplicate a local ``{0,
        EXIT_GATE_FAILURE}`` literal).
        """
        import inspect as _inspect

        from roam.mcp_server import _run_roam_inprocess, _run_roam_subprocess

        src_in = _inspect.getsource(_run_roam_inprocess)
        src_sub = _inspect.getsource(_run_roam_subprocess)

        assert "_SUCCESS_EXIT_CODES" in src_in, (
            "_run_roam_inprocess must reference the module-level _SUCCESS_EXIT_CODES constant (W325)"
        )
        assert "_SUCCESS_EXIT_CODES" in src_sub, (
            "_run_roam_subprocess must reference the module-level _SUCCESS_EXIT_CODES constant (W325)"
        )
