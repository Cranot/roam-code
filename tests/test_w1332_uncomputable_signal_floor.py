"""W1332 — an uncomputable signal must not be floored to a clean-looking value.

Sibling of W1320/W1331, same doctrine, a different mechanism. W1331 finds a
disclosure that reaches ONE output branch; this file pins the case where the
disclosure is never computed at all: a broken input (unparseable config,
unreadable index, dropped table, failed query, absent component) is absorbed by
a local ``except`` that returns the value meaning FINE — ``()``, ``[]``,
``severity="ok"``, ``count=0``, ``score=100``.

The five instances fixed here:

1. ``verify --checks tenant_scope`` — a malformed ``.roam/verify.yaml`` made
   the guard loader return ``()``, indistinguishable from the deliberate
   ``tenant_guards: []`` that disables the detector, so the WARN finding and
   ``agent_contract.risks`` vanished with no disclosure anywhere.
2. ``verify --report`` — whole-repo target discovery returned ``[]`` on ANY
   index-open failure, so a corrupt ``.roam/index.db`` scored 100/PASS with
   ``verification_complete: true``. The NON-report path on the same DB already
   failed loudly, so the two disagreed about the same database.
3. ``db-check`` — three ``OperationalError`` paths were downgraded to
   ``severity: "ok"``, so ``DROP TABLE symbol_metrics`` reported ``OK`` /
   ``errors: 0`` and ``--ci`` exited 0. Plus: ``_check_zero_symbols_per_file``
   queried ``f.lang`` where the column is ``language``, so it raised on EVERY
   database and was unconditionally masked — the check had never worked.
4. ``catalog/detectors`` — 12 ``except sqlite3.Error: return []`` handlers
   pre-empted the orchestrator, which already logs the failure and records it
   in ``failed_detectors``.
5. ``service-report reachability-triage`` — three security counters used a
   ``0`` default while their own sibling sections rendered an em-dash for the
   SAME missing field, so a failed component read as "zero findings".

Every test carries a NEGATIVE CONTROL — the same code path on healthy input —
so a "fix" that simply makes everything loud cannot pass this file.
"""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import index_in_process, invoke_cli

# ---------------------------------------------------------------------------
# 1. tenant-scope guard config — "unreadable" must not read as "disabled".
# ---------------------------------------------------------------------------


class TestTenantGuardConfigDisclosure:
    """``load_tenant_guard_signals`` returned ``()`` for two opposite states."""

    @staticmethod
    def _write_config(root: Path, text: str) -> Path:
        (root / ".roam").mkdir(parents=True, exist_ok=True)
        (root / ".roam" / "verify.yaml").write_text(text, encoding="utf-8")
        return root

    def test_malformed_yaml_is_disclosed_as_an_error(self, tmp_path):
        from roam.security.tenant_scope import load_tenant_guard_signals_status

        self._write_config(tmp_path, "tenant_guards: [require_tenant\n")
        guards, error = load_tenant_guard_signals_status(tmp_path)

        assert guards == ()
        assert error, "an unparseable guard config must be disclosed, not silently empty"
        assert "verify.yaml" in error

    def test_non_mapping_config_is_disclosed_as_an_error(self, tmp_path):
        from roam.security.tenant_scope import load_tenant_guard_signals_status

        self._write_config(tmp_path, "- just\n- a\n- list\n")
        _, error = load_tenant_guard_signals_status(tmp_path)

        assert error and "mapping" in error

    def test_non_list_guards_is_disclosed_as_an_error(self, tmp_path):
        from roam.security.tenant_scope import load_tenant_guard_signals_status

        self._write_config(tmp_path, "tenant_guards: require_tenant\n")
        _, error = load_tenant_guard_signals_status(tmp_path)

        assert error and "list" in error

    # --- negative controls: the honest empty states stay silent -------------

    def test_deliberate_empty_list_is_not_an_error(self, tmp_path):
        """``tenant_guards: []`` genuinely disables the detector. Same empty
        tuple, opposite meaning — it must NOT be reported as a failure."""
        from roam.security.tenant_scope import load_tenant_guard_signals_status

        self._write_config(tmp_path, "tenant_guards: []\n")
        guards, error = load_tenant_guard_signals_status(tmp_path)

        assert guards == ()
        assert error is None

    def test_absent_config_yields_defaults_without_error(self, tmp_path):
        from roam.security.tenant_scope import (
            DEFAULT_TENANT_GUARDS,
            load_tenant_guard_signals_status,
        )

        guards, error = load_tenant_guard_signals_status(tmp_path)

        assert guards == DEFAULT_TENANT_GUARDS
        assert error is None

    def test_valid_config_is_parsed_without_error(self, tmp_path):
        from roam.security.tenant_scope import load_tenant_guard_signals_status

        self._write_config(tmp_path, "tenant_guards: [require_tenant, with_tenant]\n")
        guards, error = load_tenant_guard_signals_status(tmp_path)

        assert guards == ("require_tenant", "with_tenant")
        assert error is None


class TestVerifyTenantScopeConfigFailure:
    """The verify wire must fail closed on a config it could not honour."""

    @staticmethod
    def _empty_index_conn():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT)")
        conn.execute(
            "CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, "
            "qualified_name TEXT, kind TEXT, line_start INTEGER, line_end INTEGER)"
        )
        return conn

    def test_malformed_config_makes_the_check_incomplete(self, tmp_path):
        from roam.commands.cmd_verify import _check_tenant_scope

        (tmp_path / ".roam").mkdir()
        (tmp_path / ".roam" / "verify.yaml").write_text("tenant_guards: [require_tenant\n", encoding="utf-8")

        result = _check_tenant_scope(self._empty_index_conn(), [], ["app/routes.py"], tmp_path)

        assert result["available"] is False
        assert result["partial_success"] is True
        assert result["execution_state"] == "failed"
        assert "could not be honoured" in result["unavailable_reason"]

    def test_valid_config_does_not_trip_the_failure_branch(self, tmp_path):
        """NEGATIVE CONTROL — a readable config still produces a normal result."""
        from roam.commands.cmd_verify import _check_tenant_scope

        (tmp_path / ".roam").mkdir()
        (tmp_path / ".roam" / "verify.yaml").write_text("tenant_guards: [require_tenant]\n", encoding="utf-8")

        result = _check_tenant_scope(self._empty_index_conn(), [], ["app/routes.py"], tmp_path)

        assert result.get("available") is not False
        assert result["score"] == 100
        assert result["violations"] == []


# ---------------------------------------------------------------------------
# 2. verify --report — an unreadable index is not an empty repo.
# ---------------------------------------------------------------------------


_REPORT_PROJECT_FILES = {
    "app/handlers.py": (
        "def divide(a, b):\n    try:\n        return a / b\n    except Exception:\n        pass\n    return None\n"
    ),
}


def _make_report_project(tmp_path) -> Path:
    proj = tmp_path / "report_proj"
    (proj / "app").mkdir(parents=True)
    for rel, content in _REPORT_PROJECT_FILES.items():
        (proj / rel).write_text(content, encoding="utf-8")
    index_in_process(proj)
    return proj


class TestVerifyReportPathDiscovery:
    def test_unreadable_index_yields_an_error_not_an_empty_list(self, tmp_path, monkeypatch):
        """``_all_source_paths`` must separate "no source files" from "no index"."""
        from roam.commands import cmd_verify

        proj = _make_report_project(tmp_path)
        monkeypatch.chdir(proj)

        paths, error = cmd_verify._all_source_paths(proj)
        assert paths and error is None, "NEGATIVE CONTROL: a healthy index reports no error"

        (proj / ".roam" / "index.db").write_text("not a database at all\n", encoding="utf-8")
        paths, error = cmd_verify._all_source_paths(proj)

        assert paths == []
        assert error and "index unreadable" in error

    def test_corrupt_index_fails_the_report_instead_of_scoring_100(self, tmp_path, cli_runner, monkeypatch):
        proj = _make_report_project(tmp_path)
        monkeypatch.chdir(proj)

        healthy = invoke_cli(cli_runner, ["verify", "--report"], cwd=proj, json_mode=True)
        healthy_summary = json.loads(healthy.output)["summary"]
        assert healthy_summary["files_checked"] > 0, "NEGATIVE CONTROL: the healthy run checks files"
        assert healthy_summary["verification_complete"] is True

        (proj / ".roam" / "index.db").write_text("not a database at all\n", encoding="utf-8")
        for sidecar in (".roam/index.db-wal", ".roam/index.db-shm"):
            (proj / sidecar).unlink(missing_ok=True)

        result = invoke_cli(cli_runner, ["verify", "--report"], cwd=proj, json_mode=True)
        summary = json.loads(result.output)["summary"]

        assert result.exit_code == 5, "a report over an unreadable index must not exit 0"
        assert summary["verdict"].startswith("FAIL")
        assert summary["score"] == 0
        assert summary["verification_complete"] is False
        assert summary["partial_success"] is True
        assert summary["state"] == "report_scope_index_unreadable"
        assert "report_scope_discovery_failed" in summary["incomplete_reasons"]


# ---------------------------------------------------------------------------
# 3. db-check — a check that could not run is not a check that passed.
# ---------------------------------------------------------------------------


def _index_db_conn(rows) -> sqlite3.Connection:
    """A minimal ``files``/``symbols`` pair using the SHIPPED column names."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT, file_role TEXT)")
    conn.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT)")
    for file_id, (path, language, symbol_count) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO files (id, path, language, file_role) VALUES (?, ?, ?, 'source')",
            (file_id, path, language),
        )
        for n in range(symbol_count):
            conn.execute("INSERT INTO symbols (file_id, name) VALUES (?, ?)", (file_id, f"sym{n}"))
    conn.commit()
    return conn


class TestDbCheckZeroSymbolsQuery:
    """The check queried ``f.lang``; the column is ``files.language``."""

    def test_detects_a_parsed_nothing_source_file(self):
        from roam.commands.cmd_db_check import _check_zero_symbols_per_file

        conn = _index_db_conn([("app/empty.py", "python", 0), ("app/ok.py", "python", 2)])
        finding = _check_zero_symbols_per_file(conn)

        assert finding["count"] == 1
        assert finding["severity"] == "medium"
        assert "note" not in finding, "a check that ran must not carry an unsupported note"

    def test_clean_index_reports_zero(self):
        """NEGATIVE CONTROL — the fixed query still says OK when it should."""
        from roam.commands.cmd_db_check import _check_zero_symbols_per_file

        conn = _index_db_conn([("app/ok.py", "python", 2)])
        finding = _check_zero_symbols_per_file(conn)

        assert finding == {"name": "files_with_zero_symbols", "count": 0, "severity": "ok"}

    def test_data_languages_are_still_exempt(self):
        from roam.commands.cmd_db_check import _check_zero_symbols_per_file

        conn = _index_db_conn([("data/config.json", "json", 0)])

        assert _check_zero_symbols_per_file(conn)["count"] == 0


class TestDbCheckDroppedTable:
    def test_dropped_metrics_table_fails_the_ci_gate(self, tmp_path, cli_runner, monkeypatch):
        proj = _make_report_project(tmp_path)
        monkeypatch.chdir(proj)

        healthy = invoke_cli(cli_runner, ["db-check", "--ci"], cwd=proj, json_mode=True)
        healthy_summary = json.loads(healthy.output)["summary"]
        assert healthy.exit_code == 0, "NEGATIVE CONTROL: a healthy index passes the gate"
        assert healthy_summary["errors"] == 0
        assert healthy_summary["checks_complete"] is True

        conn = sqlite3.connect(proj / ".roam" / "index.db")
        conn.execute("DROP TABLE symbol_metrics")
        conn.commit()
        conn.close()

        result = invoke_cli(cli_runner, ["db-check", "--ci"], cwd=proj, json_mode=True)
        summary = json.loads(result.output)["summary"]

        assert result.exit_code == 5, "--ci must not exit 0 when a check could not run"
        assert summary["verdict"] == "BAD"
        assert summary["errors"] == 1
        assert summary["checks_complete"] is False
        assert summary["partial_success"] is True

        failed = [f for f in json.loads(result.output)["findings"] if f["severity"] == "error"]
        assert [f["name"] for f in failed] == ["corrupt_metrics"]
        assert failed[0]["count"] is None, "a failed check must not report a measured zero"

    def test_incomplete_verdict_outranks_ok(self):
        """An unsupported-only sweep renders INCOMPLETE, never OK."""
        from roam.commands.cmd_db_check import SEVERITY_UNSUPPORTED, _unsupported

        row = _unsupported("missing_fts_rows", "fts5 not available")
        assert row["count"] is None
        assert row["severity"] == SEVERITY_UNSUPPORTED
        assert row["severity"] != "ok"


# ---------------------------------------------------------------------------
# 4. detectors — the orchestrator already discloses; local catches pre-empt it.
# ---------------------------------------------------------------------------


_ORCHESTRATED_DETECTORS = (
    "detect_async_blocking_sleep",
    "detect_broad_except_swallow",
    "detect_useeffect_missing_deps",
    "detect_dangerous_eval",
    "detect_unremoved_event_listener",
    "detect_branching_recursion",
    "detect_quadratic_string",
    "detect_loop_invariant_call",
    "detect_async_nested_run",
    "detect_defer_in_loop",
    "detect_async_fire_and_forget",
    # Both of these route through _js_source_findings_preserving_evidence_lines,
    # the shared helper that carried the 12th catch.
    "detect_chained_collection_walks",
    "detect_spread_accumulator",
)


class _FaultyConn:
    def execute(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("no such column: injected_fault")


@pytest.mark.parametrize("detector_name", _ORCHESTRATED_DETECTORS)
def test_detector_query_failure_reaches_the_orchestrator(detector_name, caplog):
    from roam.catalog import detectors

    detect_fn = getattr(detectors, detector_name)
    with caplog.at_level(logging.WARNING, logger="roam.catalog.detectors"):
        findings, failed, executed, _tasks = detectors._execute_detectors(
            _FaultyConn(), [("t", "w", detect_fn)], None, set(), set()
        )

    assert findings == []
    assert executed == 1
    assert [entry["detector"] for entry in failed] == [detector_name]
    assert "injected_fault" in failed[0]["error"]
    assert any("failed with sqlite error" in record.message for record in caplog.records)


def test_working_detector_is_not_reported_as_failed():
    """NEGATIVE CONTROL — removing the catches must not make everything fail."""
    from roam.catalog import detectors

    conn = _index_db_conn([("app/ok.py", "python", 0)])
    conn.execute("ALTER TABLE symbols ADD COLUMN qualified_name TEXT")
    conn.execute("ALTER TABLE symbols ADD COLUMN kind TEXT")
    conn.execute("ALTER TABLE symbols ADD COLUMN line_start INTEGER")
    conn.execute("ALTER TABLE symbols ADD COLUMN line_end INTEGER")
    conn.commit()

    findings, failed, executed, _tasks = detectors._execute_detectors(
        conn, [("t", "w", detectors.detect_dangerous_eval)], None, set(), set()
    )

    assert (findings, failed, executed) == ([], [], 1)


def test_no_detector_swallows_a_query_failure_into_an_empty_result():
    """Structural guard: ``except sqlite3.Error: return []`` must not come back.

    The fallback handlers that re-query with a degraded column set are fine —
    only a handler whose ENTIRE body is ``return []`` is the defect, because it
    hands the orchestrator a result indistinguishable from "nothing found".
    """
    source = Path("src/roam/catalog/detectors.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if "sqlite3" not in ast.unparse(node.type):
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            continue
        returned = node.body[0].value
        if isinstance(returned, ast.List) and not returned.elts:
            offenders.append(node.lineno)

    assert offenders == [], f"sqlite failure floored to [] at detectors.py lines {offenders}"


# ---------------------------------------------------------------------------
# 5. service-report — the executive summary must agree with its own sections.
# ---------------------------------------------------------------------------


def _triage_markdown(env: dict) -> str:
    from roam.commands import cmd_service_report as report

    failures, degraded = report._component_health(env)
    meta = {
        "type_meta": report._REPORT_TYPES["reachability-triage"],
        "report_type": "reachability-triage",
        "client": None,
        "index_sha": "0" * 12,
        "generated_at": "2026-01-01T00:00:00Z",
        "subject": "fixture",
        "component_failures": failures,
        "component_degraded": degraded,
    }
    return report._render_reachability_triage(env=env, meta=meta)


def _bullet(markdown: str, prefix: str) -> str:
    for line in markdown.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no line starting with {prefix!r}")


class TestReachabilityTriageSummaryAgreesWithSections:
    _COMPONENTS = ("sbom", "supply_chain", "vulns", "vuln_reach", "taint", "secrets")

    def test_failed_components_render_missing_in_the_summary(self):
        from roam.commands import cmd_service_report as report

        env = {
            name: report._component_failure(name, "component_unavailable", "binary missing")
            for name in self._COMPONENTS
        }
        markdown = _triage_markdown(env)

        # Section 4/5 already rendered the em-dash for these exact fields; the
        # summary printed a hard 0 for the same missing measurement.
        assert "**—**" in _bullet(markdown, "- Taint flows:")
        assert "**—**" in _bullet(markdown, "- Active secrets:")
        assert "**—**" in _bullet(markdown, "- Reachable known vulnerabilities:")
        assert "**—**" in _bullet(markdown, "- Findings:")
        assert "**—**" in _bullet(markdown, "- Active secret findings:")
        assert "**0**" not in _bullet(markdown, "- Taint flows:")

    def test_real_data_still_renders_the_numbers(self):
        """NEGATIVE CONTROL — a genuine zero is still a zero, not an em-dash."""
        env = {
            "sbom": {"summary": {"verdict": "ok", "total_dependencies": 10, "reachable_count": 4}},
            "supply_chain": {"summary": {"verdict": "ok"}},
            "vulns": {"summary": {"verdict": "ok", "total": 9}},
            "vuln_reach": {"summary": {"verdict": "ok", "reachable_count": 0}},
            "taint": {"summary": {"verdict": "ok", "findings": 3, "rules": 7, "risk_score": 42}},
            "secrets": {"summary": {"verdict": "ok", "total_findings": 0}},
        }
        markdown = _triage_markdown(env)

        assert "**3**" in _bullet(markdown, "- Taint flows:")
        assert "**0**" in _bullet(markdown, "- Active secrets:")
        assert "**0**" in _bullet(markdown, "- Reachable known vulnerabilities:")
        assert "**3**" in _bullet(markdown, "- Findings:")
        assert "**0**" in _bullet(markdown, "- Active secret findings:")
