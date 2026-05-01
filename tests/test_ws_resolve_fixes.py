"""Regression tests for GH issues #18 and #19 (workspace resolve).

Issue #18: ``roam ws resolve`` exits in <0.1s with "Cross-repo edges: 0"
when ``connections: []``, even when frontend+backend roles are tagged.
Now: auto-derive pairs from role tags and emit a clear note.

Issue #19: ``scan_frontend_api_calls`` matches every reference to
``get/post/...`` regardless of receiver — false-positives on
``app.get('/x', handler)`` route definitions in polyglot frontends.
Now: skip lines that match server route patterns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Issue #19 — false-positive route handlers in scan_frontend_api_calls
# ---------------------------------------------------------------------------


class TestRouteDefinitionGuard:
    """Unit tests for the new ``_looks_like_route_definition`` helper."""

    def _looks_like(self, source: str, line_num: int = 1) -> bool:
        # Helper supports a Path; create a tmp file inline.
        import tempfile

        from roam.workspace.api_scanner import _looks_like_route_definition

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False, encoding="utf-8") as fh:
            fh.write(source)
            tmp = Path(fh.name)
        try:
            return _looks_like_route_definition(tmp, line_num)
        finally:
            tmp.unlink(missing_ok=True)

    def test_express_route_handler_detected(self):
        assert self._looks_like("app.get('/users', () => ({ users: [] }));")

    def test_router_route_handler_detected(self):
        assert self._looks_like("router.post('/api/users', handler);")

    def test_server_route_handler_detected(self):
        assert self._looks_like("server.delete('/health', handler);")

    def test_route_handler_with_async_arrow(self):
        assert self._looks_like("app.put('/items/:id', async (req, res) => res.json({}));")

    def test_python_decorator_route_detected(self):
        assert self._looks_like("@app.get('/health')")

    def test_laravel_route_detected(self):
        assert self._looks_like("Route::get('/users', UsersController::class);")

    def test_real_client_call_axios_not_flagged(self):
        assert not self._looks_like("const data = await axios.get('/api/users');")

    def test_real_client_call_fetch_not_flagged(self):
        assert not self._looks_like("const r = await fetch('/api/users');")

    def test_real_client_call_via_api_object(self):
        assert not self._looks_like("await api.get('/users')")

    def test_unknown_receiver_not_flagged(self):
        """Conservative: don't flag arbitrary `something.get(...)` as a route."""
        assert not self._looks_like("const v = obj.get('key');")

    def test_missing_file_returns_false(self):
        from roam.workspace.api_scanner import _looks_like_route_definition

        assert _looks_like_route_definition(Path("/does/not/exist.ts"), 1) is False

    def test_line_zero_returns_false(self):
        assert not self._looks_like("app.get('/x', handler)", line_num=0)

    def test_line_out_of_range_returns_false(self):
        assert not self._looks_like("app.get('/x', handler)", line_num=99)


# ---------------------------------------------------------------------------
# Issue #18 — empty connections array silent
# ---------------------------------------------------------------------------


class TestEmptyConnectionsBehaviour:
    """The CLI must not silently produce zero edges when roles are tagged
    but the connections array hasn't been populated.
    """

    @pytest.fixture
    def workspace_with_roles(self, tmp_path):
        """Build a workspace with two repos tagged but `connections: []`."""
        import json
        import os
        import subprocess
        import textwrap

        from click.testing import CliRunner

        from roam.cli import cli

        ws = tmp_path / "ws"
        ws.mkdir()
        fe = ws / "frontend"
        be = ws / "backend"
        fe.mkdir()
        be.mkdir()

        # Minimal source files so `roam index` succeeds.
        (fe / "src").mkdir()
        (be / "src").mkdir()
        (fe / "src" / "client.ts").write_text(
            textwrap.dedent(
                """\
                async function loadUsers() {
                  return await fetch('/api/users');
                }
                """
            ),
            encoding="utf-8",
        )
        (be / "src" / "routes.ts").write_text(
            textwrap.dedent(
                """\
                const router = {
                  get(path, handler) {},
                };
                router.get('/api/users', () => ({ users: [] }));
                """
            ),
            encoding="utf-8",
        )

        # Init each repo as a git repo + index it.
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        for repo in (fe, be):
            subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
            subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init", "--allow-empty"],
                cwd=str(repo),
                capture_output=True,
                env=env,
            )

        runner = CliRunner()
        old_cwd = os.getcwd()
        for repo in (fe, be):
            os.chdir(str(repo))
            try:
                runner.invoke(cli, ["index"])
            finally:
                os.chdir(old_cwd)

        # Write a workspace config with roles set but `connections: []`.
        config = {
            "workspace": "ws",
            "repos": [
                {"path": str(fe), "name": "frontend", "role": "frontend"},
                {"path": str(be), "name": "backend", "role": "backend"},
            ],
            "connections": [],
        }
        (ws / ".roam-workspace.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        return ws

    def test_resolve_warns_and_auto_derives(self, workspace_with_roles, monkeypatch):
        """With `connections: []` but roles tagged, resolve should auto-derive
        pairs in-memory and emit a clear note (instead of exiting in 0.04s)."""
        from click.testing import CliRunner

        from roam.cli import cli

        monkeypatch.chdir(workspace_with_roles)
        runner = CliRunner()
        result = runner.invoke(cli, ["ws", "resolve"])
        assert result.exit_code == 0, result.output
        # Auto-derivation note must mention the pair.
        assert "auto-derived" in result.output or "frontend -> backend" in result.output

    def test_resolve_warns_when_no_roles(self, tmp_path, monkeypatch):
        """With `connections: []` and no role tags, emit a clear warning."""
        import json
        import os
        import subprocess

        from click.testing import CliRunner

        from roam.cli import cli

        ws = tmp_path / "ws_no_roles"
        ws.mkdir()
        repo = ws / "repo"
        repo.mkdir()
        (repo / "x.txt").write_text("placeholder", encoding="utf-8")
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo),
            capture_output=True,
            env=env,
        )

        config = {
            "workspace": "ws_no_roles",
            "repos": [{"path": str(repo), "name": "repo"}],
            "connections": [],
        }
        (ws / ".roam-workspace.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        monkeypatch.chdir(ws)
        runner = CliRunner()
        result = runner.invoke(cli, ["ws", "resolve"])
        # Either exits 0 with a warning, or non-zero with the same warning —
        # both are acceptable improvements over the v11 silent zero-edges.
        assert "Warning" in result.output or "warning" in result.output, result.output
        assert "role" in result.output.lower(), result.output

    def test_init_next_steps_mentions_role_tagging(self, tmp_path, monkeypatch):
        """`ws init` next-steps output must mention that role tagging
        drives the auto-derivation of connections.
        """
        import os
        import subprocess

        from click.testing import CliRunner

        from roam.cli import cli

        ws = tmp_path / "init_proj"
        ws.mkdir()
        repo = ws / "repo"
        repo.mkdir()
        (repo / "x.txt").write_text("x", encoding="utf-8")
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "i"],
            cwd=str(repo),
            capture_output=True,
            env=env,
        )

        monkeypatch.chdir(ws)
        runner = CliRunner()
        result = runner.invoke(cli, ["ws", "init", str(repo), "--name", "init_proj"])
        assert result.exit_code == 0, result.output
        # When roles aren't auto-detected, the user must learn about the
        # tagging requirement BEFORE running resolve.
        assert "role" in result.output.lower(), result.output
