"""R22 — pilot tests for the ``{value, confidence, reason}`` finding-triple
shape.

Helper-level tests live in ``TestHelpers``; per-command shape tests live in
the ``TestPilotCommand*`` classes. The pilot covers five commands:

* ``smells``           — DB-only; severity → confidence
* ``clones``           — full pipeline (slow); similarity → confidence
* ``vulns``            — DB-only; source + reachability → confidence
* ``orphan-imports``   — file-walk; kind → confidence
* ``complexity``       — DB-only; score severity → confidence

When the underlying command emits an empty findings list (no data /
nothing to flag), the distribution is still present (all-zero) and the
verdict is unchanged — wrapping is a no-op on empty lists by design.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.output.confidence import (
    CONFIDENCE_LEVELS,
    confidence_distribution,
    confidence_level_rank,
    triple,
    verdict_with_high_count,
    wrap_findings,
)

# ---------------------------------------------------------------------------
# 1. Helper-level tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_triple_helper_returns_dict_with_three_keys(self):
        t = triple({"symbol": "foo"}, "high", "528 callers")
        assert isinstance(t, dict)
        assert set(t.keys()) == {"value", "confidence", "reason"}
        assert t["value"] == {"symbol": "foo"}
        assert t["confidence"] == "high"
        assert t["reason"] == "528 callers"

    def test_triple_coerces_invalid_confidence_to_medium(self):
        # Bad classifier output should not break the schema.
        t = triple({"x": 1}, "URGENT", "bad label")
        assert t["confidence"] == "medium"

    def test_triple_empty_reason_allowed(self):
        t = triple({"x": 1}, "low", "")
        assert t["reason"] == ""

    def test_wrap_findings_default_confidence_medium(self):
        findings = [{"a": 1}, {"a": 2}, {"a": 3}]
        triples = wrap_findings(findings)
        assert len(triples) == 3
        for t in triples:
            assert t["confidence"] == "medium"
            assert t["reason"] == ""

    def test_wrap_findings_with_classifier(self):
        findings = [
            {"score": 90},
            {"score": 50},
            {"score": 10},
        ]

        def cls(f):
            s = f["score"]
            if s >= 80:
                return "high", f"score {s} ≥ 80"
            if s >= 30:
                return "medium", f"score {s} in [30, 80)"
            return "low", f"score {s} < 30"

        triples = wrap_findings(findings, classifier=cls)
        assert [t["confidence"] for t in triples] == ["high", "medium", "low"]
        assert "score 90" in triples[0]["reason"]
        assert "score 10" in triples[2]["reason"]

    def test_wrap_findings_classifier_exception_falls_back(self):
        findings = [{"a": 1}, {"a": 2}]

        def bad(f):
            raise RuntimeError("boom")

        triples = wrap_findings(
            findings,
            classifier=bad,
            default_confidence="low",
            default_reason="classifier failed",
        )
        assert all(t["confidence"] == "low" for t in triples)
        assert all(t["reason"] == "classifier failed" for t in triples)

    def test_wrap_findings_empty_list(self):
        assert wrap_findings([]) == []

    def test_confidence_distribution_counts(self):
        triples = [
            triple({}, "high", ""),
            triple({}, "high", ""),
            triple({}, "medium", ""),
            triple({}, "low", ""),
        ]
        d = confidence_distribution(triples)
        assert d == {"high": 2, "medium": 1, "low": 1}

    def test_confidence_distribution_always_has_three_keys(self):
        d = confidence_distribution([])
        assert d == {"high": 0, "medium": 0, "low": 0}

    def test_verdict_with_high_count_appends_when_positive(self):
        out = verdict_with_high_count("23 findings", {"high": 12, "medium": 5, "low": 6})
        assert out == "23 findings (12 high-confidence)"

    def test_verdict_with_high_count_unchanged_when_zero(self):
        out = verdict_with_high_count("no findings", {"high": 0, "medium": 0, "low": 0})
        assert out == "no findings"

    def test_confidence_levels_is_closed_enumeration(self):
        # Lock down the public contract.
        assert CONFIDENCE_LEVELS == ("high", "medium", "low")


class TestConfidenceLevelRankW634:
    """W634 — confidence_level_rank() fail-loud vs explicit-fallback contract.

    Pre-W634 the helper silently returned ``-1`` for unknown labels and
    ``None``. That was Pattern 1 variant D — callers couldn't tell
    "this is a heuristic" from "I sent garbage and got bucketed". W634
    flips the default to fail-loud; callers that genuinely want the
    silent-bucket polarity opt in via ``fallback=-1``.
    """

    # --- Known labels: unchanged on both branches ---

    def test_known_labels_rank_without_fallback(self):
        assert confidence_level_rank("high") == 3
        assert confidence_level_rank("medium") == 2
        assert confidence_level_rank("low") == 1
        assert confidence_level_rank("unknown") == 0

    def test_known_labels_rank_with_fallback_ignores_fallback(self):
        # Fallback is only consulted on UNKNOWN input — known labels
        # always return their canonical rank.
        assert confidence_level_rank("high", fallback=-99) == 3
        assert confidence_level_rank("low", fallback=-99) == 1

    def test_case_insensitive_normalization_still_works(self):
        assert confidence_level_rank("HIGH") == 3
        assert confidence_level_rank("  Medium ") == 2

    # --- Unknown labels: fail-loud by default ---

    def test_unknown_label_raises_without_fallback(self):
        with pytest.raises(ValueError, match="unknown confidence level"):
            confidence_level_rank("bogus")

    def test_typo_raises_without_fallback(self):
        # The exact programmer-error case W634 targets — a typo in a
        # tier name must NOT silently bucket.
        with pytest.raises(ValueError, match="hihg"):
            confidence_level_rank("hihg")

    def test_none_raises_without_fallback(self):
        with pytest.raises(ValueError):
            confidence_level_rank(None)

    def test_empty_string_raises_without_fallback(self):
        with pytest.raises(ValueError):
            confidence_level_rank("")

    # --- Unknown labels: silent bucketing via explicit fallback ---

    def test_unknown_label_returns_fallback_when_provided(self):
        assert confidence_level_rank("bogus", fallback=-1) == -1

    def test_none_returns_fallback_when_provided(self):
        assert confidence_level_rank(None, fallback=-1) == -1

    def test_empty_string_returns_fallback_when_provided(self):
        assert confidence_level_rank("", fallback=-1) == -1

    def test_fallback_can_be_any_int(self):
        # Fallback polarity is the caller's choice — most pre-W634
        # sites used -1, but a sort key that wants "unknown at the
        # bottom of an ascending sort" might pass a very large int.
        assert confidence_level_rank("bogus", fallback=99) == 99
        assert confidence_level_rank("bogus", fallback=0) == 0

    def test_fallback_zero_is_distinct_from_none_default(self):
        # ``fallback=0`` is a real opt-in (returns 0, not raises).
        # Catches accidental ``if fallback:`` falsy-check bugs.
        assert confidence_level_rank("bogus", fallback=0) == 0


# ---------------------------------------------------------------------------
# Fixtures used by the pilot-command tests
# ---------------------------------------------------------------------------


def _make_smells_db(tmp_path: Path) -> None:
    """Create a `.roam/index.db` with one critical brain-method smell.

    Schema and population mirror tests/test_smells.py so we don't depend
    on its internal helpers.
    """
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0,
            health_score REAL DEFAULT NULL
        );
    """)
    # Brain method (critical) + deep nesting (warning) + message chain (info)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/engine.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'process_everything', 'function', 10, 200, '(data, config, opts)')"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 75, 6)")
    # Add a second symbol with deep nesting (warning severity)
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'src/nested.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (2, 2, 'nested_fn', 'function', 1, 50)"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (2, 12, 7)")
    # Message chain — info severity
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (3, 2, 'chatty_fn', 'function', 60, 100)"
    )
    conn.execute("INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) VALUES (3, 1, 15)")
    conn.commit()
    conn.close()


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    (path / "dummy.py").write_text("# dummy\n")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


@pytest.fixture
def smells_project(tmp_path):
    _git_init(tmp_path)
    _make_smells_db(tmp_path)
    return tmp_path


def _make_vulns_db(tmp_path: Path) -> None:
    """Create a `.roam/index.db` plus a populated `vulnerabilities` table."""
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS vulnerabilities (
            id INTEGER PRIMARY KEY,
            cve_id TEXT,
            package_name TEXT,
            severity TEXT,
            title TEXT,
            source TEXT,
            matched_symbol_id INTEGER,
            matched_file TEXT,
            reachable INTEGER DEFAULT 0,
            shortest_path TEXT,
            hop_count INTEGER
        );
    """)
    # 3 vulns with different sources and reachability — exercises every
    # arm of the classifier.
    conn.execute(
        "INSERT INTO vulnerabilities "
        "(cve_id, package_name, severity, title, source, reachable, matched_file) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("CVE-2024-0001", "lodash", "high", "Prototype Pollution", "npm-audit", 1, "src/x.js"),
    )
    conn.execute(
        "INSERT INTO vulnerabilities "
        "(cve_id, package_name, severity, title, source, reachable, matched_file) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("CVE-2024-0002", "openssl", "critical", "Buffer overflow", "trivy", 0, None),
    )
    conn.execute(
        "INSERT INTO vulnerabilities "
        "(cve_id, package_name, severity, title, source, reachable, matched_file) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("CVE-2024-0003", "leftover", "low", "Stale dep", "generic", -1, None),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def vulns_project(tmp_path):
    _git_init(tmp_path)
    _make_vulns_db(tmp_path)
    return tmp_path


def _make_complexity_db(tmp_path: Path) -> None:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0,
            cyclomatic_density REAL DEFAULT 0,
            halstead_volume REAL DEFAULT 0,
            halstead_difficulty REAL DEFAULT 0,
            halstead_effort REAL DEFAULT 0,
            halstead_bugs REAL DEFAULT 0
        );
    """)
    # Critical (cc=30), high (cc=20), medium (cc=10), low (cc=3)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/lib.py')")
    for sid, name, cc in [
        (1, "critical_fn", 30),
        (2, "high_fn", 20),
        (3, "medium_fn", 10),
        (4, "low_fn", 3),
    ]:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, 1, ?, ?, 'function', ?, ?)",
            (sid, name, f"lib.{name}", sid * 10, sid * 10 + 5),
        )
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) VALUES (?, ?)",
            (sid, cc),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def complexity_project(tmp_path):
    _git_init(tmp_path)
    _make_complexity_db(tmp_path)
    return tmp_path


@pytest.fixture
def orphan_imports_project(tmp_path):
    """A small indexed project with one obvious orphan import."""
    _git_init(tmp_path)
    # Source layout: a real package `pkg/` and a file that imports
    # `pkg.does_not_exist` (an internal_typo orphan).
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "real.py").write_text("def f(): pass\n")
    (tmp_path / "src" / "main.py").write_text(
        "from pkg.does_not_exist import thing\nfrom pkg.real import f\nimport totally_made_up_package_xyz\n"
    )

    # Build a minimal index DB that records the indexed Python files so
    # `_indexed_python_modules` returns pkg/pkg.real but NOT
    # pkg.does_not_exist.
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO files (path, language) VALUES (?, 'python')", ("src/pkg/__init__.py",))
    conn.execute("INSERT INTO files (path, language) VALUES (?, 'python')", ("src/pkg/real.py",))
    conn.execute("INSERT INTO files (path, language) VALUES (?, 'python')", ("src/main.py",))
    conn.commit()
    conn.close()
    # Commit so git status is clean
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add orphan", "--allow-empty"], cwd=str(tmp_path), capture_output=True)
    return tmp_path


# ---------------------------------------------------------------------------
# 2. Pilot-command shape tests
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], cwd: Path):
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args)
    finally:
        os.chdir(old_cwd)


class TestPilotCommandSmells:
    """`roam smells` returns wrapped triples + distribution + verdict."""

    def test_smells_returns_triples(self, smells_project):
        result = _run_cli(["--json", "--detail", "smells", "--include-tooling"], smells_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        smells = data.get("smells", [])
        assert len(smells) >= 1
        # Every entry must be a triple.
        for s in smells:
            assert set(s.keys()) == {"value", "confidence", "reason"}
            assert s["confidence"] in CONFIDENCE_LEVELS
            assert isinstance(s["reason"], str)
            # Backward-compat check — value still carries the old keys.
            assert "symbol_name" in s["value"]
            assert "severity" in s["value"]

    def test_smells_envelope_has_distribution(self, smells_project):
        result = _run_cli(["--json", "smells", "--include-tooling"], smells_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}
        # Brain method = critical = high → at least 1 high.
        assert dist["high"] >= 1

    def test_smells_verdict_mentions_high_count(self, smells_project):
        result = _run_cli(["--json", "smells", "--include-tooling"], smells_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        verdict = data["summary"]["verdict"]
        assert "high-confidence" in verdict, f"verdict missing high-count: {verdict!r}"


class TestPilotCommandClones:
    """`roam clones` returns wrapped cluster + pair triples.

    Uses the actual clone-detection pipeline (slower) so the assertions
    are tolerant — when no clones are detected we still confirm the
    distribution field is present with all-zero buckets.
    """

    def test_clones_envelope_has_distribution(self, tmp_path):
        # Minimal project that should NOT produce clones — even so, the
        # envelope must carry the distribution field.
        from tests.conftest import make_src_project

        proj = make_src_project(tmp_path, {"a.py": "def f():\n    return 1\n"})
        # Index first
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            idx = runner.invoke(cli, ["index"])
            assert idx.exit_code == 0, idx.output
            result = runner.invoke(cli, ["--json", "clones"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}

    def test_clones_returns_triples_when_clones_present(self, tmp_path):
        from tests.conftest import make_src_project

        proj = make_src_project(
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
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            idx = runner.invoke(cli, ["index"])
            assert idx.exit_code == 0, idx.output
            result = runner.invoke(cli, ["--json", "clones", "--threshold", "0.50"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        clusters = data.get("clusters", [])
        # If clones were found, each entry must be a triple.
        for c in clusters:
            assert set(c.keys()) == {"value", "confidence", "reason"}
            assert c["confidence"] in CONFIDENCE_LEVELS
            assert "avg_similarity" in c["value"]
        pairs = data.get("pairs", [])
        for p in pairs:
            assert set(p.keys()) == {"value", "confidence", "reason"}
            assert p["confidence"] in CONFIDENCE_LEVELS


class TestPilotCommandVulns:
    """`roam vulns` wraps each vulnerability in a triple."""

    def test_vulns_returns_triples(self, vulns_project):
        result = _run_cli(["--json", "vulns"], vulns_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        vulns_list = data.get("vulnerabilities", [])
        assert len(vulns_list) == 3
        for v in vulns_list:
            assert set(v.keys()) == {"value", "confidence", "reason"}
            assert v["confidence"] in CONFIDENCE_LEVELS
            # Backward-compat check — value still has cve_id/package.
            assert "cve_id" in v["value"]
            assert "package" in v["value"]

    def test_vulns_envelope_has_distribution(self, vulns_project):
        result = _run_cli(["--json", "vulns"], vulns_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}
        # We seeded 1 npm-audit+reachable (→ high), 1 trivy+nounknown
        # (→ medium), 1 generic+unreachable (→ low downgraded).
        assert dist["high"] >= 1

    def test_vulns_verdict_mentions_high_count(self, vulns_project):
        result = _run_cli(["--json", "vulns"], vulns_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        verdict = data["summary"]["verdict"]
        assert "high-confidence" in verdict, f"verdict missing high-count: {verdict!r}"


class TestPilotCommandOrphanImports:
    """`roam orphan-imports` wraps each orphan in a triple."""

    def test_orphan_imports_returns_triples(self, orphan_imports_project):
        result = _run_cli(["--json", "orphan-imports", "--lang", "python"], orphan_imports_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        orphans = data.get("orphans", [])
        assert len(orphans) >= 1
        for o in orphans:
            assert set(o.keys()) == {"value", "confidence", "reason"}
            assert o["confidence"] in CONFIDENCE_LEVELS
            assert "module" in o["value"]

    def test_orphan_imports_envelope_has_distribution(self, orphan_imports_project):
        result = _run_cli(["--json", "orphan-imports", "--lang", "python"], orphan_imports_project)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}
        # internal_typo → high; we planted one.
        assert dist["high"] >= 1


class TestPilotCommandComplexity:
    """`roam complexity` wraps each symbol in a triple."""

    def test_complexity_returns_triples(self, complexity_project):
        result = _run_cli(
            ["--json", "complexity", "--top", "10", "--include-tooling"],
            complexity_project,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        symbols = data.get("symbols", [])
        assert len(symbols) >= 1
        for s in symbols:
            assert set(s.keys()) == {"value", "confidence", "reason"}
            assert s["confidence"] in CONFIDENCE_LEVELS
            assert "name" in s["value"]
            assert "cognitive_complexity" in s["value"]

    def test_complexity_envelope_has_distribution(self, complexity_project):
        result = _run_cli(
            ["--json", "complexity", "--top", "10", "--include-tooling"],
            complexity_project,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}
        # critical (cc=30) + high (cc=20) → at least 2 high
        assert dist["high"] >= 2

    def test_complexity_verdict_mentions_high_count(self, complexity_project):
        result = _run_cli(
            ["--json", "complexity", "--top", "10", "--include-tooling"],
            complexity_project,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        verdict = data["summary"]["verdict"]
        assert "high-confidence" in verdict, f"verdict missing high-count: {verdict!r}"


# ---------------------------------------------------------------------------
# 3. W12 sweep — eight more commands migrated to the triple format.
#
# These tests are classifier-focused (cheap, deterministic) plus a few
# end-to-end shape checks where a project fixture is already on hand.
# ---------------------------------------------------------------------------


class TestPilotCommandSecrets:
    """`roam secrets` wraps each finding in a triple."""

    def test_secrets_classifier_high_entropy(self):
        from roam.commands.cmd_secrets import _secrets_classify

        conf, reason = _secrets_classify({"pattern": "High Entropy String", "matched_text": "abc..xyz"})
        assert conf == "high"
        assert "entropy" in reason.lower()

    def test_secrets_classifier_known_prefix(self):
        from roam.commands.cmd_secrets import _secrets_classify

        conf, reason = _secrets_classify({"pattern": "AWS Access Key", "matched_text": "AKIA..ZZZZ"})
        assert conf == "medium"
        assert "prefix" in reason.lower()

    def test_secrets_classifier_generic_low(self):
        from roam.commands.cmd_secrets import _secrets_classify

        conf, _reason = _secrets_classify({"pattern": "Generic Password Assignment", "matched_text": "pass..rd"})
        assert conf == "low"

    def test_secrets_envelope_has_distribution(self, tmp_path):
        _git_init(tmp_path)
        # Seed an AWS Access Key into a source file so `secrets` finds it.
        (tmp_path / "app.py").write_text("API = 'AKIAIOSFODNN7TESTDATA'\n")
        # Build a minimal index DB so ensure_index() and the file scan
        # path both work.
        db_path = tmp_path / ".roam" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                language TEXT, file_role TEXT DEFAULT 'source',
                hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
            );
            """
        )
        conn.execute("INSERT INTO files (path, language) VALUES ('app.py', 'python')")
        conn.commit()
        conn.close()
        result = _run_cli(["--json", "secrets"], tmp_path)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}
        findings = data.get("findings", [])
        for f in findings:
            assert set(f.keys()) == {"value", "confidence", "reason"}
            assert f["confidence"] in CONFIDENCE_LEVELS


class TestPilotCommandN1:
    """`roam n1` wraps each finding in a triple."""

    def test_n1_classifier_high(self):
        from roam.commands.cmd_n1 import _n1_classify

        conf, reason = _n1_classify(
            {
                "confidence": "high",
                "io_type": "relationship",
                "collection_contexts": [{"x": 1}, {"x": 2}],
            }
        )
        assert conf == "high"
        assert "collection" in reason.lower()

    def test_n1_classifier_medium(self):
        from roam.commands.cmd_n1 import _n1_classify

        conf, _r = _n1_classify({"confidence": "medium", "io_type": "query builder"})
        assert conf == "medium"

    def test_n1_classifier_low(self):
        from roam.commands.cmd_n1 import _n1_classify

        conf, _r = _n1_classify({"confidence": "low", "io_type": "?"})
        assert conf == "low"

    def test_n1_classifier_bad_label_falls_back(self):
        from roam.commands.cmd_n1 import _n1_classify

        conf, _r = _n1_classify({"confidence": "URGENT"})
        assert conf == "medium"


class TestPilotCommandMissingIndex:
    """`roam missing-index` wraps each finding in a triple."""

    def test_missing_index_classifier_high_paginate(self):
        from roam.commands.cmd_missing_index import _missing_index_classify

        conf, reason = _missing_index_classify(
            {"confidence": "high", "pattern_type": "single_where", "has_paginate": True}
        )
        assert conf == "high"
        assert "paginat" in reason.lower()

    def test_missing_index_classifier_medium(self):
        from roam.commands.cmd_missing_index import _missing_index_classify

        conf, _r = _missing_index_classify({"confidence": "medium", "pattern_type": "orderby", "has_paginate": False})
        assert conf == "medium"

    def test_missing_index_classifier_low_composite(self):
        from roam.commands.cmd_missing_index import _missing_index_classify

        conf, _r = _missing_index_classify({"confidence": "low", "pattern_type": "orderby_with_where"})
        assert conf == "low"


class TestPilotCommandTaint:
    """`roam taint` wraps each finding in a triple."""

    def test_taint_classifier_high_unsanitised(self):
        from roam.commands.cmd_taint import _taint_classify

        conf, reason = _taint_classify({"severity": "error", "sanitizer_in_path": False, "path_length": 4})
        assert conf == "high"
        assert "no sanit" in reason.lower() or "direct" in reason.lower()

    def test_taint_classifier_medium_sanitised(self):
        from roam.commands.cmd_taint import _taint_classify

        conf, _r = _taint_classify({"severity": "error", "sanitizer_in_path": True, "path_length": 3})
        assert conf == "medium"

    def test_taint_classifier_low_unknown(self):
        from roam.commands.cmd_taint import _taint_classify

        conf, _r = _taint_classify({"severity": "info", "sanitizer_in_path": False})
        assert conf == "low"

    def test_taint_envelope_has_distribution(self, tmp_path):
        # Empty project — no findings — but the envelope must still
        # ship the (all-zero) distribution.
        _git_init(tmp_path)
        # Build a schema-compatible empty index DB so the taint engine
        # can issue its source/sink queries without sqlite errors.
        db_path = tmp_path / ".roam" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                language TEXT, file_role TEXT DEFAULT 'source',
                hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
                signature TEXT, line_start INTEGER, line_end INTEGER,
                docstring TEXT, visibility TEXT DEFAULT 'public',
                is_exported INTEGER DEFAULT 1, parent_id INTEGER,
                default_value TEXT
            );
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
                line INTEGER, bridge TEXT, confidence REAL,
                source_file_id INTEGER
            );
            """
        )
        conn.commit()
        conn.close()
        result = _run_cli(["--json", "taint"], tmp_path)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        dist = data["summary"].get("findings_confidence_distribution")
        assert dist is not None
        assert set(dist.keys()) == {"high", "medium", "low"}


class TestPilotCommandFitness:
    """`roam fitness` wraps each violation in a triple."""

    def test_fitness_classifier_severity_error(self):
        from roam.commands.cmd_fitness import _fitness_classify

        conf, reason = _fitness_classify({"severity": "error", "type": "dependency"})
        assert conf == "high"
        assert "error" in reason.lower()

    def test_fitness_classifier_severity_warning(self):
        from roam.commands.cmd_fitness import _fitness_classify

        conf, _r = _fitness_classify({"severity": "warning", "type": "trend"})
        assert conf == "medium"

    def test_fitness_classifier_severity_info(self):
        from roam.commands.cmd_fitness import _fitness_classify

        conf, _r = _fitness_classify({"severity": "info", "type": "naming"})
        assert conf == "low"

    def test_fitness_classifier_infers_from_type(self):
        """Rules without an explicit severity → confidence inferred from type."""
        from roam.commands.cmd_fitness import _fitness_classify

        conf_dep, _ = _fitness_classify({"type": "dependency"})
        conf_metric, _ = _fitness_classify({"type": "metric"})
        conf_trend, _ = _fitness_classify({"type": "trend"})
        conf_name, _ = _fitness_classify({"type": "naming"})
        assert conf_dep == "high"
        assert conf_metric == "high"
        assert conf_trend == "medium"
        assert conf_name == "low"


class TestPilotCommandCoverageGaps:
    """`roam coverage-gaps` wraps each uncovered entry in a triple."""

    def test_coverage_gaps_classifier_high(self):
        from roam.commands.cmd_coverage_gaps import _coverage_gaps_classify

        conf, reason = _coverage_gaps_classify({"caller_count": 25})
        assert conf == "high"
        assert "25" in reason

    def test_coverage_gaps_classifier_medium(self):
        from roam.commands.cmd_coverage_gaps import _coverage_gaps_classify

        conf, _r = _coverage_gaps_classify({"caller_count": 5})
        assert conf == "medium"

    def test_coverage_gaps_classifier_low(self):
        from roam.commands.cmd_coverage_gaps import _coverage_gaps_classify

        conf, _r = _coverage_gaps_classify({"caller_count": 1})
        assert conf == "low"

    def test_coverage_gaps_classifier_missing_count_low(self):
        from roam.commands.cmd_coverage_gaps import _coverage_gaps_classify

        conf, _r = _coverage_gaps_classify({})
        assert conf == "low"


class TestPilotCommandConventions:
    """`roam conventions` wraps each violation in a triple."""

    def test_conventions_classifier_high(self):
        from roam.commands.cmd_conventions import _convention_classify

        conf, reason = _convention_classify(
            {"group_dominant_pct": 95.0, "expected_style": "snake_case", "actual_style": "camelCase"}
        )
        assert conf == "high"
        assert "95" in reason

    def test_conventions_classifier_medium(self):
        from roam.commands.cmd_conventions import _convention_classify

        conf, _r = _convention_classify(
            {"group_dominant_pct": 80.0, "expected_style": "snake_case", "actual_style": "PascalCase"}
        )
        assert conf == "medium"

    def test_conventions_classifier_low(self):
        from roam.commands.cmd_conventions import _convention_classify

        conf, _r = _convention_classify(
            {"group_dominant_pct": 55.0, "expected_style": "snake_case", "actual_style": "camelCase"}
        )
        assert conf == "low"

    def test_conventions_classifier_missing_pct_low(self):
        from roam.commands.cmd_conventions import _convention_classify

        conf, _r = _convention_classify({"expected_style": "snake_case", "actual_style": "camelCase"})
        # No pct → defaults to 0 → low
        assert conf == "low"


class TestPilotCommandDead:
    """`roam dead` wraps each dead-export entry in a triple."""

    def test_dead_classifier_high_stable_unused(self):
        from roam.commands.cmd_dead import _dead_classify

        conf, reason = _dead_classify({"action": "SAFE", "tested": False, "aging": {"age_days": 90}})
        assert conf == "high"
        assert "90" in reason

    def test_dead_classifier_medium_recent_edit(self):
        from roam.commands.cmd_dead import _dead_classify

        conf, _r = _dead_classify({"action": "SAFE", "tested": False, "aging": {"age_days": 5}})
        assert conf == "medium"

    def test_dead_classifier_medium_tested(self):
        """SAFE-action but test-referenced → medium (downgrade from high)."""
        from roam.commands.cmd_dead import _dead_classify

        conf, _r = _dead_classify({"action": "SAFE", "tested": True, "aging": {"age_days": 200}})
        assert conf == "medium"

    def test_dead_classifier_low_intentional(self):
        from roam.commands.cmd_dead import _dead_classify

        conf_a, _ = _dead_classify({"action": "INTENTIONAL"})
        conf_b, _ = _dead_classify({"action": "INTENTIONAL_SCAFFOLDING"})
        assert conf_a == "low"
        assert conf_b == "low"

    def test_dead_classifier_review_fallback_medium(self):
        from roam.commands.cmd_dead import _dead_classify

        conf, _r = _dead_classify({"action": "REVIEW", "tested": False})
        assert conf == "medium"
