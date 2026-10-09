"""Frozen user phrasings cover each ask recipe and coupling/ranking ambiguity."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.ask.classifier import classify
from roam.cli import cli

CASES = [
    ("safe-delete-check", "is it safe to delete UserSession"),
    ("safe-delete-check", "can I remove the legacy Auth class"),
    ("onboard", "where do I start in this repo"),
    ("onboard", "what does this codebase do"),
    ("trace-task", "where does the login flow validate sessions"),
    ("trace-task", "trace the n+1 query in checkout"),
    ("locate-symbol", "where is _canonicalize_task defined and what calls it"),
    ("locate-symbol", "where is compile_plan defined"),
    ("module-deps", "what imports cmd_ask.py"),
    ("module-deps", "what files import recipes.py"),
    ("complexity-ranking", "top 5 most complex functions"),
    ("complexity-ranking", "most complex code"),
    ("describe-file", "what does compiler.py do"),
    ("describe-file", "what is cmd_ask.py for"),
    ("verify-patch", "review my changes"),
    ("verify-patch", "is my pending diff safe"),
    ("plan-fleet", "split this refactor across multiple agents"),
    ("plan-fleet", "plan parallel work for the auth migration"),
    ("find-bug", "diagnose why handle_login is failing"),
    ("find-bug", "what is wrong with the AuthService.refresh method"),
    ("trace-flow", "trace the call chain through handle_login"),
    ("trace-flow", "follow the path from request to database"),
    ("what-broke", "what regressed since last week"),
    ("what-broke", "did anything get worse this sprint"),
    ("hot-spots", "show me the riskiest code"),
    ("hot-spots", "where are the high-churn complex files"),
    ("security-audit", "trace attack surface"),
    ("security-audit", "find sql injection or xss reach"),
    ("fixture-impact", "if I rename cli_runner what tests break"),
    ("fixture-impact", "what depends on the cli_runner fixture"),
    ("dead-code-sweep", "find dead code"),
    ("dead-code-sweep", "what's unused"),
    ("architecture-debt", "how coupled is this codebase"),
    ("architecture-debt", "show architecture debt"),
    ("trace-bug", "why is my test failing"),
    ("trace-bug", "trace this exception to its source"),
    ("who-owns", "who owns this code"),
    ("who-owns", "who should review my change to UserSession"),
    ("what-changed", "what changed in this file recently"),
    ("what-changed", "show recent commits to auth.py"),
    ("audit-security", "audit security"),
    ("audit-security", "comprehensive security audit"),
    ("explore-impact", "what will break if I change UserSession"),
    ("explore-impact", "predict the impact of editing auth.py"),
    ("find-similar", "find duplicates of this function"),
    ("find-similar", "are there similar implementations of UserSession"),
    ("why-this-exists", "why does this function exist"),
    ("why-this-exists", "explain this code"),
    ("check-pr", "review this PR"),
    ("check-pr", "is my pull request ready to merge"),
    ("explore-tests", "show test coverage"),
    ("explore-tests", "find untested code"),
    ("dependency-update", "is it safe to upgrade flask"),
    ("dependency-update", "remove this package"),
    ("visualize-architecture", "show me an architecture diagram"),
    ("visualize-architecture", "draw the layers"),
    ("find-broken-links", "find broken links"),
    ("find-broken-links", "any dead doc links in this repo"),
    ("is-feature-wired", "is create_user actually hooked up"),
    ("is-feature-wired", "is the checkout feature wired end to end"),
    ("field-trace", "blast radius of renaming user_email"),
    ("field-trace", "what touches the email field across layers"),
    ("architecture-debt", "most tightly coupled functions"),
    ("architecture-debt", "which functions are most coupled"),
    ("architecture-debt", "highest coupled functions"),
    ("architecture-debt", "what are the most coupled functions"),
    ("architecture-debt", "top 5 most coupled functions"),
]


@pytest.mark.parametrize("expected,question", CASES)
def test_recipe_question_table(expected, question):
    assert classify(question)[0][0].name == expected


def test_coupling_question_through_cli(monkeypatch):
    monkeypatch.setattr("roam.commands.cmd_ask.run_recipe", lambda recipe, query: [])
    result = CliRunner().invoke(cli, ["--json", "ask", "most tightly coupled functions"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "architecture-debt" in result.output
    assert data["summary"]["recipe"] == "architecture-debt"
