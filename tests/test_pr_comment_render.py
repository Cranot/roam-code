"""Tests for ``roam pr-comment-render`` — markdown PR comment rendering."""

from __future__ import annotations

import json as _json

import pytest

from roam.commands.cmd_pr_comment_render import (
    _render_github_markdown,
    _render_plain,
    _signal_explanation,
)


def _envelope(
    verdict: str = "REVIEW",
    blast: int = 50,
    ai: int = 70,
    violations: int = 0,
    rule_violations: list[dict] | None = None,
    rationale: dict | None = None,
    drift: dict | None = None,
    ai_likelihood: dict | None = None,
) -> dict:
    return {
        "summary": {
            "verdict": verdict,
            "blast_radius": blast,
            "ai_likelihood": ai,
            "rule_violations": violations,
            "high_severity_critique": 0,
        },
        "rule_violations": rule_violations or [],
        "rationale": rationale or {"summary_text": "Test rationale.", "concerns": [], "next_steps": []},
        "drift": drift or {},
        "ai_likelihood": ai_likelihood or {"score": ai, "signals": {}},
    }


def test_render_github_markdown_includes_verdict():
    md = _render_github_markdown(_envelope(verdict="REVIEW"), include_links=True)
    assert "## Roam Agent Review" in md
    assert "Verdict: REVIEW" in md


def test_render_github_markdown_includes_scores():
    md = _render_github_markdown(_envelope(blast=67, ai=82, violations=3), include_links=False)
    assert "**67/100**" in md
    assert "**82/100**" in md
    assert "**3**" in md


def test_render_github_markdown_links_omitted_with_no_links():
    md = _render_github_markdown(_envelope(), include_links=False)
    assert "Powered by" not in md


def test_render_github_markdown_links_included_with_default():
    md = _render_github_markdown(_envelope(), include_links=True)
    assert "Powered by" in md
    assert "roam-code" in md


def test_render_github_markdown_drift_arrows_on_regression():
    drift = {
        "blast_radius_delta": 5,
        "ai_likelihood_delta": -22,
        "new_violation_count": 3,
        "resolved_violation_count": 0,
        "regression": True,
        "improvement": False,
        "verdict_changed": True,
        "previous_verdict": "SAFE",
    }
    md = _render_github_markdown(_envelope(drift=drift), include_links=False)
    # New before-after-delta rendering: blast 50 = before 45 + delta 5 → "(45 → 50, +5)"
    assert "(45 → 50, +5)" in md
    assert "(92 → 70, -22)" in md
    assert "(+3 new, -0 resolved)" in md
    assert "Regression" in md
    assert "was: SAFE" in md


def test_render_github_markdown_drift_improvement_banner():
    drift = {
        "blast_radius_delta": -10,
        "ai_likelihood_delta": -5,
        "new_violation_count": 0,
        "resolved_violation_count": 2,
        "regression": False,
        "improvement": True,
        "verdict_changed": False,
        "previous_verdict": "BLOCK",
    }
    md = _render_github_markdown(_envelope(drift=drift), include_links=False)
    assert "Improvement" in md
    assert "resolved violations: 2" in md


def test_render_github_markdown_rule_violation_block():
    violations = [
        {
            "rule_id": "no-eval",
            "severity": "BLOCK",
            "file": "src/foo.py",
            "matched_import": "eval",
            "description": "Banned eval",
        },
        {
            "rule_id": "no-pickle",
            "severity": "WARN",
            "file": "src/bar.py",
            "matched_import": "pickle.loads",
        },
    ]
    md = _render_github_markdown(
        _envelope(violations=2, rule_violations=violations),
        include_links=False,
    )
    assert "Architecture rule violations" in md
    assert "**BLOCK** `no-eval`" in md
    assert "WARN `no-pickle`" in md


def test_render_github_markdown_reviewer_block_when_present():
    rationale = {
        "summary_text": "Test.",
        "concerns": [],
        "next_steps": [],
        "suggested_reviewers": [
            {"name": "alice", "score": 0.9, "source": "blame"},
            {"name": "bob", "score": 0.7},
        ],
    }
    md = _render_github_markdown(_envelope(rationale=rationale), include_links=False)
    assert "Suggested reviewers" in md
    assert "@alice" in md
    assert "@bob" in md


def test_render_github_markdown_concerns_rendered():
    rationale = {
        "summary_text": "Test.",
        "concerns": [
            {"concern": "high blast radius", "score": 67, "evidence": "fan-in is 47"},
            {"concern": "AI-likelihood elevated", "score": 82, "evidence": "generic naming dominant"},
        ],
        "next_steps": ["Resolve concerns."],
    }
    md = _render_github_markdown(_envelope(rationale=rationale), include_links=False)
    assert "### Concerns" in md
    assert "high blast radius (67/100)" in md
    assert "AI-likelihood elevated (82/100)" in md


def test_render_github_markdown_next_steps_rendered():
    rationale = {
        "summary_text": "Test.",
        "concerns": [],
        "next_steps": [
            "Resolve every BLOCK-severity finding.",
            "Add tests for the new behaviour.",
        ],
    }
    md = _render_github_markdown(_envelope(rationale=rationale), include_links=False)
    assert "### Next steps" in md
    assert "Resolve every BLOCK-severity finding" in md
    assert "Add tests for the new behaviour" in md


def test_render_plain_format_no_markdown_syntax():
    plain = _render_plain(_envelope(verdict="BLOCK", blast=80, ai=85))
    assert "Roam Agent Review" in plain
    assert "BLOCK" in plain
    # Plain format must not have GFM-style ## headers.
    assert "## " not in plain.replace("blast", "x")  # lenient — just check no ## header marker


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


def test_cli_pr_comment_render_help(cli_runner):
    from roam.cli import cli

    result = cli_runner.invoke(cli, ["pr-comment-render", "--help"])
    assert "--style" in result.output
    assert "--input" in result.output


def test_cli_pr_comment_render_from_input_file(cli_runner, tmp_path):
    from roam.cli import cli

    env_path = tmp_path / "env.json"
    env_path.write_text(_json.dumps(_envelope(verdict="SAFE")))
    result = cli_runner.invoke(cli, ["pr-comment-render", "--input", str(env_path), "--no-links"])
    assert result.exit_code == 0
    assert "Verdict: SAFE" in result.output


def test_cli_pr_comment_render_plain_style(cli_runner, tmp_path):
    from roam.cli import cli

    env_path = tmp_path / "env.json"
    env_path.write_text(_json.dumps(_envelope(verdict="REVIEW")))
    result = cli_runner.invoke(cli, ["pr-comment-render", "--input", str(env_path), "--style", "plain"])
    assert result.exit_code == 0
    assert "REVIEW" in result.output


# ---- Signal explanations (C.1.hh) -------------------------------------------


def test_signal_explanation_comment_density():
    out = _signal_explanation("comment_density", {"comment_ratio": 0.42})
    assert "42%" in out
    assert "over-explain" in out


def test_signal_explanation_generic_naming():
    out = _signal_explanation("generic_naming", {"generic_function_names": 4, "new_functions": 6})
    assert "4 of 6" in out


def test_signal_explanation_orphan_imports():
    out = _signal_explanation("orphan_imports", {"orphan_imports": 3})
    assert "3 added import" in out


def test_signal_explanation_test_coverage():
    out = _signal_explanation("test_coverage", {"test_coverage_ratio": 0.05})
    assert "0.05" in out


def test_signal_explanation_returns_empty_for_missing_data():
    assert _signal_explanation("comment_density", {}) == ""
    assert _signal_explanation("generic_naming", {"generic_function_names": 1}) == ""  # missing new_functions


def test_signal_explanation_unknown_signal_returns_empty():
    assert _signal_explanation("not_a_signal", {"x": 1}) == ""


@pytest.mark.parametrize(
    "signal,raw",
    [
        ("comment_density", {"comment_ratio": 0.4}),
        ("generic_naming", {"generic_function_names": 3, "new_functions": 5}),
        ("orphan_imports", {"orphan_imports": 2}),
        ("test_coverage", {"test_coverage_ratio": 0.1}),
        ("add_remove_ratio", {"add_remove_ratio": 5.0}),
        ("function_size", {"new_functions": 5}),  # function_size doesn't read raw, returns generic line
        ("placeholder_density", {"placeholder_count": 3}),
        ("llm_phrase_density", {"llm_phrase_count": 2}),
        ("suspicious_imports", {"suspicious_import_count": 1}),
    ],
)
def test_every_scorer_signal_has_an_explanation(signal, raw):
    """Guardrail: each signal name in the scorer MUST have a plain-English explainer.

    If the scorer adds a new signal but the explanation function isn't updated,
    the PR comment quietly shows the score without context. This parametrised
    test catches that regression.
    """
    out = _signal_explanation(signal, raw)
    assert out != "", f"signal {signal!r} returned empty explanation; update _signal_explanation"


def test_render_includes_previous_verdict_line_when_drift_present():
    drift = {
        "blast_radius_delta": 5,
        "ai_likelihood_delta": 3,
        "new_violation_count": 1,
        "resolved_violation_count": 0,
        "regression": True,
        "improvement": False,
        "verdict_changed": True,
        "previous_verdict": "SAFE",
        "baseline_timestamp": "2026-05-04T12:00:00Z",
    }
    md = _render_github_markdown(_envelope(drift=drift), include_links=False)
    assert "Previous: SAFE at 2026-05-04T12:00:00Z" in md


def test_render_includes_baseline_age_when_loaded_from_baseline():
    md = _render_github_markdown(_envelope(), include_links=False, baseline_age_days=3)
    assert "saved 3 days ago" in md


def test_render_includes_today_when_baseline_age_zero():
    md = _render_github_markdown(_envelope(), include_links=False, baseline_age_days=0)
    assert "saved today" in md


def test_render_omits_baseline_age_block_when_not_from_baseline():
    md = _render_github_markdown(_envelope(), include_links=False)
    assert "Rendered from" not in md


def test_envelope_age_days_handles_missing_meta():
    from roam.commands.cmd_pr_comment_render import _envelope_age_days

    assert _envelope_age_days({}) is None
    assert _envelope_age_days({"_meta": {}}) is None
    assert _envelope_age_days({"_meta": {"timestamp": "garbage"}}) is None


def test_envelope_age_days_returns_integer_for_valid_timestamp():
    import datetime as _dt

    from roam.commands.cmd_pr_comment_render import _envelope_age_days

    five_days_ago = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    age = _envelope_age_days({"_meta": {"timestamp": five_days_ago}})
    assert age in (4, 5)  # tolerate cross-midnight runs


def test_signal_names_in_test_match_scorer_signals():
    """Sanity: the names in the parametrise above must equal cmd_pr_analyze's signal keys.

    Catches the case where a future signal is added to the scorer but not to this test.
    """
    from roam.commands.cmd_pr_analyze import _DEFAULT_WEIGHTS

    expected = set(_DEFAULT_WEIGHTS.keys())
    tested = {
        "comment_density",
        "generic_naming",
        "orphan_imports",
        "test_coverage",
        "add_remove_ratio",
        "function_size",
        "placeholder_density",
        "llm_phrase_density",
        "suspicious_imports",
    }
    assert tested == expected, (
        f"signal coverage mismatch — scorer has {expected}, test covers {tested}; "
        "update test_every_scorer_signal_has_an_explanation parametrise list."
    )


def test_render_github_markdown_includes_signal_explanations():
    """High AI score should include signal-explanation lines under the details block."""
    env = _envelope(
        ai=70,
        ai_likelihood={
            "score": 70,
            "signals": {
                "comment_density": 75,
                "generic_naming": 60,
                "orphan_imports": 40,
            },
            "weights": {
                "comment_density": 0.25,
                "generic_naming": 0.20,
                "orphan_imports": 0.20,
            },
            "raw_metrics": {
                "comment_ratio": 0.42,
                "generic_function_names": 4,
                "new_functions": 6,
                "orphan_imports": 3,
            },
        },
    )
    md = _render_github_markdown(env, include_links=False)
    # Top signal contribution rendering
    assert "x0.25" in md
    # Plain-English explanation under each signal
    assert "42%" in md
    assert "4 of 6" in md
    assert "3 added import" in md


# ---- D6: 5-line context lines surfaced in concerns + violations ----


def test_section_concerns_renders_context_lines_as_fenced_code():
    rationale = {
        "summary_text": "",
        "concerns": [
            {
                "concern": "rule violation",
                "evidence": "Triggered: x.",
                "context_lines": [
                    "import dangerous_module as dm",
                    "x = dm.bad_call()",
                    "    print(x)",
                ],
            }
        ],
        "next_steps": [],
    }
    md = _render_github_markdown(_envelope(rationale=rationale), include_links=False)
    assert "```" in md
    assert "x = dm.bad_call()" in md


def test_section_concerns_no_context_lines_no_fence():
    rationale = {
        "summary_text": "",
        "concerns": [{"concern": "x", "evidence": "y"}],
        "next_steps": [],
    }
    md = _render_github_markdown(_envelope(rationale=rationale), include_links=False)
    assert "```" not in md


def test_section_rule_violations_renders_context_lines():
    violations = [
        {
            "rule_id": "no-pickle",
            "severity": "BLOCK",
            "file": "src/x.py",
            "matched_import": "pickle",
            "context_lines": [
                "import os",
                "import pickle",
                "pickle.loads(payload)",
            ],
        }
    ]
    md = _render_github_markdown(_envelope(rule_violations=violations, violations=1), include_links=False)
    assert "import pickle" in md
    assert "pickle.loads(payload)" in md
    assert "```" in md


def test_render_plain_includes_context_lines_indented():
    rationale = {
        "summary_text": "",
        "concerns": [
            {
                "concern": "x",
                "evidence": "y",
                "context_lines": ["a()", "b()", "c()"],
            }
        ],
        "next_steps": [],
    }
    out = _render_plain(_envelope(rationale=rationale))
    assert "       | a()" in out
    assert "       | c()" in out


def test_check_rules_attaches_context_lines():
    """Each violation produced by _check_rules carries up to 5 context lines."""
    from roam.commands.cmd_pr_analyze import _check_rules

    diff_text = "\n".join(
        [
            "diff --git a/src/x.py b/src/x.py",
            "+++ b/src/x.py",
            "@@ -1,0 +1,6 @@",
            "+import os",
            "+import sys",
            "+import pickle",
            "+def load(payload):",
            "+    return pickle.loads(payload)",
            "+    # done",
            "",
        ]
    )
    rules = [
        {
            "id": "no-pickle",
            "pattern": "import_from",
            "forbidden_target_glob": "pickle",
            "severity": "BLOCK",
        }
    ]
    vios = _check_rules(diff_text, rules)
    assert len(vios) == 1
    ctx = vios[0]["context_lines"]
    # window is matched line +/-2 within added lines
    assert any("import pickle" in line for line in ctx)
    assert 2 <= len(ctx) <= 5
