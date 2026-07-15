"""Tests for the advisory ``calc_divergence`` verify check."""

from __future__ import annotations

import pytest

from roam.commands.cmd_verify import (
    _VERIFY_CALC_DIVERGENCE_CATEGORY,
    _check_calc_divergence,
    auto_select_checks,
)


def _grammars_available() -> bool:
    try:
        from tree_sitter_language_pack import get_parser

        for lang in ("php", "typescript", "python"):
            get_parser(lang)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _grammars_available(),
    reason="tree-sitter grammars unavailable (offline / transient language-pack download failure)",
)


def test_flags_cross_language_rounding_semantics_divergence(tmp_path):
    # same field 'vat' rounded half-away (PHP) and half-up (JS Math.round)
    (tmp_path / "backend.php").write_text("<?php $vat = round($base * $rate / 100, 2);")
    (tmp_path / "frontend.ts").write_text("const vat = Math.round((base * rate / 100) * 100) / 100;")
    result = _check_calc_divergence(["backend.php"], tmp_path)
    assert result["advisory"] is True
    assert len(result["violations"]) == 1
    v = result["violations"][0]
    assert v["severity"] == "WARN"
    assert v["file"] == "backend.php"
    assert v["category"] == _VERIFY_CALC_DIVERGENCE_CATEGORY
    assert "vat" in v["message"]


def test_clean_when_rounding_consistent(tmp_path):
    (tmp_path / "a.php").write_text("<?php $vat = round($base * $rate, 2);")
    (tmp_path / "b.php").write_text("<?php $vat = round($base * $rate / 1, 2);")
    result = _check_calc_divergence(["a.php"], tmp_path)
    assert result["violations"] == []


def test_skips_changed_test_files(tmp_path):
    (tmp_path / "backend.php").write_text("<?php $vat = round($base * $rate, 2);")
    (tmp_path / "frontend.ts").write_text("const vat = Math.round(base * rate * 100) / 100;")
    # the CHANGED file is a test file -> no findings even though divergence exists
    assert _check_calc_divergence(["backend.test.php"], tmp_path)["violations"] == []


def test_no_findings_when_field_only_in_changed_file(tmp_path):
    # a field computed in exactly one place cannot diverge
    (tmp_path / "only.php").write_text("<?php $vat = round($base * $rate, 2);")
    assert _check_calc_divergence(["only.php"], tmp_path)["violations"] == []


def test_auto_select_includes_calc_divergence_for_source_edit():
    assert _VERIFY_CALC_DIVERGENCE_CATEGORY in auto_select_checks(["app/Services/Vat.php"])
    assert _VERIFY_CALC_DIVERGENCE_CATEGORY in auto_select_checks(["src/utils/vat.ts"])


def test_auto_select_excludes_calc_divergence_for_test_only_edit():
    # a test-only or docs-only change should not select the check
    assert _VERIFY_CALC_DIVERGENCE_CATEGORY not in auto_select_checks(["tests/test_vat.php"])
    assert _VERIFY_CALC_DIVERGENCE_CATEGORY not in auto_select_checks(["README.md"])


def test_advisory_not_in_category_weights():
    # advisory: must not move the composite gate score
    from roam.commands.cmd_verify import _CATEGORY_WEIGHTS

    assert _VERIFY_CALC_DIVERGENCE_CATEGORY not in _CATEGORY_WEIGHTS
