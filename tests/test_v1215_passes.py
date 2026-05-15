"""Tests for v12.15 passes 28-30 (errors, parallel index, plugins cmd)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_pass28_doc_link_filled_for_known_codes():
    """Every classified error_code surfaces a stable troubleshooting URL."""
    from roam.mcp_server import _DOC_LINKS, _structured_error

    for code in ["INDEX_NOT_FOUND", "DB_LOCKED", "INDEX_STALE", "USAGE_ERROR"]:
        out = _structured_error({"error_code": code, "hint": "msg"})
        assert out["doc_link"] == _DOC_LINKS[code]
        assert out["doc_link"].startswith("https://")
        assert out["isError"] is True


def test_pass28_unknown_code_falls_back_to_root():
    from roam.mcp_server import _DOC_LINKS, _structured_error

    out = _structured_error({"error_code": "TOTALLY_NEW_CODE"})
    assert out["doc_link"] == _DOC_LINKS["UNKNOWN"]


def test_pass29_prefetch_thread_pool_populates_cache(tmp_path, monkeypatch):
    """When ROAM_PARALLEL_INDEX=1, _prefetch_sources reads files into cache."""
    from roam.index.indexer import Indexer

    (tmp_path / "a.py").write_bytes(b"print('a')\n")
    (tmp_path / "b.py").write_bytes(b"print('b')\n")

    monkeypatch.setenv("ROAM_PARALLEL_INDEX", "1")
    idx = Indexer(project_root=tmp_path)
    idx.root = tmp_path
    idx._prefetch_sources(["a.py", "b.py"], verbose=False)

    cache = getattr(idx, "_source_cache", None)
    assert cache is not None
    assert cache["a.py"] == b"print('a')\n"
    assert cache["b.py"] == b"print('b')\n"


def test_pass29_prefetch_enabled_by_default(tmp_path, monkeypatch):
    """W404: prefetch is on-by-default; no env var → prefetch runs."""
    from roam.index.indexer import Indexer

    (tmp_path / "a.py").write_bytes(b"print('a')\n")
    monkeypatch.delenv("ROAM_PARALLEL_INDEX", raising=False)
    idx = Indexer(project_root=tmp_path)
    idx.root = tmp_path
    idx._prefetch_sources(["a.py"], verbose=False)
    cache = getattr(idx, "_source_cache", None)
    assert cache is not None
    assert cache["a.py"] == b"print('a')\n"


def test_pass29_prefetch_opt_out_via_env_zero(tmp_path, monkeypatch):
    """W404: ROAM_PARALLEL_INDEX=0 forces serial (no prefetch)."""
    from roam.index.indexer import Indexer

    (tmp_path / "a.py").write_bytes(b"print('a')\n")
    monkeypatch.setenv("ROAM_PARALLEL_INDEX", "0")
    idx = Indexer(project_root=tmp_path)
    idx.root = tmp_path
    idx._prefetch_sources(["a.py"], verbose=False)
    assert getattr(idx, "_source_cache", None) is None


def test_pass29_read_index_source_consumes_prefetched_bytes(tmp_path, monkeypatch):
    """_read_index_source pops from cache when present, falling back to disk otherwise."""
    from roam.index.indexer import Indexer

    f = tmp_path / "x.py"
    f.write_bytes(b"data")
    monkeypatch.setenv("ROAM_PARALLEL_INDEX", "1")
    idx = Indexer(project_root=tmp_path)
    idx.root = tmp_path
    idx._prefetch_sources(["x.py"], verbose=False)
    out = idx._read_index_source(f, "x.py", verbose=False)
    assert out == b"data"
    # cache is drained on consume
    assert "x.py" not in idx._source_cache
    # second read falls back to disk transparently
    out2 = idx._read_index_source(f, "x.py", verbose=False)
    assert out2 == b"data"


def test_pass30_plugins_command_text_no_plugins(monkeypatch):
    """`roam plugins` works with no plugins registered."""
    monkeypatch.delenv("ROAM_PLUGIN_MODULES", raising=False)
    from roam import plugins as plugin_mod

    plugin_mod._reset_plugin_state_for_tests()

    runner = CliRunner()
    result = runner.invoke(cli, ["plugins"])
    assert result.exit_code == 0, result.output
    assert "VERDICT" in result.output
    assert "ROAM_PLUGIN_MODULES" in result.output


def test_pass30_plugins_command_json_envelope(monkeypatch):
    """`--json plugins` returns a well-formed envelope."""
    monkeypatch.delenv("ROAM_PLUGIN_MODULES", raising=False)
    from roam import plugins as plugin_mod

    plugin_mod._reset_plugin_state_for_tests()

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "plugins"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "plugins"
    summary = payload["summary"]
    assert "verdict" in summary
    for k in ("commands", "detectors", "languages", "extensions"):
        assert k in summary
    assert isinstance(payload["commands"], list)
    assert isinstance(payload["languages"], list)
