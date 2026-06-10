"""Tests for `roam ask` workflow recipes."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.ask.classifier import classify
from roam.ask.recipes import RECIPES, by_name
from roam.ask.runner import extract_symbol, fill_args, fill_followups
from roam.cli import cli
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Recipe registry shape
# ---------------------------------------------------------------------------


class TestRecipes:
    def test_v12_recipe_set(self):
        names = {r.name for r in RECIPES}
        # The v12.0 expanded set + v12.8 fixture-impact + v12.15
        assert names == {
            # First batch
            "safe-delete-check",
            "onboard",
            "trace-task",
            "verify-patch",
            "plan-fleet",
            # Second batch — added 2026-05-01
            "find-bug",
            "trace-flow",
            "what-broke",
            "hot-spots",
            "security-audit",
            "dead-code-sweep",
            "architecture-debt",
            # v12.8 — pytest fixture impact
            "fixture-impact",
            # v12.15 / agent workflow recipes
            "trace-bug",
            "who-owns",
            "what-changed",
            "audit-security",
            "explore-impact",
            "find-similar",
            "why-this-exists",
            "check-pr",
            "explore-tests",
            "dependency-update",
            "visualize-architecture",
            # v12.48 — dangling-doc-reference scan
            "find-broken-links",
            # 2026-06-06 — precise named-symbol definition+callers. Fixes roam_ask
            # routing "where is X defined / what calls X" to search+uses instead of
            # trace-task's fuzzy retrieve (which matched the query word "defined"
            # against symbols literally named `definition`).
            "locate-symbol",
            # 2026-06-06 — file dependency-direction (what imports/depends on X.py)
            # → roam deps; fixes the deps gap found in the roam_ask routing hunt.
            "module-deps",
            # 2026-06-06 (overnight) — top-N routing gaps from the comprehensive
            # battery: precise "most complex" → roam complexity; file-role
            # "what does X.py do" → roam file (was noise-routing to module-deps).
            "complexity-ranking",
            "describe-file",
        }

    def test_recipe_count(self):
        # Lock in the recipe surface — bump together with surface counts
        # in CLAUDE.md / README when changing.
        assert len(RECIPES) == 29

    def test_readme_recipe_count_matches_registry(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        match = re.search(r"\b(\d+)-recipe registry\b", readme)
        assert match, "README missing '<N>-recipe registry' phrase"
        assert int(match.group(1)) == len(RECIPES)

    def test_recipe_names_unique(self):
        names = [r.name for r in RECIPES]
        assert len(names) == len(set(names))

    def test_recipes_have_kebab_case_names(self):
        import re

        kebab = re.compile(r"^[a-z]+(-[a-z]+)*$")
        for r in RECIPES:
            assert kebab.match(r.name), f"recipe name '{r.name}' is not kebab-case"

    def test_every_recipe_has_workflow_metadata(self):
        for r in RECIPES:
            assert r.intent, f"recipe {r.name} missing intent"
            assert r.examples, f"recipe {r.name} missing examples"
            assert r.commands, f"recipe {r.name} missing commands"
            assert r.phase, f"recipe {r.name} missing phase"
            assert r.perspectives, f"recipe {r.name} missing perspectives"
            assert r.followups, f"recipe {r.name} missing followups"
            assert r.gates, f"recipe {r.name} missing gates"

    def test_recipes_compose_existing_commands(self):
        from roam.surface_counts import cli_commands

        valid = set(cli_commands().keys())
        for r in RECIPES:
            for cmd_name, _args in r.commands:
                assert cmd_name in valid, f"recipe '{r.name}' references unknown command '{cmd_name}'"

    def test_by_name(self):
        assert by_name("safe-delete-check").name == "safe-delete-check"
        assert by_name("nope") is None


# ---------------------------------------------------------------------------
# Classifier — pure unit tests
# ---------------------------------------------------------------------------


class TestClassifier:
    def test_empty_query_returns_empty(self):
        assert classify("") == []
        assert classify("   ") == []

    def test_safe_delete_keyword_match(self):
        ranked = classify("is it safe to delete UserSession")
        top = ranked[0][0].name
        assert top == "safe-delete-check"

    def test_onboard_match(self):
        ranked = classify("where do I start in this repo")
        top = ranked[0][0].name
        assert top == "onboard"

    def test_trace_task_match(self):
        ranked = classify("where does login validate sessions")
        top = ranked[0][0].name
        assert top == "trace-task"

    def test_verify_patch_match(self):
        ranked = classify("audit my pending diff")
        top = ranked[0][0].name
        assert top == "verify-patch"

    def test_plan_fleet_match(self):
        ranked = classify("split this refactor across 4 agents")
        top = ranked[0][0].name
        assert top == "plan-fleet"

    def test_find_bug_match(self):
        ranked = classify("diagnose why handle_login is failing")
        top = ranked[0][0].name
        assert top == "find-bug"

    def test_trace_flow_match(self):
        ranked = classify("what calls UserSession.refresh")
        top = ranked[0][0].name
        # locate-symbol (search+uses) is also a correct callers route — added
        # 2026-06-06; "what calls X" is a precise caller query, which `uses` answers.
        assert top in {"trace-flow", "trace-task", "locate-symbol"}

    def test_what_broke_match(self):
        ranked = classify("what regressed since last week")
        top = ranked[0][0].name
        assert top == "what-broke"

    def test_hot_spots_match(self):
        ranked = classify("show me the riskiest code")
        top = ranked[0][0].name
        assert top == "hot-spots"

    def test_security_audit_match(self):
        ranked = classify("any sql injection or xss reach")
        top = ranked[0][0].name
        assert top == "security-audit"

    def test_what_files_import_routes_to_module_deps(self):
        # "what FILES import X": the "files" token used to dilute the match below
        # the confidence threshold (codex nav A/B q2 phrasing → "no confident
        # recipe match" → wasted roam_ask call + roam_deps fallback). Added the
        # phrasing to module-deps 2026-06-07 so roam_ask routes + EXECUTES it in
        # one call. Guards the routing so the phrasing can't silently regress.
        for q in (
            "what files import recipes.py",
            "which files import compiler.py",
            "what files import src/roam/ask/recipes.py",
        ):
            ranked = classify(q)
            assert ranked, f"no recipe matched: {q!r}"
            assert ranked[0][0].name == "module-deps", f"{q!r} -> {ranked[0][0].name}"

    def test_dead_code_sweep_match(self):
        ranked = classify("find dead code I can delete")
        top = ranked[0][0].name
        assert top in {"dead-code-sweep", "safe-delete-check"}

    def test_architecture_debt_match(self):
        ranked = classify("show architecture debt and god components")
        top = ranked[0][0].name
        assert top == "architecture-debt"

    def test_low_confidence_when_unrelated(self):
        ranked = classify("the weather is nice today")
        # Top score should be modest — definitely below the 0.15 CLI threshold
        assert ranked[0][1] < 0.5

    def test_score_is_in_unit_range(self):
        ranked = classify("split parallel work")
        for r, s in ranked:
            assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


class TestExtractSymbol:
    def test_pascal_case(self):
        assert extract_symbol("delete UserSession safely") == "UserSession"

    def test_snake_case_with_underscore(self):
        assert extract_symbol("call handle_login first") == "handle_login"

    def test_two_identifiers_returns_none(self):
        # Ambiguous — let the command resolve.
        assert extract_symbol("UserSession and handle_login") is None

    def test_no_identifier_returns_none(self):
        assert extract_symbol("the login flow") is None

    def test_single_lowercase_word_returns_none(self):
        # Bare lowercase words have a high false-positive rate.
        assert extract_symbol("session please") is None


class TestFillArgs:
    def test_symbol_placeholder(self):
        out = fill_args(("{symbol}",), "delete UserSession", "UserSession")
        assert out == ["UserSession"]

    def test_symbol_falls_back_to_query(self):
        # No identifier extracted → use the whole query
        out = fill_args(("{symbol}",), "the login flow", None)
        assert out == ["the login flow"]

    def test_task_placeholder(self):
        out = fill_args(("{task}",), "trace login flow", None)
        assert out == ["trace login flow"]

    def test_literal_args_pass_through(self):
        out = fill_args(("plan", "{task}"), "split work", None)
        assert out == ["plan", "split work"]

    def test_followups_render_symbol_and_task(self):
        out = fill_followups(
            ("roam safe-delete {symbol}", "roam retrieve {task}"),
            "delete UserSession safely",
            "UserSession",
        )
        assert out == ["roam safe-delete UserSession", "roam retrieve delete UserSession safely"]


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


@pytest.fixture
def ask_project(tmp_path):
    proj = _make_project(
        tmp_path,
        {
            "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token
                def handle_login(user):
                    return UserSession()
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


class TestAskCLI:
    def test_list_shows_all_recipes(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["ask", "--list"])
        assert result.exit_code == 0, result.output
        for r in RECIPES:
            assert r.name in result.output

    def test_list_json_mode(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "ask", "--list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "ask"
        assert data["summary"]["recipe_count"] == len(RECIPES)
        names = {r["name"] for r in data["recipes"]}
        assert names == {r.name for r in RECIPES}
        for recipe in data["recipes"]:
            assert recipe["phase"]
            assert recipe["perspectives"]
            assert recipe["followups"]
            assert recipe["gates"]

    def test_recipe_json_result_includes_workflow_metadata(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "ask", "--recipe", "onboard", "x"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["recipe"] == "onboard"
        assert data["phase"] == by_name("onboard").phase
        assert data["perspectives"] == list(by_name("onboard").perspectives)
        assert data["followups"] == list(by_name("onboard").followups)
        assert data["gates"] == list(by_name("onboard").gates)

    def test_report_list_json_includes_recipe_workflows(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "report", "--list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["workflows"]["first-contact"]["recipe"] == "onboard"
        assert data["workflows"]["security"]["recipe"] == "security-audit"

    def test_workflow_json_inspects_recipe_without_running(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "workflow", "safe-delete-check", "--query", "delete UserSession safely"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "workflow"
        assert data["recipe"] == "safe-delete-check"
        assert data["phase"] == "scope"
        assert data["commands"][0] == {"cmd": "preflight", "args": ["UserSession"]}
        assert data["followups"][0] == "roam safe-delete UserSession"
        assert "HIGH/CRITICAL" in data["gates"][0]

    def test_no_args_shows_examples(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["ask"])
        assert result.exit_code == 0, result.output
        assert "type a question" in result.output.lower() or "example" in result.output.lower()

    def test_low_confidence_query_lists_candidates(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["ask", "uvw xyz qwe"])
        assert result.exit_code == 0, result.output
        # Expect either the low-confidence message or a list of candidates
        assert "no confident recipe match" in result.output or "Closest matches" in result.output

    def test_recipe_override(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "ask", "--recipe", "onboard", "x"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["recipe"] == "onboard"

    def test_unknown_recipe_rejected(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["ask", "--recipe", "no_such_recipe", "x"])
        assert result.exit_code != 0
        assert "unknown recipe" in result.output.lower()

    def test_help_lists_in_getting_started(self, ask_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "ask" in result.output
