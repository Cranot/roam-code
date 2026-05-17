"""Parity tests for ``roam_batch_search`` / ``roam_complete`` MCP tools.

The W3.1 round added ``cmd_batch_search.py`` and ``cmd_complete.py`` to the
CLI with fixed semantics:

- ``batch-search`` matches symbol name only by default; ``--include-paths``
  opts back into the old name-OR-path wide match.
- ``complete`` is strict left-anchored prefix match (``use`` matches
  ``useFoo`` but NOT ``MyUseFoo``).

These tests confirm the MCP-side wrappers expose the same semantics, so an
agent calling ``roam_batch_search(queries=["X"])`` gets the same answer as
``roam batch-search X`` on the shell.
"""

from __future__ import annotations

import textwrap

import pytest

pytest.importorskip(
    "fastmcp",
    reason="MCP tool tests require fastmcp; mcp_server module won't import without it.",
)

from roam.mcp_server import batch_search, complete


def _unwrap(fn):
    """Strip the outermost FastMCP ``FunctionTool`` shell so the test
    can call the tool's underlying function synchronously.

    We deliberately do NOT chase ``__wrapped__`` past that point — going
    further would strip the concurrency / handle-off wrappers and we
    want the test to exercise the full chain.
    """
    if hasattr(fn, "fn") and callable(getattr(fn, "fn", None)):
        return fn.fn
    return fn


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """Build a tiny indexed project where one symbol's NAME contains a
    distinctive token, and a different file path also contains it.

    Layout:
      src/useful.py                  # defines ``useFoo`` (symbol-name match)
      tests/composables/useFoo/x.py  # defines ``setup``  (path-only match)
      src/MyUseFoo.py                # defines ``MyUseFoo`` (substring of "use")

    With the CLI's fix in place:
      - ``batch-search useFoo``                 -> useFoo only
      - ``batch-search useFoo --include-paths`` -> useFoo + setup
      - ``complete use``                        -> useFoo (NOT MyUseFoo)
    """
    monkeypatch.chdir(tmp_path)

    # Create source files.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "useful.py").write_text(
        textwrap.dedent(
            """\
            def useFoo(x):
                return x

            def useBar(y):
                return y
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "src" / "MyUseFoo.py").write_text(
        textwrap.dedent(
            """\
            def MyUseFoo():
                return 1
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "composables" / "useFoo").mkdir(parents=True)
    (tmp_path / "tests" / "composables" / "useFoo" / "x.py").write_text(
        textwrap.dedent(
            """\
            def setup():
                pass
            """
        ),
        encoding="utf-8",
    )

    # Pretend it's a git project so roam's discovery is happy.
    import subprocess

    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=False)
        subprocess.run(
            ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "-m", "init"],
            cwd=tmp_path,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("git not available")

    # Build the index.
    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    res = runner.invoke(cli, ["init"], catch_exceptions=True)
    if res.exit_code != 0:
        # Some sandboxes can't run init; surface for debugging.
        pytest.skip(f"roam init failed: {res.output}")

    yield tmp_path
    # W478-followup-2: cleanup delegated to pytest's tmp_path fixture
    # (Windows-handle-retry aware). The previous explicit
    # `shutil.rmtree(..., ignore_errors=True)` was a redundant swallow site.


# ---------------------------------------------------------------------------
# batch_search parity
# ---------------------------------------------------------------------------


def test_mcp_batch_search_default_no_path_match(isolated_project):
    """Default mode: a query that appears only in a file path must NOT
    match (the W3.1 fix). Searches for ``useFoo`` should return
    ``useFoo`` symbol but NOT ``setup`` (which only matches by path)."""
    fn = _unwrap(batch_search)
    r = fn(queries=["useFoo"], limit_per_query=10)

    # Envelope shape sanity
    assert r["command"] == "batch-search"
    assert r["summary"]["match_mode"] == "name-only"
    assert r["summary"]["include_paths"] is False

    rows = r["results"].get("useFoo", [])
    names = {row["name"] for row in rows}
    # Must include the symbol named ``useFoo``.
    assert "useFoo" in names, f"expected useFoo in results, got {names}"
    # Must NOT include ``setup`` (would be a path-only match in legacy mode).
    assert "setup" not in names, f"setup leaked in via path match: {names}"


def test_mcp_batch_search_include_paths_match(isolated_project):
    """``include_paths=True`` restores the wider match. Now the path-only
    fixture should also appear."""
    fn = _unwrap(batch_search)
    r = fn(queries=["useFoo"], limit_per_query=10, include_paths=True)

    assert r["summary"]["match_mode"] == "name+path"
    assert r["summary"]["include_paths"] is True

    rows = r["results"].get("useFoo", [])
    names = {row["name"] for row in rows}
    # Both should appear when include_paths is on.
    assert "useFoo" in names
    assert "setup" in names, f"include_paths=True must surface path-only matches; got {names}"


def test_mcp_batch_search_empty_queries_returns_no_data_envelope(isolated_project):
    """No queries → no-data envelope with the parity fields populated."""
    fn = _unwrap(batch_search)
    r = fn(queries=[], limit_per_query=5)

    assert r["summary"]["queries_executed"] == 0
    assert r["summary"]["total_matches"] == 0
    assert r["summary"]["match_mode"] == "name-only"


# ---------------------------------------------------------------------------
# complete parity
# ---------------------------------------------------------------------------


def test_mcp_complete_prefix_strict(isolated_project):
    """``use`` must match ``useFoo`` and ``useBar`` but NOT ``MyUseFoo``.

    This is the W3.1 fix: FTS5's camelCase tokenizer would split
    ``MyUseFoo`` -> ``My Use Foo`` so the legacy MCP ``complete``
    matched it on a ``use*`` query. The strict LIKE-based matcher now
    enforces left-anchored prefix only.
    """
    fn = _unwrap(complete)
    r = fn(prefix="use", kind="symbol", limit=30)

    assert r["command"] == "roam_complete"
    syms = r.get("symbols", [])
    # Should include the two real prefix matches.
    assert "useFoo" in syms, f"expected useFoo in {syms}"
    assert "useBar" in syms, f"expected useBar in {syms}"
    # Must NOT include MyUseFoo (only a substring match).
    assert "MyUseFoo" not in syms, f"strict prefix should exclude MyUseFoo, got {syms}"


def test_mcp_complete_returns_match_mode(isolated_project):
    """Envelope declares ``match_mode: "prefix"`` so agents can verify
    the semantic contract is the strict one."""
    fn = _unwrap(complete)
    r = fn(prefix="use", kind="symbol", limit=10)
    assert r["summary"]["match_mode"] == "prefix"
    assert r["summary"]["kind"] == "symbol"
    assert "total" in r["summary"]


def test_mcp_complete_empty_prefix_returns_empty(isolated_project):
    """Empty prefix is a degenerate input — should return zero results
    rather than every symbol in the index."""
    fn = _unwrap(complete)
    r = fn(prefix="", kind="symbol", limit=10)
    assert r["summary"]["match_mode"] == "prefix"
    assert r.get("symbols", []) == []
