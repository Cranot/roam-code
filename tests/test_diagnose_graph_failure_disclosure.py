"""A run whose own warnings name a sqlite error is not `partial_success: false`.

``_iso_summary`` set ``"partial_success": True`` and then merged
``**resolution_block`` on the very next lines. For an EXACT match
``resolution_disclosure("symbol")`` returns
``partial_success = ("symbol" != "symbol") = False``, so the last key won and
the True was overwritten. The comment three lines above asserted
"partial_success is already True here", and ``formatter.py``'s own docstring
listed ``cmd_diagnose`` (W1244) as "no pre-existing flag -> direct merge" --
both false for this branch.

Measured against the tree that shipped, on a normal indexed repo whose
``index.db`` then had ``ALTER TABLE edges RENAME TO edges_gone`` applied
(edges rows before: 2)::

    $ roam diagnose beta
      VERDICT: Symbol 'beta' resolved but is not connected in the
               dependency graph
        Tip: Run `roam index` to rebuild the graph. ...              rc 1
    $ roam --json diagnose beta
      "partial_success": false,
      "resolution": "symbol",
      "state": "isolated_in_graph",
      "warnings_out": ["diagnose_build_graph_failed:OperationalError:
                        no such table: edges"],
      "target_metrics": {"in_degree": 1, "out_degree": 1, ...}       rc 0

Note ``out_degree: 1`` in the same envelope that says the symbol is not
connected. And the text channel said nothing about the database at all: it
made a statement about the user's CODE for a failure in roam's own store.

WHAT IS NOT COVERED HERE
------------------------
The text/JSON exit-code divergence itself (text 1, json 0) is pinned as
deliberate in ``tests/data/disclosure_asymmetry_baseline.json`` and is NOT
touched. Only the false ``partial_success`` and the text channel's silence
are corrected.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = (
    "def alpha(x):\n    return x + 1\n\n\ndef beta(x):\n    return alpha(x)\n\n\ndef gamma(x):\n    return beta(x)\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1"),
    )


@pytest.fixture()
def indexed_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "src" / "m.py").write_text(MODULE, encoding="utf-8")
    _git(root, "add", "src/m.py")
    _git(root, "commit", "-q", "-m", "fixture")
    built = _run(root, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    return root


def _break_the_graph(root: Path) -> None:
    """Rename the edges table away.

    A schema-level break rather than a monkeypatch, so the failure the
    command sees is a real ``sqlite3.OperationalError`` from the real
    ``build_symbol_graph``.
    """
    conn = sqlite3.connect(root / ".roam" / "index.db")
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] > 0, "fixture must have edges to lose"
    conn.execute("ALTER TABLE edges RENAME TO edges_gone")
    conn.commit()
    conn.close()


def _summary(out: str) -> dict:
    payload, _end = json.JSONDecoder().raw_decode(out[out.find("{") :])
    return payload


def test_a_run_that_could_not_build_the_graph_is_partial(indexed_repo: Path) -> None:
    _break_the_graph(indexed_repo)

    payload = _summary(_run(indexed_repo, "--json", "diagnose", "beta").stdout)
    summary = payload["summary"]

    assert summary["warnings_out"], summary
    assert any("build_graph_failed" in w for w in summary["warnings_out"]), summary
    assert summary["partial_success"] is True, (
        f"the envelope asserts a complete run in the same breath as naming a sqlite error.\nsummary: {summary}"
    )
    assert payload["partial_success"] is True, "the top-level mirror had the same collision"


def test_the_resolution_field_is_still_reported(indexed_repo: Path) -> None:
    """The fix must OR-combine, not drop the resolver's disclosure.

    Overwriting the whole block would have traded one silent field for
    another.
    """
    _break_the_graph(indexed_repo)
    summary = _summary(_run(indexed_repo, "--json", "diagnose", "beta").stdout)["summary"]
    assert summary["resolution"] == "symbol", summary
    assert summary["state"] == "isolated_in_graph", summary


def test_the_text_channel_names_the_database_error(indexed_repo: Path) -> None:
    """A statement about the user's code for a failure in roam's own store.

    The text branch printed only "not connected in the dependency graph"
    plus a tip to reindex -- no mention of the OperationalError anywhere.
    """
    _break_the_graph(indexed_repo)
    run = _run(indexed_repo, "diagnose", "beta")
    # ``echo_text_warnings`` is the shared emitter and writes to stderr; the
    # assertion is that a human running the command SEES the cause, not which
    # of the two streams carries it.
    seen = run.stdout + run.stderr

    assert "build_graph_failed" in seen, seen[:600]
    assert "no such table" in seen, seen[:600]


def test_a_healthy_run_is_not_marked_partial(indexed_repo: Path) -> None:
    """The must-not-fire control.

    ``beta`` has a caller and a callee, so on a healthy index it resolves,
    is connected, and the run is complete. If this flipped, the fix would
    have made every diagnose look degraded.
    """
    run = _run(indexed_repo, "--json", "diagnose", "beta")
    payload = _summary(run.stdout)
    summary = payload["summary"]

    assert summary.get("state") != "isolated_in_graph", summary
    assert not summary.get("warnings_out"), summary
    assert summary.get("partial_success") is not True, summary


def test_a_genuinely_isolated_symbol_still_resolves_cleanly(tmp_path: Path) -> None:
    """The other direction of the same control.

    A symbol with no callers and no callees IS isolated, on a graph that
    built fine. That branch may report the isolation -- what it must not do
    is claim a degradation that did not happen.
    """
    root = tmp_path / "lonely"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "src" / "m.py").write_text("def lonely(x):\n    return x + 1\n", encoding="utf-8")
    _git(root, "add", "src/m.py")
    _git(root, "commit", "-q", "-m", "fixture")
    assert _run(root, "init").returncode == 0

    summary = _summary(_run(root, "--json", "diagnose", "lonely").stdout)["summary"]
    assert not summary.get("warnings_out"), summary
