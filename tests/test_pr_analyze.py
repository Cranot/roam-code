"""Tests for ``roam pr-analyze`` — the Roam Agent Review CLI engine.

Most tests target the pure helper functions (``_compute_ai_likelihood``,
``_check_rules``, ``_determine_verdict``, ``_load_rules_yaml``) so they
run fast and don't require an indexed project. One end-to-end test
exercises the full CLI against a tiny indexed fixture to confirm the
pr-prep aggregation actually wires up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

from roam.commands.cmd_pr_analyze import (  # noqa: E402
    EXIT_GATE_BLOCK,
    _check_rules,
    _compute_ai_likelihood,
    _compute_drift,
    _detect_primary_language,
    _determine_verdict,
    _emit_audit_trail_record,
    _load_baseline,
    _load_rules_yaml,
    _save_baseline,
)

# ---------------------------------------------------------------------------
# Diff fixtures
# ---------------------------------------------------------------------------


_TRIVIAL_DIFF = """\
diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 def hello():
-    return 'hi'
+    return 'hello'
"""


_AI_SHAPED_DIFF = """\
diff --git a/src/utils/helper.py b/src/utils/helper.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/src/utils/helper.py
@@ -0,0 +1,40 @@
+# Comprehensive helper module for various operations.
+# Each function is designed to be reusable across the codebase.
+
+import os
+import sys
+import threading
+import collections
+import functools
+
+
+# Handle the user input by validating it.
+def handle_user_input(data):
+    # Validate input
+    if not data:
+        return None
+    return data
+
+
+# Process the request.
+def process_request(request):
+    # Check request
+    if not request:
+        return False
+    return True
+
+
+# Manage connection.
+def manage_connection(conn):
+    # Manage lifecycle
+    if conn is None:
+        return None
+    return conn
+
+
+# Execute the operation.
+def execute_operation(op):
+    # Execute safely
+    return op()
"""


_DIFF_WITH_TESTS = """\
diff --git a/src/foo.py b/src/foo.py
index 1111111..2222222 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,5 @@
 def add(a, b):
     return a + b
+
+def subtract(a, b):
+    return a - b
diff --git a/tests/test_foo.py b/tests/test_foo.py
index 1111111..2222222 100644
--- a/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -1,3 +1,6 @@
 from foo import add
 def test_add():
     assert add(1, 2) == 3
+
+def test_subtract():
+    assert subtract(3, 1) == 2
"""


# ---------------------------------------------------------------------------
# AI-likelihood scorer
# ---------------------------------------------------------------------------


def test_ai_likelihood_empty_diff_returns_zero():
    result = _compute_ai_likelihood("")
    assert result["score"] == 0
    assert result["signals"] == {}


def test_ai_likelihood_trivial_diff_scores_low():
    result = _compute_ai_likelihood(_TRIVIAL_DIFF)
    assert result["score"] < 50, f"trivial diff scored {result['score']}, expected <50"


def test_ai_likelihood_ai_shaped_diff_scores_high():
    result = _compute_ai_likelihood(_AI_SHAPED_DIFF)
    assert result["score"] >= 50, f"AI-shaped diff scored {result['score']}, expected >=50"
    # Generic-naming signal must fire — every function uses a generic prefix.
    assert result["signals"]["generic_naming"] >= 50
    # Test-coverage signal must fire — no test files modified.
    assert result["signals"]["test_coverage"] >= 50
    # Add/remove ratio must be high — pure addition, no removals.
    assert result["signals"]["add_remove_ratio"] >= 50


def test_ai_likelihood_diff_with_tests_scores_lower_than_no_tests():
    no_tests = _compute_ai_likelihood(_AI_SHAPED_DIFF)
    with_tests = _compute_ai_likelihood(_DIFF_WITH_TESTS)
    assert with_tests["signals"]["test_coverage"] < no_tests["signals"]["test_coverage"], (
        "diff that modifies test files should score lower on the test-coverage AI signal"
    )


def test_ai_likelihood_raw_metrics_present():
    result = _compute_ai_likelihood(_AI_SHAPED_DIFF)
    raw = result["raw_metrics"]
    assert raw["added_lines"] > 0
    assert raw["files_touched"] == 1
    assert raw["test_files"] == 0
    assert raw["new_functions"] >= 4  # 4 generic-named functions in the fixture


# ---------------------------------------------------------------------------
# Rule loading + enforcement
# ---------------------------------------------------------------------------


def test_load_rules_yaml_missing_file(tmp_path):
    rules, warnings = _load_rules_yaml(tmp_path / "nonexistent.yml")
    assert rules == []
    assert warnings and "not found" in warnings[0]


def test_load_rules_yaml_simple(tmp_path):
    rules_file = tmp_path / "rules.yml"
    rules_file.write_text(
        "rules:\n"
        "  - id: no-os-imports\n"
        "    description: Banned os import\n"
        "    pattern: import_from\n"
        "    source_glob: src/**\n"
        "    forbidden_target_glob: os\n"
        "    severity: BLOCK\n"
    )
    rules, warnings = _load_rules_yaml(rules_file)
    assert len(rules) == 1
    assert rules[0]["id"] == "no-os-imports"
    assert rules[0]["severity"] == "BLOCK"
    assert warnings == []


def test_load_rules_yaml_strict_raises_on_missing(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="not found"):
        _load_rules_yaml(tmp_path / "nope.yml", strict=True)


def test_load_rules_yaml_coerces_non_string_severity(tmp_path):
    rules_file = tmp_path / "rules.yml"
    rules_file.write_text(
        "rules:\n  - id: bad-sev\n    pattern: import_from\n    forbidden_target_glob: bad\n    severity: 42\n",
        encoding="utf-8",
    )
    rules, warnings = _load_rules_yaml(rules_file)
    assert len(rules) == 1
    assert rules[0]["severity"] == "42"  # coerced to string
    assert any("non-string severity" in w for w in warnings)


def test_load_rules_yaml_skips_non_string_glob(tmp_path):
    rules_file = tmp_path / "rules.yml"
    rules_file.write_text(
        "rules:\n  - id: bad-glob\n    pattern: import_from\n    forbidden_target_glob: 42\n",
        encoding="utf-8",
    )
    rules, warnings = _load_rules_yaml(rules_file)
    assert rules == []  # broken rule filtered out
    assert any("non-string forbidden_target_glob" in w for w in warnings)


def test_check_rules_no_match():
    rules = [
        {
            "id": "no-os",
            "pattern": "import_from",
            "source_glob": "src/**",
            "forbidden_target_glob": "os",
            "severity": "BLOCK",
        }
    ]
    violations = _check_rules(_TRIVIAL_DIFF, rules)
    assert violations == []


def test_check_rules_match_python_import():
    rules = [
        {
            "id": "no-threading-utils",
            "pattern": "import_from",
            "source_glob": "src/utils/*",
            "forbidden_target_glob": "threading",
            "severity": "WARN",
        }
    ]
    violations = _check_rules(_AI_SHAPED_DIFF, rules)
    assert len(violations) == 1
    v = violations[0]
    assert v["rule_id"] == "no-threading-utils"
    assert v["severity"] == "WARN"
    assert v["matched_import"] == "threading"
    assert "src/utils/helper.py" in v["file"]


def test_check_rules_unsupported_pattern_skipped():
    """Patterns we haven't implemented yet must not crash."""
    rules = [
        {
            "id": "future-pattern",
            "pattern": "lambda_use",  # genuinely not supported
            "source_glob": "src/**",
            "forbidden_target_glob": "x",
            "severity": "BLOCK",
        }
    ]
    violations = _check_rules(_AI_SHAPED_DIFF, rules)
    assert violations == []


def test_check_rules_function_call_matches_eval():
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def run(code):\n"
        "+    return eval(code)\n"
    )
    rules = [
        {
            "id": "no-eval",
            "pattern": "function_call",
            "source_glob": "src/**",
            "forbidden_target_glob": "eval",
            "severity": "BLOCK",
        }
    ]
    violations = _check_rules(diff, rules)
    assert len(violations) == 1
    assert violations[0]["matched_target"] == "eval"


def test_check_rules_class_inherit_matches_dangerous_base():
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+class MyService(DangerousBase, metaclass=Meta):\n"
    )
    rules = [
        {
            "id": "no-dangerous-base",
            "pattern": "class_inherit",
            "source_glob": "src/**",
            "forbidden_target_glob": "DangerousBase",
            "severity": "BLOCK",
        }
    ]
    violations = _check_rules(diff, rules)
    assert len(violations) == 1
    assert violations[0]["matched_target"] == "DangerousBase"


def test_check_rules_decorator_use_matches_deprecated():
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+@deprecated\n"
        "+def foo(): pass\n"
    )
    rules = [
        {
            "id": "no-deprecated",
            "pattern": "decorator_use",
            "source_glob": "src/**",
            "forbidden_target_glob": "deprecated",
            "severity": "WARN",
        }
    ]
    violations = _check_rules(diff, rules)
    assert len(violations) == 1
    assert violations[0]["matched_target"] == "deprecated"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def test_detect_primary_language_python():
    assert _detect_primary_language(["src/foo.py", "src/bar.py", "tests/test_foo.py"]) == "python"


def test_detect_primary_language_typescript_over_javascript():
    paths = ["app/page.tsx", "app/foo.ts", "lib/util.js"]
    # Two TS files vs one JS — TS should win
    assert _detect_primary_language(paths) == "typescript"


def test_detect_primary_language_returns_none_on_unknown():
    assert _detect_primary_language(["README.md", "config.toml", "foo.unknown"]) is None


def test_detect_primary_language_empty_list():
    assert _detect_primary_language([]) is None


def test_ai_likelihood_uses_python_weights_when_python_files():
    py_diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- /dev/null\n"
        "+++ b/src/foo.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def handle_x():\n"
        "+    return None\n"
    )
    result = _compute_ai_likelihood(py_diff)
    # Python weight set: comment_density is the dominant weight for the Python lane.
    # Exact value rebalanced 2026-05-06 when 3 v2 signals were added; check it's
    # the highest-weighted signal among the original six, not a hard-coded number.
    py_weights = result["weights"]
    assert result["primary_language"] == "python"
    original_six = {
        "add_remove_ratio",
        "comment_density",
        "test_coverage",
        "function_size",
        "generic_naming",
        "orphan_imports",
    }
    six_only = {k: v for k, v in py_weights.items() if k in original_six}
    assert max(six_only, key=six_only.get) == "comment_density"


def test_ai_likelihood_language_override():
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- /dev/null\n"
        "+++ b/src/foo.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def handle_x():\n"
        "+    return None\n"
    )
    result = _compute_ai_likelihood(diff, language_override="typescript")
    # TS weights: orphan_imports is the dominant signal (LLMs auto-import a lot in TS).
    # Exact weight rebalanced when 3 v2 signals added; check dominance not absolute value.
    assert result["primary_language"] == "typescript"
    ts_weights = result["weights"]
    original_six = {
        "add_remove_ratio",
        "comment_density",
        "test_coverage",
        "function_size",
        "generic_naming",
        "orphan_imports",
    }
    six_only = {k: v for k, v in ts_weights.items() if k in original_six}
    assert max(six_only, key=six_only.get) == "orphan_imports"


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


def _mk_envelope(verdict="SAFE", blast=20, ai=15, violations=None):
    return {
        "summary": {
            "verdict": verdict,
            "blast_radius": blast,
            "ai_likelihood": ai,
            "rule_violations": len(violations or []),
        },
        "rule_violations": violations or [],
    }


def test_drift_none_when_no_baseline():
    drift = _compute_drift(_mk_envelope(), None)
    assert drift is None


def test_drift_zero_when_envelopes_match():
    base = _mk_envelope(verdict="SAFE", blast=20, ai=15)
    cur = _mk_envelope(verdict="SAFE", blast=20, ai=15)
    drift = _compute_drift(cur, base)
    assert drift["regression"] is False
    assert drift["improvement"] is False
    assert drift["blast_radius_delta"] == 0
    assert drift["ai_likelihood_delta"] == 0
    assert drift["new_violation_count"] == 0


def test_drift_regression_on_blast_increase():
    base = _mk_envelope(verdict="SAFE", blast=20, ai=15)
    cur = _mk_envelope(verdict="REVIEW", blast=70, ai=15)
    drift = _compute_drift(cur, base)
    assert drift["regression"] is True
    assert drift["blast_radius_delta"] == 50
    assert drift["verdict_changed"] is True
    assert drift["previous_verdict"] == "SAFE"


def test_drift_new_violations_detected():
    v_old = [{"rule_id": "r1", "file": "a.py", "matched_target": "x"}]
    v_new = [
        {"rule_id": "r1", "file": "a.py", "matched_target": "x"},
        {"rule_id": "r2", "file": "b.py", "matched_target": "y"},
    ]
    base = _mk_envelope(violations=v_old)
    cur = _mk_envelope(violations=v_new)
    drift = _compute_drift(cur, base)
    assert drift["new_violation_count"] == 1
    assert drift["resolved_violation_count"] == 0
    assert drift["regression"] is True


def test_drift_resolved_violations_detected():
    v_old = [
        {"rule_id": "r1", "file": "a.py", "matched_target": "x"},
        {"rule_id": "r2", "file": "b.py", "matched_target": "y"},
    ]
    v_new = []
    base = _mk_envelope(violations=v_old, blast=70)
    cur = _mk_envelope(violations=v_new, blast=20)
    drift = _compute_drift(cur, base)
    assert drift["resolved_violation_count"] == 2
    assert drift["new_violation_count"] == 0
    assert drift["improvement"] is True


def test_baseline_save_and_load_roundtrip(tmp_path):
    bundle = _mk_envelope(verdict="REVIEW", blast=42, ai=55)
    bundle["extra"] = {"nested": "value"}
    p = tmp_path / "baseline.json"
    _save_baseline(p, bundle)
    loaded = _load_baseline(p)
    assert loaded["summary"]["verdict"] == "REVIEW"
    assert loaded["extra"]["nested"] == "value"


def test_load_baseline_missing_returns_none(tmp_path):
    assert _load_baseline(tmp_path / "nonexistent.json") is None


def test_load_baseline_corrupt_json_returns_none(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("not valid json {")
    assert _load_baseline(p) is None


# ---------------------------------------------------------------------------
# Audit-trail emission
# ---------------------------------------------------------------------------


def test_audit_trail_record_includes_required_fields(tmp_path):
    trail = tmp_path / "audit-trail.jsonl"
    bundle = {
        "summary": {
            "verdict": "REVIEW",
            "blast_radius": 50,
            "ai_likelihood": 70,
            "rule_violations": 1,
            "high_severity_critique": 0,
        },
        "rationale": {
            "summary_text": "Test rationale.",
            "suggested_reviewers": [{"name": "alice"}],
        },
    }
    record = _emit_audit_trail_record(
        audit_trail_path=trail,
        diff_text="diff --git a/foo b/foo\n+x\n",
        bundle=bundle,
        intent="[intentional] testing",
        reviewers_payload=None,
    )
    assert record["schema"] == "roam-audit-trail-v1"
    assert record["verdict"] == "REVIEW"
    assert record["blast_radius"] == 50
    assert record["intent_marker"] == "[intentional] testing"
    assert "diff_sha256" in record
    assert record["previous_record_hash"] == ""  # genesis
    assert trail.exists()
    assert trail.stat().st_size > 0


def test_audit_trail_chain_links_consecutive_records(tmp_path):
    trail = tmp_path / "audit-trail.jsonl"
    bundle1 = {
        "summary": {"verdict": "SAFE", "blast_radius": 10, "ai_likelihood": 5, "rule_violations": 0},
        "rationale": {"summary_text": "First."},
    }
    bundle2 = {
        "summary": {"verdict": "REVIEW", "blast_radius": 50, "ai_likelihood": 70, "rule_violations": 1},
        "rationale": {"summary_text": "Second."},
    }

    rec1 = _emit_audit_trail_record(
        audit_trail_path=trail,
        diff_text="diff1",
        bundle=bundle1,
        intent=None,
        reviewers_payload=None,
    )
    rec2 = _emit_audit_trail_record(
        audit_trail_path=trail,
        diff_text="diff2",
        bundle=bundle2,
        intent=None,
        reviewers_payload=None,
    )

    assert rec1["previous_record_hash"] == ""  # genesis
    assert rec2["previous_record_hash"] != ""  # links to rec1
    # Verify hash is sha256 of rec1's stable JSON encoding
    import hashlib
    import json as _j

    line1 = _j.dumps(rec1, separators=(",", ":"), sort_keys=True)
    expected_prev = hashlib.sha256(line1.encode()).hexdigest()
    assert rec2["previous_record_hash"] == expected_prev


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------


def test_verdict_intentional_marker_overrides_block():
    verdict, reasons = _determine_verdict(
        blast_radius=99,
        ai_likelihood=95,
        rule_violations=[{"severity": "BLOCK", "rule_id": "x"}],
        high_severity_findings=5,
        intent="[intentional] migrate auth backend",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "INTENTIONAL"
    assert "[intentional]" in reasons[0]


def test_verdict_block_on_block_severity_rule():
    verdict, reasons = _determine_verdict(
        blast_radius=20,
        ai_likelihood=10,
        rule_violations=[{"severity": "BLOCK", "rule_id": "no-os"}],
        high_severity_findings=0,
        intent="",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "BLOCK"
    assert any("BLOCK-severity rule" in r for r in reasons)


def test_verdict_block_on_high_blast_radius():
    verdict, reasons = _determine_verdict(
        blast_radius=90,
        ai_likelihood=10,
        rule_violations=[],
        high_severity_findings=0,
        intent="",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "BLOCK"
    assert any("blast radius" in r for r in reasons)


def test_verdict_block_on_high_ai_plus_high_blast():
    verdict, _ = _determine_verdict(
        blast_radius=70,
        ai_likelihood=92,
        rule_violations=[],
        high_severity_findings=0,
        intent="",
        block_threshold=200,  # disabled blast-radius gate
        pr_prep_error=False,
    )
    assert verdict == "BLOCK"


def test_verdict_review_on_warn_rule():
    verdict, reasons = _determine_verdict(
        blast_radius=10,
        ai_likelihood=10,
        rule_violations=[{"severity": "WARN", "rule_id": "no-threading"}],
        high_severity_findings=0,
        intent="",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "REVIEW"
    assert any("WARN-severity" in r for r in reasons)


def test_verdict_review_on_high_severity_critique():
    verdict, _ = _determine_verdict(
        blast_radius=20,
        ai_likelihood=10,
        rule_violations=[],
        high_severity_findings=3,
        intent="",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "REVIEW"


def test_verdict_review_on_high_ai_alone():
    verdict, _ = _determine_verdict(
        blast_radius=10,
        ai_likelihood=75,
        rule_violations=[],
        high_severity_findings=0,
        intent="",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "REVIEW"


def test_verdict_review_when_pr_prep_failed():
    verdict, reasons = _determine_verdict(
        blast_radius=0,
        ai_likelihood=0,
        rule_violations=[],
        high_severity_findings=0,
        intent="",
        block_threshold=85,
        pr_prep_error=True,
    )
    assert verdict == "REVIEW"
    assert any("pr-prep" in r for r in reasons)


def test_verdict_safe_default():
    verdict, _ = _determine_verdict(
        blast_radius=20,
        ai_likelihood=15,
        rule_violations=[],
        high_severity_findings=0,
        intent="",
        block_threshold=85,
        pr_prep_error=False,
    )
    assert verdict == "SAFE"


# ---------------------------------------------------------------------------
# CLI integration (tiny indexed fixture)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def tiny_indexed(tmp_path, monkeypatch):
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def add(a, b):\n    return a + b\n")
    git_init(proj)
    git_commit(proj, "initial")
    monkeypatch.chdir(proj)
    index_in_process(proj)
    return proj


def test_cli_pr_analyze_help_lists_options(cli_runner):
    result = invoke_cli(cli_runner, ["pr-analyze", "--help"])
    out = result.output
    assert "--input" in out
    assert "--rules" in out
    assert "--gate" in out
    assert "--block-threshold" in out


def test_cli_pr_analyze_with_input_file_returns_safe_for_trivial_diff(tmp_path, tiny_indexed, cli_runner):
    diff_file = tmp_path / "trivial.diff"
    diff_file.write_text(_TRIVIAL_DIFF)
    result = invoke_cli(
        cli_runner,
        ["pr-analyze", "--input", str(diff_file)],
        json_mode=True,
    )
    assert result.exit_code in (0, 5), result.output
    payload = parse_json_output(result)
    summary = payload.get("summary") or {}
    assert summary.get("verdict") in ("SAFE", "REVIEW"), summary
    assert "ai_likelihood" in summary
    assert "blast_radius" in summary


def test_cli_pr_analyze_gate_returns_exit_5_on_block(tmp_path, tiny_indexed, cli_runner):
    diff_file = tmp_path / "ai.diff"
    diff_file.write_text(_AI_SHAPED_DIFF)
    rules_file = tmp_path / "rules.yml"
    rules_file.write_text(
        "rules:\n"
        "  - id: no-os\n"
        "    description: Banned os import\n"
        "    pattern: import_from\n"
        "    source_glob: src/utils/*\n"
        "    forbidden_target_glob: os\n"
        "    severity: BLOCK\n"
    )
    result = invoke_cli(
        cli_runner,
        [
            "pr-analyze",
            "--input",
            str(diff_file),
            "--rules",
            str(rules_file),
            "--gate",
        ],
    )
    assert result.exit_code == EXIT_GATE_BLOCK, (
        f"expected exit {EXIT_GATE_BLOCK}, got {result.exit_code}\n{result.output}"
    )


def test_cli_pr_analyze_intentional_marker_skips_gate(tmp_path, tiny_indexed, cli_runner):
    diff_file = tmp_path / "ai.diff"
    diff_file.write_text(_AI_SHAPED_DIFF)
    rules_file = tmp_path / "rules.yml"
    rules_file.write_text(
        "rules:\n"
        "  - id: no-os\n"
        "    description: Banned os import\n"
        "    pattern: import_from\n"
        "    source_glob: src/utils/*\n"
        "    forbidden_target_glob: os\n"
        "    severity: BLOCK\n"
    )
    result = invoke_cli(
        cli_runner,
        [
            "pr-analyze",
            "--input",
            str(diff_file),
            "--rules",
            str(rules_file),
            "--gate",
            "--intent",
            "[intentional] add helper module",
        ],
    )
    assert result.exit_code == 0, f"intentional marker should bypass gate, got exit {result.exit_code}\n{result.output}"
    assert "INTENTIONAL" in result.output


def test_cli_pr_analyze_quiet_mode_one_line(tmp_path, tiny_indexed, cli_runner):
    """--quiet mode emits a single VERDICT line, no breakdowns."""
    diff_file = tmp_path / "trivial.diff"
    diff_file.write_text(_TRIVIAL_DIFF)
    result = invoke_cli(cli_runner, ["pr-analyze", "--input", str(diff_file), "--quiet"])
    assert result.exit_code == 0
    # Find the VERDICT line — there should be exactly one trailing line of output.
    out_lines = [line for line in result.output.splitlines() if line.startswith("VERDICT:")]
    assert len(out_lines) == 1
    # The single line should include blast / ai / rules counts inline.
    assert "blast" in out_lines[0]
    assert "ai" in out_lines[0]
    assert "rules" in out_lines[0]
    # No breakdown headers in quiet mode.
    assert "blast radius:" not in result.output
    assert "ai-likelihood:" not in result.output


def test_cli_pr_analyze_quiet_help_lists_option(cli_runner):
    result = invoke_cli(cli_runner, ["pr-analyze", "--help"])
    assert "--quiet" in result.output


def test_cli_pr_analyze_batch_help_lists_parallel_and_progress(cli_runner):
    """Batch mode help should advertise the new --parallel + --progress options."""
    result = invoke_cli(cli_runner, ["pr-analyze", "--help"])
    assert "--parallel" in result.output
    assert "--progress" in result.output


def test_cli_pr_analyze_batch_progress_emits_stderr(tmp_path, tiny_indexed, cli_runner):
    """--progress emits per-file lines to stderr."""
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "a.diff").write_text(_TRIVIAL_DIFF)
    (batch / "b.diff").write_text(_TRIVIAL_DIFF)
    result = invoke_cli(
        cli_runner,
        ["pr-analyze", "--batch", str(batch), "--progress"],
        json_mode=True,
    )
    assert result.exit_code == 0
    # CliRunner merges stderr into result.output unless mix_stderr=False, so
    # progress lines should appear somewhere in the combined output.
    assert "[1/2]" in result.output or "[2/2]" in result.output


def test_emit_batch_helper_processes_single_diff_directly(tmp_path):
    """Smoke-test the standalone _process_single_diff helper used by parallel mode."""
    from roam.commands.cmd_pr_analyze import _process_single_diff

    diff = tmp_path / "x.diff"
    diff.write_text(_TRIVIAL_DIFF)
    # Call without an indexed project — the underlying pr-analyze will index
    # in-place and we just want to confirm the helper handles the result shape.
    row = _process_single_diff(str(diff), None, 85, 10, None)
    assert "file" in row
    assert row["file"] == "x.diff"
    # Either successful parse or "error" key — both are valid contracts.
    assert ("verdict" in row) or ("error" in row)
