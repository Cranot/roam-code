"""W607-C — ``cmd_findings`` threads ``warnings_out`` onto its JSON envelopes.

The W595-W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader. W607-A landed the
first consumer-layer wave on ``cmd_search_semantic``. W607-B landed
the second consumer-layer wave on ``cmd_retrieve`` (outer-guard-only,
since cmd_retrieve does not call the W605-plumbed substrate directly).
W607-C is the third consumer-layer wave on ``cmd_findings`` — the
read-side surface for the W89/W93 findings registry substrate.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_findings.py`` head-to-tail:

* The command has three subcommands (``list`` / ``show`` / ``count``),
  each with its own envelope-emit site (multiple per subcommand for
  different states: unknown_detector / not_yet_emitted / empty /
  populated / unknown_finding / found).
* Each subcommand invokes the W89/W93 substrate via ``count_by_detector``
  / ``known_detector_names`` / ``list_findings`` / ``get_finding``
  (all from ``roam.db.findings`` — W604 fail-loud-correct, no
  try/except inside the substrate; OperationalError propagates).
* No local ``warnings_out`` list existed in cmd_findings before
  W607-C. No markers were ever surfaced on any of the three
  subcommand envelopes.

Therefore the consumer-side gap was REAL. Because ``db/findings.py``
is W604 fail-loud-correct (raises ``sqlite3.OperationalError`` on
substrate failure), the disclosure shape lives at the
**outer-guard boundary**: any uncaught exception from the registry
query (substrate corruption, schema drift, locked DB, malformed
migration) emits ``findings_query_failed:<exc_class>:<detail>``.
This mirrors the cmd_retrieve W607-B
``retrieve_pipeline_failed:<exc_class>:<detail>`` outer-guard marker.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` locals (one per subcommand) are plain
accumulators; no shared module was created or hoisted. The
``db/findings.py`` substrate remains W604 NO-OP untouched.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.db.findings import FindingRecord, emit_finding

# ---------------------------------------------------------------------------
# Helpers — mirror the test_cmd_findings.py fixture shape so the two test
# files share project-shape assumptions without coupling.
# ---------------------------------------------------------------------------


def _seed_repo_and_index(tmp_path):
    """Create a tiny git-tracked repo + index it. Findings registry empty."""
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
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(proj),
        capture_output=True,
    )
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
    """Invoke CLI in-process at *cwd*; return (result, parsed_json_or_None)."""
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


def _emit_two_findings(proj):
    """Seed two findings so populated-path tests have something to query."""
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


# ---------------------------------------------------------------------------
# (1) HAPPY PATH — clean ``findings list`` → no warnings_out / no partial_success
# ---------------------------------------------------------------------------


def test_clean_findings_list_no_warnings(tmp_path):
    """Clean ``findings list`` on empty registry → no warnings_out."""
    proj = _seed_repo_and_index(tmp_path)
    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None
    assert data["command"] == "findings-list"

    assert "warnings_out" not in data, (
        f"clean findings-list must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean findings-list must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )
    assert data["summary"]["partial_success"] is False


def test_clean_findings_show_no_warnings(tmp_path):
    """``findings show`` on a non-existent id → unknown_finding state, no warnings_out."""
    proj = _seed_repo_and_index(tmp_path)
    # exit 2 is expected for unknown finding; parser handles both 0 and 2.
    result, data = _run(["--json", "findings", "show", "no:such:id"], cwd=proj)
    assert result.exit_code == 2, result.output
    assert data is not None
    assert data["command"] == "findings-show"
    assert data["summary"]["state"] == "unknown_finding"

    # The state legitimately sets partial_success=True (unknown finding),
    # but warnings_out must NOT appear because no substrate fault disclosed.
    assert "warnings_out" not in data, (
        f"clean unknown-finding must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean unknown-finding must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


def test_clean_findings_count_no_warnings(tmp_path):
    """Clean ``findings count`` on empty registry → no warnings_out."""
    proj = _seed_repo_and_index(tmp_path)
    result, data = _run(["--json", "findings", "count"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None
    assert data["command"] == "findings-count"
    assert data["summary"]["state"] == "empty"

    assert "warnings_out" not in data
    assert "warnings_out" not in data["summary"]
    assert data["summary"]["partial_success"] is False


# ---------------------------------------------------------------------------
# (2) OUTER-GUARD — ``list_findings`` raises → marker reaches envelope
# ---------------------------------------------------------------------------


def test_findings_list_outer_guard_emits_marker(tmp_path, monkeypatch):
    """Monkeypatch ``list_findings`` → exception → marker on envelope.

    Pattern-2 outer-guard contract: when the registry query raises
    before producing rows, the envelope surfaces a structured marker
    rather than a Click traceback. Mirrors cmd_retrieve W607-B's
    ``retrieve_pipeline_failed:`` outer-guard idiom.
    """
    proj = _seed_repo_and_index(tmp_path)
    # Seed a row so we land on a non-empty path before the failure occurs.
    _emit_two_findings(proj)

    from roam.commands import cmd_findings

    def _boom(*a, **kw):
        raise ConnectionError("synthetic-list-failure from W607-C test")

    monkeypatch.setattr(cmd_findings, "list_findings", _boom)

    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None

    # Top-level disclosure (canonical idiom — preserved-list field).
    top_wo = data.get("warnings_out")
    assert top_wo, (
        f"list_findings ConnectionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("findings_query_failed:") for m in top_wo), (
        f"expected ``findings_query_failed:`` marker in top-level warnings_out; got {top_wo!r}"
    )
    # ConnectionError class name must propagate for triage.
    assert any("ConnectionError" in m for m in top_wo), top_wo
    # Synthetic detail must propagate.
    assert any("synthetic-list-failure from W607-C test" in m for m in top_wo), top_wo


def test_findings_count_outer_guard_emits_marker(tmp_path, monkeypatch):
    """Monkeypatch ``count_by_detector`` → exception → marker on envelope.

    Same outer-guard contract for the ``count`` subcommand. The
    canonical marker family is shared (``findings_query_failed:``).
    """
    proj = _seed_repo_and_index(tmp_path)

    from roam.commands import cmd_findings

    # Replace count_by_detector ONLY for the cmd_findings module-level
    # binding so the empty-floor path inside cmd_findings_count fires.
    real_count = cmd_findings.count_by_detector

    def _boom_count(conn):
        # Only blow up on the count_by_detector call from cmd_findings_count,
        # not the one used inside cmd_findings_list (which has its own
        # outer-guard exercised by the test above).
        raise RuntimeError("synthetic-count-failure from W607-C test")

    monkeypatch.setattr(cmd_findings, "count_by_detector", _boom_count)
    # ``known_detector_names`` is imported separately in cmd_findings_list,
    # not used by cmd_findings_count, so we don't need to monkeypatch it.

    try:
        result, data = _run(["--json", "findings", "count"], cwd=proj)
    finally:
        monkeypatch.setattr(cmd_findings, "count_by_detector", real_count)

    assert result.exit_code == 0, result.output
    assert data is not None

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out")
    assert top_wo, (
        f"count_by_detector RuntimeError must surface warnings_out; "
        f"got top={data.get('warnings_out')!r} "
        f"summary.warnings_out={data['summary'].get('warnings_out')!r}"
    )
    assert any(m.startswith("findings_query_failed:") for m in top_wo), top_wo
    assert any("RuntimeError" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flips_on_warning_present(tmp_path, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    findings list" from "findings list ran with substrate degradation"
    via summary.partial_success alone.
    """
    proj = _seed_repo_and_index(tmp_path)
    from roam.commands import cmd_findings

    def _boom(*a, **kw):
        raise RuntimeError("synthetic-runtime-partial-success-test")

    monkeypatch.setattr(cmd_findings, "list_findings", _boom)

    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None

    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) summary.warnings_out is populated alongside top-level on disclosure
# ---------------------------------------------------------------------------


def test_warnings_out_summary_mirror(tmp_path, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out are populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror
    gives consumers reading only the summary block visibility too.
    """
    proj = _seed_repo_and_index(tmp_path)
    from roam.commands import cmd_findings

    def _boom(*a, **kw):
        raise ValueError("synthetic-mirror-test")

    monkeypatch.setattr(cmd_findings, "list_findings", _boom)

    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Top-level mirror explicitly checked (W607-A/B discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(tmp_path, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. cmd_search_semantic W607-A + cmd_retrieve
    W607-B pinned the same discipline; W607-C extends it to findings.
    """
    proj = _seed_repo_and_index(tmp_path)
    from roam.commands import cmd_findings

    def _boom(*a, **kw):
        raise OSError("synthetic-top-level-mirror-check")

    monkeypatch.setattr(cmd_findings, "list_findings", _boom)

    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (6) Marker-shape parity — three-segment prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_marker_shape_three_segments(tmp_path, monkeypatch):
    """Marker must have three colon-separated segments.

    The marker shape MUST be ``<prefix>:<exc_class>:<detail>`` — three
    colon-separated segments — so downstream consumers can parse the
    exception class without regex gymnastics. Mirrors cmd_retrieve
    W607-B's outer-guard contract.
    """
    proj = _seed_repo_and_index(tmp_path)
    from roam.commands import cmd_findings

    def _boom(*a, **kw):
        raise TypeError("synthetic-outer-guard-emit-check")

    monkeypatch.setattr(cmd_findings, "list_findings", _boom)

    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None

    top_wo = data.get("warnings_out") or []
    assert top_wo, "outer-guard must emit at least one marker"

    pipeline_markers = [m for m in top_wo if m.startswith("findings_query_failed:")]
    assert pipeline_markers, f"outer-guard must emit findings_query_failed marker; got {top_wo!r}"

    marker = pipeline_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "findings_query_failed", parts
    assert parts[1] == "TypeError", parts
    assert "synthetic-outer-guard-emit-check" in parts[2], parts


# ---------------------------------------------------------------------------
# (7) W604 substrate unchanged — ``db/findings.py`` is fail-loud-correct
# ---------------------------------------------------------------------------


def test_w604_substrate_unchanged():
    """AST-check ``src/roam/db/findings.py`` carries NO ``try/except``.

    The W604 audit declared the findings registry substrate
    fail-loud-correct: every ``conn.execute(...)`` call propagates
    ``sqlite3.OperationalError`` (schema mismatch, malformed SQL,
    missing table, etc.) loudly to the caller. W607-C is a
    consumer-layer-only change; this test pins that we did NOT
    regress the substrate by adding silent try/except inside
    ``db/findings.py``.
    """
    findings_py = Path(__file__).resolve().parent.parent / "src" / "roam" / "db" / "findings.py"
    tree = ast.parse(findings_py.read_text(encoding="utf-8"))

    try_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    assert not try_nodes, (
        f"W604 substrate ``db/findings.py`` must remain fail-loud — no "
        f"try/except blocks. Found {len(try_nodes)} try block(s) at "
        f"lines {[n.lineno for n in try_nodes]!r}. If you added one, "
        f"either lift the disclosure to the consumer (cmd_findings outer-guard) "
        f"or update W604 + W607-C contracts together."
    )


# ---------------------------------------------------------------------------
# (8) Populated path stays byte-stable on the happy path (regression net)
# ---------------------------------------------------------------------------


def test_populated_findings_list_no_warnings_slot(tmp_path):
    """Populated ``findings list`` → NO ``warnings_out`` slot in summary.

    Hash-stable: empty bucket on happy path → no slot emitted. This
    test is the pre-W607-C contract pin: adding warnings_out wiring
    must NOT introduce an empty slot on the populated branch.
    """
    proj = _seed_repo_and_index(tmp_path)
    _emit_two_findings(proj)

    result, data = _run(["--json", "findings", "list"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert data is not None
    assert data["summary"]["state"] == "populated"

    assert "warnings_out" not in data, (
        f"populated findings-list must NOT surface top-level warnings_out "
        f"on happy path; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"populated findings-list must NOT populate summary.warnings_out "
        f"on happy path; got {data['summary'].get('warnings_out')!r}"
    )
    assert data["summary"]["partial_success"] is False
