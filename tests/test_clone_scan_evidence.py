"""Clone evidence survives persistence and remains qualified at review time."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.graph import clone_detect
from tests.conftest import git_init, index_in_process


@pytest.fixture
def project(tmp_path, monkeypatch):
    body = "    total = 0\n" + "    total += value\n" * 8 + "    return total\n"
    (tmp_path / "a.py").write_text("def first(value):\n" + body, encoding="utf-8")
    (tmp_path / "b.py").write_text("def second(value):\n" + body, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    git_init(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args):
    result = CliRunner().invoke(cli, ["--json", *args])
    assert result.exit_code in (0, 5), (result.output, result.exception)
    return json.loads(result.stdout)


def review(project, *, batch=False):
    diff = "--- a/a.py\n+++ b/a.py\n@@ -2,1 +2,1 @@\n-    total = 0\n+    total = 1\n"
    if batch:
        directory = project / "patches"
        directory.mkdir()
        (directory / "one.diff").write_text(diff, encoding="utf-8")
        return run("critique", "--batch", str(directory), "--intent", "adjust first")
    path = project / "change.diff"
    path.write_text(diff, encoding="utf-8")
    return run("critique", "--input", str(path), "--intent", "adjust first")


def test_failed_detection_preserves_saved_pairs(project, monkeypatch):
    assert run("clones", "--persist")["summary"]["clone_pairs"] > 0
    with open_db(readonly=True) as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM clone_pairs")]

    def fail(*args, **kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(clone_detect, "detect_clones", fail)
    assert run("clones", "--persist")["summary"]["partial_success"] is True
    with open_db(readonly=True) as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM clone_pairs")] == before


def test_completed_empty_scan_is_not_missing_evidence(project):
    run("clones", "--persist", "--min-lines", "100")
    result = review(project)
    assert result["summary"]["check_status"]["clones-not-edited"] == "ran"
    assert result["summary"]["partial_success"] is False


def test_nonfinite_threshold_is_rejected_without_replacing_saved_scan(project):
    run("clones", "--persist")
    with open_db(readonly=True) as conn:
        before = conn.execute("SELECT metadata_json FROM clone_scan_state WHERE id = 1").fetchone()[0]
    result = CliRunner().invoke(cli, ["--json", "clones", "--threshold", "nan", "--persist"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "usage_error"
    assert payload["error_code"] == "USAGE_ERROR"
    assert payload["isError"] is True
    assert payload["summary"]["partial_success"] is True
    with open_db(readonly=True) as conn:
        assert conn.execute("SELECT metadata_json FROM clone_scan_state WHERE id = 1").fetchone()[0] == before


@pytest.mark.parametrize("mode", ["text", "json", "sarif"])
def test_nonfinite_threshold_fails_before_index_access(monkeypatch, mode):
    from roam.commands import cmd_clones

    monkeypatch.setattr(cmd_clones, "ensure_index", lambda: pytest.fail("invalid threshold opened the index"))
    args = ["clones", "--threshold", "nan"]
    result = CliRunner().invoke(cli, args if mode == "text" else [f"--{mode}", *args])
    assert result.exit_code == 2, result.output
    if mode == "json":
        payload = json.loads(result.stdout)
        assert payload["command"] == "clones"
        assert payload["error_code"] == "USAGE_ERROR"
    else:
        assert "finite" in result.stderr and "--threshold" in result.stderr


@pytest.mark.parametrize("args", [("--scope", "a.py"), ("--exclude-tests",)])
def test_filtered_scan_cannot_become_a_full_review(project, args):
    run("clones", "--persist", *args)
    result = review(project)
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["check_status"]["clones-not-edited"].startswith("partial:")


def test_stale_scan_keeps_positive_findings_but_qualifies_review(project):
    run("clones", "--persist")
    path = project / "a.py"
    path.write_text(path.read_text(encoding="utf-8").replace("total = 0", "total = 1"), encoding="utf-8")
    result = review(project)
    assert result["summary"]["partial_success"] is True
    assert "stale" in result["summary"]["check_status"]["clones-not-edited"]
    assert any(f["check"] == "clones-not-edited" for f in result["findings"])


def test_cap_is_disclosed_and_persisted(project, monkeypatch):
    original = clone_detect.detect_clones

    def capped(*args, **kwargs):
        return original(*args, **kwargs, max_functions=1)

    monkeypatch.setattr(clone_detect, "detect_clones", capped)
    result = run("clones", "--persist")
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["scan"]["eligible_functions"] == 2
    assert result["summary"]["scan"]["compared_functions"] == 1
    assert "partial" in result["summary"]["verdict"].lower()
    assert "incomplete" in review(project)["summary"]["check_status"]["clones-not-edited"]


def test_parse_failure_is_not_an_empty_success(project, monkeypatch):
    import roam.index.parser as parser

    monkeypatch.setattr(parser, "parse_file", lambda *a, **kw: (None, None, None))
    result = run("clones", "--persist")
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["scan"]["unavailable_files"] == 2


def test_batch_keeps_partial_check_status(project):
    run("clones", "--persist", "--exclude-tests")
    result = review(project, batch=True)
    assert result["summary"]["partial_success"] is True
    assert result["diffs"][0]["partial_success"] is True
    assert result["diffs"][0]["check_status"]["clones-not-edited"].startswith("partial:")


def test_failed_registry_enrichment_rolls_back_rows_and_metadata(project, monkeypatch):
    from roam.commands import cmd_clones

    run("clones", "--persist")
    with open_db(readonly=True) as conn:
        before = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("clone_pairs", "clone_clusters", "clone_scan_state", "findings")
        }

    def fail(*args):
        raise RuntimeError("enrichment interrupted")

    monkeypatch.setattr(cmd_clones, "_enrich_clones_findings_with_role_bucket", fail)
    result = run("clones", "--persist", "--min-lines", "100")
    assert result["summary"]["partial_success"] is True
    with open_db(readonly=True) as conn:
        for table, rows in before.items():
            assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == rows


def test_legacy_database_migration_keeps_pairs_without_inventing_scan(project):
    run("clones", "--persist")
    with open_db(readonly=False) as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM clone_pairs")]
        conn.execute("DROP TABLE clone_scan_state")
        conn.execute("PRAGMA user_version = 19")
        conn.commit()
    with open_db(readonly=False) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        assert not conn.execute("SELECT * FROM clone_scan_state").fetchall()
        assert [tuple(row) for row in conn.execute("SELECT * FROM clone_pairs")] == before
    result = review(project)
    assert "metadata_missing" in result["summary"]["check_status"]["clones-not-edited"]
    assert any(f["check"] == "clones-not-edited" for f in result["findings"])


def test_index_change_qualifies_saved_scan(project):
    run("clones", "--persist")
    with open_db(readonly=False) as conn:
        conn.execute("UPDATE files SET hash = 'new-index-generation' WHERE path = 'b.py'")
        conn.commit()
    assert "stale_index" in review(project)["summary"]["check_status"]["clones-not-edited"]


def test_actual_parallel_scan_retains_completion_metadata(project, monkeypatch):
    monkeypatch.delenv("ROAM_NO_PARALLEL", raising=False)
    monkeypatch.setattr(clone_detect, "_PARALLEL_MIN_FILES", 2)
    monkeypatch.setattr(clone_detect, "_PARALLEL_MAX_WORKERS", 2)
    result = run("clones", "--persist")
    assert result["summary"]["scan"]["complete"] is True
    assert result["summary"]["clone_pairs"] > 0
    assert review(project)["summary"]["check_status"]["clones-not-edited"] == "ran"


@pytest.mark.parametrize("metadata", ["not json", "[]", '{"format_version": 999}', "{}"])
def test_bad_scan_metadata_never_certifies_review(project, metadata):
    run("clones", "--persist")
    with open_db(readonly=False) as conn:
        conn.execute("UPDATE clone_scan_state SET metadata_json = ?", (metadata,))
        conn.commit()
    result = review(project)
    assert result["summary"]["partial_success"] is True
    assert "partial:" in result["summary"]["check_status"]["clones-not-edited"]


@pytest.mark.parametrize("batch", [False, True])
def test_missing_source_metadata_stays_partial_through_real_review(project, batch):
    run("clones", "--persist")
    with open_db(readonly=False) as conn:
        scan = json.loads(conn.execute("SELECT metadata_json FROM clone_scan_state WHERE id = 1").fetchone()[0])
        scan["sources"].pop("b.py")
        scan["eligible_files"] -= 1
        conn.execute("UPDATE clone_scan_state SET metadata_json = ?", (json.dumps(scan),))
        conn.commit()
    result = review(project, batch=batch)
    assert result["summary"]["partial_success"] is True
    status = result["diffs"][0]["check_status"] if batch else result["summary"]["check_status"]
    assert status["clones-not-edited"] == "partial:clone_scan_metadata_invalid"
    if batch:
        assert result["diffs"][0]["findings"] > 0
    else:
        assert any(finding["check"] == "clones-not-edited" for finding in result["findings"])


def test_review_exposes_saved_detector_bounds(project):
    run("clones", "--persist", "--min-lines", "100")
    scan = review(project)["summary"]["clone_scan"]
    assert scan["min_lines"] == 100
    assert scan["complete"] is True
    assert "sources" not in scan


def test_unknown_check_state_is_not_complete():
    from roam.critique.aggregator import aggregate

    result = aggregate([], {"clones-not-edited": "unexpected"})
    assert result["partial_success"] is True
    assert "1 incomplete" in result["verdict"]


def test_late_detector_failure_cannot_keep_a_complete_scan_flag(project, monkeypatch):
    def fail(*args):
        raise RuntimeError("comparison interrupted after extraction")

    monkeypatch.setattr(clone_detect, "_find_clone_pairs", fail)
    result = run("clones", "--persist")
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["scan"]["complete"] is False
    assert result["summary"]["scan"]["compared_functions"] is None


@pytest.mark.parametrize("batch", [False, True])
def test_review_reads_rows_and_metadata_from_one_snapshot(project, monkeypatch, batch):
    from roam.graph import clone_evidence

    run("clones", "--persist")
    original = clone_evidence.clone_check_status

    def replace_after_check(conn):
        status = original(conn)
        assert status == "ran"
        # A second WAL connection changes the snapshot between status checking
        # and the actual pair query. The reader must retain its original view.
        with open_db(readonly=False) as writer:
            writer.execute("DELETE FROM clone_pairs")
            writer.execute("UPDATE clone_scan_state SET metadata_json = '{}' WHERE id = 1")
            writer.commit()
        return status

    monkeypatch.setattr(clone_evidence, "clone_check_status", replace_after_check)
    result = review(project, batch=batch)
    if batch:
        assert result["diffs"][0]["findings"] > 0
        assert result["diffs"][0]["clone_scan"]["complete"] is True
    else:
        assert any(f["check"] == "clones-not-edited" for f in result["findings"])
        assert result["summary"]["clone_scan"]["complete"] is True


def test_read_only_scan_keeps_its_original_index_generation(project, monkeypatch):
    import hashlib

    from roam.graph.clone_evidence import index_identity

    with open_db(readonly=True) as conn:
        before = index_identity(conn)
    original = clone_detect._parallel_extract_func_infos

    def change_index_after_extraction(*args):
        functions = original(*args)
        content = (project / "a.py").read_bytes()
        (project / "later.py").write_bytes(content)
        with open_db(readonly=False) as writer:
            writer.execute(
                "INSERT INTO files (path, language, hash, mtime) VALUES (?, ?, ?, ?)",
                ("later.py", "python", hashlib.sha256(content).hexdigest(), 0),
            )
            writer.commit()
        return functions

    monkeypatch.setattr(clone_detect, "_parallel_extract_func_infos", change_index_after_extraction)
    result = run("clones")
    with open_db(readonly=True) as conn:
        assert index_identity(conn) != before, "the concurrent writer must actually change the indexed file set"
    assert result["summary"]["scan"]["index_identity"] == before
