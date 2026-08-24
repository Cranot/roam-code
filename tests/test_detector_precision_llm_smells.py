"""Regression tripwire for llm-smells, not a precision proof.

The labelled pair locks the documented moving-alias rule and dated-model
suppression against refactors; it does not claim a precision number.
"""

from pathlib import Path

from roam.commands.cmd_llm_smells import (
    _detect_no_model_pinning,
    _is_llm_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "llm-smells"


def test_llm_model_pinning_tp_fires_and_pinned_tn_is_clean():
    tp = FIXTURES / "tp_moving_model.py"
    tn = FIXTURES / "tn_pinned_model.py"
    assert _detect_no_model_pinning(str(tp), tp.read_text())
    assert _detect_no_model_pinning(str(tn), tn.read_text()) == []


def test_anthropic_agent_sdk_import_reaches_llm_detectors():
    """The TypeScript Agent SDK import must open the LLM-file gate."""
    fixture = FIXTURES / "tp_anthropic_agent_sdk.ts"
    source = fixture.read_text(encoding="utf-8")

    assert _is_llm_file(source)
