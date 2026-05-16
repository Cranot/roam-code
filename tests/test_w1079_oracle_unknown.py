"""W1079 — closest-match suggestions on `roam oracle batch` unknown names.

Drive-by from the W1074 audit: `cmd_oracle.oracle_batch_cmd` had a
per-line result-row append shape on unknown oracle name (line 664,
distinct from W1066/W1074's UsageError + warning pattern). Each
unknown-oracle row now carries a `did_you_mean: list[str]` field
populated via `difflib.get_close_matches(cutoff=0.6, n=2)`; text mode
appends the same suggestion fragment to the error column.

Scenarios:
1. Known oracle name → row has no `error` and no `did_you_mean`.
2. Unknown oracle with a close match ("symbol_exists" → "symbol-exists").
3. Unknown oracle with NO close match → `did_you_mean: []`.
4. Mixed batch: known rows clean, unknown rows annotated.
5. Text-mode rendering includes "Did you mean: ..." when matches exist.

The batch runner is driven via stdin JSONL — uses CliRunner with
``input=`` to avoid filesystem fixtures and keeps the test self-contained.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_oracle import _KNOWN_ORACLE_NAMES, _suggest_oracle_names
from tests.conftest import make_src_project as _make_project

_FIXTURE = {
    "auth.py": """
        def handle_login(user):
            return user

        def main():
            return handle_login("alice")
    """,
}


@pytest.fixture
def indexed_project(tmp_path: Path) -> Path:
    proj = _make_project(tmp_path, _FIXTURE)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["index"])
        assert result.exit_code == 0, result.output
        yield proj
    finally:
        os.chdir(old_cwd)


def _run_batch(lines: list[dict]) -> dict:
    """Invoke `roam --json oracle batch -` with one JSONL line per dict."""
    runner = CliRunner()
    stdin_payload = "\n".join(json.dumps(spec) for spec in lines) + "\n"
    result = runner.invoke(cli, ["--json", "oracle", "batch", "--input", "-"], input=stdin_payload)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# Pure-unit tests on the helper — no DB needed
# ---------------------------------------------------------------------------


class TestSuggestOracleNames:
    def test_typo_underscore_for_dash(self):
        # The canonical typo the W1079 audit flagged.
        assert _suggest_oracle_names("symbol_exists") == ["symbol-exists"]

    def test_unknown_no_close_match(self):
        # Wholly unrelated input — nothing clears cutoff=0.6.
        assert _suggest_oracle_names("zzz_completely_unrelated_xyz") == []

    def test_close_match_short_name(self):
        matches = _suggest_oracle_names("route-exits")
        assert matches == ["route-exists"]

    def test_known_oracle_names_immutable_sorted(self):
        # Drift guard: registry is hardcoded, must stay aligned with the
        # if/elif dispatch in oracle_batch_cmd. Sorted for deterministic
        # suggestion order when difflib returns equal-score matches.
        assert _KNOWN_ORACLE_NAMES == tuple(sorted(_KNOWN_ORACLE_NAMES))
        assert "symbol-exists" in _KNOWN_ORACLE_NAMES
        assert "is-clone-of" in _KNOWN_ORACLE_NAMES


# ---------------------------------------------------------------------------
# End-to-end batch tests — actual CLI invocation
# ---------------------------------------------------------------------------


class TestOracleBatchUnknownName:
    def test_known_oracle_row_has_no_did_you_mean(self, indexed_project):
        env = _run_batch([{"oracle": "symbol-exists", "args": {"name": "handle_login"}}])
        results = env["results"]
        assert len(results) == 1
        row = results[0]
        assert "error" not in row
        assert "did_you_mean" not in row
        assert row["oracle"] == "symbol-exists"

    def test_unknown_oracle_with_close_match(self, indexed_project):
        env = _run_batch([{"oracle": "symbol_exists", "args": {"name": "handle_login"}}])
        results = env["results"]
        assert len(results) == 1
        row = results[0]
        assert "error" in row
        assert "unknown oracle" in row["error"]
        assert row["did_you_mean"] == ["symbol-exists"]

    def test_unknown_oracle_no_close_match(self, indexed_project):
        env = _run_batch([{"oracle": "zzz_completely_unrelated_xyz", "args": {"name": "x"}}])
        results = env["results"]
        assert len(results) == 1
        row = results[0]
        assert "error" in row
        assert row["did_you_mean"] == []

    def test_mixed_batch_known_and_unknown(self, indexed_project):
        env = _run_batch(
            [
                {"oracle": "symbol-exists", "args": {"name": "handle_login"}},
                {"oracle": "symbol_exists", "args": {"name": "handle_login"}},
                {"oracle": "totally_made_up_name", "args": {"name": "x"}},
            ]
        )
        results = env["results"]
        assert len(results) == 3
        # Known row stays clean — no did_you_mean leakage.
        assert "error" not in results[0]
        assert "did_you_mean" not in results[0]
        # Close-typo row gets the suggestion.
        assert results[1]["did_you_mean"] == ["symbol-exists"]
        # No-close-match row has empty did_you_mean.
        assert results[2]["did_you_mean"] == []


class TestOracleBatchTextMode:
    def test_text_mode_includes_did_you_mean(self, indexed_project):
        runner = CliRunner()
        stdin_payload = json.dumps({"oracle": "symbol_exists", "args": {"name": "handle_login"}}) + "\n"
        result = runner.invoke(cli, ["oracle", "batch", "--input", "-"], input=stdin_payload)
        assert result.exit_code == 0, result.output
        assert "unknown oracle 'symbol_exists'" in result.output
        assert "Did you mean: 'symbol-exists'" in result.output

    def test_text_mode_no_did_you_mean_when_empty(self, indexed_project):
        runner = CliRunner()
        stdin_payload = json.dumps({"oracle": "zzz_completely_unrelated_xyz", "args": {"name": "x"}}) + "\n"
        result = runner.invoke(cli, ["oracle", "batch", "--input", "-"], input=stdin_payload)
        assert result.exit_code == 0, result.output
        assert "unknown oracle" in result.output
        assert "Did you mean" not in result.output
