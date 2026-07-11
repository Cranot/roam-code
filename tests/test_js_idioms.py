"""Tests for the JS/TS idiom-detector pack (``roam.catalog.js_idioms``).

Mirrors ``tests/test_idiom_precision.py``: regex-level positive AND negative
cases per detector, registry/trigger wiring, and an end-to-end run against an
in-memory index pointing at a real temp ``.js`` file.
"""

from __future__ import annotations

import pytest

from roam.catalog.js_idioms import (
    _CONCAT_REASSIGN_IN_LOOP_RE,
    _DELETE_IN_LOOP_RE,
    _JSON_DEEPCLONE_RE,
    _PUSH_THEN_SORT_IN_LOOP_RE,
    _SHIFT_IN_LOOP_RE,
    _strip_js_strings_and_comments,
)


def _hits(regex, code):
    return list(regex.finditer(_strip_js_strings_and_comments(code)))


# ---- stripping --------------------------------------------------------------


def test_strip_js_strings_and_comments_is_length_preserving():
    code = 'const a = "x.shift()";\n// b.shift() in a comment\nconst c = `multi\nline ${tpl}`;\n/* block\ncomment */\nreal();\n'
    stripped = _strip_js_strings_and_comments(code)
    assert len(stripped) == len(code)
    assert stripped.count("\n") == code.count("\n")
    assert "shift" not in stripped  # both string + comment content blanked
    assert "tpl" not in stripped  # template literal blanked (multiline)
    assert "comment" not in stripped  # block comment blanked
    assert "real()" in stripped  # real code survives


# ---- per-detector regex positives / negatives -------------------------------


def test_shift_in_loop():
    bug = "  while (q.length) {\n    const item = q.shift();\n  }\n"
    assert _hits(_SHIFT_IN_LOOP_RE, bug)
    # shift OUTSIDE any loop — one-off dequeue is fine
    safe = "  const item = q.shift();\n"
    assert not _hits(_SHIFT_IN_LOOP_RE, safe)
    # shift inside a comment must not fire (stripped)
    safe2 = "  while (q.length) {\n    // q.shift() would be O(n)\n    use(q);\n  }\n"
    assert not _hits(_SHIFT_IN_LOOP_RE, safe2)


def test_concat_reassign_in_loop():
    bug = "  for (const x of xs) {\n    acc = acc.concat([x]);\n  }\n"
    assert _hits(_CONCAT_REASSIGN_IN_LOOP_RE, bug)
    # concat from a DIFFERENT variable — not the self-rebuild idiom
    safe = "  for (const x of xs) {\n    acc = other.concat([x]);\n  }\n"
    assert not _hits(_CONCAT_REASSIGN_IN_LOOP_RE, safe)
    # outside a loop
    safe2 = "  acc = acc.concat([x]);\n"
    assert not _hits(_CONCAT_REASSIGN_IN_LOOP_RE, safe2)
    # mid-identifier scan start must not cross-match (newAcc = acc.concat)
    safe3 = "  for (const x of xs) {\n    newAcc = acc.concat([x]);\n  }\n"
    assert not _hits(_CONCAT_REASSIGN_IN_LOOP_RE, safe3)


def test_push_then_sort_in_loop():
    bug = "  for (const x of xs) {\n    acc.push(x);\n    acc.sort();\n  }\n"
    assert _hits(_PUSH_THEN_SORT_IN_LOOP_RE, bug)
    # sort AFTER the loop (dedented to the header's level) — the CORRECT
    # idiom; the indent guard in the shared scan helper rejects it. At the
    # regex level the window still matches, so assert via the guard logic.
    safe = "  for (const x of xs) {\n    acc.push(x);\n  }\n  acc.sort();\n"
    for m in _PUSH_THEN_SORT_IN_LOOP_RE.finditer(_strip_js_strings_and_comments(safe)):
        text = _strip_js_strings_and_comments(safe)
        header_indent = len(m.group("ind") or "")
        line_start = text.rfind("\n", 0, m.end() - 1) + 1
        trigger_line = text[line_start : m.end()]
        trigger_indent = len(trigger_line) - len(trigger_line.lstrip(" \t"))
        assert trigger_indent <= header_indent, "post-loop sort must be rejected by the indent guard"
    # sorting a DIFFERENT array — not the accumulator-resort idiom
    safe2 = "  for (const x of xs) {\n    acc.push(x);\n    other.sort();\n  }\n"
    assert not _hits(_PUSH_THEN_SORT_IN_LOOP_RE, safe2)


def test_json_deepclone():
    assert _hits(_JSON_DEEPCLONE_RE, "const copy = JSON.parse(JSON.stringify(obj));\n")
    # whitespace/newline between the calls still matches
    assert _hits(_JSON_DEEPCLONE_RE, "const copy = JSON.parse(\n  JSON.stringify(obj)\n);\n")
    # inside a comment — stripped, must not fire
    assert not _hits(_JSON_DEEPCLONE_RE, "// const copy = JSON.parse(JSON.stringify(obj));\n")
    # inside a string — stripped, must not fire
    assert not _hits(_JSON_DEEPCLONE_RE, 'const tip = "JSON.parse(JSON.stringify(x)) is slow";\n')
    # parse alone is fine
    assert not _hits(_JSON_DEEPCLONE_RE, "const v = JSON.parse(raw);\n")


def test_delete_in_loop():
    bug = "  for (const k of keys) {\n    delete obj[k];\n  }\n"
    assert _hits(_DELETE_IN_LOOP_RE, bug)
    bug2 = "  while (busy) {\n    delete cache.entry;\n  }\n"
    assert _hits(_DELETE_IN_LOOP_RE, bug2)
    # delete OUTSIDE a loop — one-off teardown is fine
    safe = "  delete obj[k];\n"
    assert not _hits(_DELETE_IN_LOOP_RE, safe)
    # map.delete(k) is a method call, not the delete operator
    safe2 = "  for (const k of keys) {\n    cache.delete(k);\n  }\n"
    assert not _hits(_DELETE_IN_LOOP_RE, safe2)


# ---- registry + applicability gate -------------------------------------------


def test_js_idioms_are_registered_with_triggers():
    from roam.catalog.js_idioms import JS_IDIOM_DETECTORS, JS_IDIOM_TRIGGERS

    expected = {
        "js-shift-in-loop",
        "js-concat-reassign-in-loop",
        "js-push-then-sort-in-loop",
        "js-json-deepclone",
        "js-delete-in-loop",
    }
    registered = {t for t, _w, _f in JS_IDIOM_DETECTORS}
    assert registered == expected
    assert expected <= set(JS_IDIOM_TRIGGERS), "every JS detector must carry a trigger gate"
    for task_id, _way, fn in JS_IDIOM_DETECTORS:
        assert callable(fn), task_id


def test_applicable_js_idiom_detectors_is_content_driven():
    from roam.catalog.js_idioms import applicable_js_idiom_detectors

    ids = lambda txt: {t for t, _w, _f in applicable_js_idiom_detectors(txt)}
    plain = ids("function f(x) {\n  return x + 1;\n}\n")
    assert plain == set()  # every JS detector has a trigger; none present
    assert "js-shift-in-loop" in ids("q.shift()")
    assert "js-json-deepclone" in ids("JSON.parse(JSON.stringify(x))")
    assert "js-concat-reassign-in-loop" in ids("a = a.concat(b)")
    assert "js-push-then-sort-in-loop" in ids("a.sort()")
    assert "js-delete-in-loop" in ids("delete obj.k")


def test_js_scope_setter_is_named_for_js_and_independent_from_python():
    from roam.catalog import js_idioms, python_idioms

    assert not hasattr(js_idioms, "set_idiom_scope")

    python_idioms.set_idiom_scope(None)
    js_idioms.set_js_idiom_scope(None)
    try:
        js_idioms.set_js_idiom_scope({101})
        assert js_idioms._SCOPE_FILE_IDS == {101}
        assert python_idioms._SCOPE_FILE_IDS is None

        python_idioms.set_idiom_scope({202})
        assert js_idioms._SCOPE_FILE_IDS == {101}
        assert python_idioms._SCOPE_FILE_IDS == {202}
    finally:
        js_idioms.set_js_idiom_scope(None)
        python_idioms.set_idiom_scope(None)


def test_js_pack_is_on_the_runtime_and_cli_surface():
    """The three detectors.py integration sites: runtime generator + surface."""
    from roam.catalog.detectors import _iter_registered_detectors, list_detector_surface

    runtime_ids = {t for t, _w, _f in _iter_registered_detectors()}
    assert "js-shift-in-loop" in runtime_ids
    assert "js-json-deepclone" in runtime_ids

    js_entries = [e for e in list_detector_surface() if e.get("source") == "js_idioms"]
    assert len(js_entries) == 5
    for e in js_entries:
        assert e["languages"] == ("javascript", "typescript")
        assert e["version"]  # detector_version falls back to DEFAULT_VERSION


# ---- end-to-end against a real temp .js file ---------------------------------


_JS_FIXTURE = """\
function bugShift(q) {
  while (q.length) {
    const item = q.shift();
    use(item);
  }
}

function safeShift(q) {
  const first = q.shift();
  return first;
}

function bugConcat(xs) {
  let acc = [];
  for (const x of xs) {
    acc = acc.concat([x]);
  }
  return acc;
}

function safeConcat(xs, other) {
  let acc = [];
  for (const x of xs) {
    acc = other.concat([x]);
  }
  return acc;
}

function bugPushSort(xs) {
  const acc = [];
  for (const x of xs) {
    acc.push(x);
    acc.sort();
  }
  return acc;
}

function safePushSort(xs) {
  const acc = [];
  for (const x of xs) {
    acc.push(x);
  }
  acc.sort();
  return acc;
}

function bugClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function safeClone(obj) {
  // JSON.parse(JSON.stringify(obj)) would drop functions
  return structuredClone(obj);
}

function bugDelete(obj, keys) {
  for (const k of keys) {
    delete obj[k];
  }
}

function safeDelete(obj, k) {
  delete obj[k];
}
"""

# (name, line_start, line_end) — 1-based, matching the fixture above.
_FIXTURE_SYMBOLS = [
    ("bugShift", 1, 6),
    ("safeShift", 8, 11),
    ("bugConcat", 13, 19),
    ("safeConcat", 21, 27),
    ("bugPushSort", 29, 36),
    ("safePushSort", 38, 45),
    ("bugClone", 47, 49),
    ("safeClone", 51, 54),
    ("bugDelete", 56, 60),
    ("safeDelete", 62, 64),
]


def _e2e_flake_diagnostics(conn, js_path, caught_warnings) -> str:
    """State dump appended to e2e assertion failures so a CI flake
    self-identifies: did the fixture file actually READ (parse), was the
    text the fixture's or something stale, and what was the scope?

    Motivated by the 2026-07 CI flake (``detect_js_shift_in_loop: expected
    {bugShift}, got set()``): the failure output alone could not distinguish
    a wrong-answer bug from a silently-broken setup. Root cause was
    ``_FILE_TEXT_CACHE`` aliasing across recycled connection ids — the
    "STALE TEXT" branch below would have named it from the CI log.
    """
    from roam.catalog import js_idioms
    from roam.catalog.python_idioms import _file_text

    text = _file_text(conn, 1)
    if text is None:
        parsed = "READ FAILED (_file_text -> None; fixture never parsed)"
    elif text == _JS_FIXTURE:
        parsed = f"read OK ({len(text)} chars, matches fixture)"
    else:
        parsed = f"STALE TEXT ({len(text)} chars, does NOT match fixture — cache aliasing?)"
    return (
        f"\n[flake diagnostics] fixture file: {parsed}"
        f" | on disk: exists={js_path.exists()}"
        f" | js scope _SCOPE_FILE_IDS={js_idioms._SCOPE_FILE_IDS!r}"
        f" | warnings during detection={[str(w.message) for w in caught_warnings]!r}"
    )


def test_js_detectors_end_to_end(tmp_path):
    """Each detector finds exactly its bug symbol; safe siblings stay clean."""
    import sqlite3
    import warnings

    from roam.catalog.js_idioms import (
        detect_js_concat_reassign_in_loop,
        detect_js_delete_in_loop,
        detect_js_json_deepclone,
        detect_js_push_then_sort_in_loop,
        detect_js_shift_in_loop,
        set_js_idiom_scope,
    )
    from roam.catalog.python_idioms import _clear_file_text_cache, _file_text

    js_path = tmp_path / "app.js"
    js_path.write_text(_JS_FIXTURE, encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT)")
    conn.execute(
        "CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,"
        " kind TEXT, line_start INTEGER, line_end INTEGER)"
    )
    conn.execute("INSERT INTO files VALUES (1, ?, ?)", (str(js_path), "javascript"))
    for i, (name, start, end) in enumerate(_FIXTURE_SYMBOLS, start=1):
        conn.execute("INSERT INTO symbols VALUES (?, 1, ?, 'function', ?, ?)", (i, name, start, end))

    set_js_idiom_scope(None)  # reset any leaked scope from another test
    _clear_file_text_cache()  # hermetic: no other test's cached text can serve this conn
    try:
        # Preflight: the fixture must actually read back before any detector
        # runs, so a setup/read failure fails HERE with its own name instead
        # of masquerading as "detector found nothing" (the got-set() flake).
        preflight = _file_text(conn, 1)
        assert preflight == _JS_FIXTURE, "e2e precondition failed: fixture did not read back — " + (
            "_file_text returned None (read FAILED)"
            if preflight is None
            else f"got {len(preflight)} unexpected chars (stale/aliased text?)"
        )

        cases = [
            (detect_js_shift_in_loop, "bugShift"),
            (detect_js_concat_reassign_in_loop, "bugConcat"),
            (detect_js_push_then_sort_in_loop, "bugPushSort"),
            (detect_js_json_deepclone, "bugClone"),
            (detect_js_delete_in_loop, "bugDelete"),
        ]
        for detect_fn, expected in cases:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                findings = detect_fn(conn)
            names = {f["symbol_name"] for f in findings}
            assert names == {expected}, (
                f"{detect_fn.__name__}: expected {{{expected!r}}}, got {names}"
                + _e2e_flake_diagnostics(conn, js_path, caught)
            )

        # scope narrowing: an empty scope yields no findings; reset restores
        set_js_idiom_scope(set())
        try:
            assert detect_js_shift_in_loop(conn) == []
        finally:
            set_js_idiom_scope(None)
        assert {f["symbol_name"] for f in detect_js_shift_in_loop(conn)} == {"bugShift"}
    finally:
        set_js_idiom_scope(None)  # never leak scope into a later test, even on failure
        conn.close()


def test_js_detectors_warn_loudly_when_an_indexed_file_cannot_be_read(tmp_path):
    """Loud-fallback ratchet: an indexed JS file whose source can't be READ
    must emit a RuntimeWarning instead of silently contributing zero findings
    (a read failure masquerading as "no findings" is exactly the got-set()
    flake shape). Return type stays a plain (empty) findings list."""
    import sqlite3

    from roam.catalog.js_idioms import detect_js_shift_in_loop, set_js_idiom_scope

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT)")
    conn.execute(
        "CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,"
        " kind TEXT, line_start INTEGER, line_end INTEGER)"
    )
    conn.execute("INSERT INTO files VALUES (1, ?, 'javascript')", (str(tmp_path / "does-not-exist.js"),))
    set_js_idiom_scope(None)
    try:
        with pytest.warns(RuntimeWarning, match="could not be read"):
            findings = detect_js_shift_in_loop(conn)
        assert findings == []
    finally:
        set_js_idiom_scope(None)
        conn.close()


def test_file_text_cache_survives_recycled_connection_ids(tmp_path):
    """Regression for the 2026-07 CI flake: ``_FILE_TEXT_CACHE`` was keyed by
    ``(id(conn), file_id)``; CPython recycles a dead connection's address, so
    a NEW connection whose ``id()`` collided with a dead one was silently
    served the DEAD connection's cached text — the JS e2e test then scanned a
    python-idiom test's PYTHON source and found nothing (``got set()``).
    Force the id collision and pin that the fresh connection reads ITS OWN
    file."""
    import gc
    import sqlite3

    from roam.catalog.python_idioms import _file_text

    py_path = tmp_path / "a.py"
    py_path.write_text("PYTHON_TEXT = 1\n", encoding="utf-8")
    js_path = tmp_path / "b.js"
    js_path.write_text("const JS_TEXT = 1;\n", encoding="utf-8")

    def _mk(path, language):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT)")
        conn.execute("INSERT INTO files VALUES (1, ?, ?)", (str(path), language))
        return conn

    conn_a = _mk(py_path, "python")
    assert _file_text(conn_a, 1) == "PYTHON_TEXT = 1\n"  # populates the cache
    dead_id = id(conn_a)
    conn_a.close()
    del conn_a
    gc.collect()

    held = []
    conn_b = None
    try:
        for _ in range(2000):
            candidate = _mk(js_path, "javascript")
            if id(candidate) == dead_id:
                conn_b = candidate  # landed on the recycled address
                break
            held.append(candidate)  # hold the ref so the allocator moves on
        if conn_b is None:
            pytest.skip("allocator never recycled the connection id (cannot exercise the alias)")
        assert _file_text(conn_b, 1) == "const JS_TEXT = 1;\n", (
            "recycled conn id was served another connection's stale cached text"
        )
    finally:
        for c in held:
            c.close()
        if conn_b is not None:
            conn_b.close()


def test_js_files_covers_the_language_variants(tmp_path):
    """``_js_files`` must include javascript/jsx/typescript/tsx AND vue/svelte
    rows (SFCs included after the 2026-06-11 Vue3 dogfood)."""
    import sqlite3

    from roam.catalog.js_idioms import _js_files, set_js_idiom_scope

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT)")
    rows = [
        (1, "a.js", "javascript"),
        (2, "b.jsx", "jsx"),
        (3, "c.ts", "typescript"),
        (4, "d.tsx", "tsx"),
        (5, "e.py", "python"),
        (6, "f.css", "css"),
        (7, "g.vue", "vue"),
        (8, "h.svelte", "svelte"),
    ]
    conn.executemany("INSERT INTO files VALUES (?, ?, ?)", rows)
    set_js_idiom_scope(None)
    ids = {fid for fid, _p in _js_files(conn)}
    assert ids == {1, 2, 3, 4, 7, 8}
