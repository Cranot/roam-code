"""Tests for ``roam calc-inventory`` and the calc_extract core."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.commands.cmd_calc_inventory import calc_inventory
from roam.index.calc_extract import (
    extract_calcs,
    normalize_formula,
    normalize_target,
    rounding_semantic,
)


def _grammars_available() -> bool:
    """True when the tree-sitter grammars this suite needs can be loaded.

    The language pack fetches grammars from GitHub releases on first use; a
    transient outage (HTTP 5xx) or offline CI would make every extraction return
    ``[]`` (the extractor fails open). Skip rather than hard-fail on infra — the
    extractor's own fail-open behaviour is covered by
    ``test_extract_missing_grammar_returns_empty``.
    """
    try:
        from tree_sitter_language_pack import get_parser

        for lang in ("php", "javascript", "typescript", "python"):
            get_parser(lang)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _grammars_available(),
    reason="tree-sitter grammars unavailable (offline / transient language-pack download failure)",
)

# ---- core extractor: language coverage ------------------------------------


def test_extract_php_vat_with_rounding():
    src = b"<?php $vat = round($base * $rate / 100, 2); $name = $first;"
    calcs = extract_calcs("php", src)
    assert len(calcs) == 1
    c = calcs[0]
    assert c.target == "$vat"
    assert c.rounding == "round"
    assert "$base" in c.operands and "$rate" in c.operands
    assert "100" in c.literals and "2" in c.literals
    assert c.kind == "assign"


def test_extract_skips_non_calc_assignment():
    # plain copy / call assignment is not a calculation
    src = b"<?php $x = $y; $z = foo($y); $w = 'literal';"
    assert extract_calcs("php", src) == []


def test_extract_augmented_assignment_is_calc():
    src = b"<?php $total += $vat;"
    calcs = extract_calcs("php", src)
    assert len(calcs) == 1
    assert calcs[0].kind == "augmented"
    assert calcs[0].target == "$total"


def test_extract_javascript_declarator_and_mathround():
    src = b"const vat = Math.round(base * rate) / 100; const nm = user.name;"
    calcs = extract_calcs("javascript", src)
    assert len(calcs) == 1
    assert calcs[0].target == "vat"
    assert calcs[0].rounding == "round"  # Math.round -> round


def test_extract_python_binary_and_call():
    src = b"vat = round(base * rate / 100, 2)\nnet = gross * 100 / (100 + r)\nname = first\n"
    calcs = extract_calcs("python", src)
    targets = {c.target for c in calcs}
    assert targets == {"vat", "net"}  # 'name = first' skipped


def test_extract_skips_function_valued_rhs():
    # assigning an arrow/closure is not a scalar calc even if its body has math
    src = b"const round2 = (n) => Math.round(n * 100) / 100;"
    assert extract_calcs("javascript", src) == []


def test_extract_missing_grammar_returns_empty():
    assert extract_calcs("no_such_language", b"x = 1 + 2") == []


# ---- normalization helpers ------------------------------------------------


def test_normalize_target_strips_access_and_sigil():
    assert normalize_target("$this->vatAmount") == "vatamount"
    assert normalize_target("self.vat_amount") == "vat_amount"
    assert normalize_target("$vat") == "vat"


def test_normalize_formula_whitespace_paren_insensitive():
    assert normalize_formula("round(a * b, 2)") == normalize_formula("round( a*b ,2 )")
    assert normalize_formula("a + b") != normalize_formula("a - b")


# ---- command: envelope + filters ------------------------------------------


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_command_json_envelope_shape(tmp_path):
    _write(tmp_path, "calc.php", "<?php $vat = round($base * $rate / 100, 2);")
    runner = CliRunner()
    result = runner.invoke(calc_inventory, [str(tmp_path)], obj={"json": True})
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["summary"]["calculations"] == 1
    assert env["calculations"][0]["target"] == "$vat"
    assert env["calculations"][0]["rounding"] == "round"
    assert "facts" in env["agent_contract"]


def test_command_money_filter(tmp_path):
    _write(tmp_path, "m.php", "<?php $vat = $base * $rate; $width = $cols * $gap;")
    runner = CliRunner()
    full = json.loads(runner.invoke(calc_inventory, [str(tmp_path)], obj={"json": True}).output)
    money = json.loads(runner.invoke(calc_inventory, [str(tmp_path), "--money"], obj={"json": True}).output)
    assert full["summary"]["calculations"] == 2
    assert money["summary"]["calculations"] == 1  # only $vat is money-shaped
    assert money["calculations"][0]["target"] == "$vat"


def test_command_divergence_cross_language(tmp_path):
    # same field 'vat' computed two ways in two languages
    _write(tmp_path, "back.php", "<?php $vat = round($base * $rate / 100, 2);")
    _write(tmp_path, "front.ts", "const vat = Math.round(base * rate) / 100;")
    runner = CliRunner()
    env = json.loads(runner.invoke(calc_inventory, [str(tmp_path), "--divergence"], obj={"json": True}).output)
    divs = {d["field"]: d for d in env["divergences"]}
    assert "vat" in divs
    assert divs["vat"]["cross_language"] is True
    assert set(divs["vat"]["languages"]) == {"php", "typescript"}
    assert divs["vat"]["distinct_formulas"] == 2


def test_command_round_funcs_extension(tmp_path):
    # a project rounding wrapper `r(...)` is recognized when declared
    _write(tmp_path, "r.php", "<?php $vat = r($base * $rate);")
    runner = CliRunner()
    env = json.loads(runner.invoke(calc_inventory, [str(tmp_path), "--round-funcs", "r"], obj={"json": True}).output)
    assert env["calculations"][0]["rounding"] == "r"


def test_command_path_not_found():
    runner = CliRunner()
    result = runner.invoke(calc_inventory, ["/no/such/path/xyz"], obj={"json": True})
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "path_not_found"


# ---- round-funcs threading (no module-global leak) + rounding semantics ----


def test_extra_round_funcs_param_recognized():
    src = b"<?php $vat = r($base * $rate);"
    # without the wrapper it's still a calc (arithmetic), but rounding is unknown
    base = extract_calcs("php", src)[0]
    assert base.rounding is None
    # with the wrapper declared, r() is recognized as the rounding fn
    widened = extract_calcs("php", src, extra_round_funcs=frozenset({"r"}))[0]
    assert widened.rounding == "r"


def test_extra_round_funcs_does_not_leak():
    # the correctness fix: a per-call widening must NOT persist to later calls
    # (would corrupt a long-running MCP server). Widen once, then call plain.
    src = b"<?php $vat = money($base);"
    extract_calcs("php", src, extra_round_funcs=frozenset({"money"}))
    after = extract_calcs("php", src)  # no widening this time
    # 'money' is not a standard rounder, so a plain call must not report it
    assert after == [] or after[0].rounding != "money"


def test_mode_arg_captured_and_overrides_language_default():
    # the proven mislabel: round($x, 2, PHP_ROUND_HALF_EVEN) is banker's, NOT half-away
    src = b"<?php $vat = round($base * $rate, 2, PHP_ROUND_HALF_EVEN);"
    c = extract_calcs("php", src)[0]
    assert c.rounding == "round"
    assert c.rounding_mode == "PHP_ROUND_HALF_EVEN"
    assert rounding_semantic(c.language, c.rounding, c.rounding_mode) == "half_to_even"


def test_python_decimal_mode_captured():
    src = b"vat = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
    c = extract_calcs("python", src)[0]
    assert c.rounding == "quantize"
    assert c.rounding_mode == "ROUND_HALF_UP"
    assert rounding_semantic("python", "quantize", "ROUND_HALF_UP") == "half_away_from_zero"


def test_unrecognized_mode_returns_none_not_wrong_label():
    # honest-unknown beats confident-wrong
    assert rounding_semantic("php", "round", "SOME_FUTURE_MODE") is None


def test_no_mode_falls_back_to_language_default():
    src = b"<?php $vat = round($base * $rate, 2);"
    c = extract_calcs("php", src)[0]
    assert c.rounding_mode is None
    assert rounding_semantic(c.language, c.rounding, c.rounding_mode) == "half_away_from_zero"


def test_same_language_mode_divergence_detected(tmp_path):
    # NEW catch: same language, same call name, different MODE -> semantics divergent
    _write(tmp_path, "a.php", "<?php $vat = round($base * $rate, 2);")
    _write(tmp_path, "b.php", "<?php $vat = round($base * $rate, 2, PHP_ROUND_HALF_EVEN);")
    runner = CliRunner()
    env = json.loads(runner.invoke(calc_inventory, [str(tmp_path), "--divergence"], obj={"json": True}).output)
    vat = next(d for d in env["divergences"] if d["field"] == "vat")
    assert vat["rounding_semantics_divergent"] is True
    assert set(vat["rounding_semantics"]) == {"half_away_from_zero", "half_to_even"}


def test_rounding_semantic_lookup():
    # the subtle cross-language bug: same "round" name, different tie behavior
    assert rounding_semantic("php", "round") == "half_away_from_zero"
    assert rounding_semantic("python", "round") == "half_to_even"  # banker's
    assert rounding_semantic("javascript", "round") == "half_up_toward_positive"
    assert rounding_semantic("php", "bcmul") == "truncate"
    assert rounding_semantic("php", None) is None
    assert rounding_semantic("cobol", "round") is None  # unknown lang


def test_fail_on_divergence_gate_exits_5(tmp_path):
    _write(tmp_path, "a.php", "<?php $vat = round($base * $rate, 2);")
    _write(tmp_path, "b.py", "vat = round(base * rate, 2)\n")
    runner = CliRunner()
    result = runner.invoke(calc_inventory, [str(tmp_path), "--fail-on-divergence"], obj={"json": True})
    assert result.exit_code == 5
    assert json.loads(result.output)["summary"]["gate_failed"] is True


def test_fail_on_divergence_gate_passes_when_consistent(tmp_path):
    # same rounding semantics on both sides -> no gate failure
    _write(tmp_path, "a.php", "<?php $vat = round($base * $rate, 2);")
    _write(tmp_path, "b.php", "<?php $vat = round($base * $rate / 1, 2);")
    runner = CliRunner()
    result = runner.invoke(calc_inventory, [str(tmp_path), "--fail-on-divergence"], obj={"json": True})
    assert result.exit_code == 0
    assert json.loads(result.output)["summary"]["gate_failed"] is False


def test_command_rounding_semantics_divergence(tmp_path):
    # same field 'vat' rounded with round() in PHP (half-away) and Python
    # (banker's) — identical call name, DIFFERENT semantics → flagged.
    _write(tmp_path, "a.php", "<?php $vat = round($base * $rate, 2);")
    _write(tmp_path, "b.py", "vat = round(base * rate, 2)\n")
    runner = CliRunner()
    env = json.loads(runner.invoke(calc_inventory, [str(tmp_path), "--divergence"], obj={"json": True}).output)
    vat = next(d for d in env["divergences"] if d["field"] == "vat")
    assert vat["rounding_semantics_divergent"] is True
    assert set(vat["rounding_semantics"]) == {"half_away_from_zero", "half_to_even"}
