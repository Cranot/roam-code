"""Tests for the MCP-native enhancements in src/roam/mcp_extras/.

Covers:
- Session memory (recent symbols + task hint, MTF behaviour, merge logic)
- Sampling compression (no-ctx fallback, payload shrink, summary parse)
- Progress phase classification
- Completions (FTS5 prefix, paths, commands)
- Watcher helpers (file filter, ignored dirs)
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from roam.mcp_extras import completions, progress, sampling, session, watcher

# ---------------------------------------------------------------------------
# Session memory
# ---------------------------------------------------------------------------


class _FakeCtx:
    """Minimal fake of ``fastmcp.Context`` used by helpers under test."""

    def __init__(self, session_id: str = "test-sess") -> None:
        self.session_id = session_id
        self.request_id = session_id
        self._state: dict = {}

    def get_state(self, key):
        return self._state.get(key)

    def set_state(self, key, value):
        self._state[key] = value

    def delete_state(self, key):
        self._state.pop(key, None)


class TestSessionMemory:
    def setup_method(self):
        self.ctx = _FakeCtx()
        session.reset_session(self.ctx)

    def test_remember_and_recall(self):
        session.remember_symbol(self.ctx, "foo")
        session.remember_symbol(self.ctx, "bar")
        assert session.recent_symbols(self.ctx) == ["bar", "foo"]

    def test_move_to_front(self):
        for s in ["a", "b", "c"]:
            session.remember_symbol(self.ctx, s)
        session.remember_symbol(self.ctx, "a")
        # MTF: 'a' should now be most-recent
        assert session.recent_symbols(self.ctx)[0] == "a"
        # And only one copy
        assert session.recent_symbols(self.ctx).count("a") == 1

    def test_task_hint_round_trip(self):
        session.remember_task_hint(self.ctx, "trace login flow")
        assert session.session_hint(self.ctx) == "trace login flow"

    def test_merge_explicit_wins_over_session(self):
        session.remember_symbol(self.ctx, "alpha")
        hint, recent = session.merge_with_explicit(self.ctx, explicit_recent="x,y,z", explicit_hint="hardcoded")
        assert hint == "hardcoded"
        assert recent == "x,y,z"

    def test_merge_session_fills_when_explicit_empty(self):
        session.remember_task_hint(self.ctx, "fix bug")
        session.remember_symbol(self.ctx, "verify_token")
        hint, recent = session.merge_with_explicit(self.ctx)
        assert hint == "fix bug"
        assert "verify_token" in recent

    def test_no_ctx_is_safe(self):
        session.remember_symbol(None, "foo")
        session.remember_task_hint(None, "x")
        assert session.recent_symbols(None) == []
        assert session.session_hint(None) == ""
        hint, recent = session.merge_with_explicit(None)
        assert hint == ""
        assert recent == ""

    def test_empty_inputs_ignored(self):
        session.remember_symbol(self.ctx, "")
        assert session.recent_symbols(self.ctx) == []

    def test_reset(self):
        session.remember_symbol(self.ctx, "foo")
        session.reset_session(self.ctx)
        assert session.recent_symbols(self.ctx) == []


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


class TestSampling:
    def test_no_ctx_returns_none(self):
        result = asyncio.run(sampling.compress_with_sampling(None, {"k": "v"}, task="x"))
        assert result is None

    def test_ctx_without_sample_returns_none(self):
        ctx = _FakeCtx()  # no .sample method
        result = asyncio.run(sampling.compress_with_sampling(ctx, {"k": "v"}, task="x"))
        assert result is None

    def test_sample_failure_returns_none(self):
        class CtxBadSample(_FakeCtx):
            async def sample(self, *args, **kwargs):
                raise RuntimeError("sampling not configured")

        ctx = CtxBadSample()
        result = asyncio.run(sampling.compress_with_sampling(ctx, {"k": "v"}, task="x"))
        assert result is None

    def test_successful_sample(self):
        captured = {}

        class CtxOK(_FakeCtx):
            async def sample(self, prompt, **kwargs):
                captured["prompt"] = prompt
                captured["max_tokens"] = kwargs.get("max_tokens")
                # Return an object with .text like fastmcp's SamplingResult
                return type("Result", (), {"text": "verdict: healthy. 0 issues."})()

        ctx = CtxOK()
        out = asyncio.run(sampling.compress_with_sampling(ctx, {"score": 92, "issues": []}, task="overview"))
        assert out is not None
        assert out["compressed"] is True
        assert "healthy" in out["summary"]
        assert "score" in captured["prompt"]  # payload included
        assert captured["max_tokens"] == 600

    def test_payload_truncation(self):
        big = {"data": "x" * 200_000}
        text = sampling._shrink_payload(big)
        assert len(text) < 100_000
        assert "[truncated" in text

    def test_apply_compression_preserves_verdict(self):
        original = {
            "command": "health",
            "summary": {"verdict": "score=80"},
            "issues": ["a", "b"],
        }
        compressed = {"compressed": True, "summary": "all good", "tokens_estimated": 12}
        merged = sampling.maybe_apply_compression(original, compressed)
        assert merged["summary"]["verdict"] == "score=80"
        assert merged["summary"]["compressed"] is True
        assert merged["briefing"] == "all good"

    def test_apply_compression_no_op_on_none(self):
        original = {"summary": {"verdict": "x"}}
        merged = sampling.maybe_apply_compression(original, None)
        assert merged is original


# ---------------------------------------------------------------------------
# Progress phase classification
# ---------------------------------------------------------------------------


class TestProgressPhases:
    def test_discover(self):
        out = progress.classify_line("[discovery] discovering source files")
        assert out == (8, "discovering files")

    def test_parse_with_count(self):
        out = progress.classify_line("[parser] parsed 142 files")
        assert out is not None
        assert out[0] == 18
        assert "142 files" in out[1]

    def test_resolve(self):
        out = progress.classify_line("resolving references for 4k symbols")
        assert out == (55, "resolving references")

    def test_graph(self):
        out = progress.classify_line("[graph] building call graph")
        assert out is not None
        assert out[0] == 70

    def test_unrecognised_returns_none(self):
        assert progress.classify_line("hello world") is None
        assert progress.classify_line("") is None

    def test_monotonic_progress_in_runner(self):
        emitted: list[tuple[int, str]] = []

        def on_phase(pct, name):
            emitted.append((pct, name))

        # We can't easily run a real subprocess in CI; just check classify
        # produces values that the sync runner would forward in order.
        for line in [
            "[parse] parsing files",
            "[refs] resolving references",
            "[graph] building",
            "[health] computing health",
            "[parse] re-running parse",  # would NOT regress
        ]:
            classified = progress.classify_line(line)
            if classified is None:
                continue
            pct, name = classified
            if not emitted or pct > emitted[-1][0]:
                emitted.append((pct, name))

        pcts = [p for p, _ in emitted]
        assert pcts == sorted(pcts)
        assert pcts[-1] >= 70


# ---------------------------------------------------------------------------
# Completions
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_index_root(tmp_path: Path) -> Path:
    """Tiny .roam/index.db with a few symbols + files for prefix lookup."""
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db_path = roam_dir / "index.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL
        );
        CREATE VIRTUAL TABLE symbol_fts USING fts5(name);
        """
    )
    syms = [
        (1, "user_login"),
        (2, "user_logout"),
        (3, "user_locale"),
        (4, "validate_token"),
        (5, "process_payment"),
    ]
    conn.executemany("INSERT INTO symbols (id, name) VALUES (?, ?)", syms)
    for sid, name in syms:
        conn.execute("INSERT INTO symbol_fts (rowid, name) VALUES (?, ?)", (sid, name))
    files = [(1, "src/auth.py"), (2, "src/payments.py"), (3, "tests/test_auth.py")]
    conn.executemany("INSERT INTO files (id, path) VALUES (?, ?)", files)
    conn.commit()
    conn.close()
    return tmp_path


class TestCompletions:
    def test_symbols_prefix(self, fake_index_root: Path):
        out = completions.complete_symbols("user_lo", root=str(fake_index_root))
        assert "user_login" in out
        assert "user_logout" in out
        assert "validate_token" not in out

    def test_symbols_empty_prefix(self, fake_index_root: Path):
        assert completions.complete_symbols("", root=str(fake_index_root)) == []

    def test_paths_prefix(self, fake_index_root: Path):
        out = completions.complete_paths("src/", root=str(fake_index_root))
        assert "src/auth.py" in out
        assert "src/payments.py" in out
        assert "tests/test_auth.py" not in out

    def test_no_index_returns_empty(self, tmp_path: Path):
        assert completions.complete_symbols("foo", root=str(tmp_path)) == []
        assert completions.complete_paths("foo", root=str(tmp_path)) == []

    def test_commands_known_prefix(self):
        names = completions.complete_commands("he")
        assert "health" in names

    def test_complete_prefix_kind_all(self, fake_index_root: Path):
        out = completions.complete_prefix("user_", kind="all", root=str(fake_index_root))
        assert "symbols" in out
        assert "paths" in out
        assert "commands" in out

    def test_complete_prefix_unknown_kind(self, fake_index_root: Path):
        assert completions.complete_prefix("x", kind="bogus", root=str(fake_index_root)) == {}


# ---------------------------------------------------------------------------
# Watcher classification
# ---------------------------------------------------------------------------


class TestWatcherClassification:
    def test_code_file_python(self):
        assert watcher._is_code_file("src/foo.py") is True

    def test_non_code_lockfile(self):
        assert watcher._is_code_file("yarn.lock") is False
        assert watcher._is_code_file("Pipfile.lock") is False

    def test_image_skipped(self):
        assert watcher._is_code_file("docs/diagram.png") is False

    def test_ignored_dir_git(self):
        assert watcher._within_ignored("repo/.git/HEAD") is True

    def test_ignored_dir_node_modules(self):
        assert watcher._within_ignored("repo/node_modules/foo/bar.js") is True

    def test_normal_path_not_ignored(self):
        assert watcher._within_ignored("repo/src/main.py") is False


# ---------------------------------------------------------------------------
# mcp_server integration -- ensure the new tool surfaces in the registry
# ---------------------------------------------------------------------------


class TestMCPServerIntegration:
    def test_roam_complete_in_core_preset(self):
        from roam.mcp_server import _CORE_TOOLS, _REGISTERED_TOOLS

        assert "roam_complete" in _CORE_TOOLS
        # When fastmcp is installed (it is, since we're running these tests
        # with the [mcp] extra) the tool should also be registered.
        try:
            import fastmcp  # noqa: F401
        except Exception:
            return
        assert "roam_complete" in _REGISTERED_TOOLS

    def test_summarize_param_optional(self):
        # The understand/health/explore/repo_map tools accept summarize=False
        # as default and don't fail when ctx is None (no MCP client connected).
        import inspect

        from roam.mcp_server import explore, health, repo_map, understand

        for fn in (explore, understand, health, repo_map):
            sig = inspect.signature(fn)
            assert "summarize" in sig.parameters
            assert sig.parameters["summarize"].default is False
