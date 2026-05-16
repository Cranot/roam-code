"""CLI tests for ``roam findings`` (list / show / count).

The findings table is the cross-detector registry shipped by A4. The CLI
in ``src/roam/commands/cmd_findings.py`` is the read-side surface. These
tests exercise it end-to-end through the Click runner so the envelope
contract, the empty-state path (Pattern 1), and the integration with the
``db.findings`` helpers are all covered.

Most repos will have an empty findings table until per-detector emit
sites migrate to also write here — the empty-state tests are the
primary regression net (a JSON-on-empty-input crash is the #1
anti-pattern in CLAUDE.md).
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.db.findings import FindingRecord, emit_finding


def _seed_repo_and_index(tmp_path):
    """Create a tiny git-tracked repo and index it.

    Returns the project root path. The findings table is empty after
    indexing — no detector has been migrated to emit into it yet.
    """
    import subprocess

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(proj),
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed: {result.output}"
    finally:
        os.chdir(old_cwd)

    return proj


def _run(args, cwd):
    """Invoke the CLI in-process at *cwd*; return (result, parsed_json_or_None)."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    parsed = None
    if "--json" in args and result.exit_code in (0, 2):
        raw = getattr(result, "stdout", None) or result.output
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
    return result, parsed


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_findings_list_empty_repo_text_mode(tmp_path):
    """Empty registry → text-mode VERDICT line, no crash."""
    proj = _seed_repo_and_index(tmp_path)
    result, _ = _run(["findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "VERDICT:" in result.output
    assert "no findings" in result.output.lower()


def test_findings_list_empty_repo_json_mode(tmp_path):
    """Empty registry under --json emits a well-formed envelope (Pattern 1)."""
    proj = _seed_repo_and_index(tmp_path)
    result, parsed = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None, f"non-JSON output: {result.output!r}"
    assert parsed["command"] == "findings-list"
    summary = parsed["summary"]
    assert summary["state"] == "empty"
    assert summary["total_findings"] == 0
    assert summary["partial_success"] is False
    assert "verdict" in summary
    assert parsed["findings"] == []


def test_findings_list_with_data_json(tmp_path):
    """After emit_finding writes rows, list returns them under --json."""
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="alpha:sym:1",
                subject_kind="symbol",
                subject_id=1,
                claim="alpha finding one",
                source_detector="alpha",
            ),
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="beta:file:1",
                subject_kind="file",
                claim="beta finding one",
                source_detector="beta",
            ),
        )
        conn.commit()

    result, parsed = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    summary = parsed["summary"]
    assert summary["state"] == "populated"
    assert summary["total_findings"] == 2
    detectors = set(summary["detectors"])
    assert detectors == {"alpha", "beta"}
    assert len(parsed["findings"]) == 2


def test_findings_list_filter_by_detector(tmp_path):
    """--detector filter limits the rows returned."""
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="alpha:sym:1",
                subject_kind="symbol",
                claim="alpha",
                source_detector="alpha",
            ),
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="beta:sym:1",
                subject_kind="symbol",
                claim="beta",
                source_detector="beta",
            ),
        )
        conn.commit()

    result, parsed = _run(["--json", "findings", "list", "--detector", "alpha"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    assert parsed["summary"]["total_findings"] == 1
    assert parsed["findings"][0]["source_detector"] == "alpha"


def test_findings_list_unknown_detector_suggests_close_match_json(tmp_path):
    """W1066: unknown --detector with a close match emits ``did_you_mean``.

    Mirrors the W1064 ``--only/--exclude`` precedent on ``roam math``:
    when a typo lands within difflib cutoff 0.6 of a registered detector,
    the JSON envelope's summary carries a ``did_you_mean`` list AND
    ``agent_contract.facts`` names the closest match.
    """
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="clones:sym:1",
                subject_kind="symbol",
                claim="clones finding",
                source_detector="clones",
            ),
        )
        conn.commit()

    # "cloens" is one transposition from "clones" — well inside cutoff 0.6.
    result, parsed = _run(["--json", "findings", "list", "--detector", "cloens"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    summary = parsed["summary"]
    assert summary["state"] == "unknown_detector"
    assert summary["partial_success"] is True
    assert "did_you_mean" in summary, summary
    assert "clones" in summary["did_you_mean"]
    # Closest-match also surfaces on the agent contract.
    facts = parsed.get("agent_contract", {}).get("facts", [])
    assert any("closest match" in f for f in facts), facts


def test_findings_list_unknown_detector_no_close_match_omits_field(tmp_path):
    """W1066: with no close match, the envelope stays byte-identical to pre-W1066.

    ``did_you_mean`` MUST be absent (not present-and-empty) when no
    registered detector is within cutoff 0.6 of the user input. The
    facts list MUST NOT include a closest-match line.
    """
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="clones:sym:1",
                subject_kind="symbol",
                claim="clones finding",
                source_detector="clones",
            ),
        )
        conn.commit()

    # "zzzzzzzz" is nowhere near "clones" — well outside cutoff 0.6.
    result, parsed = _run(["--json", "findings", "list", "--detector", "zzzzzzzz"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    summary = parsed["summary"]
    assert summary["state"] == "unknown_detector"
    assert "did_you_mean" not in summary, summary
    facts = parsed.get("agent_contract", {}).get("facts", [])
    assert not any("closest match" in f for f in facts), facts


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_findings_show_missing_id_json(tmp_path):
    """Unknown id under --json → envelope with state=unknown_finding, exit 2."""
    proj = _seed_repo_and_index(tmp_path)
    result, parsed = _run(["--json", "findings", "show", "does-not-exist"], cwd=proj)
    assert result.exit_code == 2
    assert parsed is not None
    assert parsed["summary"]["state"] == "unknown_finding"
    assert parsed["summary"]["error"] == "finding_not_found"
    assert parsed["finding"] is None


def test_findings_show_existing(tmp_path):
    """Show returns the full record by stable id."""
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="alpha:sym:abc",
                subject_kind="symbol",
                subject_id=7,
                claim="alpha finding seven",
                source_detector="alpha",
                source_version="0.1.0",
            ),
        )
        conn.commit()

    result, parsed = _run(["--json", "findings", "show", "alpha:sym:abc"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    assert parsed["summary"]["state"] == "found"
    record = parsed["finding"]
    assert record["finding_id_str"] == "alpha:sym:abc"
    assert record["source_version"] == "0.1.0"
    assert record["subject_id"] == 7


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


def test_findings_count_empty(tmp_path):
    """Empty registry → count envelope says state=empty, total=0."""
    proj = _seed_repo_and_index(tmp_path)
    result, parsed = _run(["--json", "findings", "count"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    assert parsed["summary"]["state"] == "empty"
    assert parsed["summary"]["total_findings"] == 0
    assert parsed["counts"] == {}


def test_findings_count_per_detector(tmp_path):
    """count groups rows by detector correctly."""
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        for i in range(3):
            emit_finding(
                conn,
                FindingRecord(
                    finding_id_str=f"alpha:sym:{i}",
                    subject_kind="symbol",
                    claim=f"alpha {i}",
                    source_detector="alpha",
                ),
            )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="beta:sym:1",
                subject_kind="symbol",
                claim="beta",
                source_detector="beta",
            ),
        )
        conn.commit()

    result, parsed = _run(["--json", "findings", "count"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None
    assert parsed["summary"]["total_findings"] == 4
    assert parsed["summary"]["total_detectors"] == 2
    assert parsed["counts"] == {"alpha": 3, "beta": 1}


# ---------------------------------------------------------------------------
# Help / registration smoke
# ---------------------------------------------------------------------------


def test_findings_help_includes_subcommands(tmp_path):
    """`roam findings --help` lists the three subcommands."""
    proj = _seed_repo_and_index(tmp_path)
    result, _ = _run(["findings", "--help"], cwd=proj)
    assert result.exit_code == 0
    out = result.output
    assert "list" in out
    assert "show" in out
    assert "count" in out
