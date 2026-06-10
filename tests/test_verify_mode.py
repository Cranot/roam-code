"""The output-side verify MODE: config / freedom / auto-select / toggle.

These exercise the selection layer added on top of the existing 5-check verify
(naming/imports/error_handling/duplicates/syntax) — opt-in, never forced.
"""

from __future__ import annotations

from roam.commands.cmd_verify import (
    _ALL_CHECKS,
    _DEFAULT_CHECKS,
    _compute_composite,
    auto_select_checks,
    load_verify_config,
    resolve_selected_checks,
    write_verify_enabled,
)


def test_config_defaults_when_absent(tmp_path):
    cfg = load_verify_config(tmp_path)
    assert cfg["enabled"] is True
    assert cfg["checks"] is None
    assert cfg["auto"] is False


def test_toggle_off_then_on(tmp_path):
    write_verify_enabled(tmp_path, False)
    assert load_verify_config(tmp_path)["enabled"] is False
    write_verify_enabled(tmp_path, True)
    assert load_verify_config(tmp_path)["enabled"] is True


def test_config_checks_filtered_and_threshold(tmp_path):
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "verify.yaml").write_text("checks: [syntax, bogus, naming]\nthreshold: 90\n")
    cfg = load_verify_config(tmp_path)
    assert cfg["checks"] == ["syntax", "naming"]  # unknown 'bogus' dropped
    assert cfg["threshold"] == 90


def test_auto_select_python_source(tmp_path):
    sel = auto_select_checks(["src/foo.py"])
    assert {"naming", "imports", "error_handling", "syntax"} <= set(sel)


def test_auto_select_test_only_skips_naming(tmp_path):
    # a test file is not "non-test source" → naming/duplicates not unlocked,
    # but it's still .py so the Python checks run.
    sel = auto_select_checks(["tests/test_foo.py"])
    assert "naming" not in sel
    assert "syntax" in sel


def test_resolve_precedence(tmp_path):
    cfg = {"enabled": True, "checks": ["naming"], "auto": False, "threshold": None}
    # explicit --checks wins
    assert resolve_selected_checks("syntax,duplicates", False, cfg, []) == ["duplicates", "syntax"]
    # --auto wins over config.checks
    assert {"syntax", "error_handling"} <= set(resolve_selected_checks(None, True, cfg, ["a.py"]))
    # config.checks when neither flag set
    assert resolve_selected_checks(None, False, cfg, []) == ["naming"]
    # nothing set → the conventions-grade DEFAULT five (backward compatible),
    # NOT the structural checks.
    empty = {"enabled": True, "checks": None, "auto": False, "threshold": None}
    assert resolve_selected_checks(None, False, empty, []) == list(_DEFAULT_CHECKS)
    assert "complexity" not in resolve_selected_checks(None, False, empty, [])
    # `--checks all` opts into every available check incl. complexity + cycles
    assert resolve_selected_checks("all", False, empty, []) == list(_ALL_CHECKS)


def test_structural_checks_available_and_opt_in():
    # complexity + cycles exist as checks but are NOT in the default set
    assert "complexity" in _ALL_CHECKS and "cycles" in _ALL_CHECKS
    assert "complexity" not in _DEFAULT_CHECKS and "cycles" not in _DEFAULT_CHECKS
    # auto unlocks them on a Python edit (that's when they regress)
    sel = auto_select_checks(["src/foo.py"])
    assert "complexity" in sel and "cycles" in sel


def test_tests_check_is_opt_in_only():
    # the EXECUTABLE-signal check is available but never in the default set or
    # auto (running tests is expensive — opt in via --checks/--all).
    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "tests" in _ALL_CHECKS
    assert "tests" not in _DEFAULT_CHECKS
    assert "tests" not in auto_select_checks(["src/foo.py"])
    # --all and explicit --checks unlock it
    assert "tests" in resolve_selected_checks("all", False, {}, [])
    assert resolve_selected_checks("tests", False, {}, []) == ["tests"]


def test_pytest_failure_parsing():
    from roam.commands.cmd_verify import _PYTEST_FAIL_RE

    out = (
        "tests/test_a.py::test_one PASSED\n"
        "FAILED tests/test_b.py::test_two - assert 1 == 2\n"
        "ERROR tests/test_c.py::test_three\n"
    )
    nodes = sorted(set(_PYTEST_FAIL_RE.findall(out)))
    assert nodes == ["tests/test_b.py::test_two", "tests/test_c.py::test_three"]


def test_composite_renormalizes_over_subset():
    cats = {"naming": {"score": 100}, "syntax": {"score": 0}}
    # weights naming 0.25 + syntax 0.15 = 0.40 → (25 + 0) / 0.40 = 62.5 → 62
    assert _compute_composite(cats, ["naming", "syntax"]) == 62
    # default (all checks, all perfect) unchanged
    assert _compute_composite({c: {"score": 100} for c in _ALL_CHECKS}) == 100
