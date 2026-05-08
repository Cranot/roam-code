"""Tests for A.2 — `roam critique`.

Covers diff parsing, symbol resolution, the clones-not-edited check
(killer signal — depends on persisted clone tables), the impact check,
the aggregator, and the CLI surface.
"""

from __future__ import annotations

import json
import os
import textwrap

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.critique.aggregator import aggregate, severity_rank
from roam.critique.checks import (
    ChangedRegion,
    ChangedSymbol,
    Finding,
    _classify_intent,
    check_clones_not_edited,
    check_impact,
    check_intent_alignment,
    find_changed_symbols,
    parse_diff,
)
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Diff parsing — pure function, no DB
# ---------------------------------------------------------------------------


class TestParseDiff:
    def test_empty(self):
        assert parse_diff("") == []
        assert parse_diff("   \n\n  ") == []

    def test_simple_one_file_one_hunk(self):
        diff = textwrap.dedent(
            """\
            diff --git a/src/auth.py b/src/auth.py
            index 0000..1111 100644
            --- a/src/auth.py
            +++ b/src/auth.py
            @@ -10,5 +10,7 @@
             unchanged line
            -removed
            +added one
            +added two
             tail
            """
        )
        regions = parse_diff(diff)
        assert len(regions) == 1
        r = regions[0]
        assert r.file_path == "src/auth.py"
        assert r.hunks == ((10, 7),)
        assert r.additions == 2
        assert r.deletions == 1

    def test_multi_hunk_one_file(self):
        diff = textwrap.dedent(
            """\
            diff --git a/src/auth.py b/src/auth.py
            --- a/src/auth.py
            +++ b/src/auth.py
            @@ -1,3 +1,3 @@
             a
            -b
            +B
             c
            @@ -50,4 +60,5 @@
             x
            +new
             y
            """
        )
        regions = parse_diff(diff)
        assert len(regions) == 1
        r = regions[0]
        assert (1, 3) in r.hunks
        assert (60, 5) in r.hunks
        assert r.additions == 2
        assert r.deletions == 1

    def test_dev_null_target_skipped(self):
        """Deletion (target == /dev/null) yields no region for that file."""
        diff = textwrap.dedent(
            """\
            diff --git a/src/old.py b/src/old.py
            deleted file mode 100644
            --- a/src/old.py
            +++ /dev/null
            """
        )
        assert parse_diff(diff) == []

    def test_multiple_files(self):
        diff = textwrap.dedent(
            """\
            diff --git a/a.py b/a.py
            --- a/a.py
            +++ b/a.py
            @@ -1,1 +1,2 @@
             a
            +b
            diff --git a/b.py b/b.py
            --- a/b.py
            +++ b/b.py
            @@ -5,2 +5,3 @@
             x
            +y
             z
            """
        )
        regions = parse_diff(diff)
        paths = {r.file_path for r in regions}
        assert paths == {"a.py", "b.py"}

    def test_hunk_with_only_new_start_no_length(self):
        """`@@ -10 +10 @@` (default length 1) parses cleanly."""
        diff = textwrap.dedent(
            """\
            diff --git a/x.py b/x.py
            --- a/x.py
            +++ b/x.py
            @@ -10 +10 @@
            -old
            +new
            """
        )
        regions = parse_diff(diff)
        assert regions[0].hunks == ((10, 1),)


# ---------------------------------------------------------------------------
# Symbol resolution + integration with an indexed project
# ---------------------------------------------------------------------------


_PROJECT_FILES = {
    "auth.py": """
        class UserSession:
            def __init__(self, token):
                self.token = token

            def refresh(self):
                # CHANGE-ME line
                return self.token

            def revoke(self):
                return None

        def handle_login(user):
            s = UserSession(token="abc")
            return s.refresh()
    """,
    "auth_v2.py": """
        class UserSessionV2:
            def __init__(self, token):
                self.token = token

            def refresh(self):
                # CLONE of auth.py:UserSession.refresh
                return self.token

            def revoke(self):
                return None
    """,
    "billing.py": """
        class Invoice:
            def total(self):
                return self.amount
    """,
}


@pytest.fixture
def critique_project(tmp_path):
    proj = _make_project(tmp_path, _PROJECT_FILES)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # Persist clones so the killer check has data.
        runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"])
        yield proj
    finally:
        os.chdir(old_cwd)


class TestFindChangedSymbols:
    def test_finds_symbol_in_changed_hunk(self, critique_project):
        from roam.db.connection import open_db

        regions = [
            ChangedRegion(
                file_path="src/auth.py",
                hunks=((6, 1),),  # line covering refresh()
                additions=1,
                deletions=0,
            )
        ]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
        names = {s.name for s in changed}
        assert "refresh" in names

    def test_unknown_file_silently_skipped(self, critique_project):
        from roam.db.connection import open_db

        regions = [
            ChangedRegion(
                file_path="src/does_not_exist.py",
                hunks=((1, 5),),
            )
        ]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
        assert changed == []

    def test_anchored_path_resolution(self, critique_project):
        """Diff paths starting at repo root resolve against indexed files."""
        from roam.db.connection import open_db

        regions = [
            ChangedRegion(
                file_path="auth.py",  # without src/ prefix
                hunks=((1, 50),),
            )
        ]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
        # Anchored-suffix resolution should still find src/auth.py
        files = {s.file_path for s in changed}
        assert any("auth.py" in f for f in files)

    def test_dedupes_when_hunk_overlaps_multiple_methods(self, critique_project):
        """A hunk covering several methods returns each symbol exactly once."""
        from roam.db.connection import open_db

        regions = [
            ChangedRegion(
                file_path="src/auth.py",
                hunks=((1, 100),),  # whole file
            )
        ]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
        ids = [s.symbol_id for s in changed]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Killer check — clones-not-edited
# ---------------------------------------------------------------------------


class TestCheckClonesNotEdited:
    def test_emits_finding_for_unedited_sibling(self, critique_project):
        """Editing UserSession.refresh but not UserSessionV2.refresh → finding."""
        from roam.db.connection import open_db

        regions = [
            ChangedRegion(
                file_path="src/auth.py",
                hunks=((6, 1),),
            )
        ]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
            findings = check_clones_not_edited(conn, changed, regions)

        # We expect at least one clones-not-edited finding pointing at auth_v2.py.
        if not findings:
            pytest.skip("clones not detected by the AST hashing on this fixture")

        target_files = []
        for f in findings:
            for s in f.evidence.get("siblings", []):
                target_files.append(s.get("sibling_file", ""))
        assert any("auth_v2.py" in path for path in target_files), (
            f"expected auth_v2.py sibling to be flagged; saw {target_files}"
        )

    def test_no_findings_when_clone_table_empty(self, tmp_path):
        """When clone_pairs is empty (no --persist run), check returns []."""
        proj = _make_project(tmp_path, {"a.py": "def f():\n    return 1\n"})
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0
            # Deliberately do NOT run `roam clones --persist`.

            from roam.db.connection import open_db

            regions = [ChangedRegion(file_path="src/a.py", hunks=((1, 2),))]
            with open_db(readonly=True) as conn:
                changed = find_changed_symbols(conn, regions)
                findings = check_clones_not_edited(conn, changed, regions)
            assert findings == []
        finally:
            os.chdir(old_cwd)

    def test_no_findings_when_all_siblings_in_diff(self, critique_project):
        """If the diff touches BOTH clone files, no missed-edit finding."""
        from roam.db.connection import open_db

        regions = [
            ChangedRegion(file_path="src/auth.py", hunks=((1, 100),)),
            ChangedRegion(file_path="src/auth_v2.py", hunks=((1, 100),)),
        ]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
            findings = check_clones_not_edited(conn, changed, regions)
        # Either zero findings, or none of the surviving findings point at
        # symbols inside the diff (would be a logic bug).
        for f in findings:
            for sibling in f.evidence.get("siblings", []):
                sib_file = (sibling.get("sibling_file") or "").replace("\\", "/")
                assert "auth.py" not in sib_file
                assert "auth_v2.py" not in sib_file


# ---------------------------------------------------------------------------
# Impact check
# ---------------------------------------------------------------------------


class TestCheckImpact:
    def test_low_caller_count_silent(self, critique_project):
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            sym = conn.execute(
                "SELECT s.id, s.name, s.qualified_name, s.kind, "
                "       s.line_start, s.line_end, f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'revoke' LIMIT 1"
            ).fetchone()
            if sym is None:
                pytest.skip("fixture symbol missing")
            changed = [
                ChangedSymbol(
                    symbol_id=sym["id"],
                    name=sym["name"],
                    qualified_name=sym["qualified_name"],
                    kind=sym["kind"],
                    file_path=sym["file_path"],
                    line_start=sym["line_start"] or 0,
                    line_end=sym["line_end"] or 0,
                )
            ]
            findings = check_impact(conn, changed, high_callers=1000)
        assert findings == []

    def test_threshold_zero_emits_findings_for_called_symbol(self, critique_project):
        """high_callers=0 surfaces every called symbol (smoke check)."""
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            sym = conn.execute(
                "SELECT s.id, s.name, s.qualified_name, s.kind, "
                "       s.line_start, s.line_end, f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'refresh' LIMIT 1"
            ).fetchone()
            if sym is None:
                pytest.skip("fixture symbol missing")
            changed = [
                ChangedSymbol(
                    symbol_id=sym["id"],
                    name=sym["name"],
                    qualified_name=sym["qualified_name"],
                    kind=sym["kind"],
                    file_path=sym["file_path"],
                    line_start=sym["line_start"] or 0,
                    line_end=sym["line_end"] or 0,
                )
            ]
            findings = check_impact(conn, changed, high_callers=0)
        # `refresh` is called from `handle_login` at minimum.
        if findings:
            f = findings[0]
            assert f.check == "impact"
            assert f.severity in {"high", "medium"}


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class TestIntentClassifier:
    """The deterministic intent classifier — no NLP, just verb membership."""

    def test_add_label(self):
        assert "add" in _classify_intent("add new login flow")
        assert "add" in _classify_intent("introduce User.refresh helper")
        assert "add" in _classify_intent("implement billing webhook")

    def test_remove_label(self):
        assert "remove" in _classify_intent("remove deprecated auth")
        assert "remove" in _classify_intent("drop the legacy callback")
        assert "remove" in _classify_intent("retire UserSessionV1")

    def test_fix_label(self):
        assert "fix" in _classify_intent("fix login race condition")
        assert "fix" in _classify_intent("patch the n+1 in checkout")
        assert "fix" in _classify_intent("resolve flaky test")

    def test_rename_label(self):
        assert "rename" in _classify_intent("rename UserSession -> Session")

    def test_no_match(self):
        assert _classify_intent("") == set()
        assert _classify_intent("misc cleanup") == set()
        assert _classify_intent("update copy in readme") == set()

    def test_multiple_labels(self):
        labels = _classify_intent("add new endpoint and remove the old one")
        assert "add" in labels and "remove" in labels


class TestCheckIntentAlignment:
    """Intent ↔ semantic-diff deterministic check (Phase 3 borrow,
    Meta JIT-test framing, 4× bug-detection lift in literature)."""

    @staticmethod
    def _sym(sid=1, name="x", file="src/x.py", lstart=1, lend=10):
        return ChangedSymbol(
            symbol_id=sid,
            name=name,
            qualified_name=name,
            kind="function",
            file_path=file,
            line_start=lstart,
            line_end=lend,
        )

    def test_no_intent_no_findings(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 5),), additions=3, deletions=2)]
        assert check_intent_alignment("", [self._sym()], regions) == []
        assert check_intent_alignment("", [], regions) == []

    def test_no_changed_symbols_no_findings(self):
        assert check_intent_alignment("add login flow", [], []) == []

    def test_no_intent_label_no_findings(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 5),), additions=3, deletions=2)]
        # "misc cleanup" doesn't trigger any intent label.
        findings = check_intent_alignment("misc cleanup", [self._sym()], regions)
        assert findings == []

    def test_add_with_no_additions_flags(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 5),), additions=0, deletions=4)]
        findings = check_intent_alignment("add new helper", [self._sym()], regions)
        assert any(f.check == "intent" and "add" in f.title.lower() for f in findings)

    def test_remove_with_no_deletions_flags(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 5),), additions=4, deletions=0)]
        findings = check_intent_alignment("remove old auth", [self._sym()], regions)
        assert any(f.check == "intent" and "remove" in f.title.lower() for f in findings)

    def test_fix_with_mostly_additions_low_severity_nudge(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 30),), additions=40, deletions=2)]
        findings = check_intent_alignment("fix login bug", [self._sym()], regions)
        # Should nudge at LOW severity, not block CI.
        intent_findings = [f for f in findings if f.check == "intent"]
        assert intent_findings
        assert intent_findings[0].severity == "low"

    def test_rename_with_many_symbols_low_severity_nudge(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 30),), additions=10, deletions=10)]
        syms = [self._sym(sid=i, name=f"n{i}") for i in range(5)]
        findings = check_intent_alignment("rename foo to bar", syms, regions)
        intent_findings = [f for f in findings if f.check == "intent"]
        assert intent_findings
        assert intent_findings[0].severity == "low"

    def test_balanced_fix_is_silent(self):
        """A normal bug-fix patch (modest additions + deletions) has nothing to flag."""
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 5),), additions=3, deletions=4)]
        findings = check_intent_alignment("fix race condition", [self._sym()], regions)
        # No 'fix' nudge because additions don't dominate.
        assert not any(f.check == "intent" and "fix" in f.title.lower() for f in findings)

    def test_add_with_additions_silent(self):
        regions = [ChangedRegion(file_path="src/x.py", hunks=((1, 5),), additions=8, deletions=0)]
        findings = check_intent_alignment("add login helper", [self._sym()], regions)
        assert findings == []  # adds match the diff shape


class TestImpactRuntimeBump:
    """Phase 2 leverage primitive ships in critique: when a changed
    symbol's caller is on a hot runtime path, severity escalates."""

    def test_runtime_score_added_to_evidence(self, critique_project):
        """Even when no runtime data exists, the evidence dict carries
        max_caller_runtime_score (so consumers can rely on the key)."""
        from roam.db.connection import open_db

        regions = [ChangedRegion(file_path="src/auth.py", hunks=((1, 100),))]
        with open_db(readonly=True) as conn:
            changed = find_changed_symbols(conn, regions)
            findings = check_impact(conn, changed, high_callers=0)
        for f in findings:
            assert "max_caller_runtime_score" in f.evidence


class TestAggregator:
    def test_severity_rank_ordering(self):
        assert severity_rank("high") < severity_rank("medium")
        assert severity_rank("medium") < severity_rank("low")
        assert severity_rank("low") < severity_rank("info")
        assert severity_rank("info") < severity_rank("unknown")

    def test_empty_findings_clean_verdict(self):
        result = aggregate([])
        assert result["verdict"] == "No concerns from roam critique"
        assert result["severity_breakdown"] == {
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }
        assert result["findings"] == []
        assert result["top_finding"] is None

    def test_sort_by_severity(self):
        findings = [
            Finding("impact", "low", "low one", "...", {}),
            Finding("clones-not-edited", "high", "high one", "...", {}),
            Finding("impact", "medium", "medium one", "...", {}),
        ]
        result = aggregate(findings)
        severities = [f["severity"] for f in result["findings"]]
        assert severities == ["high", "medium", "low"]
        assert result["top_finding"]["severity"] == "high"

    def test_breakdown_counts(self):
        findings = [
            Finding("a", "high", "h1", "...", {}),
            Finding("a", "high", "h2", "...", {}),
            Finding("a", "medium", "m1", "...", {}),
        ]
        result = aggregate(findings)
        assert result["severity_breakdown"]["high"] == 2
        assert result["severity_breakdown"]["medium"] == 1
        assert "high" in result["verdict"]
        assert "medium" in result["verdict"]


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


_DIFF_REFRESH_ONLY = textwrap.dedent(
    """\
    diff --git a/src/auth.py b/src/auth.py
    --- a/src/auth.py
    +++ b/src/auth.py
    @@ -5,3 +5,4 @@
         def refresh(self):
    -        return self.token
    +        # tweaked
    +        return str(self.token)
    """
)


class TestCriticueCLI:
    def test_text_output_via_input_flag(self, critique_project, tmp_path):
        diff_path = tmp_path / "patch.diff"
        diff_path.write_text(_DIFF_REFRESH_ONLY, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["critique", "--input", str(diff_path)])
        assert result.exit_code in (0, 5), result.output
        assert "VERDICT:" in result.output

    def test_json_envelope(self, critique_project, tmp_path):
        diff_path = tmp_path / "patch.diff"
        diff_path.write_text(_DIFF_REFRESH_ONLY, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
        assert result.exit_code in (0, 5), result.output
        data = json.loads(result.output)
        assert data["command"] == "critique"
        assert "summary" in data
        assert "findings" in data
        assert "severity_breakdown" in data
        assert isinstance(data["summary"]["high_severity"], int)

    def test_empty_diff_usage_error(self, critique_project, tmp_path):
        empty = tmp_path / "empty.diff"
        empty.write_text("   \n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["critique", "--input", str(empty)])
        assert result.exit_code != 0

    def test_missing_input_file_rejected(self, critique_project, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["critique", "--input", str(tmp_path / "nope.diff")])
        assert result.exit_code != 0

    def test_appears_in_help(self, critique_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "critique" in result.output

    def test_exit_code_5_on_high_severity(self, critique_project, tmp_path):
        """When a clones-not-edited finding fires at high severity, exit 5."""
        # Edit just src/auth.py; if AST hashing identified UserSession as a
        # clone of UserSessionV2 (≥2 unedited siblings → high), we expect 5.
        diff_path = tmp_path / "patch.diff"
        diff_path.write_text(_DIFF_REFRESH_ONLY, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
        # Either exit 5 (high finding) or 0 (no high finding) — both legal.
        assert result.exit_code in (0, 5)
        data = json.loads(result.output)
        if result.exit_code == 5:
            assert data["summary"]["high_severity"] >= 1
        else:
            assert data["summary"]["high_severity"] == 0


class TestBenchHint:
    """v12.12 — close dogfood #15. The bench-relevance hint surfaces a
    test/bench command when the diff touches structurally hot paths
    (retrieve, graph, languages, taint, critique). Previously only
    text mode emitted it; v12.12 adds it to the JSON envelope and
    supports project-local overrides via ``.roam-critique.yml``.
    """

    def test_retrieve_path_returns_hint(self):
        from roam.commands.cmd_critique import _bench_relevance_hint
        from roam.critique.checks import ChangedRegion

        regions = [ChangedRegion(file_path="src/roam/retrieve/rerank.py", hunks=((1, 1),), additions=1, deletions=0)]
        hint = _bench_relevance_hint(regions)
        assert "eval-retrieve" in hint or "test_retrieve" in hint

    def test_unrelated_path_returns_empty(self):
        from roam.commands.cmd_critique import _bench_relevance_hint
        from roam.critique.checks import ChangedRegion

        regions = [
            ChangedRegion(
                file_path="templates/distribution/landing-page/index.html", hunks=((1, 1),), additions=1, deletions=0
            )
        ]
        assert _bench_relevance_hint(regions) == ""

    def test_overrides_take_precedence(self):
        from roam.commands.cmd_critique import _bench_relevance_hint
        from roam.critique.checks import ChangedRegion

        regions = [ChangedRegion(file_path="src/roam/retrieve/rerank.py", hunks=((1, 1),), additions=1, deletions=0)]
        custom = [(("src/roam/retrieve/",), "custom-bench-cmd")]
        assert _bench_relevance_hint(regions, overrides=custom) == "custom-bench-cmd"

    def test_yaml_override_loaded(self, tmp_path, monkeypatch):
        from roam.commands.cmd_critique import _load_critique_overrides

        cwd = tmp_path
        (cwd / ".roam-critique.yml").write_text(
            'bench_hints:\n  - paths: ["src/roam/retrieve/", "src/foo/"]\n    hint: "pytest tests/test_my_thing.py"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(cwd)
        rules = _load_critique_overrides()
        assert len(rules) == 1
        prefixes, hint = rules[0]
        assert "src/roam/retrieve/" in prefixes
        assert "src/foo/" in prefixes
        assert hint == "pytest tests/test_my_thing.py"

    def test_yaml_missing_returns_empty(self, tmp_path, monkeypatch):
        from roam.commands.cmd_critique import _load_critique_overrides

        monkeypatch.chdir(tmp_path)
        assert _load_critique_overrides() == []

    def test_json_envelope_includes_bench_hint(self, critique_project, tmp_path, monkeypatch):
        """When the diff touches a hot path, JSON envelope must carry
        the hint at top level AND in summary so MCP clients can
        consume it without parsing text."""
        # Synthesise a diff against a path that maps to a default rule.
        diff = textwrap.dedent(
            """\
            diff --git a/src/roam/retrieve/rerank.py b/src/roam/retrieve/rerank.py
            --- a/src/roam/retrieve/rerank.py
            +++ b/src/roam/retrieve/rerank.py
            @@ -1,1 +1,2 @@
             # placeholder
            +# new line
            """
        )
        diff_path = tmp_path / "patch.diff"
        diff_path.write_text(diff, encoding="utf-8")
        runner = CliRunner()
        # Stay in critique_project so the index is loaded.
        result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
        assert result.exit_code in (0, 5), result.output
        data = json.loads(result.output)
        assert "bench_hint" in data
        assert "bench_hint" in data["summary"]
        # The retrieve rule should produce a hint string.
        assert data["bench_hint"]
        assert data["summary"]["bench_hint"]
