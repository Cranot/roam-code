"""Tests for ``roam rules-validate``."""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_rules_validate import (
    ALLOWED_PATTERNS,
    _check_duplicate_ids,
    _validate_glob,
    _validate_rule,
)


def _run(args: list[str]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(cli, args, catch_exceptions=False)
    return result.exit_code, result.output


# ---- _validate_rule unit tests -----------------------------------------------


def test_validate_rule_minimal_valid():
    rule = {
        "id": "no-eval",
        "pattern": "function_call",
        "forbidden_target_glob": "eval",
        "severity": "BLOCK",
        "source_glob": "src/**/*.py",
        "description": "test",
    }
    errors, warnings = _validate_rule(rule, 0)
    assert errors == []
    assert warnings == []


def test_validate_rule_missing_required_fields():
    errors, warnings = _validate_rule({"id": "incomplete"}, 0)
    assert any("pattern" in e for e in errors)
    assert any("forbidden_target_glob" in e for e in errors)


def test_validate_rule_unknown_pattern():
    errors, _ = _validate_rule({"id": "x", "pattern": "macro_eval", "forbidden_target_glob": "y"}, 0)
    assert any("unknown pattern `macro_eval`" in e for e in errors)


def test_validate_rule_unknown_severity():
    errors, _ = _validate_rule(
        {"id": "x", "pattern": "import_from", "forbidden_target_glob": "y", "severity": "BLOK"},
        0,
    )
    assert any("unknown severity `BLOK`" in e for e in errors)


def test_validate_rule_severity_must_be_string():
    errors, _ = _validate_rule(
        {"id": "x", "pattern": "import_from", "forbidden_target_glob": "y", "severity": 42},
        0,
    )
    assert any("must be a string" in e for e in errors)


def test_validate_rule_warns_on_missing_severity():
    _, warnings = _validate_rule({"id": "x", "pattern": "import_from", "forbidden_target_glob": "y"}, 0)
    assert any("severity" in w for w in warnings)


def test_validate_rule_warns_on_unknown_keys():
    _, warnings = _validate_rule(
        {
            "id": "x",
            "pattern": "import_from",
            "forbidden_target_glob": "y",
            "severity": "WARN",
            "description": "d",
            "source_glob": "*",
            "garbage_key": "oops",
        },
        0,
    )
    assert any("garbage_key" in w for w in warnings)


def test_validate_rule_non_dict():
    errors, _ = _validate_rule("not a dict", 3)  # type: ignore[arg-type]
    assert any("not a mapping" in e for e in errors)


# ---- _validate_glob ----------------------------------------------------------


def test_validate_glob_rejects_empty():
    err = _validate_glob("", field="source_glob", rule_id="x")
    assert err and "empty" in err


def test_validate_glob_rejects_non_string():
    err = _validate_glob(42, field="source_glob", rule_id="x")  # type: ignore[arg-type]
    assert err and "must be a string" in err


def test_validate_glob_rejects_unbalanced_brackets():
    err = _validate_glob("foo[abc", field="source_glob", rule_id="x")
    assert err and "unbalanced" in err


def test_validate_glob_accepts_well_formed():
    assert _validate_glob("src/**/*.py", field="source_glob", rule_id="x") is None
    assert _validate_glob("*.{py,ts}", field="source_glob", rule_id="x") is None


# ---- _check_duplicate_ids ----------------------------------------------------


def test_check_duplicate_ids_finds_dups():
    rules = [{"id": "a"}, {"id": "b"}, {"id": "a"}]
    errs = _check_duplicate_ids(rules)
    assert len(errs) == 1
    assert "duplicate rule id `a`" in errs[0]


def test_check_duplicate_ids_no_dups():
    rules = [{"id": "a"}, {"id": "b"}]
    assert _check_duplicate_ids(rules) == []


# ---- CLI integration tests ---------------------------------------------------


def test_cli_against_existing_sample_rules(tmp_path):
    """The shipped sample at templates/examples/.roam-rules.yml validates clean."""
    sample = Path("templates/examples/.roam-rules.yml")
    if not sample.exists():
        return  # repo layout-dependent; skip if not in roam-code itself
    code, out = _run(["rules-validate", str(sample)])
    assert code == 0
    assert "valid" in out


def test_cli_missing_file_returns_zero_without_gate(tmp_path):
    code, out = _run(["rules-validate", str(tmp_path / "nope.yml")])
    assert code == 0
    assert "load failed" in out


def test_cli_missing_file_with_gate_exits_5(tmp_path):
    code, _ = _run(["rules-validate", str(tmp_path / "nope.yml"), "--gate"])
    assert code == 5


def test_cli_invalid_rules_json_envelope(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "rules:\n  - id: a\n    pattern: function_call\n    forbidden_target_glob: eval\n    severity: BLOK\n",
        encoding="utf-8",
    )
    code, out = _run(["--json", "rules-validate", str(bad)])
    assert code == 0  # no --gate
    env = _json.loads(out)
    assert env["summary"]["errors_count"] >= 1
    assert "BLOK" in " ".join(env["errors"])


def test_cli_invalid_rules_with_gate_exits_5(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "rules:\n  - id: a\n    pattern: macro_eval\n    forbidden_target_glob: x\n",
        encoding="utf-8",
    )
    code, _ = _run(["rules-validate", str(bad), "--gate"])
    assert code == 5


def test_cli_strict_treats_warnings_as_failures(tmp_path):
    # Rule with no severity => warning, no errors. Strict + gate => exit 5.
    warn_only = tmp_path / "warn.yml"
    warn_only.write_text(
        "rules:\n  - id: a\n    pattern: import_from\n    forbidden_target_glob: bad.module\n    source_glob: '*.py'\n    description: d\n",
        encoding="utf-8",
    )
    code, _ = _run(["rules-validate", str(warn_only), "--strict", "--gate"])
    assert code == 5


def test_cli_dry_run_against_diff(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "rules:\n  - id: no-eval\n    pattern: function_call\n    forbidden_target_glob: eval\n    severity: BLOCK\n    source_glob: '*.py'\n    description: d\n",
        encoding="utf-8",
    )
    diff = tmp_path / "sample.diff"
    diff.write_text(
        "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -0,0 +1,1 @@\n+result = eval(user_input)\n",
        encoding="utf-8",
    )
    code, out = _run(["rules-validate", str(rules), "--against", str(diff)])
    assert code == 0
    assert "1 match" in out


def test_cli_dry_run_missing_diff_file(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n", encoding="utf-8")
    code, out = _run(["rules-validate", str(rules), "--against", str(tmp_path / "nope.diff")])
    # missing diff is reported as an error in the output
    assert code == 0  # no gate
    assert "diff file not found" in out


def test_allowed_patterns_match_pr_analyze():
    """rules-validate's allow-list MUST match cmd_pr_analyze's pattern matchers
    so we never flag a real pattern as unknown.
    """
    from roam.commands.cmd_pr_analyze import _PATTERN_MATCHERS

    assert set(ALLOWED_PATTERNS) == set(_PATTERN_MATCHERS.keys())


def test_cli_explain_prints_pattern_reference(tmp_path):
    """--explain prints a docs block per pattern, with examples + use cases."""
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    code, out = _run(["rules-validate", str(rules), "--explain"])
    assert code == 0
    assert "Pattern reference:" in out
    assert "import_from" in out
    assert "function_call" in out
    assert "class_inherit" in out
    assert "decorator_use" in out
    # Each pattern should have at least an example line
    assert "from lib.unsafe.crypto" in out  # import_from example
    assert "eval(user_input)" in out  # function_call example
    assert "@deprecated" in out  # decorator_use example


def test_pattern_docs_cover_all_allowed_patterns():
    """Guardrail: pattern docs MUST exist for every allowed pattern."""
    from roam.commands.cmd_rules_validate import _PATTERN_DOCS

    assert set(_PATTERN_DOCS.keys()) == set(ALLOWED_PATTERNS)


# ---- B3: --fix mode ---------------------------------------------------------


def test_apply_safe_fixes_normalises_severity_case():
    from roam.commands.cmd_rules_validate import _apply_safe_fixes

    rules = [{"id": "x", "pattern": "function_call", "forbidden_target_glob": "eval", "severity": "block"}]
    fixed, applied = _apply_safe_fixes(rules)
    assert fixed[0]["severity"] == "BLOCK"
    assert any("severity 'block' → 'BLOCK'" in a for a in applied)


def test_apply_safe_fixes_trims_glob_whitespace():
    from roam.commands.cmd_rules_validate import _apply_safe_fixes

    rules = [
        {
            "id": "x",
            "pattern": "import_from",
            "forbidden_target_glob": "  bad.module  ",
            "source_glob": "  src/**/*.py ",
        }
    ]
    fixed, applied = _apply_safe_fixes(rules)
    assert fixed[0]["forbidden_target_glob"] == "bad.module"
    assert fixed[0]["source_glob"] == "src/**/*.py"
    assert len(applied) >= 2  # both fields trimmed


def test_apply_safe_fixes_skips_real_typos():
    """Misspelled severity (BLOK) is NOT auto-fixed — that's a human-judgment call."""
    from roam.commands.cmd_rules_validate import _apply_safe_fixes

    rules = [{"id": "x", "pattern": "function_call", "forbidden_target_glob": "eval", "severity": "BLOK"}]
    fixed, applied = _apply_safe_fixes(rules)
    # severity BLOK stays — validate will still flag it as an error
    assert fixed[0]["severity"] == "BLOK"
    assert applied == []


def test_apply_safe_fixes_handles_non_dict():
    from roam.commands.cmd_rules_validate import _apply_safe_fixes

    rules = ["not a dict", {"id": "valid", "severity": "warn"}]
    fixed, applied = _apply_safe_fixes(rules)
    assert fixed[0] == "not a dict"  # passed through unchanged
    assert fixed[1]["severity"] == "WARN"
    assert any("severity 'warn'" in a for a in applied)


def test_cli_fix_mode_writes_back_to_file(tmp_path):
    """--fix should rewrite the file with normalised values."""
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "rules:\n  - id: a\n    pattern: function_call\n    forbidden_target_glob: eval\n    severity: block\n",
        encoding="utf-8",
    )
    code, out = _run(["rules-validate", str(rules), "--fix"])
    assert code == 0
    # File should now contain BLOCK (uppercase)
    new_text = rules.read_text(encoding="utf-8")
    assert "BLOCK" in new_text
    assert "Fixes applied" in out


def test_cli_fix_mode_no_op_on_clean_file(tmp_path):
    """--fix on an already-clean file applies no fixes + reports 0."""
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "rules:\n  - id: a\n    description: test\n    pattern: function_call\n    "
        "forbidden_target_glob: eval\n    source_glob: 'src/**'\n    severity: BLOCK\n",
        encoding="utf-8",
    )
    code, out = _run(["--json", "rules-validate", str(rules), "--fix"])
    assert code == 0
    env = _json.loads(out)
    assert env["summary"]["fixes_applied"] == 0
