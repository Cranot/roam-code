"""Tests for ``roam observability-opt`` — the diagnosability super-optimizer (P2).

Engine-level coverage (the CLI surface lands in a later phase): the
print-debug-leftover detector (pure-function unit level + comment/language
handling), the family-local registry + closed-enum validation, the shared
CATALOG family tagging, the source harvester (file-role exclusion + on-disk
read), the orchestrator's only/exclude + partial_success discipline, and the
A4 persistence wiring.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from roam import observability_opt as oo
from roam.catalog.tasks import CATALOG


# ---------------------------------------------------------------------------
# Detector unit tests (pure function — deterministic, no live harvest)
# ---------------------------------------------------------------------------
class TestPrintDebugLeftover:
    def test_flags_python_and_js_debug_prints(self):
        sources = [
            ("app/svc.py", "python", "def f(x):\n    print('dbg', x)\n    return x\n"),
            ("web/ui.ts", "typescript", "function g(){\n  console.log('here');\n  return 2;\n}\n"),
        ]
        out = oo.detect_print_debug_leftover(sources)
        subjects = {f["subject"] for f in out}
        assert subjects == {"app/svc.py:2", "web/ui.ts:2"}
        for f in out:
            assert f["task_id"] == "print-debug-leftover"
            assert f["detected_way"] == "raw-debug-print"
            assert f["suggested_way"] == "structured-logger"
            assert f["confidence_basis"] == "heuristic"

    def test_skips_commented_out_lines(self):
        sources = [("a.py", "python", "x = 1\n# print('old debug')\n  // not python anyway\n")]
        assert oo.detect_print_debug_leftover(sources) == []

    def test_skips_unknown_language_and_empty_text(self):
        sources = [("x.cob", "cobol", "DISPLAY 'hi'.\n"), ("y.py", "python", "")]
        assert oo.detect_print_debug_leftover(sources) == []

    def test_php_var_dump_is_high_signal(self):
        sources = [("a.php", "php", "<?php\nvar_dump($data);\n")]
        out = oo.detect_print_debug_leftover(sources)
        assert len(out) == 1
        assert out[0]["confidence"] == "high"

    def test_ruby_assignment_not_flagged_but_puts_is(self):
        # Bare `p` was dropped: `p = 1` (assignment) must NOT flag; `puts`/`pp` do.
        sources = [("a.rb", "ruby", "p = 1\nputs 'debug'\npp obj\n")]
        out = oo.detect_print_debug_leftover(sources)
        flagged = {f["subject"] for f in out}
        assert flagged == {"a.rb:2", "a.rb:3"}

    def test_c_cpp_not_flagged(self):
        # C/C++ omitted: printf/cout are the normal output, not a logger anti-pattern.
        sources = [
            ("a.c", "c", 'int main(){ printf("x"); }\n'),
            ("b.cpp", "cpp", "int main(){ std::cout << 1; }\n"),
        ]
        assert oo.detect_print_debug_leftover(sources) == []

    def test_does_not_flag_clean_source(self):
        sources = [
            ("a.py", "python", "import logging\nlog = logging.getLogger(__name__)\nlog.debug('x')\n"),
            ("b.go", "go", "package main\nfunc main() { return }\n"),
        ]
        assert oo.detect_print_debug_leftover(sources) == []

    def test_reports_line_number_and_evidence(self):
        sources = [("a.py", "python", "a = 1\nb = 2\nprint(a + b)\n")]
        out = oo.detect_print_debug_leftover(sources)
        assert len(out) == 1
        assert out[0]["subject"] == "a.py:3"
        assert out[0]["evidence"]["lineno"] == 3
        assert out[0]["evidence"]["language"] == "python"


# ---------------------------------------------------------------------------
# Registry + closed-enum discipline
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_detector_is_registered_under_family_task(self):
        names = {d["name"] for d in oo.list_observability_opt_detectors()}
        assert "detect_print_debug_leftover" in names
        entry = next(d for d in oo.list_observability_opt_detectors() if d["name"] == "detect_print_debug_leftover")
        assert entry["task_id"] == "print-debug-leftover"
        assert entry["family"] == "observability-opt"
        assert entry["confidence_basis"] == "heuristic"
        assert entry["query_cost"] == "medium"

    def test_decorator_rejects_bad_confidence_basis(self):
        with pytest.raises(ValueError):
            oo.observability_opt_detector(task_id="print-debug-leftover", confidence_basis="bogus")(lambda s: [])

    def test_decorator_rejects_bad_query_cost(self):
        with pytest.raises(ValueError):
            oo.observability_opt_detector(task_id="print-debug-leftover", query_cost="instant")(lambda s: [])

    def test_decorator_rejects_untagged_task(self):
        # 'sorting' is a real CATALOG task but has no observability-opt family tag.
        with pytest.raises(ValueError):
            oo.observability_opt_detector(task_id="sorting")(lambda s: [])


# ---------------------------------------------------------------------------
# Shared CATALOG tagging
# ---------------------------------------------------------------------------
class TestCatalog:
    def test_task_is_family_tagged_with_ranked_ways(self):
        task = CATALOG["print-debug-leftover"]
        assert task["family"] == "observability-opt"
        ranks = {w["id"]: w["rank"] for w in task["ways"]}
        assert ranks["structured-logger"] == 1  # best
        assert ranks["raw-debug-print"] == 10  # weak
        # best_way resolves to rank 1
        from roam.catalog.tasks import best_way

        assert best_way("print-debug-leftover")["id"] == "structured-logger"

    def test_task_ids_accessor_matches_catalog(self):
        assert oo.observability_opt_task_ids() == ["print-debug-leftover"]


# ---------------------------------------------------------------------------
# Source harvester (DB file-role exclusion + on-disk read)
# ---------------------------------------------------------------------------
def _files_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (path TEXT, language TEXT, file_role TEXT)")
    return conn


class TestHarvester:
    def test_excludes_test_and_non_source_roles(self, tmp_path):
        (tmp_path / "svc.py").write_text("print('x')\n", encoding="utf-8")
        (tmp_path / "test_svc.py").write_text("print('t')\n", encoding="utf-8")
        (tmp_path / "conf.py").write_text("print('c')\n", encoding="utf-8")
        conn = _files_db()
        conn.executemany(
            "INSERT INTO files (path, language, file_role) VALUES (?,?,?)",
            [("svc.py", "python", "source"), ("test_svc.py", "python", "test"), ("conf.py", "python", "config")],
        )
        sources, unreadable = oo.harvest_source_files(conn, root=str(tmp_path))
        paths = {p for p, _l, _t in sources}
        assert paths == {"svc.py"}
        assert unreadable == []

    def test_records_unreadable_missing_files(self, tmp_path):
        conn = _files_db()
        conn.execute("INSERT INTO files (path, language, file_role) VALUES (?,?,?)", ("gone.py", "python", "source"))
        sources, unreadable = oo.harvest_source_files(conn, root=str(tmp_path))
        assert sources == []
        assert unreadable == ["gone.py"]

    def test_skips_languages_with_no_pattern(self, tmp_path):
        (tmp_path / "data.cob").write_text("DISPLAY 'x'.\n", encoding="utf-8")
        conn = _files_db()
        conn.execute("INSERT INTO files (path, language, file_role) VALUES (?,?,?)", ("data.cob", "cobol", "source"))
        sources, _ = oo.harvest_source_files(conn, root=str(tmp_path))
        assert sources == []

    def test_excludes_ci_workflow_paths(self, tmp_path):
        # ``.github/scripts/*.py`` are CI workflow scripts: ``print("::warning::...")``
        # is the GHA workflow-command mechanism, not a debug leftover. Same for
        # ``.gitlab/`` / ``.circleci/`` / ``.buildkite/``.
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "scripts").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / ".github" / "scripts" / "gate.py").write_text("print('::warning::x')\n", encoding="utf-8")
        (tmp_path / "src" / "svc.py").write_text("print('debug')\n", encoding="utf-8")
        conn = _files_db()
        conn.executemany(
            "INSERT INTO files (path, language, file_role) VALUES (?,?,?)",
            [(".github/scripts/gate.py", "python", "source"), ("src/svc.py", "python", "source")],
        )
        sources, _ = oo.harvest_source_files(conn, root=str(tmp_path))
        paths = {p for p, _l, _t in sources}
        assert paths == {"src/svc.py"}, f"CI scripts should be excluded; got {paths}"


# ---------------------------------------------------------------------------
# Orchestrator + A4 persistence
# ---------------------------------------------------------------------------
class TestOrchestrator:
    def test_run_with_injected_sources(self):
        sources = [("app/svc.py", "python", "print('x')\n")]
        findings, meta = oo.run_observability_opt(None, sources=sources)
        assert len(findings) == 1
        assert meta["detectors_executed"] == 1
        assert meta["partial_success"] is False
        assert meta["sources"]["source_files_scanned"] == 1

    def test_partial_success_when_no_sources(self):
        findings, meta = oo.run_observability_opt(None, sources=[])
        assert findings == []
        assert meta["partial_success"] is True  # disclose absent signal, never fake SAFE

    def test_only_unknown_task_is_disclosed(self):
        findings, meta = oo.run_observability_opt(None, only=["not-a-task"], sources=[("a.py", "python", "print(1)\n")])
        assert findings == []
        assert meta["only_unknown"] == ["not-a-task"]
        assert meta["active_tasks"] == []

    def test_exclude_silences_the_task(self):
        sources = [("a.py", "python", "print(1)\n")]
        findings, meta = oo.run_observability_opt(None, exclude=["print-debug-leftover"], sources=sources)
        assert findings == []
        assert meta["active_tasks"] == []

    def test_build_finding_records_shape(self):
        findings, _ = oo.run_observability_opt(None, sources=[("a.py", "python", "print(1)\n")])
        records = oo.build_finding_records(findings)
        assert len(records) == 1
        r = records[0]
        assert r.source_detector == "observability-opt.print-debug-leftover"
        assert r.subject_id is None
        assert r.subject_kind == "symbol"
        payload = json.loads(r.evidence_json)
        assert payload["task_id"] == "print-debug-leftover"
        assert payload["recommended_way"] == "structured-logger"
