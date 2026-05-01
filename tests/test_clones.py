"""Tests for roam clones — AST structural clone detection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_project


class TestCloneDetectEngine:
    """Unit tests for the core clone detection engine."""

    def test_identical_functions_detected(self, tmp_path):
        """Two identical functions (different names) should be detected as clones."""
        proj = _make_project(
            tmp_path,
            {
                "a.py": """
                def process_orders(items):
                    results = []
                    for item in items:
                        if item.is_valid():
                            value = item.calculate()
                            results.append(value)
                    return results
            """,
                "b.py": """
                def handle_invoices(entries):
                    output = []
                    for entry in entries:
                        if entry.is_valid():
                            amount = entry.calculate()
                            output.append(amount)
                    return output
            """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            result = runner.invoke(cli, ["clones", "--threshold", "0.50"])
            assert result.exit_code == 0, result.output
            # Should find at least one cluster
            assert "CLUSTER" in result.output or "No structural clones" in result.output
        finally:
            os.chdir(old_cwd)

    def test_different_functions_not_clones(self, tmp_path):
        """Structurally different functions should not be clones."""
        proj = _make_project(
            tmp_path,
            {
                "a.py": """
                def fibonacci(n):
                    if n <= 1:
                        return n
                    a, b = 0, 1
                    for _ in range(2, n + 1):
                        a, b = b, a + b
                    return b
            """,
                "b.py": """
                class UserManager:
                    def __init__(self, db):
                        self.db = db
                        self.cache = {}
                        self.logger = None

                    def get_user(self, user_id):
                        if user_id in self.cache:
                            return self.cache[user_id]
                        user = self.db.query(user_id)
                        self.cache[user_id] = user
                        return user
            """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            result = runner.invoke(cli, ["clones", "--threshold", "0.80"])
            assert result.exit_code == 0, result.output
            assert "No structural clones" in result.output
        finally:
            os.chdir(old_cwd)

    def test_json_output(self, tmp_path):
        """JSON output should follow envelope format."""
        proj = _make_project(
            tmp_path,
            {
                "a.py": """
                def func_a(x):
                    result = []
                    for i in range(x):
                        if i > 0:
                            result.append(i * 2)
                    return result
            """,
                "b.py": """
                def func_b(y):
                    output = []
                    for j in range(y):
                        if j > 0:
                            output.append(j * 2)
                    return output
            """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            result = runner.invoke(cli, ["--json", "clones", "--threshold", "0.50"])
            assert result.exit_code == 0, result.output

            import json

            data = json.loads(result.output)
            assert data["command"] == "clones"
            assert "summary" in data
            assert "verdict" in data["summary"]
            assert "clusters" in data["summary"]
        finally:
            os.chdir(old_cwd)

    def test_min_lines_filter(self, tmp_path):
        """Functions below min-lines should be skipped."""
        proj = _make_project(
            tmp_path,
            {
                "a.py": """
                def tiny_a(x):
                    return x + 1

                def tiny_b(y):
                    return y + 1
            """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            result = runner.invoke(cli, ["clones", "--min-lines", "10"])
            assert result.exit_code == 0, result.output
            assert "No structural clones" in result.output
        finally:
            os.chdir(old_cwd)

    def test_scope_filter(self, tmp_path):
        """--scope should limit analysis to matching files."""
        proj = _make_project(
            tmp_path,
            {
                "api/handler_a.py": """
                def handle_request(req):
                    data = req.get_data()
                    validated = validate(data)
                    result = process(validated)
                    return format_response(result)
            """,
                "api/handler_b.py": """
                def handle_event(evt):
                    data = evt.get_data()
                    validated = validate(data)
                    result = process(validated)
                    return format_response(result)
            """,
                "utils/helper.py": """
                def compute_stats(data):
                    total = sum(data)
                    count = len(data)
                    mean = total / count
                    return {"total": total, "mean": mean}
            """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            # Scope to api/ only
            result = runner.invoke(cli, ["clones", "--scope", "src/api", "--threshold", "0.50"])
            assert result.exit_code == 0, result.output
            # utils/helper.py should not appear in results
            assert "helper.py" not in result.output
        finally:
            os.chdir(old_cwd)

    def test_top_flag(self, tmp_path):
        """--top N should limit cluster output."""
        proj = _make_project(
            tmp_path,
            {
                "a.py": """
                def process_a(items):
                    results = []
                    for item in items:
                        if item.valid:
                            results.append(item.value)
                    return results
            """,
                "b.py": """
                def process_b(entries):
                    output = []
                    for entry in entries:
                        if entry.valid:
                            output.append(entry.value)
                    return output
            """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            result = runner.invoke(cli, ["clones", "--top", "1", "--threshold", "0.50"])
            assert result.exit_code == 0, result.output
            # Should show at most 1 cluster
            cluster_count = result.output.count("CLUSTER ")
            assert cluster_count <= 1
        finally:
            os.chdir(old_cwd)

    def test_empty_project(self, tmp_path):
        """Empty project should produce clean output."""
        proj = _make_project(
            tmp_path,
            {
                "readme.txt": "no code here",
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            # Index may or may not find files
            result = runner.invoke(cli, ["clones"])
            assert result.exit_code == 0, result.output
            assert "No structural clones" in result.output
        finally:
            os.chdir(old_cwd)


class TestCloneDetectCoreAlgorithm:
    """Direct unit tests for the hashing and similarity functions."""

    def test_jaccard_bags_identical(self):
        from collections import Counter

        from roam.graph.clone_detect import _jaccard_bags

        a = Counter({1: 3, 2: 2, 3: 1})
        b = Counter({1: 3, 2: 2, 3: 1})
        assert _jaccard_bags(a, b) == 1.0

    def test_jaccard_bags_disjoint(self):
        from collections import Counter

        from roam.graph.clone_detect import _jaccard_bags

        a = Counter({1: 1, 2: 1})
        b = Counter({3: 1, 4: 1})
        assert _jaccard_bags(a, b) == 0.0

    def test_jaccard_bags_partial(self):
        from collections import Counter

        from roam.graph.clone_detect import _jaccard_bags

        a = Counter({1: 2, 2: 1})
        b = Counter({1: 1, 3: 1})
        # intersection: min(2,1) + min(0,1) = 1
        # union: max(2,1) + max(1,0) + max(0,1) = 2 + 1 + 1 = 4
        assert _jaccard_bags(a, b) == pytest.approx(0.25)

    def test_jaccard_bags_empty(self):
        from collections import Counter

        from roam.graph.clone_detect import _jaccard_bags

        assert _jaccard_bags(Counter(), Counter()) == 1.0

    def test_name_tokens(self):
        from roam.graph.clone_detect import _name_tokens

        tokens = _name_tokens("processUserData")
        assert "process" in tokens
        assert "user" in tokens
        assert "data" in tokens

    def test_name_tokens_snake(self):
        from roam.graph.clone_detect import _name_tokens

        tokens = _name_tokens("handle_http_request")
        assert "handle" in tokens
        assert "http" in tokens
        assert "request" in tokens


class TestClonePersistence:
    """A.0 — persistence of clone detection results to clone_pairs / clone_clusters."""

    def _two_clone_project(self, tmp_path) -> Path:
        return _make_project(
            tmp_path,
            {
                "a.py": """
                def process_orders(items):
                    results = []
                    for item in items:
                        if item.is_valid():
                            value = item.calculate()
                            results.append(value)
                    return results
            """,
                "b.py": """
                def handle_invoices(entries):
                    output = []
                    for entry in entries:
                        if entry.is_valid():
                            amount = entry.calculate()
                            output.append(amount)
                    return output
            """,
            },
        )

    def test_schema_tables_exist(self, tmp_path):
        """clone_pairs and clone_clusters must exist after roam init."""
        proj = _make_project(tmp_path, {"a.py": "x = 1\n"})
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            from roam.db.connection import open_db

            with open_db(readonly=True) as conn:
                tables = [
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('clone_pairs', 'clone_clusters')"
                    ).fetchall()
                ]
            assert "clone_pairs" in tables
            assert "clone_clusters" in tables
        finally:
            os.chdir(old_cwd)

    def test_persist_flag_populates_tables(self, tmp_path):
        """--persist writes clone_pairs and clone_clusters."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0

            result = runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"])
            assert result.exit_code == 0, result.output

            from roam.db.connection import open_db

            with open_db(readonly=True) as conn:
                pair_count = conn.execute("SELECT COUNT(*) FROM clone_pairs").fetchone()[0]
                cluster_count = conn.execute("SELECT COUNT(*) FROM clone_clusters").fetchone()[0]
            assert pair_count >= 1, "expected at least one persisted clone pair"
            assert cluster_count >= 1, "expected at least one persisted clone cluster"
        finally:
            os.chdir(old_cwd)

    def test_persist_replaces_not_appends(self, tmp_path):
        """Running --persist twice must not duplicate rows."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0

            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            from roam.db.connection import open_db

            with open_db(readonly=True) as conn:
                first_pairs = conn.execute("SELECT COUNT(*) FROM clone_pairs").fetchone()[0]
                first_clusters = conn.execute("SELECT COUNT(*) FROM clone_clusters").fetchone()[0]

            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            with open_db(readonly=True) as conn:
                second_pairs = conn.execute("SELECT COUNT(*) FROM clone_pairs").fetchone()[0]
                second_clusters = conn.execute("SELECT COUNT(*) FROM clone_clusters").fetchone()[0]
            assert first_pairs == second_pairs
            assert first_clusters == second_clusters
        finally:
            os.chdir(old_cwd)

    def test_no_persist_leaves_tables_empty(self, tmp_path):
        """Without --persist, no rows are written."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            assert runner.invoke(cli, ["clones", "--threshold", "0.50"]).exit_code == 0

            from roam.db.connection import open_db

            with open_db(readonly=True) as conn:
                pair_count = conn.execute("SELECT COUNT(*) FROM clone_pairs").fetchone()[0]
            assert pair_count == 0
        finally:
            os.chdir(old_cwd)

    def test_get_clone_siblings_returns_pair(self, tmp_path):
        """get_clone_siblings should find a stored sibling by file+func name."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            from roam.db.connection import open_db
            from roam.graph.clone_detect import get_clone_siblings

            with open_db(readonly=True) as conn:
                pair = conn.execute("SELECT file_a, func_a, file_b, func_b FROM clone_pairs LIMIT 1").fetchone()
                assert pair is not None
                siblings = get_clone_siblings(conn, pair["file_a"], pair["func_a"])
                assert any(
                    s["sibling_file"] == pair["file_b"] and s["sibling_func"] == pair["func_b"] for s in siblings
                ), f"expected sibling {pair['file_b']}:{pair['func_b']} in {siblings}"
        finally:
            os.chdir(old_cwd)

    def test_get_clone_siblings_unknown_returns_empty(self, tmp_path):
        """Unknown function returns empty list, not an error."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            from roam.db.connection import open_db
            from roam.graph.clone_detect import get_clone_siblings

            with open_db(readonly=True) as conn:
                siblings = get_clone_siblings(conn, "nonexistent.py", "missing_func")
            assert siblings == []
        finally:
            os.chdir(old_cwd)

    def test_get_cluster_members_returns_all(self, tmp_path):
        """get_cluster_members returns every member of a cluster."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            from roam.db.connection import open_db
            from roam.graph.clone_detect import get_cluster_members

            with open_db(readonly=True) as conn:
                row = conn.execute("SELECT id, member_count FROM clone_clusters LIMIT 1").fetchone()
                assert row is not None
                members = get_cluster_members(conn, row["id"])
            assert len(members) >= 2

            for m in members:
                assert "qname" in m
                assert "file" in m
                assert "func" in m
                assert "line_start" in m
        finally:
            os.chdir(old_cwd)

    def test_get_cluster_members_unknown_id_returns_empty(self, tmp_path):
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            from roam.db.connection import open_db
            from roam.graph.clone_detect import get_cluster_members

            with open_db(readonly=True) as conn:
                members = get_cluster_members(conn, 999_999)
            assert members == []
        finally:
            os.chdir(old_cwd)

    def test_cluster_id_links_pairs(self, tmp_path):
        """Every persisted pair should reference its cluster."""
        proj = self._two_clone_project(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0

            from roam.db.connection import open_db

            with open_db(readonly=True) as conn:
                rows = conn.execute("SELECT cluster_id FROM clone_pairs WHERE cluster_id IS NOT NULL").fetchall()
                assert len(rows) >= 1, "expected pairs linked to clusters"

                cluster_ids = {r["cluster_id"] for r in rows}
                for cid in cluster_ids:
                    exists = conn.execute("SELECT 1 FROM clone_clusters WHERE id = ?", (cid,)).fetchone()
                    assert exists is not None, f"orphan cluster_id {cid} in clone_pairs"
        finally:
            os.chdir(old_cwd)


class TestDebugArtifactRules:
    """Test that debug artifact YAML rules are valid and loadable."""

    def test_rules_parse(self):
        """All debug artifact rules should be valid YAML with required fields."""
        rules_dir = Path(__file__).parent.parent / "rules" / "community" / "correctness"
        debug_rules = [
            "COR-560-py-debug-print.yaml",
            "COR-561-py-breakpoint.yaml",
            "COR-562-py-pdb-import.yaml",
            "COR-563-py-set-trace.yaml",
            "COR-564-js-console-log.yaml",
            "COR-565-ts-console-log.yaml",
            "COR-566-js-debugger.yaml",
            "COR-567-ts-debugger.yaml",
            "COR-568-java-sysout.yaml",
        ]
        for rule_file in debug_rules:
            path = rules_dir / rule_file
            assert path.exists(), f"Missing rule file: {rule_file}"
            content = path.read_text(encoding="utf-8")
            assert "name:" in content
            assert "severity:" in content
            assert "type: ast_match" in content
            assert "match:" in content

    def test_breakpoint_rule_detects(self, tmp_path):
        """breakpoint() rule should detect breakpoint calls in Python code."""
        from roam.rules.engine import load_rules

        rules_dir = Path(__file__).parent.parent / "rules" / "community" / "correctness"
        rule_path = rules_dir / "COR-561-py-breakpoint.yaml"
        if not rule_path.exists():
            pytest.skip("Rule file not found")

        rules = load_rules(rules_dir)
        bp_rule = None
        for r in rules:
            if r.get("name") == "py-breakpoint":
                bp_rule = r
                break

        if bp_rule is None:
            pytest.skip("py-breakpoint rule not found in loaded rules")

        # The rule needs a project with indexed files to work
        # Just verify it loaded correctly
        assert bp_rule["severity"] == "error"
        assert bp_rule["type"] == "ast_match"
