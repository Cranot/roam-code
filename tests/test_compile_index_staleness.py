"""Tests for W21 — stale-index detection in `roam compile`.

The 2026-05-30 A/B measured one failure: compile output's `named_paths`
contained a same-session-only file (`src/roam/proof_bundle.py`) that
didn't exist yet on disk, and the downstream agent took it at face
value and concluded "the file doesn't exist." The fix surfaces an
`index_staleness` warning the agent can use to verify before trusting.
"""

from __future__ import annotations

from roam.plan.compiler import _named_path_staleness


def test_named_path_staleness_fires_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = _named_path_staleness(["src/does/not/exist.py"], str(tmp_path))
    assert out is not None
    assert out["is_stale"] is None
    assert "src/does/not/exist.py" in out["missing_paths"]
    assert "Verify with Read/Grep" in out["warning"]


def test_named_path_staleness_silent_when_file_exists(project_factory, monkeypatch):
    monkeypatch.setenv("ROAM_INDEX_FRESHNESS_BUDGET_S", "30")
    project = project_factory({"src/real.py": "def present(): pass\n"})
    assert _named_path_staleness(["src/real.py"], str(project)) is None


def test_named_path_staleness_reports_manifest_age_for_changed_file(project_factory, monkeypatch):
    import sqlite3
    from contextlib import closing

    monkeypatch.setenv("ROAM_INDEX_FRESHNESS_BUDGET_S", "30")
    project = project_factory({"app.py": "def main(): pass\n"})
    with closing(sqlite3.connect(project / ".roam" / "index.db")) as conn, conn:
        conn.execute("UPDATE index_manifest SET indexed_at = indexed_at - 172800")
    (project / "app.py").write_text("def main(): return 1\n")

    out = _named_path_staleness(["app.py"], str(project))
    assert out is not None
    assert out["is_stale"] is True
    assert out["index_age_seconds"] >= 172800


def test_named_path_staleness_fires_when_no_index_at_all(tmp_path):
    real = tmp_path / "src" / "real.py"
    real.parent.mkdir(parents=True)
    real.write_text("# real")
    out = _named_path_staleness(["src/real.py"], str(tmp_path))
    assert out is not None
    assert out["is_stale"] is None
    assert out["index_age_seconds"] is None


def test_named_path_staleness_dedupes_input_paths(tmp_path):
    out = _named_path_staleness(
        ["src/missing.py", "src/missing.py", "src/other.py"],
        str(tmp_path),
    )
    assert out is not None
    # `missing.py` appears once even though we passed it twice.
    assert out["missing_paths"].count("src/missing.py") == 1
    assert "src/other.py" in out["missing_paths"]


def test_named_path_staleness_silent_on_empty_input(tmp_path):
    """No named paths AND no .roam dir → silent. Compile envelopes for
    tasks that name nothing shouldn't ship a spurious staleness warning."""
    out = _named_path_staleness([], str(tmp_path))
    assert out is None
