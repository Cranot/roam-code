"""Tests for the v2 AI-likelihood signals added 2026-05-06.

Three new signals:
- placeholder_density (TODO/FIXME/NotImplementedError density)
- llm_phrase_density ("we use this approach because..." style comments)
- suspicious_imports (numbered modules, mass typing imports, helper.helper)
"""

from __future__ import annotations

from roam.commands.cmd_pr_analyze import (
    _LANG_WEIGHT_OVERRIDES,
    _LLM_PHRASE_RE,
    _PLACEHOLDER_RE,
    _SUSPICIOUS_IMPORT_RE,
    _compute_ai_likelihood,
)

# ---- Placeholder regex unit tests ------------------------------------------


def test_placeholder_re_matches_todo():
    assert _PLACEHOLDER_RE.search("# TODO: implement this")
    assert _PLACEHOLDER_RE.search("// FIXME: bug")
    assert _PLACEHOLDER_RE.search("    pass  # XXX placeholder")


def test_placeholder_re_matches_not_implemented():
    assert _PLACEHOLDER_RE.search("    raise NotImplementedError()")


def test_placeholder_re_matches_throw_not_implemented():
    assert _PLACEHOLDER_RE.search('throw new Error("not implemented")')


def test_placeholder_re_does_not_match_legit_code():
    assert not _PLACEHOLDER_RE.search("def add(a, b): return a + b")
    assert not _PLACEHOLDER_RE.search("user.role = 'admin'")


# ---- LLM-phrase regex ------------------------------------------------------


def test_llm_phrase_re_matches_we_use():
    assert _LLM_PHRASE_RE.search("# We use this approach because of performance")
    assert _LLM_PHRASE_RE.search("// we can use this for that")


def test_llm_phrase_re_matches_helper_function():
    assert _LLM_PHRASE_RE.search("# helper function")
    assert _LLM_PHRASE_RE.search("// main entry point")


def test_llm_phrase_re_matches_note_phrasings():
    assert _LLM_PHRASE_RE.search("# Note: this is important")
    assert _LLM_PHRASE_RE.search("// Importantly, do not call this")


def test_llm_phrase_re_does_not_match_typical_comment():
    assert not _LLM_PHRASE_RE.search("# fix bug")
    assert not _LLM_PHRASE_RE.search("// max 5 retries")


# ---- Suspicious-import regex -----------------------------------------------


def test_suspicious_import_re_matches_numbered_modules():
    assert _SUSPICIOUS_IMPORT_RE.search("import foo_v2")
    assert _SUSPICIOUS_IMPORT_RE.search("from bar1 import baz")


def test_suspicious_import_re_matches_helper_helper():
    assert _SUSPICIOUS_IMPORT_RE.search("from helpers.helpers import x")
    assert _SUSPICIOUS_IMPORT_RE.search("from utils.utils import y")


def test_suspicious_import_re_matches_typing_overimport():
    line = "from typing import List, Dict, Optional, Tuple, Set, Union"
    assert _SUSPICIOUS_IMPORT_RE.search(line)


def test_suspicious_import_re_does_not_match_normal():
    assert not _SUSPICIOUS_IMPORT_RE.search("import os")
    assert not _SUSPICIOUS_IMPORT_RE.search("from typing import List")
    assert not _SUSPICIOUS_IMPORT_RE.search("from numpy import ndarray")


# ---- End-to-end: signals appear in raw_metrics ------------------------------


def _diff_with(lines: list[str]) -> str:
    """Build a unified diff with the given added lines in src/main.py."""
    body = "\n".join(f"+{line}" for line in lines)
    return (
        "diff --git a/src/main.py b/src/main.py\n--- a/src/main.py\n+++ b/src/main.py\n@@ -0,0 +1,{} @@\n".format(
            len(lines)
        )
        + body
        + "\n"
    )


def test_compute_ai_likelihood_includes_v2_signals_in_raw_metrics():
    diff = _diff_with(
        [
            "def handle_request(req):",
            "    # TODO: implement validation",
            "    raise NotImplementedError()",
        ]
    )
    out = _compute_ai_likelihood(diff)
    raw = out["raw_metrics"]
    assert "placeholder_count" in raw
    assert "llm_phrase_count" in raw
    assert "suspicious_import_count" in raw
    # Two placeholders: TODO comment + NotImplementedError
    assert raw["placeholder_count"] >= 2


def test_compute_ai_likelihood_signal_values_present():
    diff = _diff_with(
        [
            "# We use this approach because it's clean",
            "import foo_v2",
            "# TODO: actually implement this",
        ]
    )
    out = _compute_ai_likelihood(diff)
    signals = out["signals"]
    assert "placeholder_density" in signals
    assert "llm_phrase_density" in signals
    assert "suspicious_imports" in signals
    # All three should fire on this LLM-shaped diff
    assert signals["placeholder_density"] > 0
    assert signals["llm_phrase_density"] > 0
    assert signals["suspicious_imports"] > 0


def test_clean_human_diff_scores_low_on_v2_signals():
    diff = _diff_with(
        [
            "def add(a: int, b: int) -> int:",
            "    return a + b",
        ]
    )
    out = _compute_ai_likelihood(diff)
    assert out["signals"]["placeholder_density"] == 0
    assert out["signals"]["llm_phrase_density"] == 0
    assert out["signals"]["suspicious_imports"] == 0


def test_lang_weight_overrides_all_have_three_new_signals():
    """Each language weight map must include the 3 new signals (or score breaks)."""
    expected_keys = {
        "add_remove_ratio",
        "comment_density",
        "test_coverage",
        "function_size",
        "generic_naming",
        "orphan_imports",
        "placeholder_density",
        "llm_phrase_density",
        "suspicious_imports",
    }
    for lang, weights in _LANG_WEIGHT_OVERRIDES.items():
        assert set(weights.keys()) == expected_keys, f"{lang} weight map missing signals"


def test_lang_weight_overrides_sum_to_approximately_one():
    """All language weight maps should sum to ~1.0 so scores stay 0-100."""
    for lang, weights in _LANG_WEIGHT_OVERRIDES.items():
        total = sum(weights.values())
        assert 0.97 <= total <= 1.03, f"{lang} weights sum to {total}, must be ~1.0"


def test_score_remains_in_0_100_range_with_v2_signals():
    """Composite score must stay 0-100 even when all 9 signals fire."""
    high_signal_diff = _diff_with(
        [
            "# We use this approach because it's clean",
            "# TODO: implement everything",
            "import foo_v2",
            "from helpers.helpers import x",
            "from typing import List, Dict, Optional, Tuple, Set, Union",
            "def handle_request(req):",
            "    # Note: this is important",
            "    raise NotImplementedError()",
            "def process_thing():",
            "    # XXX placeholder",
            "    pass",
        ]
    )
    out = _compute_ai_likelihood(high_signal_diff)
    assert 0 <= out["score"] <= 100
    # This diff is highly AI-shaped — score should be elevated.
    assert out["score"] >= 40


def test_signal_weights_default_includes_new_keys():
    from roam.commands.cmd_pr_analyze import _DEFAULT_WEIGHTS

    assert "placeholder_density" in _DEFAULT_WEIGHTS
    assert "llm_phrase_density" in _DEFAULT_WEIGHTS
    assert "suspicious_imports" in _DEFAULT_WEIGHTS
    total = sum(_DEFAULT_WEIGHTS.values())
    assert 0.97 <= total <= 1.03
