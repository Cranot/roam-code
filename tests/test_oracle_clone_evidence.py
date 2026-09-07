"""Clone membership distinguishes observed pairs from unsupported absence."""

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
    for name in ("first", "second"):
        (tmp_path / f"{name}.py").write_text(f"def {name}(value):\n" + body, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    git_init(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args, input=None):
    result = CliRunner().invoke(cli, ["--json", *args], input=input)
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "state", ["never", "capped", "filtered", "stale_source", "stale_index", "invalid", "missing_metadata"]
)
def test_unqualified_absence_stays_unknown_through_cli(project, monkeypatch, state):
    if state == "capped":
        original = clone_detect.detect_clones
        monkeypatch.setattr(clone_detect, "detect_clones", lambda *a, **kw: original(*a, **kw, max_functions=1))
    if state != "never":
        run("clones", "--persist", *(("--scope", "first.py") if state == "filtered" else ()))
    if state == "stale_source":
        (project / "second.py").write_text("def changed(): pass\n", encoding="utf-8")
    elif state in {"stale_index", "invalid", "missing_metadata"}:
        with open_db(readonly=False) as conn:
            if state == "stale_index":
                conn.execute("UPDATE files SET hash = 'another-generation'")
            elif state == "invalid":
                conn.execute("UPDATE clone_scan_state SET metadata_json = '{}'")
            else:
                conn.execute("DELETE FROM clone_scan_state")
            conn.commit()
    summary = run("oracle", "is-clone-of", "unmatched")["summary"]
    assert summary["value"] is None, summary
    assert summary["verdict"] == "indeterminate"
    assert summary["partial_success"] is True
    assert summary["reason_class"] == "indeterminate_no_data"


def test_complete_empty_scan_supports_bounded_negative_answer(project):
    run("clones", "--persist", "--min-lines", "100")
    summary = run("oracle", "is-clone-of", "first")["summary"]
    assert summary["value"] is False
    assert summary["partial_success"] is False
    assert summary["check_status"] == "ran"
    assert summary["clone_scan"]["min_lines"] == 100
    assert "sources" not in summary["clone_scan"]


@pytest.mark.parametrize("stale", [False, True])
def test_observed_pairs_remain_visible_with_freshness_qualification(project, stale):
    run("clones", "--persist")
    if stale:
        (project / "second.py").write_text("def changed(): pass\n", encoding="utf-8")
    summary = run("oracle", "is-clone-of", "first")["summary"]
    assert summary["value"] is True
    assert summary["partial_success"] is stale
    assert (summary["check_status"] == "ran") is not stale
    assert "persisted clone pair" in summary["reason"]


@pytest.mark.parametrize("name", ["a_b", "a%b", "a\\b", "AXB"])
def test_symbol_suffixes_are_literal_not_like_patterns(project, name):
    run("clones", "--persist", "--min-lines", "100")
    with open_db(readonly=False) as conn:
        conn.execute(
            "INSERT INTO clone_pairs (qname_a,qname_b,file_a,file_b,func_a,func_b,line_a,line_b,similarity) VALUES (?,?,?,?,?,?,?,?,?)",
            ("module.axb", "module.partner", "first.py", "second.py", "axb", "partner", 1, 1, 0.95),
        )
        conn.commit()
    summary = run("oracle", "is-clone-of", name)["summary"]
    assert summary["value"] is False, summary
    with open_db(readonly=False) as conn:
        conn.execute("UPDATE clone_pairs SET qname_a = ?", (f"module.{name}",))
        conn.commit()
    assert run("oracle", "is-clone-of", name)["summary"]["value"] is True


def test_batch_preserves_unknown_and_partial_rows(project):
    result = run(
        "oracle",
        "batch",
        input='{"oracle":"is-clone-of","args":{"name":"first"}}\n{"oracle":"symbol-exists","args":{"name":"first"}}\n',
    )
    assert result["summary"]["partial_success"] is True
    assert result["results"][0]["answer"] is None
    assert result["results"][0]["partial_success"] is True
    assert result["results"][1]["answer"] is True
    assert result["results"][1]["partial_success"] is False


@pytest.mark.parametrize("caller_transaction", [False, True])
def test_pairs_and_metadata_share_one_read_snapshot(project, monkeypatch, caller_transaction):
    from roam.commands.cmd_oracle import oracle_is_clone_of
    from roam.graph import clone_evidence

    run("clones", "--persist")
    original = clone_evidence.clone_check_status

    def replace_saved_scan(conn):
        with open_db(readonly=False) as writer:
            writer.execute("DELETE FROM clone_pairs")
            writer.execute("UPDATE clone_scan_state SET metadata_json = '{}'")
            writer.commit()
        return original(conn)

    monkeypatch.setattr(clone_evidence, "clone_check_status", replace_saved_scan)
    with open_db(readonly=True) as conn:
        if caller_transaction:
            conn.execute("BEGIN")
        result = oracle_is_clone_of(conn, "first.py:first")
        assert result.value is True
        assert result.evidence["check_status"] == "ran"
        assert conn.in_transaction is caller_transaction


@pytest.mark.parametrize("scan", ["missing", "complete", "filtered"])
def test_mcp_single_and_batch_preserve_the_same_clone_evidence(project, scan):
    from roam.mcp_server import oracle_batch, roam_oracle_is_clone_of

    if scan != "missing":
        run("clones", "--persist", *(("--scope", "first.py") if scan == "filtered" else ()))
    single = roam_oracle_is_clone_of(symbol="first", root=str(project))
    batch = oracle_batch(items=[{"oracle": "is-clone-of", "name": "first"}], root=str(project))
    assert batch["summary"]["partial_success"] is (scan != "complete"), batch
    row = batch["results"][0]
    for field in ("value", "check_status", "clone_scan", "partial_success"):
        assert row[field] == single["summary"][field], (field, row, single)


def test_mcp_batch_uses_requested_root_without_indexing_server_cwd(project, monkeypatch):
    from roam.mcp_server import oracle_batch, roam_oracle_is_clone_of

    run("clones", "--persist")
    foreign = project.parent / (project.name + "-server-cwd")
    foreign.mkdir()
    (foreign / "README.md").write_text("Server working directory, not the requested project.\n", encoding="utf-8")
    git_init(foreign)
    monkeypatch.chdir(foreign)
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")
    single = roam_oracle_is_clone_of(symbol="first", root=str(project))
    batch = oracle_batch(items=[{"oracle": "is-clone-of", "name": "first"}], root=str(project))
    assert batch.get("summary", {}).get("partial_success") is False, batch
    for field in ("value", "check_status", "clone_scan", "partial_success"):
        assert batch["results"][0][field] == single["summary"][field]
    assert "INDEX STALE" not in single["summary"]["verdict"]
    assert not (foreign / ".roam").exists()


def test_explicit_root_preserves_producer_freshness_without_server_probe(monkeypatch):
    from roam import mcp_server as mcp

    result = {
        "summary": {"verdict": "qualified", "partial_success": True},
        "_meta": {"index_status": {"state": "stale", "reason": "requested project changed"}},
    }
    monkeypatch.setattr(mcp, "_run_roam_subprocess", lambda *_args: result)

    def foreign_probe():
        pytest.fail("server cwd cannot qualify another project's evidence")

    monkeypatch.setattr(mcp, "_check_stale_with_cache", foreign_probe)
    assert mcp._run_roam(["oracle", "is-clone-of", "first"], root="other-project") is result
