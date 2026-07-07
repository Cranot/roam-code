"""F5 / F6 / F8 regression — algo detector precision (D1b express battery).

express `algo` top-8: 0 report-worthy, 4 distinct FP classes. These pin the
deterministic post-filter helpers that remove them:

* F5 self-call miscount — `JSON.stringify` inside local `stringify` counted as
  branching recursion; `this.set` inside `app.set` gave a config setter a bogus
  "add memoization" recommendation (F7's evidenced cases, subsumed by F5).
* F6 loop-invariant — `key.toLowerCase()` flagged hoistable where `key` IS the
  loop variable (express res.download).
* F8 string-scan — `str.indexOf(';')` / `str.lastIndexOf(';', i)` treated as an
  O(m) collection lookup (express acceptParams) when it is char parsing.
"""

from __future__ import annotations

from roam.catalog.detectors import (
    _count_unqualified_self_calls,
    _extract_loop_variables,
    _lookup_is_string_scan,
)


# --- F5 -------------------------------------------------------------------
def test_f5_json_stringify_is_not_self_recursion() -> None:
    stringify = (
        "function stringify (value, replacer, spaces, escape) {\n"
        "  var json = replacer || spaces\n"
        "    ? JSON.stringify(value, replacer, spaces)\n"
        "    : JSON.stringify(value);\n"
        "  return json;\n"
        "}\n"
    )
    # 0 genuine unqualified self-calls (both are JSON.stringify).
    assert _count_unqualified_self_calls(stringify, "stringify") == 0


def test_f5_this_qualified_setter_is_not_self_recursion() -> None:
    app_set = (
        "app.set = function set(setting, val) {\n"
        "  if (arguments.length === 1) { return this.settings[setting]; }\n"
        "  this.settings[setting] = val;\n"
        "  switch (setting) {\n"
        "    case 'etag': this.set('etag fn', compileETag(val)); break;\n"
        "    case 'query parser': this.set('query parser fn', compileQueryParser(val)); break;\n"
        "  }\n"
        "  return this;\n"
        "};\n"
    )
    # this.set(...) are calls on the receiver, not local recursion.
    assert _count_unqualified_self_calls(app_set, "set") == 0


def test_f5_genuine_branching_recursion_survives() -> None:
    solve = "function solve(n) {\n  return solve(n - 1) + solve(n - 2);\n}\n"
    assert _count_unqualified_self_calls(solve, "solve") == 2


# --- F6 -------------------------------------------------------------------
def test_f6_extracts_loop_variables() -> None:
    js = "for (var i = 0; i < roots.length; i++) {\n  var root = roots[i];\n}\n"
    assert "i" in _extract_loop_variables(js)

    forof = "for (const key of Object.keys(headers)) { headers[key.toLowerCase()] = 1; }"
    assert "key" in _extract_loop_variables(forof)

    py = "for name in names:\n    print(name)\n"
    assert "name" in _extract_loop_variables(py)

    each = "roots.forEach(function (root) { resolve(root, name); })"
    assert "root" in _extract_loop_variables(each)


def test_f6_receiver_on_loop_var_is_not_invariant() -> None:
    # The receiver-depends-on-loop-var check the detector applies: given a
    # flagged call `key.toLowerCase` and loop var `key`, it must be dropped.
    snippet = "for (const key of headers) { out[key.toLowerCase()] = headers[key]; }"
    loop_vars = _extract_loop_variables(snippet)
    flagged = "key.toLowerCase"
    receiver = flagged.split(".", 1)[0]
    assert receiver in loop_vars  # => detector drops it


# --- F8 -------------------------------------------------------------------
def test_f8_string_indexof_is_string_scan() -> None:
    accept_params = (
        "function acceptParams (str) {\n"
        "  var colonIndex = str.indexOf(';');\n"
        "  while (index < length) {\n"
        "    var splitIndex = str.indexOf('=', index);\n"
        "    var colonIndex = str.indexOf(';', index);\n"
        "    index = str.lastIndexOf(';', splitIndex - 1) + 1;\n"
        "  }\n"
        "}\n"
    )
    assert _lookup_is_string_scan(accept_params, "indexOf")
    assert _lookup_is_string_scan(accept_params, "lastIndexOf")


def test_f8_collection_indexof_is_not_string_scan() -> None:
    # An array.indexOf(element) with a non-literal argument is a real lookup.
    coll = "for (const x of items) {\n  if (haystack.indexOf(x) === -1) { miss.push(x); }\n}\n"
    assert not _lookup_is_string_scan(coll, "indexOf")
