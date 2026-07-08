"""Argv-safety tests for `_safe_roam_argv`.

Task-derived positional values (symbols, paths, free-text tasks) can begin
with `-`. Without an end-of-options marker, Click parses such a value as a
downstream option ("roam search -foo" → unknown-option error, or a value-flag
swallowing the next token). `_safe_roam_argv` separates trusted roam flags
from untrusted positionals and inserts `--` before the positionals.
"""

from __future__ import annotations

from roam.plan.compiler import (
    _ROAM_BOOL_FLAGS,
    _ROAM_VALUE_FLAGS,
    _safe_roam_argv,
)


def test_bare_subcommand_unchanged():
    assert _safe_roam_argv(["complexity"]) == ["complexity"]


def test_empty_returns_empty():
    assert _safe_roam_argv([]) == []


def test_single_positional_guarded():
    assert _safe_roam_argv(["search", "sym"]) == ["search", "--", "sym"]


def test_two_positionals_guarded():
    assert _safe_roam_argv(["semantic-diff", "x", "y"]) == [
        "semantic-diff",
        "--",
        "x",
        "y",
    ]


def test_value_flag_keeps_its_value_before_marker():
    # `--mode exact` must stay an option/value pair; only `sym` is guarded.
    assert _safe_roam_argv(["search", "sym", "--mode", "exact"]) == [
        "search",
        "--mode",
        "exact",
        "--",
        "sym",
    ]


def test_bool_flag_after_positional_reordered_before_marker():
    assert _safe_roam_argv(["deps", "t", "--multi"]) == [
        "deps",
        "--multi",
        "--",
        "t",
    ]


def test_flag_only_call_emits_no_marker():
    assert _safe_roam_argv(["dead", "--no-decay"]) == ["dead", "--no-decay"]
    assert _safe_roam_argv(["coupling", "-n", "10"]) == ["coupling", "-n", "10"]


def test_mixed_value_bool_and_positional():
    assert _safe_roam_argv(["grep", "pat", "-n", "50", "--source-only"]) == [
        "grep",
        "-n",
        "50",
        "--source-only",
        "--",
        "pat",
    ]


def test_leading_dash_positional_is_guarded_not_an_option():
    # The core attack: a task value beginning with `-` must land after `--`.
    assert _safe_roam_argv(["search", "-foo"]) == ["search", "--", "-foo"]


def test_unknown_dash_token_treated_as_positional():
    # Fail-safe: a dash token NOT in the known flag tables is untrusted, so it
    # is pushed past the marker rather than passed through as a real option.
    assert _safe_roam_argv(["grep", "--evil", "-n", "50"]) == [
        "grep",
        "-n",
        "50",
        "--",
        "--evil",
    ]


def test_value_flag_at_tail_without_value_does_not_crash():
    assert _safe_roam_argv(["search", "--mode"]) == ["search", "--mode"]


def test_path_value_flag_kept_before_marker():
    # Regression: `--path` was omitted from the value-flag table, so the algo
    # probe's `--path <file>` was pushed past the `--` guard and parsed as a
    # positional — roam algo errored (exit 2) and the probe returned {}. This
    # was the CI-red `test_probe_embeds_scoped_findings` failure.
    assert _safe_roam_argv(["algo", "-n", "5", "--path", "src/loader.py"]) == [
        "algo",
        "-n",
        "5",
        "--path",
        "src/loader.py",
    ]


def test_fixed_string_bool_flag_kept_before_marker():
    # Regression: `--fixed-string` (grep literal mode) was unregistered, so it
    # landed after the guard as a positional and the grep probes went dark.
    assert _safe_roam_argv(["grep", "-n", "10", "--fixed-string", "name"]) == [
        "grep",
        "-n",
        "10",
        "--fixed-string",
        "--",
        "name",
    ]


def test_explicit_end_of_options_marker_is_not_doubled():
    # A call site that already passes its own `--` must not yield `-- --`
    # (which would push the real guard's positionals one slot too far).
    assert _safe_roam_argv(["grep", "-n", "10", "--fixed-string", "--", "name"]) == [
        "grep",
        "-n",
        "10",
        "--fixed-string",
        "--",
        "name",
    ]
    assert _safe_roam_argv(["search-semantic", "--", "task"]) == [
        "search-semantic",
        "--",
        "task",
    ]


def test_token_after_explicit_marker_forced_positional():
    # After an explicit `--`, even a flag-looking token (`-n`) is a literal
    # search term and must stay a guarded positional, never re-parsed.
    assert _safe_roam_argv(["grep", "--fixed-string", "--", "-n"]) == [
        "grep",
        "--fixed-string",
        "--",
        "-n",
    ]


def test_flag_tables_are_disjoint():
    assert _ROAM_VALUE_FLAGS.isdisjoint(_ROAM_BOOL_FLAGS)
