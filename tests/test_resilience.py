"""Tests for ``roam resilience`` — the reliability super-optimizer (P3).

Engine-level coverage (the CLI surface lands in a later phase): the
missing-timeout detector (pure-function unit level + comment/language/
indicator handling), the family-local registry + closed-enum validation,
the shared CATALOG family tagging, the source harvester (file-role
exclusion + CI-path exclusion + on-disk read), the orchestrator's
only/exclude + partial_success discipline, and the A4 persistence wiring.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from roam import resilience as rs
from roam.catalog.tasks import CATALOG


# ---------------------------------------------------------------------------
# Detector unit tests (pure function — deterministic, no live harvest)
# ---------------------------------------------------------------------------
class TestMissingTimeout:
    def test_flags_python_requests_without_timeout(self):
        sources = [("app/svc.py", "python", "import requests\nrequests.get('https://x.example')\n")]
        out = rs.detect_missing_timeout(sources)
        assert len(out) == 1
        f = out[0]
        assert f["task_id"] == "missing-timeout"
        assert f["detected_way"] == "no-explicit-timeout"
        assert f["suggested_way"] == "explicit-timeout"
        assert f["subject"] == "app/svc.py:2"
        assert f["confidence"] == "medium"

    def test_does_not_flag_python_requests_with_timeout(self):
        sources = [("a.py", "python", "import requests\nrequests.get('https://x', timeout=5)\n")]
        assert rs.detect_missing_timeout(sources) == []

    def test_does_not_flag_httpx_with_timeout(self):
        sources = [("a.py", "python", "import httpx\nhttpx.post(url, json=body, timeout=3.0)\n")]
        assert rs.detect_missing_timeout(sources) == []

    def test_flags_js_fetch_without_signal(self):
        sources = [("ui.ts", "typescript", "async function f(){ return fetch('/api/x'); }\n")]
        out = rs.detect_missing_timeout(sources)
        assert len(out) == 1
        assert out[0]["subject"] == "ui.ts:1"
        # JS confidence is low — pattern can miss helper-wrapped timeouts.
        assert out[0]["confidence"] == "low"

    def test_does_not_flag_js_fetch_with_signal(self):
        sources = [("ui.ts", "typescript", "fetch(u, {signal: AbortSignal.timeout(5000)})\n")]
        assert rs.detect_missing_timeout(sources) == []

    def test_flags_go_http_get_unconditionally(self):
        # http.Get uses http.DefaultClient which has NO Timeout field — there
        # is no same-line cure, so EVERY call site is high confidence.
        sources = [("client.go", "go", 'func main(){ http.Get("https://x") }\n')]
        out = rs.detect_missing_timeout(sources)
        assert len(out) == 1
        assert out[0]["confidence"] == "high"

    def test_skips_commented_lines(self):
        sources = [("a.py", "python", "# requests.get('old')\n#requests.post('also old')\n")]
        assert rs.detect_missing_timeout(sources) == []

    def test_skips_unknown_language_and_empty_text(self):
        sources = [("x.cob", "cobol", "CALL 'URL'.\n"), ("y.py", "python", "")]
        assert rs.detect_missing_timeout(sources) == []

    def test_does_not_flag_non_request_python(self):
        # ``requests`` module is sometimes a local variable named requests; we
        # only flag the .verb() shape so plain ``requests`` references are fine.
        sources = [("a.py", "python", "x = requests\nprint(x)\n")]
        assert rs.detect_missing_timeout(sources) == []


# ---------------------------------------------------------------------------
# Registry + closed-enum discipline
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_detector_is_registered_under_family_task(self):
        names = {d["name"] for d in rs.list_resilience_detectors()}
        assert "detect_missing_timeout" in names
        entry = next(d for d in rs.list_resilience_detectors() if d["name"] == "detect_missing_timeout")
        assert entry["task_id"] == "missing-timeout"
        assert entry["family"] == "resilience"
        assert entry["confidence_basis"] == "heuristic"
        assert entry["query_cost"] == "medium"

    def test_decorator_rejects_bad_confidence_basis(self):
        with pytest.raises(ValueError):
            rs.resilience_detector(task_id="missing-timeout", confidence_basis="bogus")(lambda s: [])

    def test_decorator_rejects_bad_query_cost(self):
        with pytest.raises(ValueError):
            rs.resilience_detector(task_id="missing-timeout", query_cost="instant")(lambda s: [])

    def test_decorator_rejects_untagged_task(self):
        # ``sorting`` is a real CATALOG task but has no resilience family tag.
        with pytest.raises(ValueError):
            rs.resilience_detector(task_id="sorting")(lambda s: [])


# ---------------------------------------------------------------------------
# Shared CATALOG tagging
# ---------------------------------------------------------------------------
class TestCatalog:
    def test_task_is_family_tagged_with_ranked_ways(self):
        task = CATALOG["missing-timeout"]
        assert task["family"] == "resilience"
        ranks = {w["id"]: w["rank"] for w in task["ways"]}
        assert ranks["explicit-timeout"] == 1
        assert ranks["no-explicit-timeout"] == 10
        from roam.catalog.tasks import best_way

        assert best_way("missing-timeout")["id"] == "explicit-timeout"

    def test_task_ids_accessor_matches_catalog(self):
        assert rs.resilience_task_ids() == ["missing-timeout"]


# ---------------------------------------------------------------------------
# Source harvester
# ---------------------------------------------------------------------------
def _files_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (path TEXT, language TEXT, file_role TEXT)")
    return conn


class TestHarvester:
    def test_excludes_test_and_non_source_roles(self, tmp_path):
        (tmp_path / "svc.py").write_text("requests.get('x')\n", encoding="utf-8")
        (tmp_path / "test_svc.py").write_text("requests.get('x')\n", encoding="utf-8")
        conn = _files_db()
        conn.executemany(
            "INSERT INTO files (path, language, file_role) VALUES (?,?,?)",
            [("svc.py", "python", "source"), ("test_svc.py", "python", "test")],
        )
        sources, _ = rs.harvest_source_files(conn, root=str(tmp_path))
        paths = {p for p, _l, _t in sources}
        assert paths == {"svc.py"}

    def test_excludes_ci_workflow_paths(self, tmp_path):
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "scripts").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / ".github" / "scripts" / "gate.py").write_text("requests.get('x')\n", encoding="utf-8")
        (tmp_path / "src" / "svc.py").write_text("requests.get('x')\n", encoding="utf-8")
        conn = _files_db()
        conn.executemany(
            "INSERT INTO files (path, language, file_role) VALUES (?,?,?)",
            [(".github/scripts/gate.py", "python", "source"), ("src/svc.py", "python", "source")],
        )
        sources, _ = rs.harvest_source_files(conn, root=str(tmp_path))
        paths = {p for p, _l, _t in sources}
        assert paths == {"src/svc.py"}

    def test_skips_languages_with_no_pattern(self, tmp_path):
        (tmp_path / "f.cob").write_text("CALL 'X'.\n", encoding="utf-8")
        conn = _files_db()
        conn.execute("INSERT INTO files (path, language, file_role) VALUES (?,?,?)", ("f.cob", "cobol", "source"))
        sources, _ = rs.harvest_source_files(conn, root=str(tmp_path))
        assert sources == []


# ---------------------------------------------------------------------------
# Orchestrator + A4 persistence
# ---------------------------------------------------------------------------
class TestOrchestrator:
    def test_run_with_injected_sources(self):
        sources = [("a.py", "python", "requests.get('x')\n")]
        findings, meta = rs.run_resilience(None, sources=sources)
        assert len(findings) == 1
        assert meta["detectors_executed"] == 1
        assert meta["partial_success"] is False
        assert meta["sources"]["source_files_scanned"] == 1

    def test_partial_success_when_no_sources(self):
        findings, meta = rs.run_resilience(None, sources=[])
        assert findings == []
        assert meta["partial_success"] is True

    def test_only_unknown_task_is_disclosed(self):
        findings, meta = rs.run_resilience(
            None, only=["not-a-task"], sources=[("a.py", "python", "requests.get('x')\n")]
        )
        assert findings == []
        assert meta["only_unknown"] == ["not-a-task"]
        assert meta["active_tasks"] == []

    def test_exclude_silences_the_task(self):
        sources = [("a.py", "python", "requests.get('x')\n")]
        findings, meta = rs.run_resilience(None, exclude=["missing-timeout"], sources=sources)
        assert findings == []
        assert meta["active_tasks"] == []

    def test_build_finding_records_shape(self):
        findings, _ = rs.run_resilience(None, sources=[("a.py", "python", "requests.get('x')\n")])
        records = rs.build_finding_records(findings)
        assert len(records) == 1
        r = records[0]
        assert r.source_detector == "resilience.missing-timeout"
        assert r.subject_id is None
        assert r.subject_kind == "symbol"
        payload = json.loads(r.evidence_json)
        assert payload["task_id"] == "missing-timeout"
        assert payload["recommended_way"] == "explicit-timeout"
