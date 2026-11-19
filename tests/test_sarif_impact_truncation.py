"""Truncated impact envelopes must disclose incomplete SARIF results."""

import pytest

from roam.output import sarif


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)


def _envelope(projector, retained, truncated):
    key = "affected_file_list" if projector == "impact_to_sarif" else "tests"
    row = (
        {"path": "src/dependent.py", "importance": 0.5}
        if key == "affected_file_list"
        else {"file": "tests/test_changed.py", "kind": "DIRECT", "reach_count": 2}
    )
    summary = {"target": "changed", "total_tests": 3 if truncated else retained}
    if truncated:
        summary.update(
            truncated=True,
            truncation_reason="max_bytes",
            emitted_counts={key: retained},
        )
    return {"summary": summary, "symbol": "changed", key: [row] * retained}


@pytest.mark.parametrize("projector", ["impact_to_sarif"])
@pytest.mark.parametrize("retained", [0, 1])
@pytest.mark.parametrize("truncated", [True, False])
def test_impact_envelope_discloses_incomplete_results(projector, retained, truncated):
    document = getattr(sarif, projector)(_envelope(projector, retained, truncated))
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    assert len(run["results"]) == retained
    warnings = [
        notification
        for invocation in run.get("invocations", [])
        for notification in invocation.get("toolExecutionNotifications", [])
        if notification.get("level") in {"warning", "error"} and notification.get("message", {}).get("text")
    ]
    if truncated:
        assert warnings, "Truncated envelopes must disclose incomplete results to SARIF consumers"
    else:
        assert not warnings, "Complete envelopes should not report producer degradation"


@pytest.mark.parametrize("state", ["depth_cap", "caller_cap", "timeout"])
def test_traversal_notice_uses_real_reason_and_counts(state):
    env = {"summary": {"truncated": True, "state": state, "displayed": 1, "total": 40}}
    [notice] = sarif.envelope_truncation_disclosures(env)
    assert f"reason={state}" in notice
    assert "1 of 40" in notice


def test_real_sarif_impact_command_discloses_traversal_cap(tmp_path, monkeypatch):
    import json
    import sqlite3
    from contextlib import contextmanager

    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_impact
    from roam.db.schema import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO files(id, path, language) VALUES (1, 'app.py', 'python')")
    for sid, name in enumerate(("leaf", "middle", "outer"), 1):
        conn.execute(
            "INSERT INTO symbols(id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, 1, ?, ?, 'function', ?, ?)",
            (sid, name, name, sid, sid),
        )
    conn.executemany("INSERT INTO edges(source_id, target_id, kind) VALUES (?, ?, 'calls')", [(2, 1), (3, 2)])
    conn.commit()

    @contextmanager
    def database(**kwargs):
        yield conn

    monkeypatch.setattr(cmd_impact, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_impact, "open_db", database)
    monkeypatch.setattr(cmd_impact, "auto_log", lambda *a, **kw: None)
    try:
        result = CliRunner().invoke(cli, ["--sarif", "impact", "leaf", "--depth", "1"])
        assert result.exit_code == 0, result.output
        assert result.stdout.lstrip().startswith("{"), repr(result.stdout)
        run = json.loads(result.stdout)["runs"][0]
        notes = [
            n["message"]["text"] for i in run.get("invocations", []) for n in i.get("toolExecutionNotifications", [])
        ]
        assert any("reason=depth_cap" in n and "1 of 2" in n for n in notes), notes
    finally:
        conn.close()
