"""W86 target-function extraction — recall + shape-gate regression (2026-06-10).

Fable 5 bench evidence: "write a pytest for _resolve_module_names in
src/roam/plan/compiler.py" failed extraction → the src_under_test excerpt
degraded to full_head (the module docstring of a 9k-line file) → agents
ignored the envelope, re-grepped and re-read everything → compile was
token-NEGATIVE on synthesis. The extraction now handles bare "for/of
<identifier>" phrasings, gated on identifier SHAPE (underscore / digit /
mixed case) so plain English never captures, and filenames never count as
symbols.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _embed_src_under_test_excerpt,
    _extract_test_target_function,
)


@pytest.mark.parametrize(
    "task,expected",
    [
        # bare identifier after for/of — the bench-missed phrasing
        ("write a pytest for _resolve_module_names in src/roam/plan/compiler.py", "_resolve_module_names"),
        ("write a unit test of validateEmail from utils.js", "validateEmail"),
        ("write a pytest for redact_secrets_in_string", "redact_secrets_in_string"),
        # backticks always win
        ("write a regression test for `_evaluate_mcp_mode_policy`", "_evaluate_mcp_mode_policy"),
        # covering-phrase: identifier-shaped capture only
        ("write a pytest for compile_plan covering the cache path", "compile_plan"),
        ("write a pytest for compile_plan covering open_db interactions", "open_db"),
        # plain English never captures
        ("write a test for authentication in auth.py", None),
        ("add a test for the parser in parser.py", None),
        # a FILE is not a symbol
        ("write a pytest for atomic_io.py", None),
    ],
)
def test_target_function_extraction(task, expected):
    assert _extract_test_target_function(task) == expected


def test_excerpt_embeds_symbol_slice_with_location(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "import os\n\n\ndef helper():\n    return 1\n\n\n"
        'def target_fn(x):\n    """doc"""\n    return x + 1\n\n\n'
        "def other():\n    pass\n"
    )
    got = _embed_src_under_test_excerpt("mod.py", str(tmp_path), "write a pytest for target_fn in mod.py")
    assert got is not None
    excerpt, definition = got
    assert excerpt["kind"] == "symbol:target_fn"
    assert excerpt["location"] == "mod.py:8"
    assert "def target_fn" in excerpt["content"]
    assert "do NOT grep" in definition


def test_excerpt_falls_back_to_head_without_target(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("x = 1\n" * 50)
    got = _embed_src_under_test_excerpt("mod.py", str(tmp_path), "write tests for mod.py")
    assert got is not None
    excerpt, definition = got
    assert excerpt["kind"] == "full_head"
    assert "location" not in excerpt
