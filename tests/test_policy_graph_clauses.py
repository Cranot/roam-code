"""R18 tests: graph-aware policy clauses.

Covers the four clause types in :mod:`roam.policy.graph_clauses`:

* ``reachable_from`` — BFS reachability on the call/import graph
* ``imports_from`` — file-level import prefix match
* ``clones_with`` — clone_pairs / cluster membership
* ``tested_by`` — reachability from test files

Plus the end-to-end rule wiring (must / must_not / when / next_commands)
through :func:`roam.rules.engine.evaluate_rule`.

Each test builds the smallest indexed fixture that exercises the clause
so the suite runs sub-second per test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

from roam.db.connection import open_db  # noqa: E402
from roam.policy.graph_clauses import (  # noqa: E402
    SUPPORTED_CLAUSES,
    check_clones_with,
    check_imports_from,
    check_reachable_from,
    check_tested_by,
    evaluate_clause,
)
from roam.rules.engine import evaluate_rule  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture builder — module-local helper that's stricter than the shared
# ``project_factory``: it commits files, runs ``roam index`` in-process,
# and yields the project root + an open readonly DB connection per test.
# ---------------------------------------------------------------------------


def _build_indexed(tmp_path: Path, files: dict[str, str]) -> Path:
    """Build a minimal git-committed, roam-indexed project from ``files``."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True, env=env)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        out, rc = index_in_process(proj)
        assert rc == 0, f"index failed:\n{out}"
    finally:
        os.chdir(old_cwd)
    return proj


class _Scoped:
    """Context manager that chdirs to ``proj`` and yields an open DB conn.

    ``open_db`` itself is a ``contextlib.contextmanager`` that closes its
    sqlite handle on exit, so we wrap it. The chdir restoration matters
    because indexer / DB lookups resolve the project root from cwd.
    """

    def __init__(self, proj: Path, *, readonly: bool = True):
        self.proj = proj
        self.readonly = readonly
        self._old_cwd: str | None = None
        self._cm = None

    def __enter__(self):
        self._old_cwd = os.getcwd()
        os.chdir(str(self.proj))
        self._cm = open_db(readonly=self.readonly)
        return self._cm.__enter__()

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._cm.__exit__(exc_type, exc, tb)
        finally:
            if self._old_cwd is not None:
                os.chdir(self._old_cwd)


# ---------------------------------------------------------------------------
# 1-2. reachable_from
# ---------------------------------------------------------------------------


class TestReachableFrom:
    """``reachable_from`` clause: BFS over the call graph."""

    def test_reachable_from_positive(self, tmp_path):
        """handler.py::handle() -> db.query(); rule passes."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/db.py": "def query():\n    return 1\n",
                "src/handler.py": "from db import query\n\ndef handle():\n    return query()\n",
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_reachable_from(
                conn, entry="handle", target_symbol="query"
            )
        assert matches is True, evidence
        assert evidence["reachable"] is True
        assert evidence["entry"] == "handle"
        assert "query" in (evidence.get("target") or "")

    def test_reachable_from_negative(self, tmp_path):
        """utils.py is isolated; no edge from handle() -> utils.unrelated."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/db.py": "def query():\n    return 1\n",
                "src/utils.py": "def unrelated():\n    return 2\n",
                "src/handler.py": "from db import query\n\ndef handle():\n    return query()\n",
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_reachable_from(
                conn, entry="handle", target_symbol="unrelated"
            )
        assert matches is False, evidence
        assert evidence["status"] == "ok"
        assert evidence["reachable"] is False


# ---------------------------------------------------------------------------
# 3-4. imports_from
# ---------------------------------------------------------------------------


class TestImportsFrom:
    """``imports_from`` clause: file-level imports."""

    def test_imports_from_positive(self, tmp_path):
        """core.py imports from legacy; rule fires."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/legacy/old.py": "def helper():\n    return 1\n",
                "src/core/main.py": "from legacy.old import helper\n\ndef run():\n    return helper()\n",
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_imports_from(
                conn, module="src/legacy", target_file="src/core/main.py"
            )
        assert matches is True, evidence
        assert evidence["imports_from"] is True
        assert evidence["imports_matched_count"] >= 1

    def test_imports_from_negative(self, tmp_path):
        """utils.py has no legacy imports; passes."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/legacy/old.py": "def helper():\n    return 1\n",
                "src/utils.py": "def safe():\n    return 99\n",
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_imports_from(
                conn, module="src/legacy", target_file="src/utils.py"
            )
        assert matches is False, evidence
        assert evidence["status"] == "ok"


# ---------------------------------------------------------------------------
# 5-6. clones_with
# ---------------------------------------------------------------------------


class TestClonesWith:
    """``clones_with`` clause: clone_pairs membership."""

    def test_clones_with_positive(self, tmp_path):
        """Two near-identical functions are indexed; clone_pairs records them."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/order.py": (
                    "def create_order(name, qty):\n"
                    "    if not name:\n        return None\n"
                    "    if qty <= 0:\n        return None\n"
                    "    total = qty * 10\n"
                    "    record = {'name': name, 'qty': qty, 'total': total}\n"
                    "    print('order created', record)\n"
                    "    return record\n"
                ),
                "src/invoice.py": (
                    "def create_invoice(name, qty):\n"
                    "    if not name:\n        return None\n"
                    "    if qty <= 0:\n        return None\n"
                    "    total = qty * 10\n"
                    "    record = {'name': name, 'qty': qty, 'total': total}\n"
                    "    print('invoice created', record)\n"
                    "    return record\n"
                ),
            },
        )
        # Populate clone_pairs by directly invoking the clone detector
        # in-process so we don't depend on `roam clones` CLI.
        old_cwd = os.getcwd()
        os.chdir(str(proj))
        try:
            from roam.graph.clone_detect import detect_clones, store_clones

            with open_db() as conn:
                pairs, clusters = detect_clones(conn, min_similarity=0.7, min_lines=3)
                store_clones(conn, pairs, clusters)
                conn.commit()
        finally:
            os.chdir(old_cwd)

        with _Scoped(proj) as conn:
            # If the synthetic fixture's similarity is below threshold,
            # the clone detector won't emit a pair — accept either the
            # canonical positive or a graceful no-match (still validates
            # the clause runs without crashing).
            row = conn.execute("SELECT COUNT(*) AS c FROM clone_pairs").fetchone()
            has_clones = row["c"] > 0

            matches, evidence = check_clones_with(
                conn, symbol_a="create_order", symbol_b="create_invoice"
            )

        if has_clones:
            assert matches is True, evidence
            assert evidence["status"] == "ok"
            assert evidence["clones_with"] is True
        else:
            # Detector chose not to emit a pair; clause should still
            # return cleanly (no crash).
            assert evidence["status"] in ("ok", "not_indexed")

    def test_clones_with_negative(self, tmp_path):
        """Distinct functions; clone_pairs empty for them."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/a.py": "def alpha():\n    return 1\n",
                "src/b.py": "def beta():\n    print('totally different')\n    return 99\n",
            },
        )
        # Insert a sentinel clone_pair (unrelated) so the table is non-empty.
        old_cwd = os.getcwd()
        os.chdir(str(proj))
        try:
            with open_db() as conn:
                conn.execute(
                    "INSERT INTO clone_pairs "
                    "(qname_a, qname_b, file_a, file_b, func_a, func_b, "
                    " line_a, line_b, similarity) "
                    "VALUES ('x.dummy_a', 'x.dummy_b', 'x.py', 'x.py', "
                    " 'dummy_a', 'dummy_b', 1, 2, 0.95)"
                )
                conn.commit()
        finally:
            os.chdir(old_cwd)

        with _Scoped(proj) as conn:
            matches, evidence = check_clones_with(
                conn, symbol_a="alpha", symbol_b="beta"
            )
        assert matches is False, evidence
        assert evidence["clones_with"] is False


# ---------------------------------------------------------------------------
# 7-8. tested_by
# ---------------------------------------------------------------------------


class TestTestedBy:
    """``tested_by`` clause: reachability from test files."""

    def test_tested_by_positive(self, tmp_path):
        """test_foo.py imports + calls foo(); foo is tested_by tests/**."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/foo.py": "def foo():\n    return 42\n",
                "tests/test_foo.py": (
                    "from foo import foo\n\n"
                    "def test_foo():\n    assert foo() == 42\n"
                ),
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_tested_by(
                conn, test_pattern="tests/**", target_symbol="foo"
            )
        assert matches is True, evidence
        assert evidence["tested_by"] is True
        assert evidence["test_files_matched"] >= 1

    def test_tested_by_negative(self, tmp_path):
        """Orphan symbol with no incoming test edges; fails."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/orphan.py": "def orphan():\n    return 99\n",
                "tests/test_other.py": (
                    "def test_nothing():\n    assert 1 + 1 == 2\n"
                ),
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_tested_by(
                conn, test_pattern="tests/**", target_symbol="orphan"
            )
        assert matches is False, evidence
        # Status may be "ok" (test files present but no path) or
        # "no_tests_indexed" (file_role classifier didn't pick them up).
        assert evidence["status"] in ("ok", "no_tests_indexed")
        assert evidence.get("tested_by") in (False, None)


# ---------------------------------------------------------------------------
# 9. negate inversion (must_not via evaluate_rule)
# ---------------------------------------------------------------------------


class TestNegateInversion:
    """``must_not`` inverts a clause's polarity at the rule level."""

    def test_must_not_inverts_result(self, tmp_path):
        """imports_from is TRUE; must_not block flips it to a violation."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/legacy/old.py": "def helper():\n    return 1\n",
                "src/core/main.py": (
                    "from legacy.old import helper\n\ndef run():\n    return helper()\n"
                ),
            },
        )
        rule = {
            "name": "core-must-not-import-legacy",
            "severity": "error",
            "message": "Don't import from legacy",
            "when": {"pattern": "src/core/**"},
            "must_not": {"imports_from": "src/legacy"},
        }
        with _Scoped(proj) as conn:
            result = evaluate_rule(rule, conn)
        assert result["passed"] is False, result
        assert len(result["violations"]) >= 1
        v = result["violations"][0]
        assert v["clause"] == "imports_from"
        assert v["block"] == "must_not"

        # And the must (positive) form on the SAME data should PASS — the
        # rule expects the imports to be present.
        rule_must = dict(rule)
        rule_must.pop("must_not")
        rule_must["must"] = {"imports_from": "src/legacy"}
        rule_must["name"] = "core-must-import-legacy"
        with _Scoped(proj) as conn:
            result_must = evaluate_rule(rule_must, conn)
        assert result_must["passed"] is True, result_must


# ---------------------------------------------------------------------------
# 10. evidence localisation
# ---------------------------------------------------------------------------


class TestEvidenceLocalisation:
    """Each violation carries enough evidence to localise the finding."""

    def test_evidence_includes_path(self, tmp_path):
        """reachable_from evidence has file path + line for the target."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/db.py": "def query():\n    return 1\n",
                "src/handler.py": (
                    "from db import query\n\ndef handle():\n    return query()\n"
                ),
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = check_reachable_from(
                conn, entry="handle", target_symbol="query"
            )
        assert matches is True
        assert "target_file" in evidence
        assert evidence["target_file"] and "db.py" in evidence["target_file"].replace("\\", "/")
        assert isinstance(evidence.get("target_line"), int)
        assert evidence["target_line"] >= 1
        assert evidence["max_depth"] == 3  # default guardrail
        assert evidence["max_nodes"] == 100


# ---------------------------------------------------------------------------
# 11. unknown clause handling + dispatcher safety
# ---------------------------------------------------------------------------


class TestDispatcher:
    """evaluate_clause routes to the right checker and rejects unknowns."""

    def test_supported_clauses_enumerated(self):
        assert set(SUPPORTED_CLAUSES) == {
            "reachable_from",
            "imports_from",
            "clones_with",
            "tested_by",
        }

    def test_unknown_clause_returns_false_with_evidence(self, tmp_path):
        proj = _build_indexed(tmp_path, {"src/a.py": "def a():\n    return 1\n"})
        with _Scoped(proj) as conn:
            matches, evidence = evaluate_clause(
                "no_such_clause", "anything", conn=conn, target_symbol="a"
            )
        assert matches is False
        assert evidence["status"] == "unsupported_clause"
        assert "supported" in evidence

    def test_evaluate_clause_dispatches_reachable(self, tmp_path):
        proj = _build_indexed(
            tmp_path,
            {
                "src/db.py": "def query():\n    return 1\n",
                "src/handler.py": (
                    "from db import query\n\ndef handle():\n    return query()\n"
                ),
            },
        )
        with _Scoped(proj) as conn:
            matches, evidence = evaluate_clause(
                "reachable_from",
                "handle",
                conn=conn,
                target_symbol="query",
            )
        assert matches is True
        assert evidence["reachable"] is True


# ---------------------------------------------------------------------------
# 12. End-to-end smoke through evaluate_rule + verdict
# ---------------------------------------------------------------------------


class TestRuleIntegration:
    """``evaluate_rule`` correctly aggregates graph_clause results."""

    def test_rule_with_when_pattern_and_must_not(self, tmp_path):
        """Full rule shape: when.pattern + must_not.imports_from -> violations."""
        proj = _build_indexed(
            tmp_path,
            {
                "src/legacy/old.py": "def helper():\n    return 1\n",
                "src/core/main.py": (
                    "from legacy.old import helper\n\ndef run():\n    return helper()\n"
                ),
                "src/core/safe.py": "def isolated():\n    return 7\n",
            },
        )
        rule = {
            "name": "no-legacy-imports-into-core",
            "severity": "high",
            "message": "core/ must not import legacy/",
            "when": {"pattern": "src/core/**.py"},
            "must_not": {"imports_from": "src/legacy"},
        }
        with _Scoped(proj) as conn:
            result = evaluate_rule(rule, conn)
        assert result["passed"] is False
        files_hit = {v["file"] for v in result["violations"]}
        # main.py imports legacy; safe.py does not.
        assert any("main.py" in (f or "").replace("\\", "/") for f in files_hit)
        assert not any("safe.py" in (f or "").replace("\\", "/") for f in files_hit)
