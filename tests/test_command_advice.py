from __future__ import annotations

from roam.command_advice import validate_command_advice


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
