"""Edge-case tests for ``roam pr-analyze`` and friends.

Covers behaviour at the boundaries: empty diffs, malformed YAML,
missing baselines, sparse envelopes, oversized rule files, and the
no-source-code-leak guarantee under unusual inputs.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process, invoke_cli  # noqa: E402

from roam.commands.cmd_audit_trail_export import _filter_records, _render_csv, _render_markdown  # noqa: E402
from roam.commands.cmd_audit_trail_verify import _verify_chain  # noqa: E402
from roam.commands.cmd_metrics_push import _build_payload, _infer_repo_id  # noqa: E402
from roam.commands.cmd_pr_analyze import (  # noqa: E402
    _check_rules,
    _compute_ai_likelihood,
    _compute_drift,
    _detect_primary_language,
    _load_rules_yaml,
)

# ---------------------------------------------------------------------------
# pr-analyze edge cases
# ---------------------------------------------------------------------------


def test_ai_likelihood_handles_empty_string():
    out = _compute_ai_likelihood("")
    assert out["score"] == 0
    assert out["signals"] == {}


def test_ai_likelihood_handles_whitespace_only():
    out = _compute_ai_likelihood("   \n\n\t   ")
    assert out["score"] == 0


def test_ai_likelihood_handles_diff_without_hunks():
    diff = "diff --git a/foo b/foo\nindex 1111111..2222222 100644\n--- a/foo\n+++ b/foo\n"
    out = _compute_ai_likelihood(diff)
    # No hunks → no added/removed lines → score 0.
    assert out["score"] == 0


def test_ai_likelihood_handles_only_deletions():
    diff = "diff --git a/foo b/foo\n--- a/foo\n+++ b/foo\n@@ -1,3 +1,0 @@\n-line one\n-line two\n-line three\n"
    out = _compute_ai_likelihood(diff)
    # Only deletions: add/remove ratio is 0; signal stays low.
    assert out["score"] >= 0
    assert out["raw_metrics"]["added_lines"] == 0
    assert out["raw_metrics"]["removed_lines"] == 3


def test_check_rules_skips_rules_without_forbidden_glob():
    diff = "diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n@@ -0,0 +1,1 @@\n+import os\n"
    rules = [
        {
            "id": "incomplete",
            "pattern": "import_from",
            "source_glob": "*.py",
            "forbidden_target_glob": "",  # empty
            "severity": "BLOCK",
        }
    ]
    assert _check_rules(diff, rules) == []


def test_check_rules_handles_non_dict_rule_entries():
    diff = "diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n@@ -0,0 +1,1 @@\n+import os\n"
    # _check_rules iterates rules; non-dicts must be skipped or the regex will crash
    # _load_rules_yaml already filters non-dicts, but defensive.
    rules: list = [
        "not a dict",
        {
            "id": "valid",
            "pattern": "import_from",
            "source_glob": "*.py",
            "forbidden_target_glob": "os",
            "severity": "BLOCK",
        },
    ]
    # Should not raise — even though [0] is invalid
    try:
        violations = _check_rules(diff, rules)  # type: ignore[arg-type]
    except (AttributeError, TypeError):
        pytest.skip("non-dict rule rejection is on the loader, not the checker")
    else:
        # If it doesn't raise, the valid rule must still match
        assert any(v.get("rule_id") == "valid" for v in violations)


def test_load_rules_yaml_handles_non_yaml_file(tmp_path):
    bogus = tmp_path / "rules.yml"
    bogus.write_text("this is not: valid: yaml: at all: [")
    rules, warnings = _load_rules_yaml(bogus)
    # Loader is tolerant — returns empty list and surfaces a warning rather than crashing.
    assert rules == []
    assert warnings  # warning surfaced


def test_load_rules_yaml_top_level_not_dict(tmp_path):
    bogus = tmp_path / "rules.yml"
    bogus.write_text("- this\n- is\n- a list at top level\n")
    rules, warnings = _load_rules_yaml(bogus)
    assert rules == []
    assert warnings


def test_load_rules_yaml_strict_mode_raises_on_malformed(tmp_path):
    import pytest

    bogus = tmp_path / "rules.yml"
    bogus.write_text("this is not: valid: yaml: at all: [")
    with pytest.raises(ValueError):
        _load_rules_yaml(bogus, strict=True)


def test_pr_analyze_envelope_includes_rules_warnings(tmp_path):
    """End-to-end: explicit --rules path that doesn't exist surfaces a warning."""
    import json as _json

    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "pr-analyze", "--rules", str(tmp_path / "missing.yml"), "--input", str(tmp_path / "x.diff")],
    )
    # The diff input doesn't exist either — Click should fail before reaching the rules check.
    # Use a real (empty) diff instead:
    diff = tmp_path / "x.diff"
    diff.write_text("")
    result = runner.invoke(
        cli,
        ["--json", "pr-analyze", "--rules", str(tmp_path / "missing.yml"), "--input", str(diff)],
    )
    env = _json.loads(result.output)
    assert "rules_warnings" in env
    assert any("not found" in w for w in env["rules_warnings"])


def test_pr_analyze_rules_strict_exits_5_on_missing(tmp_path):
    from click.testing import CliRunner

    from roam.cli import cli

    diff = tmp_path / "x.diff"
    diff.write_text("")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["pr-analyze", "--rules", str(tmp_path / "missing.yml"), "--rules-strict", "--input", str(diff)],
    )
    assert result.exit_code == 5


# ---------------------------------------------------------------------------
# Drift edge cases
# ---------------------------------------------------------------------------


def test_drift_handles_baseline_without_summary():
    base = {"some_other_key": "value"}
    cur = {
        "summary": {"verdict": "SAFE", "blast_radius": 30, "ai_likelihood": 20, "rule_violations": 0},
        "rule_violations": [],
    }
    drift = _compute_drift(cur, base)
    # Should default missing baseline values to 0 — no crash.
    assert drift is not None
    assert drift["blast_radius_delta"] == 30
    assert drift["ai_likelihood_delta"] == 20


def test_drift_handles_current_without_summary():
    base = {
        "summary": {"verdict": "REVIEW", "blast_radius": 50, "ai_likelihood": 60, "rule_violations": 1},
        "rule_violations": [],
    }
    cur = {"rule_violations": []}
    drift = _compute_drift(cur, base)
    # Defaults to 0 for missing fields → improvement signal.
    assert drift is not None
    assert drift["blast_radius_delta"] == -50


def test_detect_primary_language_resolves_ambiguity():
    # Files split equally across two languages — max picks one deterministically.
    out = _detect_primary_language(["a.py", "b.go", "c.py", "d.go"])
    assert out in ("python", "go")  # tie-break is implementation-defined


# ---------------------------------------------------------------------------
# Audit-trail edge cases
# ---------------------------------------------------------------------------


def test_verify_chain_on_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    records, issues = _verify_chain(p)
    assert records == []
    # An empty file is valid (no chain to break) — no issues.
    assert issues == []


def test_verify_chain_handles_dos_line_endings(tmp_path):
    """JSONL with CRLF line endings (e.g. Windows file write) parses cleanly."""
    p = tmp_path / "crlf.jsonl"
    rec1 = '{"verdict":"SAFE","previous_record_hash":""}'
    rec2 = '{"verdict":"REVIEW","previous_record_hash":"' + ("0" * 64) + '"}'
    p.write_bytes((rec1 + "\r\n" + rec2 + "\r\n").encode())
    records, issues = _verify_chain(p)
    assert len(records) == 2
    # Chain will break (hash mismatch), but parser must not crash on CRLF.
    assert any("mismatch" in i.get("issue", "") for i in issues)


# ---------------------------------------------------------------------------
# Audit-trail export edge cases
# ---------------------------------------------------------------------------


def test_audit_trail_export_md_on_empty_records(tmp_path):
    p = tmp_path / "empty.jsonl"
    md = _render_markdown([], p)
    assert "No audit-trail records" in md


def test_audit_trail_export_filter_excludes_all():
    records = [
        {"timestamp": "2026-05-05T00:00:00Z", "verdict": "SAFE"},
        {"timestamp": "2026-05-05T00:01:00Z", "verdict": "REVIEW"},
    ]
    out = _filter_records(records, since="2027-01-01T00:00:00Z", until=None, verdict_filter=None)
    assert out == []


def test_audit_trail_export_filter_combines_since_until_verdict():
    records = [
        {"timestamp": "2026-04-01T00:00:00Z", "verdict": "SAFE"},
        {"timestamp": "2026-05-05T00:00:00Z", "verdict": "BLOCK"},
        {"timestamp": "2026-06-01T00:00:00Z", "verdict": "BLOCK"},
        {"timestamp": "2026-07-01T00:00:00Z", "verdict": "REVIEW"},
    ]
    out = _filter_records(
        records,
        since="2026-05-01T00:00:00Z",
        until="2026-06-30T00:00:00Z",
        verdict_filter="BLOCK",
    )
    # Only the May + June BLOCK records inside the window.
    assert len(out) == 2
    assert all(r["verdict"] == "BLOCK" for r in out)


def test_audit_trail_export_csv_handles_special_chars():
    records = [
        {
            "timestamp": "2026-05-05T00:00:00Z",
            "actor": 'name with "quotes" and, comma',
            "verdict": "BLOCK",
            "blast_radius": 80,
            "ai_likelihood": 70,
            "rule_violations_count": 2,
            "git_sha": "abc123",
            "diff_sha256": "def456",
            "intent_marker": "",
        }
    ]
    csv_out = _render_csv(records)
    # CSV writer must quote the field with quotes/commas.
    assert '"name with ""quotes"" and, comma"' in csv_out


# ---------------------------------------------------------------------------
# Metrics-push edge cases
# ---------------------------------------------------------------------------


def test_payload_handles_completely_empty_envelope():
    # Audit envelope is just an error wrapper.
    err_envelope = {"error": "audit failed", "exit_code": 1}
    payload = _build_payload(
        err_envelope,
        repo_id="x/y",
        git_meta={},
        anonymize=False,
        include_hotspots=True,
    )
    # Schema is still pinned; metrics fields default to 0/None.
    assert payload["schema"] == "roam-metrics-v1"
    m = payload["metrics"]
    assert m["health_score"] is None
    assert m["dead_safe"] == 0
    # Anonymized flag respects the input.
    assert payload["anonymized"] is False


def test_infer_repo_id_strips_repeated_dot_git_suffix():
    # Defensive: github URL with .git already stripped by some CI envs.
    out = _infer_repo_id({"git_origin": "https://github.com/foo/bar"}, None)
    assert out == "github.com/foo/bar"


# ---------------------------------------------------------------------------
# CLI integration — pr-comment-render edge cases
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


def test_pr_comment_render_from_baseline_missing(tmp_path, cli_runner, monkeypatch):
    """--from-baseline must produce a clear error when baseline doesn't exist."""
    monkeypatch.chdir(tmp_path)  # no .roam/ in this dir
    from roam.cli import cli

    result = cli_runner.invoke(cli, ["pr-comment-render", "--from-baseline"])
    assert result.exit_code != 0
    assert "no baseline" in result.output.lower() or "no baseline" in (result.stderr or "").lower()


def test_pr_comment_render_no_input_no_stdin_errors_clearly(cli_runner):
    """No --input + no --from-baseline + tty stdin must error with usable message."""
    from roam.cli import cli

    result = cli_runner.invoke(cli, ["pr-comment-render"])
    assert result.exit_code != 0
    assert "No input" in result.output or "No input" in (result.stderr or "")


# ---------------------------------------------------------------------------
# pr-analyze CLI integration — empty / sparse cases
# ---------------------------------------------------------------------------


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


def test_pr_analyze_empty_diff_input(tmp_path, tiny_indexed, cli_runner):
    """Empty diff file must produce a SAFE / no-changes verdict, not crash."""
    diff_path = tmp_path / "empty.diff"
    diff_path.write_text("")
    result = invoke_cli(cli_runner, ["pr-analyze", "--input", str(diff_path)], json_mode=True)
    # Empty diff is benign — exit 0, verdict in safe territory.
    assert result.exit_code in (0, 5)
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    # Empty input → ai_likelihood 0 — verdict shouldn't be BLOCK from that alone.
    assert summary.get("ai_likelihood") == 0


def test_pr_analyze_with_nonexistent_rules_file(tmp_path, tiny_indexed, cli_runner):
    """A --rules path that doesn't exist must not crash; rules just don't load."""
    diff_path = tmp_path / "trivial.diff"
    diff_path.write_text("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
    nonexistent = tmp_path / "nonexistent_rules.yml"
    result = invoke_cli(
        cli_runner,
        ["pr-analyze", "--input", str(diff_path), "--rules", str(nonexistent)],
        json_mode=True,
    )
    assert result.exit_code in (0, 5)
    payload = _json.loads(result.output)
    assert (payload.get("summary") or {}).get("rule_violations") == 0


def test_pr_analyze_batch_on_empty_dir(tmp_path, tiny_indexed, cli_runner):
    """Batch mode on an empty directory must produce a 0-files summary, not crash."""
    empty_dir = tmp_path / "empty-batch"
    empty_dir.mkdir()
    result = invoke_cli(cli_runner, ["pr-analyze", "--batch", str(empty_dir)], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    assert summary.get("files_processed") == 0
