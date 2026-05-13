"""Tests for `roam ws resolve` surfacing of unmatched frontend URLs.

The user's dogfood reports that frontends with high unmatched counts
(potential 404s) had no signal in the structured output. The envelope
must include an ``unmatched`` array and an ``unmatched_count`` summary
field; verdict must mention both matched AND unmatched counts.

See ``internal/dogfood/`` patterns 1, 2, 6 and LAW 6 in ``CLAUDE.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Unit tests — find_unmatched_calls helper
# ---------------------------------------------------------------------------


class TestFindUnmatchedCalls:
    """Direct tests for the new ``find_unmatched_calls`` helper."""

    def _call(self, **kwargs):
        return {
            "symbol_id": kwargs.get("symbol_id", 1),
            "url_pattern": kwargs["url"],
            "http_method": kwargs.get("method", "GET"),
            "file_path": kwargs.get("file", "src/views/Foo.vue"),
            "line": kwargs.get("line", 10),
            "symbol_name": kwargs.get("symbol_name", "loadFoo"),
        }

    def _route(self, **kwargs):
        return {
            "symbol_id": kwargs.get("symbol_id", 100),
            "url_pattern": kwargs["url"],
            "http_method": kwargs.get("method", "GET"),
            "file_path": kwargs.get("file", "routes.php"),
            "line": kwargs.get("line", 5),
            "symbol_name": kwargs.get("symbol_name", "handler"),
        }

    def test_empty_input_returns_empty(self):
        from roam.workspace.api_scanner import find_unmatched_calls

        assert find_unmatched_calls([], [], []) == []

    def test_all_matched_returns_empty(self):
        from roam.workspace.api_scanner import (
            find_unmatched_calls,
            match_api_endpoints,
        )

        calls = [self._call(url="/api/users", method="GET")]
        routes = [self._route(url="/users", method="GET")]
        matches = match_api_endpoints(calls, routes)
        assert len(matches) == 1
        unmatched = find_unmatched_calls(calls, routes, matches)
        assert unmatched == []

    def test_unknown_path_classified(self):
        from roam.workspace.api_scanner import (
            find_unmatched_calls,
            match_api_endpoints,
        )

        # Frontend calls /api/legacy/old but backend only has /products.
        calls = [
            self._call(
                url="/api/legacy/old",
                method="POST",
                file="src/composables/useOld.ts",
                line=12,
            )
        ]
        routes = [self._route(url="/products", method="GET")]
        matches = match_api_endpoints(calls, routes)
        unmatched = find_unmatched_calls(calls, routes, matches)
        assert len(unmatched) == 1
        u = unmatched[0]
        assert u["url"] == "/api/legacy/old"
        assert u["method"] == "POST"
        assert u["frontend_file"] == "src/composables/useOld.ts"
        assert u["reason"] == "unknown_path"

    def test_method_mismatch_classified(self):
        from roam.workspace.api_scanner import (
            find_unmatched_calls,
            match_api_endpoints,
        )

        # Same URL, but backend only exposes GET and frontend POSTs.
        calls = [self._call(url="/api/users", method="POST")]
        routes = [self._route(url="/users", method="GET")]
        matches = match_api_endpoints(calls, routes)
        # match_api_endpoints excludes method mismatches → no match.
        assert matches == []
        unmatched = find_unmatched_calls(calls, routes, matches)
        assert len(unmatched) == 1
        assert unmatched[0]["reason"] == "method_mismatch"
        # reason_detail should name the accepted method.
        assert "GET" in unmatched[0]["reason_detail"]

    def test_path_variable_mismatch_classified(self):
        from roam.workspace.api_scanner import (
            find_unmatched_calls,
            match_api_endpoints,
        )

        # Backend has /api/users/{id}; frontend calls /api/users/{id}/avatar
        # — same prefix `/users` but extra segment.
        calls = [self._call(url="/api/users/${id}/avatar", method="GET")]
        routes = [self._route(url="/users/{id}", method="GET")]
        matches = match_api_endpoints(calls, routes)
        assert matches == []
        unmatched = find_unmatched_calls(calls, routes, matches)
        assert len(unmatched) == 1
        assert unmatched[0]["reason"] == "path_variable_mismatch"


# ---------------------------------------------------------------------------
# CLI integration tests — envelope shape
# ---------------------------------------------------------------------------


def _git_env() -> dict:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }


def _git_init_repo(repo: Path) -> None:
    env = _git_env()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )


def _build_workspace(tmp_path: Path, *, full_match: bool) -> Path:
    """Build a workspace with frontend + backend repos.

    When ``full_match=True``: every frontend URL has a matching backend
    route. When False: the frontend has one matched URL and at least two
    unmatched URLs (an unknown path and a method-mismatch).
    """
    from click.testing import CliRunner

    from roam.cli import cli

    ws = tmp_path / "ws"
    ws.mkdir()
    fe = ws / "frontend"
    be = ws / "backend"
    fe.mkdir()
    be.mkdir()
    (fe / "src").mkdir()
    (be / "src").mkdir()

    # The api_scanner's regex path picks up `api.get(...)` / `api.post(...)`
    # client calls — that's the most reliable way to surface fetch-style
    # calls in tests because the bare `fetch('/path')` shape doesn't
    # always produce a target-name edge to `fetch` after indexing.
    if full_match:
        (fe / "src" / "client.ts").write_text(
            'async function loadUsers() {\n'
            '  return await api.get("/api/users");\n'
            '}\n',
            encoding="utf-8",
        )
    else:
        (fe / "src" / "client.ts").write_text(
            'async function loadUsers() {\n'
            '  return await api.get("/api/users");\n'
            '}\n'
            'async function loadGhost() {\n'
            '  return await api.get("/api/legacy/ghost");\n'
            '}\n'
            'async function postUsers() {\n'
            '  return await api.post("/api/users");\n'
            '}\n',
            encoding="utf-8",
        )

    # Backend exposes only GET /api/users.
    (be / "src" / "routes.ts").write_text(
        'const router = {\n'
        '  get(path, handler) {},\n'
        '  post(path, handler) {},\n'
        '};\n'
        'router.get("/api/users", () => ({ users: [] }));\n',
        encoding="utf-8",
    )

    for repo in (fe, be):
        _git_init_repo(repo)

    runner = CliRunner()
    old_cwd = os.getcwd()
    for repo in (fe, be):
        os.chdir(str(repo))
        try:
            runner.invoke(cli, ["index"])
        finally:
            os.chdir(old_cwd)

    config = {
        "workspace": "ws",
        "repos": [
            {"path": str(fe), "name": "frontend", "role": "frontend"},
            {"path": str(be), "name": "backend", "role": "backend"},
        ],
        "connections": [
            {"type": "rest-api", "frontend": "frontend", "backend": "backend"}
        ],
    }
    (ws / ".roam-workspace.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return ws


@pytest.fixture
def ws_partial(tmp_path):
    return _build_workspace(tmp_path, full_match=False)


@pytest.fixture
def ws_full(tmp_path):
    return _build_workspace(tmp_path, full_match=True)


class TestResolveEnvelopeUnmatched:
    """CLI integration: assert envelope shape after the fix."""

    def _invoke_json(self, ws: Path, monkeypatch) -> dict:
        from click.testing import CliRunner

        from roam.cli import cli

        monkeypatch.chdir(ws)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "ws", "resolve"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    def test_envelope_includes_unmatched_array(self, ws_partial, monkeypatch):
        data = self._invoke_json(ws_partial, monkeypatch)
        assert "unmatched" in data
        assert isinstance(data["unmatched"], list)
        assert len(data["unmatched"]) >= 1, data

    def test_unmatched_entries_have_required_fields(self, ws_partial, monkeypatch):
        data = self._invoke_json(ws_partial, monkeypatch)
        assert data["unmatched"], "expected at least one unmatched entry"
        for entry in data["unmatched"]:
            assert "frontend_file" in entry
            assert "url" in entry
            assert "method" in entry
            assert "reason" in entry
            # reason is a closed enum.
            assert entry["reason"] in {
                "unknown_path",
                "method_mismatch",
                "path_variable_mismatch",
            }

    def test_partial_match_state_when_unmatched_nonzero(self, ws_partial, monkeypatch):
        data = self._invoke_json(ws_partial, monkeypatch)
        summary = data["summary"]
        assert summary["state"] == "partial_match"
        assert summary["partial_success"] is True
        assert summary["unmatched_count"] >= 1
        # Verdict must mention BOTH matched and unmatched counts (LAW 6).
        assert "unmatched" in summary["verdict"].lower()
        assert str(summary["matched_count"]) in summary["verdict"]

    def test_full_match_clean_state(self, ws_full, monkeypatch):
        data = self._invoke_json(ws_full, monkeypatch)
        summary = data["summary"]
        assert summary["state"] == "ok"
        assert summary["partial_success"] is False
        assert summary["unmatched_count"] == 0
        assert data["unmatched"] == []

    def test_summary_match_rate_correct(self, ws_partial, monkeypatch):
        data = self._invoke_json(ws_partial, monkeypatch)
        summary = data["summary"]
        matched = summary["matched_count"]
        total = summary["frontend_calls"]
        assert total > 0
        expected_rate = round(matched / total, 4)
        assert summary["match_rate"] == expected_rate
        assert summary["match_pct"] == round(100 * matched / total)

    def test_agent_contract_facts_present(self, ws_partial, monkeypatch):
        data = self._invoke_json(ws_partial, monkeypatch)
        assert "agent_contract" in data
        contract = data["agent_contract"]
        assert "facts" in contract
        assert len(contract["facts"]) >= 1
        # First fact should mention the unmatched count.
        assert any("not match" in f or "unmatched" in f.lower() for f in contract["facts"])
