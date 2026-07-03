"""W468 — never persist content-bearing probe results to the cross-session
SQLite cache.

`grep`/`file`/`retrieve`/`search-semantic`/`taint` (and the preemptive
`refs-text`/`history-grep`/`config`) return raw source snippets, matched file
lines, or config values that can carry secrets. Their results must NOT round-trip
through ``compile-envelope-cache.sqlite`` (24h TTL at rest). The gate lives in
``_run_roam``; the lower-level put/get storage functions are intentionally
unfiltered (they're the persistence layer, tested directly in W147).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from roam.plan import compiler as M


SENSITIVE = ["grep", "retrieve", "search-semantic", "taint", "file", "refs-text", "history-grep", "config"]
SAFE = ["uses", "impact", "dead", "complexity", "clusters", "deps", "search", "brief", "coupling", "batch-search"]


@pytest.mark.parametrize("subcmd", SENSITIVE)
def test_w468_sensitive_subcommands_detected(subcmd):
    # First token of args is the subcommand name.
    assert M._run_roam_persist_is_sensitive([subcmd, "--", "anything"]) is True


@pytest.mark.parametrize("subcmd", SAFE)
def test_w468_metadata_subcommands_not_sensitive(subcmd):
    assert M._run_roam_persist_is_sensitive([subcmd, "sym"]) is False


def test_w468_empty_args_not_sensitive():
    assert M._run_roam_persist_is_sensitive([]) is False


def _count_rows(tmp_path) -> int:
    path = M._run_roam_persist_path(str(tmp_path))
    if path is None:
        return 0
    conn = sqlite3.connect(path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM run_roam_cache").fetchone()
        return count
    except sqlite3.OperationalError:
        # Table is created lazily on first write — its absence means 0 rows
        # (e.g. a sensitive result that was correctly never persisted at all).
        return 0
    finally:
        conn.close()


@pytest.fixture()
def _isolated_run_roam_state(tmp_path, monkeypatch):
    """A .roam-backed cwd + cleared caches + a canned in-proc roam result."""
    (tmp_path / ".roam").mkdir()
    # Module-global state would otherwise bleed across tests in the same worker.
    M._RUN_ROAM_CACHE.clear()
    M._RUN_ROAM_PERSIST_TABLE_INITED.discard(M._run_roam_persist_path(str(tmp_path)) or "")
    M._HEAD_BY_CWD.clear()
    canned = {"rows": [{"secret": "sk-leaked-token"}]}
    monkeypatch.setattr(
        M,
        "_roam_invoke_inproc",
        lambda args, cwd: (0, json.dumps(canned)),
    )
    return tmp_path, canned


def test_w468_sensitive_result_skips_persist(_isolated_run_roam_state):
    tmp_path, canned = _isolated_run_roam_state
    value = M._run_roam(["grep", "--", "secret"], str(tmp_path))
    # The value is still returned to the caller (correctness), just not stored.
    assert value == canned
    assert _count_rows(tmp_path) == 0


def test_w468_sensitive_result_not_read_back_from_disk(_isolated_run_roam_state):
    """A sensitive row seeded directly into SQLite must be ignored by _run_roam
    (no stale secret read back across sessions)."""
    tmp_path, _ = _isolated_run_roam_state
    # Seed the disk cache as if an older binary had persisted it.
    M._run_roam_persist_put(
        ["--json", "grep", "--", "secret"],
        str(tmp_path),
        "",
        {"rows": [{"secret": "sk-stale-from-old-binary"}]},
    )
    assert _count_rows(tmp_path) == 1
    # Fresh _run_roam call must NOT return the stale secret from disk; it
    # falls through to the (monkeypatched) live invocation instead.
    value = M._run_roam(["grep", "--", "secret"], str(tmp_path))
    assert value == {"rows": [{"secret": "sk-leaked-token"}]}


def test_w468_metadata_result_still_persisted(_isolated_run_roam_state):
    """Regression guard: the gate must not over-broaden and suppress metadata
    commands that are safe to cache (and were cached before W468)."""
    tmp_path, canned = _isolated_run_roam_state
    value = M._run_roam(["uses", "fooBar"], str(tmp_path))
    assert value == canned
    assert _count_rows(tmp_path) == 1
