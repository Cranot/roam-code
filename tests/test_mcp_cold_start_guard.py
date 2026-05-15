"""W296 -- MCP cold-start guard tests.

When an MCP tool that needs a built index is invoked on a fresh project
(no ``.roam/index.db``), it must return a structured envelope instead of
hanging or auto-triggering a full index build that exceeds the MCP call
timeout. Per CLAUDE.md Pattern 1 ("JSON-parse-on-empty-input"), silence
is the worst failure mode.

These tests pin:

* the cold-start envelope shape and copy
* the < 2s wall-clock budget on a fresh project (proves no hang)
* the closed ``_NO_INDEX_NEEDED`` set (drift guard)
* LAW 4 concrete-noun-anchored ``summary.verdict`` (drift guard)
* the bypass when an index is present (proves the guard doesn't
  pessimise the hot path)
"""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Anchor mirror -- kept inline to avoid a hard import dependency on
# ``tests/test_law4_lint.py`` (which has its own pytest collection
# semantics). Mirror the LAW 4 anchor set exactly; the
# ``tests/test_law4_anchor_counts.py`` drift-guard ensures the formatter
# and lint stay in lockstep, so importing the lint module here is the
# canonical anchor source.
# ---------------------------------------------------------------------------


def _law4_anchor_set() -> frozenset[str]:
    from tests.test_law4_lint import _CONCRETE_NOUN_ANCHORS

    return _CONCRETE_NOUN_ANCHORS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A tmp dir with no ``.roam/`` -- the cold-start condition."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    monkeypatch.delenv("ROAM_MCP_DISABLE_COLD_START_GUARD", raising=False)
    return tmp_path


@pytest.fixture
def warm_project(tmp_path, monkeypatch):
    """A tmp dir WITH an ``.roam/index.db`` present."""
    (tmp_path / ".git").mkdir()
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db = roam_dir / "index.db"
    # Non-empty so ``db_exists`` returns True (it checks ``stat().st_size > 0``).
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 256)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    monkeypatch.delenv("ROAM_MCP_DISABLE_COLD_START_GUARD", raising=False)
    return tmp_path


def _run_maybe_async(fn, /, **kwargs):
    """Invoke ``fn(**kwargs)``, awaiting if it's a coroutine."""
    result = fn(**kwargs)
    if inspect.iscoroutine(result):
        return asyncio.get_event_loop().run_until_complete(result) \
            if not asyncio.iscoroutine(result) else asyncio.run(result)
    return result


# ---------------------------------------------------------------------------
# helpers module unit tests -- the pure functions
# ---------------------------------------------------------------------------


class TestPreflightHelpers:
    """Pure-function unit tests for ``roam.mcp_extras.preflight``."""

    def test_no_index_needed_set_is_explicit_closed_enum(self):
        """Drift guard: ``_NO_INDEX_NEEDED`` is a frozenset with a pinned
        membership covering the documented skip-taxonomy:
        bootstrap (init, reindex), pre-init diagnostics (doctor),
        pure metadata (catalog, expand_toolset, session_metrics),
        and file-path-only operators (evidence_doctor, fetch_handle,
        pr_comment_render).
        """
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED

        assert isinstance(_NO_INDEX_NEEDED, frozenset), (
            "_NO_INDEX_NEEDED must be a frozenset for immutability + O(1) "
            "membership checks"
        )
        # Closed enumeration -- exact membership pinned. Extending requires
        # a deliberate source-code edit (and updating this test).
        expected = {
            "roam_init",
            "roam_reindex",
            "roam_doctor",
            "roam_catalog",
            "roam_expand_toolset",
            "roam_session_metrics",
            "roam_evidence_doctor",
            "roam_fetch_handle",
            "roam_pr_comment_render",
        }
        assert _NO_INDEX_NEEDED == expected, (
            f"_NO_INDEX_NEEDED drift detected.\n"
            f"  in-set but unexpected: {_NO_INDEX_NEEDED - expected}\n"
            f"  expected but missing:  {expected - _NO_INDEX_NEEDED}"
        )

    def test_needs_index_default_is_true(self):
        """A tool not in the skip-set is index-gated by default."""
        from roam.mcp_extras.preflight import needs_index

        assert needs_index("roam_health") is True
        assert needs_index("roam_dead_code") is True
        # Unknown tool name -- defaults to needs-index (safer).
        assert needs_index("definitely_not_a_real_tool") is True

    def test_needs_index_false_for_skip_set(self):
        from roam.mcp_extras.preflight import needs_index

        assert needs_index("roam_init") is False
        assert needs_index("roam_catalog") is False
        assert needs_index("roam_fetch_handle") is False

    def test_index_is_built_false_on_fresh_project(self, fresh_project):
        from roam.mcp_extras.preflight import index_is_built

        assert index_is_built(fresh_project) is False
        # Default arg path resolves to cwd, which the fixture chdir'd into.
        assert index_is_built() is False

    def test_index_is_built_true_with_db_present(self, warm_project):
        from roam.mcp_extras.preflight import index_is_built

        assert index_is_built(warm_project) is True
        assert index_is_built() is True

    def test_cold_start_envelope_shape(self):
        from roam.mcp_extras.preflight import cold_start_envelope

        env = cold_start_envelope("roam_health")
        # Status field -- the closed-enum the agent branches on.
        assert env["status"] == "index_not_built"
        # Summary block -- agents that read only the verdict get the answer.
        assert "summary" in env
        assert "verdict" in env["summary"]
        assert "roam init" in env["summary"]["verdict"]
        assert env["summary"]["level"] == "blocker"
        # next_command field -- copy-paste-executable per LAW 12.
        assert env["next_command"] == "roam init"
        # Timing hints for the agent's retry loop.
        assert env["expected_duration_seconds"] >= 1
        assert env["retry_after_seconds"] >= 1
        # agent_contract.facts is flat, positive, concrete-noun-anchored.
        facts = env["agent_contract"]["facts"]
        assert isinstance(facts, list)
        assert all(isinstance(f, str) for f in facts)
        assert any("roam init" in f for f in facts) or any(
            "prerequisite" in f for f in facts
        )
        # next_commands present and starts with the same command as
        # next_command (no string-vs-list drift).
        next_cmds = env["agent_contract"]["next_commands"]
        assert next_cmds[0] == "roam init"

    def test_cold_start_verdict_uses_concrete_noun_terminal(self):
        """LAW 4 drift guard. The terminal noun of ``summary.verdict``
        must be in the LAW 4 concrete-noun anchor set so the lint at
        ``tests/test_law4_lint.py`` accepts it. If the anchor set ever
        drops "tools", REPHRASE the verdict to land on a different
        anchor noun -- do NOT widen the anchor set just for this verdict.
        """
        from roam.mcp_extras.preflight import cold_start_envelope

        env = cold_start_envelope("roam_health")
        verdict = env["summary"]["verdict"]
        # Apply the same terminal-token derivation the lint uses.
        import re

        terminal = re.sub(r"[\s]+", " ", verdict.strip()).split()[-1]
        terminal = terminal.lower().rstrip(",.;:!?)").lstrip("(")
        anchors = _law4_anchor_set()
        assert terminal in anchors, (
            f"cold-start verdict terminal token {terminal!r} is NOT in the LAW 4 "
            f"concrete-noun anchor set. Rephrase the verdict so its last token is "
            f"an anchor (e.g. 'tools', 'symbols', 'files'); do not widen the anchor "
            f"set just for this verdict."
        )

    def test_maybe_cold_start_returns_none_for_skip_set(self, fresh_project):
        from roam.mcp_extras.preflight import maybe_cold_start_envelope

        # Even with no index, init / catalog skip the guard.
        assert maybe_cold_start_envelope("roam_init", fresh_project) is None
        assert maybe_cold_start_envelope("roam_catalog", fresh_project) is None

    def test_maybe_cold_start_returns_envelope_when_index_missing(self, fresh_project):
        from roam.mcp_extras.preflight import maybe_cold_start_envelope

        env = maybe_cold_start_envelope("roam_health", fresh_project)
        assert env is not None
        assert env["status"] == "index_not_built"

    def test_maybe_cold_start_returns_none_when_index_built(self, warm_project):
        from roam.mcp_extras.preflight import maybe_cold_start_envelope

        # Even for an index-gated tool, the guard is satisfied when the
        # DB exists -- the tool proceeds normally.
        assert maybe_cold_start_envelope("roam_health", warm_project) is None

    def test_disable_env_var_short_circuits(self, fresh_project, monkeypatch):
        from roam.mcp_extras.preflight import maybe_cold_start_envelope

        monkeypatch.setenv("ROAM_MCP_DISABLE_COLD_START_GUARD", "1")
        assert maybe_cold_start_envelope("roam_health", fresh_project) is None

    def test_maybe_decorate_description_appends_hint(self):
        from roam.mcp_extras.preflight import (
            INDEX_REQUIRED_HINT,
            maybe_decorate_description,
        )

        result = maybe_decorate_description("roam_health", "Health score.")
        assert INDEX_REQUIRED_HINT in result
        assert result.startswith("Health score.")
        # Idempotent.
        again = maybe_decorate_description("roam_health", result)
        assert again.count(INDEX_REQUIRED_HINT) == 1

    def test_maybe_decorate_description_skips_skip_set(self):
        from roam.mcp_extras.preflight import (
            INDEX_REQUIRED_HINT,
            maybe_decorate_description,
        )

        result = maybe_decorate_description("roam_init", "Initialize roam.")
        assert INDEX_REQUIRED_HINT not in result
        assert result == "Initialize roam."


# ---------------------------------------------------------------------------
# Wrapper integration tests -- exercise the @_tool decorator wiring
# ---------------------------------------------------------------------------


class TestColdStartGuardWiring:
    """Integration: a real @_tool-wrapped function must short-circuit."""

    def test_index_not_built_returns_cold_start_envelope(self, fresh_project):
        """The primary user bug: ``roam_health`` on a fresh project. The
        cold-start envelope replaces the hang.
        """
        from roam.mcp_server import health

        result = _run_maybe_async(health, root=str(fresh_project))
        assert isinstance(result, dict)
        assert result.get("status") == "index_not_built"
        assert "roam init" in result["summary"]["verdict"]
        assert result["next_command"] == "roam init"

    def test_dead_code_also_returns_cold_start_envelope(self, fresh_project):
        """The other tool from the original user report (``roam_dead``)."""
        from roam.mcp_server import _TOOL_METADATA

        # ``roam_dead_code`` is registered via ``@_tool``; resolve via the
        # registered-attribute name in the module rather than guessing.
        import roam.mcp_server as m

        # The function name inside the module may differ from the tool
        # name. Look it up from the module's __dict__ via attribute walk.
        fn = None
        for attr in dir(m):
            obj = getattr(m, attr)
            if callable(obj) and getattr(obj, "__name__", "").endswith(
                ("dead_code", "roam_dead_code")
            ):
                # Confirm metadata is registered for the tool.
                if "roam_dead_code" in _TOOL_METADATA:
                    fn = obj
                    break
        # Fall back to the underlying registered helper via module attr.
        if fn is None:
            fn = getattr(m, "roam_dead_code", None) or getattr(m, "dead_code", None)
        assert fn is not None, "could not locate the roam_dead_code wrapper"

        result = _run_maybe_async(fn, root=str(fresh_project))
        assert isinstance(result, dict)
        assert result.get("status") == "index_not_built"

    def test_cold_start_envelope_returns_in_under_2_seconds(self, fresh_project):
        """No hang. The guard returns BEFORE the underlying CLI can
        kick off an index build.
        """
        from roam.mcp_server import health

        t0 = time.monotonic()
        result = _run_maybe_async(health, root=str(fresh_project))
        elapsed = time.monotonic() - t0
        assert result.get("status") == "index_not_built"
        assert elapsed < 2.0, (
            f"cold-start guard took {elapsed:.3f}s -- must be under 2s to "
            f"prove no hang and no accidental indexing kick-off"
        )

    def test_setup_tool_skips_cold_start_guard(self, fresh_project):
        """``roam_catalog`` is in ``_NO_INDEX_NEEDED`` -- it must return
        its real metadata response, NOT the cold-start envelope, even on
        a project with no index.
        """
        from roam.mcp_server import roam_catalog

        result = _run_maybe_async(roam_catalog, root=str(fresh_project))
        assert isinstance(result, dict)
        # Catalog's real shape has a tool_count + tools list.
        assert result.get("status") != "index_not_built"
        assert "tools" in result or "tool_count" in (result.get("summary") or {})

    def test_existing_index_skips_cold_start_envelope(self, warm_project, monkeypatch):
        """When ``.roam/index.db`` exists, the guard is a pass-through:
        the cold-start envelope is NOT returned. (The underlying CLI may
        emit other errors because we only wrote an empty stub DB -- we
        only assert that the COLD-START path didn't fire.)
        """
        # Avoid the in-process result-cache poisoning across tests.
        from roam.mcp_server import _ROAM_RESULT_CACHE, health

        _ROAM_RESULT_CACHE.clear()

        result = _run_maybe_async(health, root=str(warm_project))
        assert isinstance(result, dict)
        # The cold-start envelope is identifiable by ``status`` ==
        # ``index_not_built`` AND ``next_command`` == ``roam init``.
        # Either field present in that combination would be the guard
        # firing; we assert it did NOT.
        assert result.get("status") != "index_not_built"

    def test_roam_doctor_works_on_fresh_project_without_index(self, fresh_project):
        """W296-followup-C: ``roam_doctor`` is in ``_NO_INDEX_NEEDED``, so it
        must actually work on a project with no ``.roam/`` directory -- not
        hang, not crash, not return a cold-start envelope. Cheap insurance
        against silent regressions in the cold-doctor path.
        """
        from roam.mcp_server import _ROAM_RESULT_CACHE, roam_doctor

        _ROAM_RESULT_CACHE.clear()

        # Time-bounded -- if doctor hangs, this test fails.
        t0 = time.monotonic()
        result = _run_maybe_async(roam_doctor, root=str(fresh_project))
        elapsed = time.monotonic() - t0

        # Assert no hang. Doctor on a fresh project runs ~20 environment
        # checks; 30s is generous and only catches a true hang.
        assert elapsed < 30.0, (
            f"roam_doctor took {elapsed:.1f}s on fresh repo (should be fast)"
        )

        # Assert NOT cold-start envelope (since doctor is in
        # ``_NO_INDEX_NEEDED`` it must produce a real diagnostic).
        assert isinstance(result, dict)
        assert result.get("status") != "index_not_built"

        # Assert structured output exists -- doctor should produce a
        # diagnostic verdict even when the index is absent.
        # W325 sealed this: the Pattern-1 Variant B pass-through in
        # ``_run_roam_inprocess`` / ``_run_roam_subprocess`` now surfaces
        # doctor's structured envelope (exit 1) annotated with
        # ``_meta.cli_exit_code`` + ``isError: True`` instead of mapping it
        # to a USAGE_ERROR envelope.
        summary = result.get("summary") or {}
        assert "verdict" in summary or "checks" in result, (
            f"roam_doctor returned no structured diagnostic on fresh repo: "
            f"keys={list(result.keys())}"
        )
