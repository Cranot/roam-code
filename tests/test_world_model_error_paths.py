"""Error-path coverage for world_model detectors using _source_helpers.

This file validates that every detector that calls _read_source / _parse_function
handles the error returns gracefully:

* _read_source returns ("", []) for a non-existent or unreadable path.
* _parse_function returns None when the body slice cannot be parsed as a
  Python function.

Both paths must produce a consistent, empty findings list — never a crash
and never a wrong return type.

Detectors covered (9 consumers of _source_helpers):
  none_eq_comparison, redundant_boolean_return, self_comparison,
  unreachable_after_return, fabricated_success, return_in_finally,
  unchecked_result, unreachable_except, restore_loss

The tests inject pre-built SideEffectClassification objects so that no
database is needed: the error paths are exercised at the file-read and
AST-parse layer, not at the DB layer.
"""

from __future__ import annotations

import ast

import pytest

from roam.world_model._source_helpers import _parse_function, _read_source
from roam.world_model.side_effects import SideEffectClassification

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _se(
    file: str,
    symbol: str = "fn",
    kinds: list[str] | None = None,
    line_start: int = 1,
    line_end: int = 5,
) -> SideEffectClassification:
    """Build a minimal SideEffectClassification for test injection."""
    return SideEffectClassification(
        symbol=symbol,
        file=file,
        kinds=kinds or ["none"],
        line_start=line_start,
        line_end=line_end,
        symbol_id=0,
    )


# ---------------------------------------------------------------------------
# Unit tests for _source_helpers directly
# ---------------------------------------------------------------------------


def test_read_source_missing_file(tmp_path):
    """_read_source on a non-existent path returns the empty sentinel."""
    text, lines = _read_source(tmp_path, "does_not_exist.py")
    assert text == ""
    assert lines == []


def test_read_source_missing_file_nested(tmp_path):
    """_read_source on a nested non-existent path returns the empty sentinel."""
    text, lines = _read_source(tmp_path, "sub/dir/missing.py")
    assert text == ""
    assert lines == []


def test_read_source_existing_file(tmp_path):
    """_read_source on an existing file returns content and non-empty lines."""
    p = tmp_path / "src.py"
    p.write_text("def f():\n    pass\n", encoding="utf-8")
    text, lines = _read_source(tmp_path, "src.py")
    assert text == "def f():\n    pass\n"
    assert len(lines) == 2


def test_parse_function_none_on_empty_string():
    """_parse_function returns None for an empty body slice."""
    result = _parse_function("")
    assert result is None


def test_parse_function_none_on_invalid_syntax():
    """_parse_function returns None for syntactically invalid Python."""
    result = _parse_function("def broken(:\n    pass\n")
    assert result is None


def test_parse_function_none_on_no_function_body():
    """_parse_function returns None when the slice has no function definition."""
    result = _parse_function("x = 1 + 2\n")
    assert result is None


def test_parse_function_returns_function_node():
    """_parse_function returns the outer FunctionDef for a valid slice."""
    body = "def greet(name):\n    return f'Hello {name}'\n"
    result = _parse_function(body)
    assert isinstance(result, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert result.name == "greet"


def test_parse_function_returns_async_function_node():
    """_parse_function returns an AsyncFunctionDef for async functions."""
    body = "async def fetch(url):\n    return await client.get(url)\n"
    result = _parse_function(body)
    assert isinstance(result, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# Detector: none_eq_comparison
# ---------------------------------------------------------------------------


def test_none_eq_comparison_missing_file(tmp_path, monkeypatch):
    """classify_none_eq_comparison returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.none_eq_comparison import classify_none_eq_comparison

    se = _se("no/such/file.py")
    result = classify_none_eq_comparison(None, side_effects=[se])
    assert result == []


def test_none_eq_comparison_unparseable_body(tmp_path, monkeypatch):
    """classify_none_eq_comparison returns [] when the body does not parse."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "broken.py"
    # Write a file whose content is not a function (no def), so _parse_function returns None.
    src.write_text("x = 1\ny = 2\n", encoding="utf-8")

    from roam.world_model.none_eq_comparison import classify_none_eq_comparison

    se = _se("broken.py", line_start=1, line_end=2)
    result = classify_none_eq_comparison(None, side_effects=[se])
    # No function in the slice → no findings
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: redundant_boolean_return
# ---------------------------------------------------------------------------


def test_redundant_boolean_return_missing_file(tmp_path, monkeypatch):
    """classify_redundant_boolean_return returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.redundant_boolean_return import classify_redundant_boolean_return

    se = _se("ghost.py")
    result = classify_redundant_boolean_return(None, side_effects=[se])
    assert result == []


def test_redundant_boolean_return_no_function_in_slice(tmp_path, monkeypatch):
    """classify_redundant_boolean_return returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "plain.py"
    src.write_text("a = True\nb = False\n", encoding="utf-8")

    from roam.world_model.redundant_boolean_return import classify_redundant_boolean_return

    se = _se("plain.py", line_start=1, line_end=2)
    result = classify_redundant_boolean_return(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: self_comparison
# ---------------------------------------------------------------------------


def test_self_comparison_missing_file(tmp_path, monkeypatch):
    """classify_self_comparison returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.self_comparison import classify_self_comparison

    se = _se("absent.py")
    result = classify_self_comparison(None, side_effects=[se])
    assert result == []


def test_self_comparison_no_function_in_slice(tmp_path, monkeypatch):
    """classify_self_comparison returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "plain.py"
    src.write_text("a = 1\n", encoding="utf-8")

    from roam.world_model.self_comparison import classify_self_comparison

    se = _se("plain.py", line_start=1, line_end=1)
    result = classify_self_comparison(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: unreachable_after_return
# ---------------------------------------------------------------------------


def test_unreachable_after_return_missing_file(tmp_path, monkeypatch):
    """classify_unreachable_after_return returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.unreachable_after_return import classify_unreachable_after_return

    se = _se("missing.py")
    result = classify_unreachable_after_return(None, side_effects=[se])
    assert result == []


def test_unreachable_after_return_no_function_in_slice(tmp_path, monkeypatch):
    """classify_unreachable_after_return returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "data.py"
    src.write_text("x = 42\n", encoding="utf-8")

    from roam.world_model.unreachable_after_return import classify_unreachable_after_return

    se = _se("data.py", line_start=1, line_end=1)
    result = classify_unreachable_after_return(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: fabricated_success
# ---------------------------------------------------------------------------


def test_fabricated_success_missing_file(tmp_path, monkeypatch):
    """classify_fabricated_success returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.fabricated_success import classify_fabricated_success

    se = _se("nope.py")
    result = classify_fabricated_success(None, side_effects=[se], causal_graphs=[])
    assert result == []


def test_fabricated_success_no_function_in_slice(tmp_path, monkeypatch):
    """classify_fabricated_success returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "conf.py"
    src.write_text("VALUE = True\n", encoding="utf-8")

    from roam.world_model.fabricated_success import classify_fabricated_success

    se = _se("conf.py", line_start=1, line_end=1)
    result = classify_fabricated_success(None, side_effects=[se], causal_graphs=[])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: return_in_finally
# ---------------------------------------------------------------------------


def test_return_in_finally_missing_file(tmp_path, monkeypatch):
    """classify_return_in_finally returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.return_in_finally import classify_return_in_finally

    se = _se("gone.py")
    result = classify_return_in_finally(None, side_effects=[se])
    assert result == []


def test_return_in_finally_no_function_in_slice(tmp_path, monkeypatch):
    """classify_return_in_finally returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "cfg.py"
    src.write_text("DEBUG = False\n", encoding="utf-8")

    from roam.world_model.return_in_finally import classify_return_in_finally

    se = _se("cfg.py", line_start=1, line_end=1)
    result = classify_return_in_finally(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: unchecked_result
# ---------------------------------------------------------------------------


def test_unchecked_result_missing_file(tmp_path, monkeypatch):
    """classify_unchecked_result returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.unchecked_result import classify_unchecked_result

    se = _se("nowhere.py")
    result = classify_unchecked_result(None, side_effects=[se])
    assert result == []


def test_unchecked_result_no_function_in_slice(tmp_path, monkeypatch):
    """classify_unchecked_result returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "mod.py"
    src.write_text("TIMEOUT = 30\n", encoding="utf-8")

    from roam.world_model.unchecked_result import classify_unchecked_result

    se = _se("mod.py", line_start=1, line_end=1)
    result = classify_unchecked_result(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: unreachable_except
# ---------------------------------------------------------------------------


def test_unreachable_except_missing_file(tmp_path, monkeypatch):
    """classify_unreachable_except returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.unreachable_except import classify_unreachable_except

    se = _se("vanished.py")
    result = classify_unreachable_except(None, side_effects=[se])
    assert result == []


def test_unreachable_except_no_function_in_slice(tmp_path, monkeypatch):
    """classify_unreachable_except returns [] when the slice has no function."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "stub.py"
    src.write_text("LOG_LEVEL = 'INFO'\n", encoding="utf-8")

    from roam.world_model.unreachable_except import classify_unreachable_except

    se = _se("stub.py", line_start=1, line_end=1)
    result = classify_unreachable_except(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Detector: restore_loss
# ---------------------------------------------------------------------------


def test_restore_loss_missing_file(tmp_path, monkeypatch):
    """classify_restore_loss returns [] when the source file is absent."""
    monkeypatch.chdir(tmp_path)
    from roam.world_model.restore_loss import classify_restore_loss

    se = _se("lost.py", kinds=["io_write"])
    result = classify_restore_loss(None, side_effects=[se])
    assert result == []


def test_restore_loss_empty_body(tmp_path, monkeypatch):
    """classify_restore_loss returns [] when the source slice is empty text."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "empty.py"
    src.write_text("\n", encoding="utf-8")

    from roam.world_model.restore_loss import classify_restore_loss

    se = _se("empty.py", kinds=["io_write"], line_start=1, line_end=1)
    result = classify_restore_loss(None, side_effects=[se])
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Return-type consistency: all detectors must return list on error path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detector_import,fn_name,extra_kwargs",
    [
        ("roam.world_model.none_eq_comparison", "classify_none_eq_comparison", {}),
        ("roam.world_model.redundant_boolean_return", "classify_redundant_boolean_return", {}),
        ("roam.world_model.self_comparison", "classify_self_comparison", {}),
        ("roam.world_model.unreachable_after_return", "classify_unreachable_after_return", {}),
        ("roam.world_model.fabricated_success", "classify_fabricated_success", {"causal_graphs": []}),
        ("roam.world_model.return_in_finally", "classify_return_in_finally", {}),
        ("roam.world_model.unchecked_result", "classify_unchecked_result", {}),
        ("roam.world_model.unreachable_except", "classify_unreachable_except", {}),
        ("roam.world_model.restore_loss", "classify_restore_loss", {}),
    ],
)
def test_all_detectors_return_list_on_missing_file(tmp_path, monkeypatch, detector_import, fn_name, extra_kwargs):
    """Every detector returns a list (never raises) when passed a non-existent file."""
    monkeypatch.chdir(tmp_path)
    import importlib

    mod = importlib.import_module(detector_import)
    fn = getattr(mod, fn_name)

    se = _se("not_here.py", kinds=["io_write"])
    result = fn(None, side_effects=[se], **extra_kwargs)
    assert isinstance(result, list), f"{fn_name} must return list on missing file, got {type(result)}"
    assert result == [], f"{fn_name} must return empty list on missing file, got {result!r}"
