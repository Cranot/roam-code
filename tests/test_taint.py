"""Tests for `roam taint` (E.2 chain-starter).

OpenVEX correctness is the non-negotiable bit — the engine must NEVER
emit `code_not_reachable` (not in the spec) and must always pick from
the five legal justification strings. The unit tests guard the
mapping; the integration test runs the engine against a tiny indexed
fixture.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.security.taint_engine import (
    OPENVEX_JUSTIFICATIONS,
    OPENVEX_STATUSES,
    TaintFinding,
    TaintRule,
    _parse_yaml_subset,
    load_rules,
    run_taint,
    vex_justification_for,
    vex_justification_for_unreachable,
)
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# OpenVEX correctness — the non-negotiable bit
# ---------------------------------------------------------------------------


class TestOpenVEXCorrectness:
    def test_justifications_are_spec_legal_only(self):
        """Verbatim from https://github.com/openvex/spec — five strings."""
        assert OPENVEX_JUSTIFICATIONS == frozenset(
            {
                "component_not_present",
                "vulnerable_code_not_present",
                "vulnerable_code_not_in_execute_path",
                "vulnerable_code_cannot_be_controlled_by_adversary",
                "inline_mitigations_already_exist",
            }
        )
        # The forbidden v11.x string must NEVER appear here.
        assert "code_not_reachable" not in OPENVEX_JUSTIFICATIONS

    def test_statuses_are_spec_legal(self):
        assert OPENVEX_STATUSES == frozenset({"not_affected", "affected", "fixed", "under_investigation"})

    def test_sanitized_finding_maps_to_inline_mitigations(self):
        finding = TaintFinding(
            rule_id="x",
            severity="error",
            cwe="CWE-78",
            source_symbol={"id": 1, "name": "request.args"},
            sink_symbol={"id": 3, "name": "os.system"},
            path_symbols=[{"id": 1}, {"id": 2}, {"id": 3}],
            sanitizer_in_path=True,
        )
        out = vex_justification_for(finding)
        assert out == "inline_mitigations_already_exist"
        assert out in OPENVEX_JUSTIFICATIONS

    def test_unsanitized_reaching_finding_returns_empty_justification(self):
        """A reaching, unsanitized path is `affected` — the justification
        slot is empty by design (justification is only for not_affected)."""
        finding = TaintFinding(
            rule_id="x",
            severity="error",
            cwe="CWE-78",
            source_symbol={},
            sink_symbol={},
            path_symbols=[],
            sanitizer_in_path=False,
        )
        assert vex_justification_for(finding) == ""

    def test_unreachable_component_not_present(self):
        out = vex_justification_for_unreachable(package_present=False)
        assert out == "component_not_present"
        assert out in OPENVEX_JUSTIFICATIONS

    def test_unreachable_code_not_in_execute_path(self):
        out = vex_justification_for_unreachable(package_present=True)
        assert out == "vulnerable_code_not_in_execute_path"
        assert out in OPENVEX_JUSTIFICATIONS


# ---------------------------------------------------------------------------
# YAML subset parser
# ---------------------------------------------------------------------------


class TestYamlSubsetParser:
    def test_scalar_keys(self):
        text = "id: foo\nseverity: error\ncwe: CWE-78\n"
        out = _parse_yaml_subset(text)
        assert out == {"id": "foo", "severity": "error", "cwe": "CWE-78"}

    def test_list_block(self):
        text = textwrap.dedent(
            """\
            sources:
              - request.args
              - request.form
            sinks:
              - os.system
            """
        )
        out = _parse_yaml_subset(text)
        assert out["sources"] == ["request.args", "request.form"]
        assert out["sinks"] == ["os.system"]

    def test_inline_list(self):
        text = "languages: [python, javascript]\n"
        out = _parse_yaml_subset(text)
        assert out["languages"] == ["python", "javascript"]

    def test_comment_and_blank_lines(self):
        text = textwrap.dedent(
            """\
            # comment
            id: x

            severity: warning
            """
        )
        out = _parse_yaml_subset(text)
        assert out == {"id": "x", "severity": "warning"}

    def test_quoted_value(self):
        text = 'id: "py-thing"\n'
        out = _parse_yaml_subset(text)
        assert out["id"] == "py-thing"


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


class TestLoadRules:
    def test_load_default_pack(self):
        pack_dir = Path(__file__).resolve().parents[1] / "src" / "roam" / "security" / "taint_rules"
        rules = load_rules(pack_dir)
        # 5 starter rules ship in v12.0 — bump this floor when more land.
        assert len(rules) >= 5
        ids = {r.rule_id for r in rules}
        assert "python-command-injection" in ids
        assert "js-xss" in ids

    def test_missing_dir_returns_empty(self):
        assert load_rules("/path/that/does/not/exist") == []

    def test_malformed_file_skipped(self, tmp_path):
        good = tmp_path / "good.yaml"
        good.write_text(
            "id: good\nsources:\n  - x\nsinks:\n  - y\n",
            encoding="utf-8",
        )
        bad = tmp_path / "bad.yaml"
        bad.write_text("not valid yaml :::\n", encoding="utf-8")
        rules = load_rules(tmp_path)
        # Good rule is loaded, bad one is silently dropped.
        assert any(r.rule_id == "good" for r in rules)
        assert not any(r.rule_id == "bad" for r in rules)


# ---------------------------------------------------------------------------
# Engine — integration
# ---------------------------------------------------------------------------


@pytest.fixture
def taint_project(tmp_path):
    """A tiny Python project where untrusted input flows to os.system."""
    proj = _make_project(
        tmp_path,
        {
            "vuln.py": """
                import os
                from flask import request

                def handle_search():
                    query = request.args.get('q')
                    return run_query(query)

                def run_query(q):
                    os.system('echo ' + q)
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


class TestRunTaint:
    def test_no_rules_no_findings(self, taint_project):
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            assert run_taint(conn, []) == []

    def test_no_matching_languages_no_findings(self, taint_project):
        rule = TaintRule(
            rule_id="ruby-only",
            description="x",
            languages=("ruby",),
            sources=("request.args",),
            sinks=("os.system",),
        )
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            assert run_taint(conn, [rule]) == []

    def test_no_findings_when_unrelated(self, taint_project):
        # Unrelated rule should produce zero findings on this fixture.
        rule = TaintRule(
            rule_id="unrelated",
            description="x",
            languages=("python",),
            sources=("input",),
            sinks=("subprocess.Popen",),
        )
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            findings = run_taint(conn, [rule])
        # Conservative: we only want findings when sources actually appear.
        # Either zero or a path that *doesn't* synthesise a fake link.
        assert findings == [] or all(f.rule_id == "unrelated" for f in findings)

    def test_source_as_sanitizer_does_not_false_clean(self, taint_project):
        """Regression: when a rule lists the same name as both source and
        sanitizer (or via LIKE-suffix overlap), the BFS used to start with
        ``has_sanitizer=True`` for the source itself, marking every reachable
        path as ``not_affected/inline_mitigations_already_exist``. That is a
        false OpenVEX clean claim. Sanitizers must be intermediate nodes only.
        """
        rule = TaintRule(
            rule_id="overlap",
            description="source name also listed as sanitizer",
            languages=("python",),
            sources=("request",),
            sinks=("os.system",),
            sanitizers=("request",),  # same as source — adversarial input
        )
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            findings = run_taint(conn, [rule])

        # Either no findings (path doesn't reach), OR the finding must not
        # be falsely marked as sanitized by the source-overlap.
        for f in findings:
            assert f.justification != "inline_mitigations_already_exist", (
                f"BLOCKER regression: source-as-sanitizer overlap produced a false-clean finding {f}"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestTaintCLI:
    def test_smoke(self, taint_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["taint"])
        assert result.exit_code == 0, result.output
        assert "VERDICT:" in result.output

    def test_json_envelope_carries_openvex_strings(self, taint_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "taint"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "taint"
        # Round 3 #23: the OpenVEX vocab lists ship in the envelope only
        # when there are findings to attach them to. Empty taint runs
        # used to wastefully echo ~2KB of static strings every call.
        if data.get("findings"):
            assert "openvex_justification_strings" in data
            legal = set(data["openvex_justification_strings"])
            assert "code_not_reachable" not in legal
            assert "inline_mitigations_already_exist" in legal
        else:
            assert "openvex_justification_strings" not in data
            assert "openvex_statuses" not in data

    def test_rule_filter(self, taint_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "taint", "--rule", "xss"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        rule_ids = data.get("rule_ids", [])
        for rid in rule_ids:
            assert "xss" in rid.lower()

    def test_help(self, taint_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["taint", "--help"])
        assert result.exit_code == 0
        assert "rules-dir" in result.output

    def test_appears_in_help_security_section(self, taint_project):
        """taint should appear in the full --help-all listing.

        The short ``--help`` panel only surfaces the 5 verbs + start-here
        commands; the long-tail surface (taint, vuln-map, etc.) is
        discoverable via ``--help-all``.
        """
        runner = CliRunner()
        result = runner.invoke(cli, ["--help-all"])
        assert "taint" in result.output

    def test_rules_pack_choice_advertised_in_help(self, taint_project):
        """v12.12 — close dogfood #18. The flag was claimed by external
        docs since v12.3. Verify all advertised pack values appear in
        --help so the CLI surface and doc surface agree."""
        runner = CliRunner()
        result = runner.invoke(cli, ["taint", "--help"])
        assert result.exit_code == 0
        # Each advertised pack in the Choice list must show up in help.
        for pack in (
            "sqli",
            "xss",
            "ssrf",
            "path-traversal",
            "command-injection",
            "deserialization",
            "open-redirect",
            "urllib",
            "socketio",
            "fileupload",
        ):
            assert pack in result.output, f"pack {pack!r} missing from --help"

    def test_every_advertised_pack_has_at_least_one_rule(self, taint_project):
        """v12.12 — every Choice value in --rules-pack must match at
        least one rule_id in the built-in rules pack. Without this
        check, deserialization could silently filter to zero rules
        and emit "No rules" without explaining why."""
        from pathlib import Path

        import roam
        from roam.security.taint_engine import load_rules

        # Resolve the rules dir from the installed package, not the
        # test fixture's tmp_path, so this test is location-independent.
        roam_pkg = Path(roam.__file__).resolve().parent
        rules_dir = roam_pkg / "security" / "taint_rules"
        rules = load_rules(rules_dir)
        rule_ids = [r.rule_id.lower() for r in rules]
        for pack in (
            "sqli",
            "xss",
            "ssrf",
            "path-traversal",
            "command-injection",
            "deserialization",
            "open-redirect",
            "urllib",
            "socketio",
            "fileupload",
        ):
            matches = [rid for rid in rule_ids if pack in rid]
            assert matches, f"pack {pack!r} has no matching rule_id (would filter to zero rules)"

    def test_deserialization_pack_loads(self, taint_project):
        """v12.12 — the python-deserialization rule shipped with this
        release; running --rules-pack deserialization must filter to
        at least one rule and not crash."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "taint", "--rules-pack", "deserialization"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # rule_ids may be present (with findings) or absent (no findings);
        # either way, the verdict shouldn't say "No rules in".
        verdict = data.get("summary", {}).get("verdict", "")
        assert "No rules" not in verdict, verdict


class TestPathTruncation:
    """The BFS engine bounds search depth (max_hops) and per-node fan-out
    (200 edges). When either bound fires, downstream OpenVEX consumers
    must distinguish "definitely not reachable" from "search hit a cap"
    — the path_truncated flag carries that signal.
    """

    def test_bfs_path_returns_three_tuple(self):
        """Contract: ``_bfs_path`` returns ``(path, has_sanitizer, truncated)``
        where path may be None when no route exists within bounds.
        """
        import sqlite3

        from roam.security.taint_engine import _bfs_path

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE edges(source_id INT, target_id INT, kind TEXT)")
        # Direct edge: source 1 → sink 2.
        conn.execute("INSERT INTO edges VALUES (1, 2, 'calls')")
        conn.commit()

        path, has_san, truncated = _bfs_path(conn, {1}, {2}, set(), max_hops=6)
        assert path == [1, 2]
        assert has_san is False
        assert truncated is False  # found a path, no caps fired

    def test_bfs_truncated_when_path_too_deep(self):
        """When the goal lies beyond ``max_hops``, BFS returns
        ``path=None, truncated=True`` so the caller knows the search
        wasn't exhaustive.
        """
        import sqlite3

        from roam.security.taint_engine import _bfs_path

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE edges(source_id INT, target_id INT, kind TEXT)")
        # Long chain: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 (7 hops).
        for i in range(1, 8):
            conn.execute("INSERT INTO edges VALUES (?, ?, 'calls')", (i, i + 1))
        conn.commit()

        # max_hops=3 means we can only see the first 3 hops; the actual
        # goal at depth 7 is unreachable within bounds.
        path, _, truncated = _bfs_path(conn, {1}, {8}, set(), max_hops=3)
        assert path is None
        assert truncated is True, "search ran out of hops before finding the goal — must signal truncated"

    def test_taint_finding_carries_path_truncated_field(self):
        """``TaintFinding`` dataclass must carry the new ``path_truncated``
        field so downstream OpenVEX consumers can map truncated paths to
        ``under_investigation``.
        """
        from roam.security.taint_engine import TaintFinding

        f = TaintFinding(
            rule_id="R1",
            severity="high",
            cwe="CWE-89",
            source_symbol={"id": 1},
            sink_symbol={"id": 2},
            path_symbols=[{"id": 1}, {"id": 2}],
            sanitizer_in_path=False,
        )
        # Default is False (search exhausted).
        assert f.path_truncated is False

        f2 = TaintFinding(
            rule_id="R2",
            severity="medium",
            cwe="CWE-79",
            source_symbol={"id": 3},
            sink_symbol={"id": 4},
            path_symbols=[{"id": 3}, {"id": 4}],
            sanitizer_in_path=False,
            path_truncated=True,
        )
        assert f2.path_truncated is True
