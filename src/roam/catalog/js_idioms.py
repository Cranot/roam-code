"""JavaScript/TypeScript-specific anti-pattern detectors.

The sibling of ``src/roam/catalog/python_idioms.py`` for the JS/TS family
(javascript / jsx / typescript / tsx). The universal catalog in
``detectors.py`` covers language-agnostic algorithm patterns; this module
adds JS-canonical performance footguns that would muddy the registry there.

Each detector returns the same finding-dict shape as the Python idiom
detectors (built via :func:`python_idioms._idiom_finding`), so they plug
into the same ``roam algo`` / calibration / display plumbing.

Initial detectors (loop-body performance idioms + one global):

1. **``arr.shift()`` in a loop** — every dequeue shifts the whole array
   (O(n) per dequeue, O(n^2) drain). Use an index pointer or a real queue.
2. **``acc = acc.concat(...)`` in a loop** — rebuilds the array every
   pass (quadratic). ``acc.push(...items)`` mutates in place.
3. **``arr.push(...)`` then ``arr.sort(...)`` in the same loop body** —
   re-sorts the accumulator every pass (O(n^2 log n)).
4. **``JSON.parse(JSON.stringify(x))`` deep clone** — anywhere, not just
   loops. ``structuredClone(x)`` is the modern equivalent.
5. **``delete obj.key`` / ``delete obj[key]`` in a loop** — V8/JSC
   hidden-class deoptimization in hot paths.

Detection model
---------------
Line-anchored regex over string/comment-stripped source text, exactly like
the Python pack. The loop-window prefix (``_JS_LOOP_PREFIX``) matches a
``for``/``while`` header and a bounded body window; the shared scan helper
then applies an **indent guard**: the trigger line must be indented deeper
than the loop header. This is a heuristic that assumes conventionally
formatted JS (body indented under the header) — minified or single-line
code is invisible to it, which is the right trade-off for a precision-first
sweep.

Like the Python sibling's ``append-then-sort`` detector, the
``push-then-sort`` window cannot tell a persistent accumulator from a
collection rebuilt fresh each outer iteration (sorting a per-iteration
array once is legitimate) — hence its MEDIUM confidence.
"""

from __future__ import annotations

from functools import partial
import re
import sqlite3

# Language-agnostic plumbing shared with the Python pack: disk-read cache,
# symbol attribution, and the finding-dict constructor.
from roam.catalog.python_idioms import (
    _enclosing_symbol,
    _file_text,
    _idiom_finding,
    _line_to_symbol,
    _set_idiom_scope_value,
)

__all__ = [
    "JS_IDIOM_DETECTORS",
    "JS_IDIOM_TRIGGERS",
    "applicable_js_idiom_detectors",
    "set_js_idiom_scope",
    "detect_js_shift_in_loop",
    "detect_js_concat_reassign_in_loop",
    "detect_js_push_then_sort_in_loop",
    "detect_js_json_deepclone",
    "detect_js_delete_in_loop",
]

# ---------------------------------------------------------------------------
# File selection + call-scoped filter (mirrors python_idioms)
# ---------------------------------------------------------------------------

# Language strings as stored in the ``files.language`` column / produced by
# ``roam.languages.registry`` for the JS family. ``vue``/``svelte`` SFCs are
# INCLUDED — excluding them blinds the sweep on SFC-heavy codebases (most
# Vue apps keep nearly all logic in SFCs). Their template halves cannot trip these
# patterns — every detector needs a JS-specific call shape (``.shift()``,
# ``.concat(``, ``JSON.parse(JSON.stringify(``, ``delete x[``) that template
# directive syntax does not produce, so scanning the whole SFC is safe.
# 6 entries — 4 core JS-family labels + 2 SFC labels (vue/svelte). The SFC
# inclusion is load-bearing: a Vue3 production app indexed 370 vue vs 6 js
# files, so excluding SFCs blinded the sweep to ~98% of its JS surface.
_JS_LANGUAGES = ("javascript", "jsx", "typescript", "tsx", "vue", "svelte")

# Call-scoped file filter — the JS pack keeps its OWN module global (it cannot
# share python_idioms' since that pack's scope is applied independently), but
# the mechanism is identical: ``run_detectors`` applies/resets both setters.
_SCOPE_FILE_IDS: set[int] | None = None

set_js_idiom_scope = partial(_set_idiom_scope_value, globals())


def _js_files(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Return ``(file_id, path)`` for every JS/TS-family file in the index
    (or only the files in the active ``_SCOPE_FILE_IDS`` when a scope is set)."""
    placeholders = ",".join("?" for _ in _JS_LANGUAGES)
    rows = conn.execute(
        f"SELECT id, path FROM files WHERE language IN ({placeholders})",
        _JS_LANGUAGES,
    ).fetchall()
    out = [(int(r[0]), r[1]) for r in rows]
    if _SCOPE_FILE_IDS is not None:
        out = [(fid, p) for fid, p in out if fid in _SCOPE_FILE_IDS]
    return out


# ---------------------------------------------------------------------------
# String/comment stripping (length-preserving, like the Python pack)
# ---------------------------------------------------------------------------

# One alternation scanned left-to-right so ``"// not a comment"`` and
# ``// "not a string"`` resolve correctly. Template literals and block
# comments may span newlines; quoted strings and line comments cannot.
# Known heuristic gap: regex literals (``/.../``) are not blanked.
_JS_STRIP_RE = re.compile(
    r"`(?:\\.|[^`\\])*`"  # template literal (multiline; ${...} blanked too)
    r"|\"(?:\\.|[^\"\\\n])*\""  # double-quoted string
    r"|'(?:\\.|[^'\\\n])*'"  # single-quoted string
    r"|/\*[\s\S]*?\*/"  # block comment (multiline)
    r"|//[^\n]*",  # line comment
)


def _strip_js_strings_and_comments(text: str) -> str:
    """Replace JS strings + comments with same-length whitespace so the
    detector regexes don't false-match inside string literals or comments.

    Length-preserving (newlines kept) so ``text.count("\\n", 0, match.start())``
    still yields the original line number."""
    if not text:
        return text

    def _blank(match: re.Match) -> str:
        seg = match.group(0)
        if "\n" in seg:
            return "\n".join(" " * len(part) for part in seg.split("\n"))
        return " " * len(seg)

    return _JS_STRIP_RE.sub(_blank, text)


# ---------------------------------------------------------------------------
# Pattern regexes
# ---------------------------------------------------------------------------

# Loop-window prefix: a for/while header line, then a bounded body window.
# Mirrors python_idioms._LOOP_PREFIX; the C-style header keeps its parens.
# The named ``ind`` group feeds the indent guard in _detect_js_loop_idiom —
# a heuristic that works on conventionally formatted JS.
_JS_LOOP_PREFIX = r"^(?P<ind>[ \t]*)(?:for|while)\s*\([^\n]*\)[^\n]*\n[\s\S]{0,300}?"

# queue.shift() in a loop — O(n) per dequeue → index pointer / real queue
_SHIFT_IN_LOOP_RE = re.compile(
    _JS_LOOP_PREFIX + r"(?<![\w.$])(?P<name>\w+)\.shift\(\)",
    re.MULTILINE,
)
# acc = acc.concat(...) → acc.push(...items)  (quadratic rebuild)
_CONCAT_REASSIGN_IN_LOOP_RE = re.compile(
    _JS_LOOP_PREFIX + r"(?<![\w.$])(?P<name>\w+)\s*=\s*(?P=name)\.concat\(",
    re.MULTILINE,
)
# acc.push(...) ... acc.sort(...) in the SAME loop body (sorting a fresh
# per-iteration array is fine; sorting the accumulator every pass is
# O(n^2 log n) → sort ONCE after the loop)
_PUSH_THEN_SORT_IN_LOOP_RE = re.compile(
    _JS_LOOP_PREFIX + r"(?<![\w.$])(?P<name>\w+)\.push\([^\n]*\)[\s\S]{0,200}?(?<![\w.$])(?P=name)\.sort\(",
    re.MULTILINE,
)
# JSON.parse(JSON.stringify(x)) deep clone — anywhere, not loop-scoped
_JSON_DEEPCLONE_RE = re.compile(r"JSON\.parse\(\s*JSON\.stringify\(")
# delete obj.key / delete obj[key] in a loop — hidden-class deopt
_DELETE_IN_LOOP_RE = re.compile(
    _JS_LOOP_PREFIX + r"(?<![\w.$])delete\s+\w+[\[.]",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Shared loop-window scan (indent guard identical to python_idioms)
# ---------------------------------------------------------------------------


def _detect_js_loop_idiom(
    conn: sqlite3.Connection,
    regex: re.Pattern[str],
    *,
    task_id: str,
    detected_way: str,
    reason: str,
    fix: str,
    confidence: str,
) -> list[dict]:
    """Shared scan for the JS loop-body idioms above. The reason/fix strings
    may reference ``{name}`` — replaced with the named capture group (the
    accumulator/collection variable) when the regex has one.

    Indent guard: the trigger must sit INSIDE the loop body — the window
    regex alone also matches code AFTER the loop (e.g. push-in-loop + one
    sort after = the correct idiom). The trigger line's indentation must be
    strictly deeper than the loop header's. Heuristic; assumes
    conventionally formatted JS."""
    findings: list[dict] = []
    for file_id, path in _js_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_js_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for line_no, sym, name in _guarded_window_hits(text, regex, sym_index):
            findings.append(
                _idiom_finding(
                    task_id=task_id,
                    detected_way=detected_way,
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=reason.format(name=name),
                    confidence=confidence,
                    fix=fix.format(name=name),
                )
            )
    return findings


def _guarded_window_hits(text: str, regex: re.Pattern[str], sym_index):
    """Yield ``(line_no, enclosing_symbol, name)`` for each regex hit that
    passes the indent guard and resolves to an enclosing symbol."""
    for match in regex.finditer(text):
        header_indent = len(match.group("ind") or "")
        line_start = text.rfind("\n", 0, match.end() - 1) + 1
        trigger_line = text[line_start : match.end()]
        trigger_indent = len(trigger_line) - len(trigger_line.lstrip(" \t"))
        if trigger_indent <= header_indent:
            continue
        line_no = text.count("\n", 0, match.end()) + 1
        sym = _enclosing_symbol(line_no, sym_index)
        if sym is None:
            continue
        name = match.group("name") if "name" in regex.groupindex else ""
        yield line_no, sym, name


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_js_shift_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``arr.shift()`` inside a loop — every dequeue shifts the whole array
    (O(n) per dequeue, O(n^2) to drain). An index pointer keeps O(1)."""
    return _detect_js_loop_idiom(
        conn,
        _SHIFT_IN_LOOP_RE,
        task_id="js-shift-in-loop",
        detected_way="array-as-queue",
        reason="``{name}.shift()`` in a loop shifts every remaining element (O(n) per dequeue)",
        fix="let i = 0; ... {name}[i++]  // index pointer; or use a real queue/deque structure",
        confidence="high",
    )


def detect_js_concat_reassign_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``acc = acc.concat(...)`` inside a loop — ``concat`` returns a NEW
    array, so the whole accumulator is copied every pass (quadratic)."""
    return _detect_js_loop_idiom(
        conn,
        _CONCAT_REASSIGN_IN_LOOP_RE,
        task_id="js-concat-reassign-in-loop",
        detected_way="concat-reassign",
        reason="``{name} = {name}.concat(...)`` in a loop copies the whole array every iteration (O(n^2))",
        fix="{name}.push(...items)  // mutates in place at O(1) amortized",
        confidence="high",
    )


def detect_js_push_then_sort_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``arr.push(...)`` then ``arr.sort(...)`` in the same loop body —
    re-sorts the accumulator every pass (O(n^2 log n)).

    Confidence is MEDIUM, not high: like the Python ``append-then-sort``
    sibling, the regex cannot tell a persistent accumulator from an array
    rebuilt fresh each outer iteration (sorting a per-iteration array once
    is legitimate)."""
    return _detect_js_loop_idiom(
        conn,
        _PUSH_THEN_SORT_IN_LOOP_RE,
        task_id="js-push-then-sort-in-loop",
        detected_way="push-then-sort",
        reason="``{name}`` is pushed to AND re-sorted inside the same loop (O(n^2 log n) if it persists across iterations)",
        fix="push inside the loop; ONE {name}.sort(...) after it",
        confidence="medium",
    )


def detect_js_json_deepclone(conn: sqlite3.Connection) -> list[dict]:
    """``JSON.parse(JSON.stringify(x))`` deep clone — matches ANYWHERE, not
    just loops. Serializes + reparses the whole object graph; silently drops
    functions, ``undefined``, and mangles ``Date``/``Map``/``Set``."""
    findings: list[dict] = []
    for file_id, path in _js_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_js_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _JSON_DEEPCLONE_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="js-json-deepclone",
                    detected_way="json-roundtrip-clone",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=(
                        "``JSON.parse(JSON.stringify(x))`` clone serializes the whole graph; "
                        "drops functions/undefined and mangles Dates — and in a loop the cost is quadratic-ish"
                    ),
                    confidence="medium",
                    fix="structuredClone(x)  // the modern equivalent; handles Dates/Maps/Sets, no stringify cost",
                )
            )
    return findings


def detect_js_delete_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``delete obj.key`` / ``delete obj[key]`` inside a loop — forces the
    engine to abandon the object's hidden class (V8 shape deopt), turning
    fast property access into dictionary-mode lookups in a hot path."""
    return _detect_js_loop_idiom(
        conn,
        _DELETE_IN_LOOP_RE,
        task_id="js-delete-in-loop",
        detected_way="delete-in-hot-path",
        reason="``delete`` on an object property inside a loop triggers hidden-class deoptimization in the hot path",
        fix="set the value to undefined, or use a Map (map.delete(k) is shape-stable)",
        confidence="medium",
    )


# ---------------------------------------------------------------------------
# Registry + applicability gate (mirrors python_idioms)
# ---------------------------------------------------------------------------

# Same (task_id, way_id, detect_fn) shape as PYTHON_IDIOM_DETECTORS so
# registration in detectors.py is one import line.
JS_IDIOM_DETECTORS = [
    ("js-shift-in-loop", "array-as-queue", detect_js_shift_in_loop),
    ("js-concat-reassign-in-loop", "concat-reassign", detect_js_concat_reassign_in_loop),
    ("js-push-then-sort-in-loop", "push-then-sort", detect_js_push_then_sort_in_loop),
    ("js-json-deepclone", "json-roundtrip-clone", detect_js_json_deepclone),
    ("js-delete-in-loop", "delete-in-hot-path", detect_js_delete_in_loop),
]

# Cheap applicability gate: a detector whose trigger token can't appear in
# the changed text CANNOT produce a finding, so don't even run it. A
# detector with NO entry here would be always-applicable; every JS detector
# currently carries a trigger.
JS_IDIOM_TRIGGERS: dict[str, tuple[str, ...]] = {
    "js-shift-in-loop": (".shift(",),
    "js-concat-reassign-in-loop": (".concat(",),
    "js-push-then-sort-in-loop": (".sort(",),
    "js-json-deepclone": ("JSON.parse",),
    "js-delete-in-loop": ("delete",),
}


def applicable_js_idiom_detectors(scanned_text: str):
    """Yield ``(task_id, way, fn)`` for the JS detectors that COULD fire on
    ``scanned_text`` — i.e. those whose trigger token is present, plus every
    detector that declares no trigger. Lets a caller skip detectors that
    can't possibly apply to the change."""
    for task_id, way, fn in JS_IDIOM_DETECTORS:
        trig = JS_IDIOM_TRIGGERS.get(task_id)
        if trig is None or any(t in scanned_text for t in trig):
            yield task_id, way, fn
