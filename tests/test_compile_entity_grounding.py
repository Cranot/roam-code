"""W-ENTITY — entity-grounded no-file freeform.

The freeform probe used to return an EMPTY envelope whenever a prompt named
no file path, even when it named a code identifier. ~49% of freeform compiles
delivered no prefetch in production. These tests pin the fix: a bare-identifier
freeform prompt resolves the rarest identifier and embeds symbol_definitions.
"""

from __future__ import annotations

from roam.plan import compiler
from roam.plan.compiler import (
    _extract_freeform_identifiers,
    _probe_freeform_augment_for_task,
    _probe_freeform_entities_for_task,
)

# ---- identifier extraction (pure) -------------------------------------


def test_backticked_identifier_ranks_first() -> None:
    syms = _extract_freeform_identifiers("why does `compile_plan` drop the confidence score for data")
    assert syms[0] == "compile_plan"


def test_camelcase_extracted_english_excluded() -> None:
    syms = _extract_freeform_identifiers("explain how useThemeClasses and the database save work")
    assert "useThemeClasses" in syms
    # English words without snake/camel shape must NOT resolve.
    for noise in ("database", "save", "work", "explain"):
        assert noise not in syms


def test_no_identifier_returns_empty() -> None:
    # Conceptual prompt that names nothing resolvable.
    assert _extract_freeform_identifiers("how is auth handled here") == []


def test_stopword_identifier_shaped_tokens_rejected() -> None:
    assert _extract_freeform_identifiers("what does __init__ do for self") == []


def test_rarity_prefers_more_specific() -> None:
    syms = _extract_freeform_identifiers("compare get_user and fetch_authenticated_session")
    # Longer / more underscores = rarer/more specific = ranked first.
    assert syms[0] == "fetch_authenticated_session"


# ---- entity probe (mocked roam search) --------------------------------

_FAKE_SEARCH = {
    "results": [
        {
            "location": "src/roam/plan/compiler.py:120",
            "kind": "function",
            "signature": "def compile_plan(task: str) -> PlanV0",
            "references": ["src/roam/cli.py:40", "tests/test_cmd_compile.py:12"],
            "body_preview": "def compile_plan(task):\n    ...",
        }
    ]
}


def test_entity_probe_embeds_symbol_definitions(monkeypatch) -> None:
    monkeypatch.setattr(compiler, "_run_roam", lambda *a, **k: _FAKE_SEARCH)
    out = _probe_freeform_entities_for_task("why does `compile_plan` drop the score", cwd="/tmp/repo")
    assert out is not None
    assert out["resolved_entity"] == "compile_plan"
    assert out["symbol_definitions"][0]["file"] == "src/roam/plan/compiler.py"
    assert out["symbol_definitions"][0]["line"] == 120
    assert "body_preview" in out["symbol_definitions"][0]
    assert "references" in out["symbol_definitions"][0]


def test_entity_probe_none_when_no_identifier(monkeypatch) -> None:
    monkeypatch.setattr(compiler, "_run_roam", lambda *a, **k: _FAKE_SEARCH)
    assert _probe_freeform_entities_for_task("how is auth handled", "/tmp/repo") is None


def test_entity_probe_none_when_search_empty(monkeypatch) -> None:
    monkeypatch.setattr(compiler, "_run_roam", lambda *a, **k: {"results": []})
    assert _probe_freeform_entities_for_task("explain `compile_plan`", "/tmp/repo") is None


def test_entity_probe_none_without_cwd(monkeypatch) -> None:
    monkeypatch.setattr(compiler, "_run_roam", lambda *a, **k: _FAKE_SEARCH)
    assert _probe_freeform_entities_for_task("explain `compile_plan`", None) is None


# ---- augment delegation (the wiring) ----------------------------------


def test_augment_delegates_to_entity_grounding_when_no_file(monkeypatch) -> None:
    monkeypatch.setattr(compiler, "_run_roam", lambda *a, **k: _FAKE_SEARCH)
    out = _probe_freeform_augment_for_task("why does `compile_plan` drop the score", named_paths=[], cwd="/tmp/repo")
    assert out is not None
    assert "symbol_definitions" in out


def test_augment_no_file_no_identifier_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(compiler, "_run_roam", lambda *a, **k: _FAKE_SEARCH)
    out = _probe_freeform_augment_for_task("explain this codebase", named_paths=[], cwd="/tmp/repo")
    assert out is None
