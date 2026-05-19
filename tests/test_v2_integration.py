"""End-to-end integration test for the full v2 stack.

Exercises the entire pipeline against a tiny indexed project:

  1. roam audit                     — health/debt/dead/danger envelope
  2. roam pr-analyze                — verdict + audit-trail emission
  3. roam audit-trail-verify        — chain integrity confirmed
  4. roam audit-trail-export        — markdown + aggregate
  5. roam audit-trail-conformance-check — Article 12 score
  6. roam pr-comment-render         — markdown PR comment from envelope
  7. roam metrics-push --dry-run    — payload includes last-pr-analysis
  8. roam dogfood                   — single-shot rollup

Catches schema drift across the chain (e.g. if pr-analyze stops emitting
a key that conformance-check expects). Each step is a distinct assertion.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process, invoke_cli, parse_json_output  # noqa: E402


@pytest.fixture
def real_project(tmp_path, monkeypatch):
    """Tiny indexed project with a meaningful git diff to analyse."""
    proj = tmp_path / "real"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    git_init(proj)
    git_commit(proj, "initial")
    monkeypatch.chdir(proj)
    index_in_process(proj)
    return proj


def _last_json(text: str) -> dict:
    """Pull the last JSON object out of mixed stdout (skips index logs)."""
    idx = text.rfind("\n{\n")
    if idx == -1:
        idx = text.find("{")
    return _json.loads(text[idx:])


def test_v2_full_pipeline(real_project, cli_runner):
    """Every command in the v2 stack succeeds end-to-end and the payloads chain cleanly."""

    # ---- Step 1: audit envelope ----------------------------------------
    audit_result = invoke_cli(cli_runner, ["audit"], json_mode=True)
    assert audit_result.exit_code == 0
    audit_env = parse_json_output(audit_result)
    assert audit_env["command"] == "audit"
    assert "summary" in audit_env

    # ---- Step 2: pr-analyze with audit-trail + save-baseline ----------
    diff_path = real_project / "x.diff"
    diff_path.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -3,2 +3,5 @@\n"
        "+def handle_request(req):\n"
        "+    return req\n"
        " def sub(a, b):\n"
        "     return a - b\n"
    )
    pr_result = invoke_cli(
        cli_runner,
        ["pr-analyze", "--input", str(diff_path), "--audit-trail", "--save-baseline"],
        json_mode=True,
    )
    assert pr_result.exit_code in (0, 5)
    pr_env = _last_json(pr_result.output)
    assert pr_env["command"] == "pr-analyze"
    # Verdict prefix may be followed by " (risk_level <tier>)" annotation (W210).
    verdict_prefix = pr_env["summary"]["verdict"].split(" (", 1)[0]
    assert verdict_prefix in ("INTENTIONAL", "SAFE", "REVIEW", "BLOCK")
    # Audit-trail block must be present + chain valid (genesis case: no prior records)
    assert "audit_trail" in pr_env
    assert pr_env["audit_trail"]["chain_status"]["pre_emission_chain_valid"] is True
    # Baseline must have been saved
    baseline_path = real_project / ".roam" / "last-pr-analysis.json"
    assert baseline_path.exists()
    # Audit trail JSONL must exist with one record + sequence_number
    trail_path = real_project / ".roam" / "audit-trail.jsonl"
    assert trail_path.exists()
    first_line = trail_path.read_text(encoding="utf-8").strip().split("\n")[0]
    first_record = _json.loads(first_line)
    assert first_record["sequence_number"] == 1

    # ---- Step 3: audit-trail-verify confirms integrity -----------------
    verify_result = invoke_cli(cli_runner, ["audit-trail-verify"], json_mode=True)
    assert verify_result.exit_code == 0
    verify_env = parse_json_output(verify_result)
    assert verify_env["summary"]["chain_valid"] is True
    assert verify_env["summary"]["total_records"] == 1

    # ---- Step 4: audit-trail-export markdown + aggregate ---------------
    export_result = invoke_cli(cli_runner, ["audit-trail-export", "--format", "md"])
    assert export_result.exit_code == 0
    assert "Audit Trail" in export_result.output

    aggregate_result = invoke_cli(cli_runner, ["audit-trail-export", "--aggregate"], json_mode=True)
    assert aggregate_result.exit_code == 0
    agg_env = parse_json_output(aggregate_result)
    assert agg_env["aggregate"]["total_records"] == 1
    assert agg_env["aggregate"]["snapshot"]["top_verdict"] is not None

    # ---- Step 5: conformance check -------------------------------------
    conf_result = invoke_cli(cli_runner, ["audit-trail-conformance-check"], json_mode=True)
    assert conf_result.exit_code == 0
    conf_env = parse_json_output(conf_result)
    assert "score" in conf_env["summary"]
    assert conf_env["summary"]["checks_total"] == 6
    # All 6 individual checks must be present in the checks array
    check_ids = {c["id"] for c in conf_env["checks"]}
    assert check_ids == {
        "chain_integrity",
        "timestamp_completeness",
        "actor_attribution",
        "reproducibility_metadata",
        "verdict_and_rationale",
        "retention",
    }

    # ---- Step 6: pr-comment-render from baseline -----------------------
    render_result = invoke_cli(
        cli_runner,
        ["pr-comment-render", "--from-baseline"],
    )
    assert render_result.exit_code == 0
    md = render_result.output
    assert "## Roam Agent Review" in md
    # Baseline-age line should appear (saved seconds ago → "saved today")
    assert "saved today" in md or "saved 0 days ago" in md or "Rendered from" in md

    # ---- Step 7: metrics-push dry-run includes last-pr-analysis --------
    metrics_result = invoke_cli(cli_runner, ["metrics-push", "--dry-run"], json_mode=True)
    assert metrics_result.exit_code == 0
    metrics_env = parse_json_output(metrics_result)
    assert metrics_env["payload"]["schema"] == "roam-metrics-v1"
    # last_pr_analysis block must have been folded in (we saved a baseline in step 2)
    assert "last_pr_analysis" in metrics_env["payload"]
    # metrics-push baseline stores the core verdict; pr-analyze envelope adds an
    # optional " (risk_level <tier>)" annotation (W210). Compare the prefix only.
    assert metrics_env["payload"]["last_pr_analysis"]["verdict"] == pr_env["summary"]["verdict"].split(" (", 1)[0]

    # ---- Step 8: dogfood rollup ---------------------------------------
    dogfood_result = invoke_cli(cli_runner, ["dogfood", "--no-audit-trail"], json_mode=True)
    assert dogfood_result.exit_code == 0
    dog_env = _last_json(dogfood_result.output)
    assert "audit" in dog_env["summary"]["sections_run"]
    assert "pr_analyze" in dog_env["summary"]["sections_run"]


def test_v2_pipeline_with_rules_pack(real_project, cli_runner):
    """End-to-end with a Python rules pack from templates/rules/python."""
    pack_src = Path("templates/rules/python/.roam-rules.yml")
    if not pack_src.exists():
        pytest.skip("templates/rules/python/.roam-rules.yml not present in this checkout")

    rules_dst = real_project / ".roam" / "rules.yml"
    rules_dst.parent.mkdir(parents=True, exist_ok=True)
    rules_dst.write_text(pack_src.read_text(encoding="utf-8"), encoding="utf-8")

    # Validate the pack ships with no warnings
    validate_result = invoke_cli(cli_runner, ["rules-validate", str(rules_dst)])
    assert validate_result.exit_code == 0
    assert "valid" in validate_result.output

    # Diff that triggers no rules (clean Python)
    safe_diff = real_project / "safe.diff"
    safe_diff.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def add(a, b):\n"
        "+def add(a: int, b: int) -> int:\n"
        "     return a + b\n"
    )
    safe_result = invoke_cli(cli_runner, ["pr-analyze", "--input", str(safe_diff)], json_mode=True)
    assert safe_result.exit_code in (0, 5)
    safe_env = _last_json(safe_result.output)
    assert (safe_env.get("summary") or {}).get("rule_violations", 99) == 0

    # Diff that triggers no-eval BLOCK rule
    block_diff = real_project / "block.diff"
    block_diff.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def add(a, b):\n"
        "+    return eval(f'{a}+{b}')\n"
        "     return a + b\n"
    )
    block_result = invoke_cli(cli_runner, ["pr-analyze", "--input", str(block_diff)], json_mode=True)
    block_env = _last_json(block_result.output)
    # py-no-eval is a BLOCK-severity rule; must fire
    violations = block_env.get("rule_violations", [])
    assert any(v.get("rule_id") == "py-no-eval" for v in violations), (
        f"expected py-no-eval to fire; got: {[v.get('rule_id') for v in violations]}"
    )


def test_v2_cache_chains_through_pipeline(real_project, cli_runner):
    """Cache hit produces an envelope with a verdict that's still consumable downstream."""
    diff_path = real_project / "x.diff"
    diff_path.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def add(a, b):\n"
        "+def add(a: int, b: int) -> int:\n"
    )
    # Cold run
    cold = invoke_cli(
        cli_runner, ["pr-analyze", "--input", str(diff_path), "--cache", "--save-baseline"], json_mode=True
    )
    assert cold.exit_code in (0, 5)
    cold_env = _last_json(cold.output)
    cold_verdict = cold_env["summary"]["verdict"]
    assert not cold_env.get("cache_hit")

    # Warm run — same diff + same rules + same threshold = cache hit
    warm = invoke_cli(cli_runner, ["pr-analyze", "--input", str(diff_path), "--cache"], json_mode=True)
    assert warm.exit_code in (0, 5)
    warm_env = _last_json(warm.output)
    assert warm_env.get("cache_hit") is True
    # Cold path emits W210 risk_level annotation; warm/cached path stores the core verdict.
    # Compare prefixes — same producer-divergence shape as the metrics-push baseline (line 153).
    assert warm_env["summary"]["verdict"].split(" (", 1)[0] == cold_verdict.split(" (", 1)[0]  # consistent verdict
