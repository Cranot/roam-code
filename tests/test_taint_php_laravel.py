"""Tests for the PHP/Laravel taint rule pack.

The rule pack ships 5 YAML files covering the highest-impact attack
classes for real-world Laravel apps: SQL injection (DB::raw &
whereRaw), XSS (echo + Blade unescaped), command injection (exec /
shell_exec), open redirect, and path traversal.

The engine matches rule source/sink names against indexed symbol
names; for end-to-end reach tests we seed an in-memory edges table
with synthetic symbols so we can verify the rule's vocabulary fires
without depending on the live PHP indexer materialising every
external builtin (which it does not yet do).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from roam.security.taint_engine import (
    TaintRule,
    load_rules,
    run_taint,
)
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Helpers — in-memory DB seeded with named symbols + call edges so the
# engine can run BFS without a real index. Mirrors the approach used by
# tests/test_taint_intraprocedural.py for the same engine.
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            language TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            line_start INTEGER,
            kind TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'app/Http/Controllers/FooController.php', 'php')")
    return conn


def _add_symbol(conn, sid: int, name: str, qualified: str | None = None, kind: str = "function") -> None:
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, line_start, kind) "
        "VALUES (?, 1, ?, ?, ?, ?)",
        (sid, name, qualified or name, sid, kind),
    )


def _add_call(conn, src: int, tgt: int) -> None:
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'calls')", (src, tgt))


def _rules_dir() -> Path:
    return repo_root() / "src" / "roam" / "security" / "taint_rules"


# ---------------------------------------------------------------------------
# Rule-pack loading + structural sanity
# ---------------------------------------------------------------------------


class TestRulePackLoads:
    def test_all_five_php_yaml_files_parse(self):
        rules = load_rules(_rules_dir())
        php_rules = {r.rule_id for r in rules if "php" in r.rule_id}
        assert php_rules == {
            "php-laravel-sqli",
            "php-laravel-xss",
            "php-command-injection",
            "php-laravel-open-redirect",
            "php-path-traversal",
        }

    def test_every_php_rule_targets_php_language(self):
        rules = load_rules(_rules_dir())
        php_rules = [r for r in rules if "php" in r.rule_id]
        for r in php_rules:
            assert "php" in r.languages, f"{r.rule_id} missing 'php' language tag"

    def test_every_php_rule_has_sources_sinks_sanitizers(self):
        rules = load_rules(_rules_dir())
        php_rules = [r for r in rules if "php" in r.rule_id]
        for r in php_rules:
            assert r.sources, f"{r.rule_id} has no sources"
            assert r.sinks, f"{r.rule_id} has no sinks"
            assert r.sanitizers, f"{r.rule_id} has no sanitizers"

    def test_severity_and_cwe_set(self):
        rules = load_rules(_rules_dir())
        php_rules = [r for r in rules if "php" in r.rule_id]
        for r in php_rules:
            assert r.severity in ("error", "warning", "note"), r.rule_id
            assert r.cwe.startswith("CWE-"), f"{r.rule_id} CWE missing"

    def test_sqli_pack_filter_matches_php_sqli(self):
        """--rules-pack sqli must include php-laravel-sqli."""
        rules = load_rules(_rules_dir())
        sqli = [r for r in rules if "sqli" in r.rule_id.lower()]
        ids = {r.rule_id for r in sqli}
        assert "php-laravel-sqli" in ids

    def test_xss_pack_filter_matches_php_xss(self):
        rules = load_rules(_rules_dir())
        xss = [r for r in rules if "xss" in r.rule_id.lower()]
        ids = {r.rule_id for r in xss}
        assert "php-laravel-xss" in ids

    def test_command_injection_pack_matches_php_cmd_exec(self):
        rules = load_rules(_rules_dir())
        cmd = [r for r in rules if "command-injection" in r.rule_id.lower()]
        ids = {r.rule_id for r in cmd}
        assert "php-command-injection" in ids


# ---------------------------------------------------------------------------
# End-to-end engine tests — seed a fake index + run the real BFS
# ---------------------------------------------------------------------------


class TestSqliRuleFires:
    """request->input() flowing to DB::raw() is flagged as SQLi."""

    def test_request_input_to_db_raw_caught(self):
        conn = _make_conn()
        # Controller method id=100 calls input (1) and raw (2).
        _add_symbol(conn, 100, "index", "App\\Http\\Controllers\\FooController\\index", kind="method")
        _add_symbol(conn, 1, "input", "Illuminate\\Http\\Request\\input", kind="method")
        _add_symbol(conn, 2, "raw", "Illuminate\\Support\\Facades\\DB\\raw", kind="method")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-laravel-sqli"]
        assert rules, "php-laravel-sqli rule must be present in the pack"
        findings = run_taint(conn, rules)

        assert findings, "request->input() reaching DB::raw() must produce a finding"
        f = findings[0]
        assert f.rule_id == "php-laravel-sqli"
        assert f.cwe == "CWE-89"
        assert not f.sanitizer_in_path

    def test_whereRaw_also_caught_as_sink(self):
        conn = _make_conn()
        _add_symbol(conn, 100, "search", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 2, "whereRaw", kind="method")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-laravel-sqli"]
        findings = run_taint(conn, rules)
        assert findings, "input() -> whereRaw() must fire the SQLi rule"


class TestXssRuleFires:
    def test_request_input_to_echo_caught(self):
        conn = _make_conn()
        _add_symbol(conn, 100, "render", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 2, "echo", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-laravel-xss"]
        findings = run_taint(conn, rules)
        assert findings, "request->input() reaching echo must produce an XSS finding"
        assert findings[0].cwe == "CWE-79"

    def test_blade_unescaped_sink_caught(self):
        """The blade_unescaped sink is the marker for {!! !!} flows."""
        conn = _make_conn()
        _add_symbol(conn, 100, "render", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 2, "blade_unescaped", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-laravel-xss"]
        findings = run_taint(conn, rules)
        assert findings, "blade_unescaped sink must be recognised by the rule"


class TestSanitizerBreaksTaint:
    """A path that visits htmlspecialchars between source and sink is
    marked sanitized — the OpenVEX layer cites
    ``inline_mitigations_already_exist``."""

    def test_htmlspecialchars_marks_xss_path_sanitized(self):
        conn = _make_conn()
        _add_symbol(conn, 100, "render", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 9, "htmlspecialchars", kind="function")
        _add_symbol(conn, 2, "echo", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 9)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-laravel-xss"]
        findings = run_taint(conn, rules)
        assert findings, "the intraprocedural co-call pass must still report this"
        # The XSS finding here must carry sanitizer_in_path=True so
        # downstream attestation maps it to inline_mitigations_already_exist.
        assert any(f.sanitizer_in_path for f in findings), (
            "htmlspecialchars on the path must mark sanitizer_in_path=True"
        )


class TestCommandExecFires:
    def test_request_input_to_exec_caught(self):
        conn = _make_conn()
        _add_symbol(conn, 100, "run", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 2, "exec", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-command-injection"]
        findings = run_taint(conn, rules)
        assert findings, "input() -> exec() must produce a command-injection finding"
        assert findings[0].cwe == "CWE-78"

    def test_escapeshellarg_marks_cmd_path_sanitized(self):
        conn = _make_conn()
        _add_symbol(conn, 100, "run", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 9, "escapeshellarg", kind="function")
        _add_symbol(conn, 2, "shell_exec", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 9)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-command-injection"]
        findings = run_taint(conn, rules)
        assert findings
        assert any(f.sanitizer_in_path for f in findings), (
            "escapeshellarg on the path must mark sanitizer_in_path=True"
        )


class TestPathTraversalFires:
    def test_request_input_to_file_get_contents_caught(self):
        conn = _make_conn()
        _add_symbol(conn, 100, "download", kind="method")
        _add_symbol(conn, 1, "input", kind="method")
        _add_symbol(conn, 2, "file_get_contents", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-path-traversal"]
        findings = run_taint(conn, rules)
        assert findings, "input() -> file_get_contents() must fire the path-traversal rule"
        assert findings[0].cwe == "CWE-22"


class TestRulesIsolatedToPhp:
    """A PHP rule on a JS-only fixture must not produce findings —
    `languages: [php]` is enforced by the engine via the f.language join.
    """

    def test_php_sqli_rule_skips_js_files(self):
        conn = _make_conn()
        # Override the file to be JS instead of PHP
        conn.execute("UPDATE files SET language='javascript' WHERE id=1")
        _add_symbol(conn, 100, "search", kind="function")
        _add_symbol(conn, 1, "input", kind="function")
        _add_symbol(conn, 2, "raw", kind="function")
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        conn.commit()

        rules = [r for r in load_rules(_rules_dir()) if r.rule_id == "php-laravel-sqli"]
        findings = run_taint(conn, rules)
        assert findings == [], "php-laravel-sqli must not fire on JS code"


# ---------------------------------------------------------------------------
# CLI integration — make sure the new rule IDs surface via roam taint
# ---------------------------------------------------------------------------


class TestCliSurfacesNewRules:
    def test_rule_filter_php_matches_all_five(self, tmp_path):
        """Running `roam taint --rule php` filters to the new PHP pack
        without crashing."""
        import json
        import os

        from click.testing import CliRunner

        from roam.cli import cli
        from tests.conftest import make_src_project

        proj = make_src_project(
            tmp_path=tmp_path,
            files={"placeholder.py": "x = 1\n"},
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            idx = runner.invoke(cli, ["index"])
            assert idx.exit_code == 0
            result = runner.invoke(cli, ["--json", "taint", "--rule", "php"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            rule_ids = data.get("rule_ids", [])
            php_ids = [r for r in rule_ids if "php" in r]
            assert len(php_ids) == 5, f"expected 5 PHP rules, got {php_ids}"
        finally:
            os.chdir(old_cwd)
