"""Retrieve error-hardening (2026-07-14) — regression guards.

Hardens the remaining unguarded paths in ``cmd_retrieve`` on top of the
W607-B outer-guard + W607-BI per-phase substrate plumbing:

1. ``--repair-intent`` input handling — missing / unreadable / non-UTF-8
   file, empty patch, non-diff patch, and ``-`` on an interactive stdin
   all raise STRUCTURED usage errors (``FILE_NOT_FOUND`` /
   ``INVALID_DIFF`` / ``EMPTY_INPUT``) instead of a raw traceback — and
   the TTY case fails fast instead of hanging forever on
   ``sys.stdin.read()`` (Pattern-1A).
2. ``symbols`` / ``symbol_fts`` count queries — previously the symbols
   count was entirely unguarded (raw traceback on a corrupt DB) and the
   fts count failed SILENTLY. Both now surface
   ``retrieve_symbol_count_failed:`` / ``retrieve_fts_count_failed:``
   markers through the W607-BI bucket.
3. Text-mode disclosure parity — a degraded text-mode run prints a
   ``DEGRADED:`` block under the verdict; previously a wholesale
   pipeline failure printed the SAME output as a legitimate
   zero-candidate result (Pattern-2 silent fallback).
4. Shape floors — a pipeline result / semantic-coverage diagnostic /
   confidence pair that RETURNS a malformed value (rather than raising)
   is normalized with a disclosed ``retrieve_*_shape_failed:`` marker
   instead of KeyError/TypeError-crashing the envelope build.

Empty bucket -> byte-identical output (both JSON and text) — pinned by
the clean-path tests below and by the pre-existing W607-B/BI guards.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def retrieve_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + FTS5 rows."""
    proj = tmp_path / "retrieve_hardening_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "def login(user, password):\n"
        "    session = create_session(user)\n"
        "    return session\n\n"
        "def create_session(user):\n"
        "    return {'user': user}\n\n"
        "def logout(session):\n"
        "    session.clear()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _invoke_retrieve(runner: CliRunner, cwd, *extra, json_mode: bool = True, input=None):
    """Invoke ``roam retrieve`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("retrieve")
    args.extend(extra)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, input=input)
    finally:
        os.chdir(old_cwd)


def _all_output(result) -> str:
    """stdout + stderr regardless of the Click version's capture mode."""
    out = result.output or ""
    try:
        err = result.stderr or ""
    except (AttributeError, ValueError):
        err = ""
    return out + err


def _all_warnings(data: dict) -> list[str]:
    return list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])


# ---------------------------------------------------------------------------
# (1) --repair-intent input handling — structured errors, no tracebacks
# ---------------------------------------------------------------------------


class TestRepairIntentInputHardening:
    def test_missing_file_is_structured_file_not_found(self, cli_runner, retrieve_project):
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", "does-not-exist.diff")
        combined = _all_output(result)
        assert result.exit_code != 0
        assert "FILE_NOT_FOUND:" in combined, combined
        assert "Traceback" not in combined, combined

    def test_directory_path_is_structured_file_not_found(self, cli_runner, retrieve_project):
        (retrieve_project / "some_dir").mkdir()
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", "some_dir")
        combined = _all_output(result)
        assert result.exit_code != 0
        assert "FILE_NOT_FOUND:" in combined, combined
        assert "Traceback" not in combined, combined

    def test_empty_patch_file_is_structured_empty_input(self, cli_runner, retrieve_project):
        patch = retrieve_project / "empty.diff"
        patch.write_text("   \n\n", encoding="utf-8")
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", str(patch))
        combined = _all_output(result)
        assert result.exit_code != 0
        assert "EMPTY_INPUT:" in combined, combined

    def test_non_diff_text_is_structured_invalid_diff(self, cli_runner, retrieve_project):
        """A patch with zero +/- change lines used to be a SILENT no-op:
        the rerank contributed nothing while the user believed it applied
        (Pattern-2). It now fails loudly with INVALID_DIFF."""
        patch = retrieve_project / "not-a-diff.txt"
        patch.write_text("this is prose, not a unified diff\nno change lines here\n", encoding="utf-8")
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", str(patch))
        combined = _all_output(result)
        assert result.exit_code != 0
        assert "INVALID_DIFF:" in combined, combined

    def test_non_utf8_file_is_structured_invalid_diff(self, cli_runner, retrieve_project):
        patch = retrieve_project / "binary.diff"
        patch.write_bytes(b"\x80\x81\xfe\xff not utf8")
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", str(patch))
        combined = _all_output(result)
        assert result.exit_code != 0
        assert "INVALID_DIFF:" in combined, combined
        assert "Traceback" not in combined, combined

    def test_stdin_dash_on_tty_fails_fast_instead_of_hanging(self, cli_runner, retrieve_project, monkeypatch):
        """Pattern-1A: ``--repair-intent -`` with an interactive stdin used
        to block forever inside ``sys.stdin.read()``. It must now raise a
        structured EMPTY_INPUT error immediately."""
        from roam.commands import cmd_retrieve

        monkeypatch.setattr(cmd_retrieve, "_stdin_is_tty", lambda: True)
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", "-")
        combined = _all_output(result)
        assert result.exit_code != 0
        assert "EMPTY_INPUT:" in combined, combined

    def test_valid_diff_file_still_works(self, cli_runner, retrieve_project):
        """Happy path preserved: a real unified diff reranks cleanly."""
        patch = retrieve_project / "fix.diff"
        patch.write_text(
            "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1,2 +1,2 @@\n-def login(user):\n+def login(user, password):\n",
            encoding="utf-8",
        )
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", str(patch))
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        assert data["command"] == "retrieve"

    def test_valid_diff_on_stdin_still_works(self, cli_runner, retrieve_project):
        diff_text = (
            "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1,2 +1,2 @@\n-def login(user):\n+def login(user, password):\n"
        )
        result = _invoke_retrieve(cli_runner, retrieve_project, "login", "--repair-intent", "-", input=diff_text)
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        assert data["command"] == "retrieve"


# ---------------------------------------------------------------------------
# (2) Count-query substrate boundaries
# ---------------------------------------------------------------------------


class TestCountQueryHardening:
    def test_symbol_count_failure_surfaces_marker(self, cli_runner, retrieve_project, monkeypatch):
        """A corrupt-DB symbols count used to be a raw traceback (the query
        was entirely unguarded). It must now degrade to a structured
        envelope with a retrieve_symbol_count_failed: marker."""
        import sqlite3 as _sqlite3

        from roam.commands import cmd_retrieve

        def _boom_symbols(conn):
            raise _sqlite3.OperationalError("no such table: symbols (synthetic)")

        monkeypatch.setattr(cmd_retrieve, "_count_symbols", _boom_symbols)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_symbol_count_failed:")]
        assert markers, f"expected retrieve_symbol_count_failed: marker; got {_all_warnings(data)!r}"
        assert data["summary"].get("partial_success") is True

    def test_fts_count_failure_is_disclosed_not_silent(self, cli_runner, retrieve_project, monkeypatch):
        """The fts count used to swallow sqlite3.Error with NO marker."""
        import sqlite3 as _sqlite3

        from roam.commands import cmd_retrieve

        def _boom_fts(conn):
            raise _sqlite3.OperationalError("no such table: symbol_fts (synthetic)")

        monkeypatch.setattr(cmd_retrieve, "_count_fts_rows", _boom_fts)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_fts_count_failed:")]
        assert markers, f"expected retrieve_fts_count_failed: marker; got {_all_warnings(data)!r}"
        # An unreadable count must NOT be reported as an empty index.
        assert "search index is empty" not in data["summary"]["verdict"]

    def test_unreadable_symbol_count_never_claims_empty_index(self, cli_runner, retrieve_project, monkeypatch):
        """Pattern-2: 'count unavailable' and 'index empty' are different
        states — the empty-index remediation must not fire on a failed
        count."""
        import sqlite3 as _sqlite3

        from roam.commands import cmd_retrieve

        def _boom_symbols(conn):
            raise _sqlite3.OperationalError("synthetic")

        monkeypatch.setattr(cmd_retrieve, "_count_symbols", _boom_symbols)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        assert "search index is empty" not in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (3) Text-mode disclosure parity
# ---------------------------------------------------------------------------


class TestTextModeDisclosure:
    def test_degraded_text_run_prints_degraded_block(self, cli_runner, retrieve_project, monkeypatch):
        from roam.commands import cmd_retrieve

        def _boom_semantic(conn):
            raise RuntimeError("synthetic-semantic-text-mode")

        monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_semantic)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=False)
        assert result.exit_code == 0, _all_output(result)
        assert "DEGRADED:" in result.output, result.output
        assert "retrieve_compute_semantic_coverage_failed:" in result.output, result.output

    def test_total_pipeline_failure_text_mode_is_not_silent(self, cli_runner, retrieve_project, monkeypatch):
        """THE Pattern-2 fix: both pipelines raising used to print the same
        'No candidates matched the task text' as a legitimate empty
        result. Text mode must now disclose the degradation."""
        from roam.commands import cmd_retrieve

        def _boom_full(conn, task_str, *, budget, k, rerank, seed_files, repair_intent=None):
            raise RuntimeError("synthetic-full-failure")

        def _boom_lexical(conn, task_str, *, budget, k, seed_files):
            raise RuntimeError("synthetic-lexical-failure")

        monkeypatch.setattr(cmd_retrieve, "_fts5_search_full", _boom_full)
        monkeypatch.setattr(cmd_retrieve, "_fts5_search_lexical_only", _boom_lexical)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=False)
        assert result.exit_code == 0, _all_output(result)
        assert "DEGRADED:" in result.output, result.output
        assert "retrieve_pipeline_failed:" in result.output, result.output

    def test_clean_text_run_has_no_degraded_block(self, cli_runner, retrieve_project):
        """Byte-identical clean path: no DEGRADED block on a healthy run."""
        result = _invoke_retrieve(cli_runner, retrieve_project, "login session", json_mode=False)
        assert result.exit_code == 0, _all_output(result)
        assert "DEGRADED:" not in result.output, result.output

    def test_suggest_refinements_failure_in_text_mode_does_not_crash(self, cli_runner, retrieve_project, monkeypatch):
        """_suggest_refinements was previously called UNGUARDED in text
        mode (only the JSON path wrapped it)."""
        from roam.commands import cmd_retrieve

        def _boom_refine(task, candidates):
            raise RuntimeError("synthetic-refine-text-mode")

        def _forced_low_conf(candidates, task=""):
            return 0.10, "low"

        monkeypatch.setattr(cmd_retrieve, "_suggest_refinements", _boom_refine)
        monkeypatch.setattr(cmd_retrieve, "_retrieve_confidence_score", _forced_low_conf)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=False)
        assert result.exit_code == 0, _all_output(result)
        # If candidates surfaced, the failure must be disclosed.
        if "candidates" in result.output or "VERDICT:" in result.output:
            assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# (4) Shape floors — malformed RETURNS (not raises) degrade with disclosure
# ---------------------------------------------------------------------------


class TestShapeFloors:
    def test_partial_pipeline_result_fills_keys_with_marker(self, cli_runner, retrieve_project, monkeypatch):
        from roam.commands import cmd_retrieve

        def _partial_result(conn, task_str, *, budget, k, rerank, seed_files, repair_intent=None):
            return {"candidates": []}  # missing task/rerank/seeds/budget/... keys

        monkeypatch.setattr(cmd_retrieve, "_fts5_search_full", _partial_result)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_result_shape_failed:")]
        assert markers, f"expected retrieve_result_shape_failed: marker; got {_all_warnings(data)!r}"
        assert data["summary"].get("partial_success") is True
        # The envelope still carries every summary key downstream consumers read.
        for key in ("candidates", "total_candidates", "budget", "budget_used", "k", "rerank"):
            assert key in data["summary"], f"summary missing {key}: {data['summary']!r}"

    def test_non_dict_pipeline_result_degrades_with_marker(self, cli_runner, retrieve_project, monkeypatch):
        from roam.commands import cmd_retrieve

        def _garbage_result(conn, task_str, *, budget, k, rerank, seed_files, repair_intent=None):
            return ["not", "a", "dict"]

        monkeypatch.setattr(cmd_retrieve, "_fts5_search_full", _garbage_result)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_result_shape_failed:")]
        assert markers, f"expected retrieve_result_shape_failed: marker; got {_all_warnings(data)!r}"

    def test_malformed_semantic_diag_fills_defaults_with_marker(self, cli_runner, retrieve_project, monkeypatch):
        from roam.commands import cmd_retrieve

        monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", lambda conn: {})

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_semantic_coverage_shape_failed:")]
        assert markers, f"expected retrieve_semantic_coverage_shape_failed: marker; got {_all_warnings(data)!r}"
        assert data["summary"]["semantic_embeddings"] == 0

    def test_malformed_confidence_pair_degrades_with_marker(self, cli_runner, retrieve_project, monkeypatch):
        from roam.commands import cmd_retrieve

        monkeypatch.setattr(cmd_retrieve, "_retrieve_confidence_score", lambda candidates, task="": "garbage")

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_confidence_shape_failed:")]
        assert markers, f"expected retrieve_confidence_shape_failed: marker; got {_all_warnings(data)!r}"
        assert data["summary"]["confidence"] == 0.0


# ---------------------------------------------------------------------------
# (5) Empty-index early return carries accumulated markers
# ---------------------------------------------------------------------------


class TestEmptyIndexEarlyReturnDisclosure:
    def test_empty_index_envelope_carries_markers(self, cli_runner, retrieve_project, monkeypatch):
        from roam.commands import cmd_retrieve
        from roam.db.connection import open_db

        with open_db(readonly=False) as conn:
            conn.execute("DELETE FROM symbol_fts")
            conn.commit()

        def _boom_semantic(conn):
            raise RuntimeError("synthetic-semantic-early-return")

        monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_semantic)

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        assert "search index is empty" in data["summary"]["verdict"]
        markers = [m for m in _all_warnings(data) if m.startswith("retrieve_compute_semantic_coverage_failed:")]
        assert markers, f"empty-index early return must not drop accumulated markers; got {_all_warnings(data)!r}"
        assert data["summary"].get("partial_success") is True

    def test_clean_empty_index_envelope_stays_marker_free(self, cli_runner, retrieve_project):
        from roam.db.connection import open_db

        with open_db(readonly=False) as conn:
            conn.execute("DELETE FROM symbol_fts")
            conn.commit()

        result = _invoke_retrieve(cli_runner, retrieve_project, "login")
        assert result.exit_code == 0, _all_output(result)
        data = _json.loads(result.output)
        assert "search index is empty" in data["summary"]["verdict"]
        assert "warnings_out" not in data
        assert "warnings_out" not in data["summary"]
