from __future__ import annotations

from roam.command_advice import (
    _ADVICE_EXAMPLES,
    _INTENT_RULES,
    recommend_commands,
    validate_command_advice,
)


def test_validate_roam_command_advice_accepts_known_command_flags():
    check = validate_command_advice("next", "roam verify --auto")

    assert check["command_kind"] == "roam_cli"
    assert check["subcommand"] == "verify"
    assert check["registry_status"] == "known"
    assert check["parse_status"] == "parsed"
    assert check["executable_status"] == "checked"


def test_validate_roam_command_advice_accepts_root_help():
    check = validate_command_advice("next", "roam --help")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "not_applicable"
    assert check["parse_status"] == "parsed"
    assert check["executable_status"] == "checked"


def test_validate_roam_command_advice_accepts_trailing_global_json():
    check = validate_command_advice("next", "roam surface --json")

    assert check["command_kind"] == "roam_cli"
    assert check["subcommand"] == "surface"
    assert check["registry_status"] == "known"
    assert check["parse_status"] == "parsed"
    assert check["executable_status"] == "checked"


def test_validate_roam_command_advice_rejects_unknown_subcommand():
    check = validate_command_advice("next", "roam definitely-not-a-command --flag")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "unknown"
    assert check["parse_status"] == "not_checked"
    assert check["executable_status"] == "failed"
    assert "unknown roam subcommand" in check["reason"]


def test_validate_roam_command_advice_rejects_invalid_flag():
    check = validate_command_advice("next", "roam verify --definitely-not-a-flag")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "known"
    assert check["parse_status"] == "failed"
    assert check["executable_status"] == "failed"


def test_validate_roam_command_advice_marks_placeholders_unchecked():
    check = validate_command_advice("next", "roam preflight <symbol>")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "known"
    assert check["parse_status"] == "not_checked"
    assert check["target_status"] == "placeholder"
    assert check["executable_status"] == "not_checked"
    assert check["placeholders"] == ["<symbol>"]


def test_validate_roam_command_advice_marks_square_bracket_usage_unchecked():
    check = validate_command_advice("docs", "roam cycles [--actionable-only]")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "known"
    assert check["parse_status"] == "not_checked"
    assert check["target_status"] == "placeholder"
    assert check["executable_status"] == "not_checked"
    assert check["placeholders"] == ["[--actionable-only]"]


def test_validate_roam_command_advice_marks_uppercase_usage_values_unchecked():
    check = validate_command_advice("docs", "roam py-types --ci --min-coverage N")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "known"
    assert check["target_status"] == "placeholder"
    assert check["executable_status"] == "not_checked"
    assert check["placeholders"] == ["N"]


def test_validate_roam_command_advice_marks_ellipsis_unchecked():
    check = validate_command_advice(
        "docs",
        "roam --json eval-retrieve --tasks ... --min-recall-at-20 0.6",
    )

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "known"
    assert check["target_status"] == "placeholder"
    assert check["executable_status"] == "not_checked"
    assert check["placeholders"] == ["..."]


def test_validate_roam_command_advice_marks_choice_values_unchecked():
    check = validate_command_advice("docs", "roam search --mode regex|exact|substring")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "known"
    assert check["target_status"] == "placeholder"
    assert check["executable_status"] == "not_checked"
    assert check["placeholders"] == ["regex|exact|substring"]


def test_validate_roam_command_advice_marks_placeholder_subcommand_unchecked():
    check = validate_command_advice("next", "roam <command>")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "not_checked"
    assert check["target_status"] == "placeholder"
    assert check["executable_status"] == "not_checked"
    assert check["placeholders"] == ["<command>"]


def test_validate_roam_command_advice_marks_mcp_hints_not_applicable():
    check = validate_command_advice("recommended", "roam_impact + roam_uses in PARALLEL")

    assert check["command_kind"] == "mcp_tool_hint"
    assert check["registry_status"] == "not_applicable"
    assert check["executable_status"] == "not_applicable"


def test_validate_roam_command_advice_checks_first_pipeline_segment():
    check = validate_command_advice(
        "starter",
        "roam --json coupling -n 100 | jq '.pairs[0:5]'",
    )

    assert check["command_kind"] == "roam_cli"
    assert check["subcommand"] == "coupling"
    assert check["registry_status"] == "known"
    assert check["parse_status"] == "parsed"
    assert check["executable_status"] == "checked"
    assert "pipeline" in check["reason"]


def test_validate_roam_command_advice_ignores_shell_redirection():
    check = validate_command_advice("docs", "roam --help 2>&1 | grep -A1 -- '--sarif'")

    assert check["command_kind"] == "roam_cli"
    assert check["registry_status"] == "not_applicable"
    assert check["parse_status"] == "parsed"
    assert check["executable_status"] == "checked"


def test_validate_help_example_with_required_positional_is_executable():
    """`roam <cmd> --help` is ALWAYS executable: `--help` is an eager flag that
    short-circuits Click before positional validation. Regression for the
    false-FAIL where `roam search --help` (search has a REQUIRED positional)
    was stripped to `roam search` and reported "Missing parameter: pattern".
    """
    for example in ("roam search --help", "roam impact --help", "roam owner --help"):
        check = validate_command_advice("docs", example)
        assert check["command_kind"] == "roam_cli", example
        assert check["registry_status"] == "known", example
        assert check["parse_status"] == "parsed", example
        assert check["executable_status"] == "checked", (
            f"{example!r} should be executable (--help is eager), got {check['executable_status']!r}"
        )


def test_validate_help_short_flag_is_executable():
    check = validate_command_advice("docs", "roam search -h")
    assert check["executable_status"] == "checked"


def test_validate_missing_required_positional_still_fails_without_help():
    """The --help short-circuit must NOT mask a genuinely non-executable
    example: `roam search` with no positional and no --help still fails."""
    check = validate_command_advice("docs", "roam search")
    assert check["executable_status"] == "failed"


def test_validate_unknown_subcommand_with_help_still_fails():
    """`--help` does not rescue an unknown subcommand — that resolves to
    failed before the eager-flag short-circuit is reached."""
    check = validate_command_advice("docs", "roam notacommand --help")
    assert check["executable_status"] == "failed"


# --- recommend_commands ------------------------------------------------------


def test_recommend_callers_intent_maps_to_uses():
    suggestions = {s["command"]: s for s in recommend_commands("who calls handleSave")}
    assert "uses" in suggestions
    assert "impact" in suggestions
    assert suggestions["uses"]["example"] == "roam uses <symbol>"
    assert suggestions["uses"]["runnable"] is True


def test_recommend_depends_on_intent_maps_to_deps():
    suggestions = {s["command"]: s for s in recommend_commands("what depends on auth.py")}
    assert "deps" in suggestions
    assert "coupling" in suggestions
    assert all(s["runnable"] for s in suggestions.values())


def test_recommend_safe_to_delete_intent_maps_to_safe_delete():
    suggestions = {s["command"]: s for s in recommend_commands("is it safe to delete oldUtil")}
    assert "safe-delete" in suggestions
    assert "delete-check" in suggestions


def test_recommend_grep_workflow_maps_to_grep():
    """Failed grep-heavy workflows route to reachability-aware roam grep."""
    suggestions = {s["command"]: s for s in recommend_commands("grep -rn handleSave src/")}
    assert "grep" in suggestions
    # the leading suggestion is the direct roam grep replacement
    assert suggestions["grep"]["example"] == "roam grep <pattern>"


def test_recommend_every_suggestion_is_runnable_and_known():
    """Ground-truth invariant: no suggestion is ever offered for a command that
    does not exist or whose example does not validate."""
    probes = [
        "who calls x",
        "what depends on y",
        "is it safe to delete z",
        "what breaks if I refactor w",
        "trace the login flow",
        "where is parseToken defined",
        "circular imports",
        "what is this file for",
        "who owns this module",
        "grep -r foo",
        "find duplicate code",
    ]
    for intent in probes:
        for suggestion in recommend_commands(intent):
            assert suggestion["runnable"] is True, (intent, suggestion)
            check = validate_command_advice("probe", suggestion["example"])
            assert check["registry_status"] == "known", (intent, suggestion)
            assert check["executable_status"] != "failed", (intent, suggestion)


def test_recommend_empty_and_unknown_intent_returns_empty():
    assert recommend_commands("") == []
    assert recommend_commands("   ") == []
    assert recommend_commands(None) == []
    assert recommend_commands("completely unrelated gibberish xyzzy") == []


def test_recommend_non_positive_limit_returns_empty():
    assert recommend_commands("who calls x", limit=0) == []
    assert recommend_commands("who calls x", limit=-3) == []


def test_recommend_limit_caps_result_count():
    suggestions = recommend_commands("grep -rn handleSave", limit=2)
    assert len(suggestions) == 2
    # ordering preserved within the cap
    assert [s["command"] for s in suggestions] == ["grep", "uses"]


def test_recommend_dedups_across_rules():
    """A command surfaced by multiple rules appears at most once."""
    intent = "who calls handleSave — I was about to grep -rn for it"
    commands = [s["command"] for s in recommend_commands(intent)]
    assert len(commands) == len(set(commands))
    assert "uses" in commands  # shared by both the callers and grep-workflow rules


def test_recommend_curated_tables_are_self_consistent():
    """Every candidate named by a rule has a runnable example and is a real
    command; every example command is named by at least one rule or kept as a
    standalone suggestion surface."""
    from roam.surface_counts import cli_commands

    registry = set(cli_commands().keys())
    candidates = {cmd for _, cmds, _ in _INTENT_RULES for cmd in cmds}
    # candidates are a subset of the curated examples
    assert candidates <= set(_ADVICE_EXAMPLES)
    for command, example in _ADVICE_EXAMPLES.items():
        assert command in registry, command
        check = validate_command_advice("audit", example)
        assert check["registry_status"] == "known", command
        assert check["executable_status"] != "failed", command
