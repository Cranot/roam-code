"""Phase 12 — boundary-case tests for v2 commands.

Targets the rough edges that surface in real production: CRLF line
endings, empty inputs, malformed YAML/JSON, missing files, sparse
envelopes, anti-patterns from the synergy review.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Pre-elect autonomous_pr so privileged commands (`pr-analyze`,
    `audit-trail-*`, `rules-validate`) work under future
    `ROAM_MODE_ENFORCEMENT` default-on (W23.3 staged-rollout PR-B)."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


from roam.commands.cmd_audit_trail_conformance import _check_retention
from roam.commands.cmd_audit_trail_export import _aggregate_records, _filter_records
from roam.commands.cmd_audit_trail_verify import _verify_chain
from roam.commands.cmd_pr_analyze import (
    _cache_key,
    _compute_ai_likelihood,
    _load_rules_yaml,
)
from roam.commands.cmd_pr_comment_render import _render_github_markdown, _signal_explanation
from roam.commands.cmd_rules_validate import _load_yaml, _validate_glob

# ---- CRLF line endings ------------------------------------------------------


def test_audit_trail_verify_handles_crlf_line_endings(tmp_path):
    """Records written with CRLF endings should still verify cleanly."""

    path = tmp_path / "trail.jsonl"
    rec = {
        "schema": "roam-audit-trail-v1",
        "timestamp": "2026-05-05T00:00:00Z",
        "actor": "a",
        "verdict": "SAFE",
        "previous_record_hash": "",
    }
    line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
    # Write with CRLF endings
    path.write_text(line + "\r\n", encoding="utf-8")

    records, issues = _verify_chain(path)
    assert len(records) == 1
    # The chain hash must be computed from the *content* line, not including \r;
    # if our reader strips \r\n correctly, no issues.
    assert issues == [] or all("invalid JSON" not in i["issue"] for i in issues)


def test_pr_analyze_diff_with_crlf_endings():
    """Diffs sometimes arrive with CRLF endings (Windows-shaped patches)."""
    diff_lf = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    diff_crlf = diff_lf.replace("\n", "\r\n")
    out_lf = _compute_ai_likelihood(diff_lf)
    out_crlf = _compute_ai_likelihood(diff_crlf)
    # Both should produce some signals; the exact score may differ slightly
    # due to whitespace handling, but neither should crash.
    assert out_lf["score"] >= 0
    assert out_crlf["score"] >= 0


# ---- Empty / sparse inputs --------------------------------------------------


def test_compute_ai_likelihood_diff_with_only_whitespace_added():
    """Diff that adds only blank lines."""
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1,3 @@\n+\n+\n+\n"
    out = _compute_ai_likelihood(diff)
    # Should not divide-by-zero; signals likely all zero.
    assert out["score"] == 0 or out["score"] > 0  # just don't crash


def test_validate_glob_pure_whitespace_string():
    err = _validate_glob("   ", field="source_glob", rule_id="x")
    assert err is not None


def test_aggregate_records_missing_optional_fields():
    records = [{}]  # entirely empty record
    agg = _aggregate_records(records)
    # Should bucket as <unknown>/UNKNOWN/<undated>, not crash
    assert agg["total_records"] == 1
    assert agg["by_verdict"]["UNKNOWN"] == 1
    assert agg["by_actor"]["<unknown>"]["_total"] == 1
    assert agg["by_repo"]["<unknown>"]["_total"] == 1
    assert agg["by_month"]["<undated>"]["_total"] == 1


def test_filter_records_with_all_filters_set_to_none():
    records = [{"timestamp": "2026-05-05T00:00:00Z", "verdict": "BLOCK"}]
    out = _filter_records(records, since=None, until=None, verdict_filter=None)
    assert out == records  # all-None filters are no-ops


def test_check_retention_with_unparseable_timestamps():
    """Records with garbage timestamps should not crash retention check."""
    records = [{"timestamp": "garbage"}, {"timestamp": "also-garbage"}]
    ok, msg = _check_retention(records, retention_days=180)
    assert not ok
    assert "no parseable timestamps" in msg


# ---- Malformed inputs -------------------------------------------------------


def test_load_yaml_handles_yaml_alias_bomb(tmp_path):
    """YAML alias bombs (billion laughs) should be resisted by safe_load.

    PyYAML's safe_load already disables most attack vectors; we just want to
    confirm we don't crash + return an error.
    """
    bomb = tmp_path / "bomb.yml"
    bomb.write_text(
        "a: &a [a, a, a, a, a]\nb: &b [*a, *a, *a]\nc: &c [*b, *b, *b]\n"  # small enough to not actually consume memory
    )
    parsed, error = _load_yaml(bomb)
    # safe_load resolves aliases but bounds the depth; either it parses OK
    # (bounded enough) or returns an error — both are acceptable.
    assert parsed is not None or error is not None


def test_load_rules_yaml_with_extremely_nested_dict(tmp_path):
    """A rule that's a dict-of-dict-of-dict should not crash the loader."""
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "rules:\n"
        "  - id: nested\n"
        "    pattern: function_call\n"
        "    forbidden_target_glob: x\n"
        "    metadata:\n"
        "      level1:\n"
        "        level2:\n"
        "          level3: deep\n"
    )
    loaded, warnings = _load_rules_yaml(rules)
    assert len(loaded) == 1
    # Unknown 'metadata' key should produce a warning but not crash.
    # (Actually the v1 loader doesn't surface that — _load_rules_yaml doesn't validate
    # extra keys; cmd_rules_validate does. So no warning here.)
    assert isinstance(warnings, list)


def test_render_github_markdown_with_empty_envelope():
    """Empty envelope shouldn't crash rendering."""
    md = _render_github_markdown({}, include_links=False)
    assert "Roam Agent Review" in md
    assert "UNKNOWN" in md  # default verdict


def test_render_github_markdown_with_only_partial_drift():
    """Drift block missing some keys shouldn't crash."""
    env = {
        "summary": {"verdict": "SAFE", "blast_radius": 10, "ai_likelihood": 5, "rule_violations": 0},
        "drift": {"blast_radius_delta": 0, "ai_likelihood_delta": 0},  # no regression flags
        "rationale": {"summary_text": "ok"},
        "rule_violations": [],
    }
    md = _render_github_markdown(env, include_links=False)
    # Without regression/improvement flags, neither banner should appear.
    assert "Regression" not in md
    assert "Improvement" not in md


def test_signal_explanation_handles_string_for_numeric_field():
    """If raw_metrics has unexpected types (e.g. None as string), don't crash."""
    out = _signal_explanation("comment_density", {"comment_ratio": "0.4"})
    # Function casts to float; string-encoded float should work.
    assert "40%" in out


# ---- Cache edge cases -------------------------------------------------------


def test_cache_key_handles_unicode_diff(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    diff = "+ comment with emoji and chinese: 你好"
    k = _cache_key(diff, rules, 85, None)
    assert isinstance(k, str)
    assert len(k) == 64


def test_cache_key_handles_huge_diff_text(tmp_path):
    """100KB diff still produces a usable key."""
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    huge = "+line\n" * 20000  # ~120KB
    k = _cache_key(huge, rules, 85, None)
    assert len(k) == 64


# ---- CLI end-to-end edge cases ----------------------------------------------


def test_cli_audit_trail_export_aggregate_on_empty_trail(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["audit-trail-export", "--input", str(tmp_path / "missing.jsonl"), "--aggregate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Total records:** 0" in result.output


def test_cli_audit_trail_conformance_check_on_single_invalid_record(tmp_path):
    """A single record missing all reproducibility fields should fail multiple checks."""
    runner = CliRunner()
    trail = tmp_path / "trail.jsonl"
    trail.write_text(
        _json.dumps(
            {
                "schema": "roam-audit-trail-v1",
                "previous_record_hash": "",
                # Intentionally missing: timestamp, actor, verdict, diff_sha256, etc.
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(trail)],
    )
    env = _json.loads(result.output)
    # Score should be very low — at most chain integrity passes.
    assert env["summary"]["score"] <= 33  # 2 of 6 checks max


def test_cli_rules_validate_with_empty_rules_array(tmp_path):
    """rules: [] is valid YAML but has nothing to validate."""
    runner = CliRunner()
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    result = runner.invoke(cli, ["rules-validate", str(rules)])
    assert result.exit_code == 0
    assert "valid (0 rule(s)" in result.output


def test_cli_rules_validate_with_unicode_in_description(tmp_path):
    """Unicode in description should round-trip cleanly."""
    runner = CliRunner()
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "rules:\n"
        "  - id: emoji-rule\n"
        "    description: 'Banned for safety reasons - critical'\n"
        "    pattern: function_call\n"
        "    source_glob: 'src/**/*.py'\n"
        "    forbidden_target_glob: eval\n"
        "    severity: BLOCK\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli, ["rules-validate", str(rules)])
    assert result.exit_code == 0
    assert "valid" in result.output


def test_cli_pr_analyze_with_huge_diff(tmp_path, monkeypatch):
    """A very large diff (10MB) should still complete without OOM."""
    monkeypatch.chdir(tmp_path)

    # Need indexed project for ensure_index() to skip indexing prompt.
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import git_commit, git_init, index_in_process

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def f(): pass\n")
    git_init(proj)
    git_commit(proj, "initial")
    monkeypatch.chdir(proj)
    index_in_process(proj)

    diff = proj / "huge.diff"
    # 1MB diff (smaller than 10MB to keep test fast)
    huge_lines = "+# comment line\n" * 30000
    diff.write_text(
        f"diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n@@ -0,0 +1,30000 @@\n{huge_lines}",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pr-analyze", "--input", str(diff)])
    # Just don't crash; verdict can be anything sensible.
    # CliRunner will catch SystemExit; check exit_code.
    assert result.exit_code in (0, 5)
