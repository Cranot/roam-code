"""W1515 — a symbol whose source was never read must not be certified pure.

``classify_side_effects`` reads each file once and slices per-symbol bodies out
of it.  When the read failed (path gone, or ``open`` raised ``OSError``) the
body came back empty, Layer 2 and Layer 3 observed nothing, and the classifier
fell through to its "confident this is pure" branch: ``kinds=["none"]``,
``confidence="high"``, ``evidence.reason="no outgoing calls, no patterns, no
risky imports"``.  That is an affirmative denial of evidence nobody looked for,
and the command published ``partial_success: false`` beside it.

The must-not-fire half is what these tests are really for: a readable corpus
must classify exactly as before, and Layer 1 (resolved call edges, which does
not depend on the read) must keep proving side effects even when the body is
unavailable.
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_DANGER = "import os\n\n\ndef wipe_workspace(target):\n    os.system('rm -rf ' + target)\n\n\ndef add(a, b):\n    return a + b\n"


# ---------------------------------------------------------------------------
# MUST FIRE — source unavailable
# ---------------------------------------------------------------------------


def test_missing_source_classifies_unknown_not_none(project_factory, monkeypatch):
    """A file indexed and then deleted yields `unknown`/low, never `none`/high."""
    proj = project_factory({"app/danger.py": _DANGER})
    monkeypatch.chdir(proj)
    (proj / "app" / "danger.py").unlink()

    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects_status

    with open_db(readonly=True) as conn:
        results, coverage = classify_side_effects_status(conn)

    assert results, "expected the indexed symbols to still be enumerated"
    for c in results:
        assert c.kinds == ["unknown"], f"{c.symbol}: expected ['unknown'], got {c.kinds}"
        assert c.confidence == "low", f"{c.symbol}: expected low confidence, got {c.confidence}"
        assert c.evidence.get("source_status") == "missing"
        assert "source unavailable" in c.evidence.get("reason", "")

    assert coverage.scan_incomplete is True
    assert coverage.files_read == 0
    assert coverage.files_discovered == 1
    assert coverage.symbols_source_unavailable == len(results)


def test_unreadable_source_classifies_unknown_not_none(project_factory, monkeypatch):
    """An ``OSError`` from ``open`` yields `unknown`/low and is named as unreadable."""
    proj = project_factory({"app/danger.py": _DANGER})
    monkeypatch.chdir(proj)

    import roam.world_model.side_effects as se

    real_open = builtins.open

    def _refusing_open(file, *args, **kwargs):
        if str(file).replace("\\", "/").endswith("app/danger.py"):
            raise PermissionError(13, "Permission denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(se, "open", _refusing_open, raising=False)

    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        results, coverage = se.classify_side_effects_status(conn)

    assert results
    for c in results:
        assert c.kinds == ["unknown"], f"{c.symbol}: expected ['unknown'], got {c.kinds}"
        assert c.confidence == "low"
        assert c.evidence.get("source_status") == "unreadable"
        assert c.evidence.get("source_unavailable") == "app/danger.py"

    assert coverage.scan_incomplete is True
    assert coverage.files_unreadable == ["app/danger.py"]


def test_envelope_discloses_scan_incomplete(project_factory, monkeypatch, cli_runner):
    """The command publishes the denominator in BOTH channels off one boolean."""
    proj = project_factory({"app/danger.py": _DANGER})
    monkeypatch.chdir(proj)
    (proj / "app" / "danger.py").unlink()

    result = invoke_cli(cli_runner, ["side-effects"], json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(getattr(result, "stdout", None) or result.output)
    summary = data["summary"]

    assert summary["scan_incomplete"] is True
    assert summary["partial_success"] is True, "an unread corpus is not a complete scan"
    assert summary["state"] != "ok"
    assert summary["files_read"] == 0
    assert summary["files_discovered"] == 1
    assert "0 of 1 files" in summary["verdict"]
    assert summary["by_kind"].get("none", 0) == 0, "nothing may be certified pure from an unread file"

    facts = data["agent_contract"]["facts"]
    assert not any("confirmed" in f and "pure" in f for f in facts), (
        f"'confirmed N symbols are pure' must not survive an incomplete scan: {facts}"
    )
    assert any("NOT classified from source" in f for f in facts), facts

    # Text channel must carry the SAME disclosure, not just the JSON one.
    text = invoke_cli(cli_runner, ["side-effects"], json_mode=False)
    assert text.exit_code == 0, text.output
    out = getattr(text, "stdout", None) or text.output
    assert "0 of 1 files" in out
    assert "WITHOUT their source" in out


# ---------------------------------------------------------------------------
# MUST NOT FIRE — a readable corpus is unchanged
# ---------------------------------------------------------------------------


def test_readable_corpus_still_classifies_exactly_as_before(project_factory, monkeypatch, cli_runner):
    """The whole disclosure path is inert when every file could be read."""
    proj = project_factory({"app/danger.py": _DANGER})
    monkeypatch.chdir(proj)

    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects_status

    with open_db(readonly=True) as conn:
        results, coverage = classify_side_effects_status(conn)

    by_name = {c.symbol: c for c in results}
    assert by_name["add"].kinds == ["none"]
    assert by_name["add"].confidence == "high"
    assert "process" in by_name["wipe_workspace"].kinds

    assert coverage.scan_incomplete is False
    assert coverage.files_read == coverage.files_discovered == 1
    assert coverage.symbols_source_unavailable == 0

    result = invoke_cli(cli_runner, ["side-effects"], json_mode=True)
    data = json.loads(getattr(result, "stdout", None) or result.output)
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["state"] == "ok"
    assert data["summary"]["scan_incomplete"] is False
    assert any("confirmed" in f and "pure" in f for f in data["agent_contract"]["facts"])

    text = invoke_cli(cli_runner, ["side-effects"], json_mode=False)
    out = getattr(text, "stdout", None) or text.output
    assert "WITHOUT their source" not in out


def test_empty_body_from_a_readable_file_is_still_none(project_factory, monkeypatch):
    """Keying on the READ OUTCOME, not on ``body == ""``.

    A stale index whose ``line_start`` points past EOF produces an empty slice
    from a perfectly readable file.  That is a staleness problem with its own
    disclosure; it must not be re-labelled a read failure, or every stale run
    would report ``scan_incomplete``.
    """
    proj = project_factory({"src/pure.py": "def add(a, b):\n    return a + b\n"})
    monkeypatch.chdir(proj)

    import sqlite3

    from roam.db.connection import get_db_path, open_db
    from roam.world_model.side_effects import classify_side_effects_status

    db = get_db_path(proj)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE symbols SET line_start = 9000, line_end = 9001 WHERE name = 'add'")
    con.commit()
    con.close()

    with open_db(readonly=True) as conn:
        results, coverage = classify_side_effects_status(conn)

    assert coverage.scan_incomplete is False, "a readable file is a complete read even when the slice is empty"
    assert coverage.files_read == coverage.files_discovered
    assert results and results[0].kinds == ["none"]


def test_layer1_call_edges_still_prove_effects_without_the_body(monkeypatch):
    """The honest cross-language signal survives an unreadable file.

    Gating the whole classifier on the read would delete the one detector that
    does not need it.  A resolved call edge to a known side-effecting name must
    still classify — and still at high confidence, because the indexer, not the
    body, is the evidence.
    """
    from roam.world_model.side_effects import _classify_one_symbol

    kinds, evidence, confidence = _classify_one_symbol(
        "",
        ["subprocess.run"],
        set(),
        source_status="unreadable",
    )
    assert kinds == ["process"]
    assert confidence == "high"
    assert evidence.get("calls_seen") == ["subprocess.run→process"]
    assert evidence.get("source_status") == "unreadable"
    # The reason string is the affirmative denial; it must NOT be attached when
    # a real signal was found.
    assert "reason" not in evidence
