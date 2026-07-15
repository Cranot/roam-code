"""Shared string/comment-aware PHP source scanning for brace/bracket matching.

Several text-parsing detectors (auth-gaps, over-fetch, migration-safety) track
``{``/``}`` (or ``[``/``]``) depth to find the extent of a block. Counting
delimiters that appear inside comments or string literals drifts the depth and
mis-scopes the block — PHP writes URL params (``{id}``) in doc comments and
strings, so this caused measured false positives (433 false auth-gaps on a real
Laravel app from ONE ``{id}`` in a comment; over-fetch resource arrays closed
early by a ``]`` in a string).

This module is the single home for the fix so per-detector copies can't drift.
The scanner skips:

- ``'…'`` / ``"…"`` string literals (with ``\\`` escapes; state carries across
  lines — PHP strings may legally span lines),
- backtick shell-exec strings (`` `cmd` `` is a string context in PHP),
- ``//`` and ``#`` line comments — but **not** PHP 8 attributes: ``#[`` opens
  an attribute, which is code, not a comment,
- ``/* … */`` block comments (state carries across lines),
- **heredocs and nowdocs** (``<<<SQL … SQL;`` / ``<<<'SQL' … SQL;``). The
  subtle one: heredoc bodies are prose/SQL with *unpaired* apostrophes
  (``-- don't``, ``O'Brien``). A quote-aware scanner without heredoc support
  flips into string state on that apostrophe and silently swallows all
  following code — a worse failure than the naive counter it replaces.
  Everything inside a heredoc (quotes, braces, ``{$var}`` interpolation) is
  ignored; the body ends at the first line whose first token is the label
  (PHP 7.3 flexible indentation supported).

Byte-parity guarantee: on code containing none of the constructs above, the
counts equal the naive ``str.count`` result exactly — consumers see no change
on plain code.
"""

from __future__ import annotations

import re

# Opening of a heredoc (<<<LABEL / <<<"LABEL") or nowdoc (<<<'LABEL').
_HEREDOC_OPEN_RE = re.compile(r"<<<\s*(?:'(\w+)'|\"(\w+)\"|(\w+))")

_QUOTE_OF = {"sq": "'", "dq": '"', "bt": "`"}

# States threaded between lines/characters:
#   None                = code
#   "sq" | "dq" | "bt"  = inside a quoted string
#   "block"             = inside a /* */ comment
#   ("heredoc", label)  = inside a heredoc/nowdoc body


def _heredoc_line_closes(line: str, label: str) -> int:
    """If this line terminates a heredoc body, return the index just past the
    label (code resumes there); else -1. PHP 7.3+: the closing label may be
    indented and be followed by ``;`` / ``,`` / ``)`` — any non-word char."""
    stripped = line.lstrip()
    if not stripped.startswith(label):
        return -1
    rest = stripped[len(label) :]
    if rest and (rest[0].isalnum() or rest[0] == "_"):
        return -1
    return line.find(label) + len(label)


def scan_events(line: str, state, on_delim):
    """Scan one line in ``state``; call ``on_delim(ch, col)`` for every
    delimiter character (``{}[]()``) found in CODE. Returns the state to carry
    into the next line (line comments never carry — they end at the newline).

    This is the single state machine every public helper drives.
    """
    if isinstance(state, tuple):  # ("heredoc", label)
        resume = _heredoc_line_closes(line, state[1])
        if resume < 0:
            return state
        # Body ended; the rest of the line after the label is code. Scan it
        # with column offsets preserved.
        tail_state = scan_events(line[resume:], None, lambda ch, col: on_delim(ch, resume + col))
        return tail_state

    i = 0
    n = len(line)
    st = state
    while i < n:
        ch = line[i]
        if st == "block":
            if ch == "*" and i + 1 < n and line[i + 1] == "/":
                st = None
                i += 2
                continue
            i += 1
            continue
        if st in _QUOTE_OF:
            if ch == "\\":
                i += 2
                continue
            if ch == _QUOTE_OF[st]:
                st = None
            i += 1
            continue
        # --- code ---
        if ch == "#":
            if i + 1 < n and line[i + 1] == "[":
                # PHP 8 attribute `#[...]` — code, not a comment. Skip the '#'
                # and let the '[' be seen as a normal delimiter.
                i += 1
                continue
            break  # '#' line comment: rest of line is dead
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break  # '//' line comment
        if ch == "/" and i + 1 < n and line[i + 1] == "*":
            st = "block"
            i += 2
            continue
        if ch == "<" and line.startswith("<<<", i):
            m = _HEREDOC_OPEN_RE.match(line, i)
            if m:
                # Heredoc body starts on the NEXT line; nothing after the
                # opener on this line is scanned (PHP requires a newline).
                return ("heredoc", m.group(1) or m.group(2) or m.group(3))
            i += 1
            continue
        if ch == "'":
            st = "sq"
        elif ch == '"':
            st = "dq"
        elif ch == "`":
            st = "bt"
        elif ch in "{}[]()":
            on_delim(ch, i)
        i += 1
    return st if st in ("block", "sq", "dq", "bt") else None


def brace_deltas(source: str) -> list[tuple[int, int]]:
    """Per-line ``(opens, closes)`` of ``{``/``}`` appearing in CODE.

    One tuple per ``splitlines()`` line; multi-line string / block-comment /
    heredoc state carries across lines so those constructs can't drift depth.
    """
    return code_brace_deltas(source.splitlines())


def code_brace_deltas(lines: list[str]) -> list[tuple[int, int]]:
    """:func:`brace_deltas` for callers that already hold a line list."""
    deltas: list[tuple[int, int]] = []
    state = None
    for line in lines:
        counts = {"{": 0, "}": 0}

        def _hit(ch, _col, _c=counts):
            if ch in _c:
                _c[ch] += 1

        state = scan_events(line, state, _hit)
        deltas.append((counts["{"], counts["}"]))
    return deltas


def matching_delim_end(text: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    """Absolute index of the ``close_ch`` matching the ``open_ch`` at/after
    ``open_pos`` — string/comment/heredoc-aware.

    ``open_pos`` must point at (or before) the opener within CODE. Returns
    ``len(text) - 1`` when unbalanced (run-to-EOF, mirroring the prior
    detector behaviour).
    """
    depth = 0
    found_open = False
    result = -1
    state = None
    offset = 0
    for raw in text[open_pos:].splitlines(keepends=True):
        line = raw.rstrip("\n").rstrip("\r")
        hits: list[tuple[str, int]] = []
        state = scan_events(line, state, lambda ch, col, _h=hits: _h.append((ch, col)))
        for ch, col in hits:
            if ch == open_ch:
                depth += 1
                found_open = True
            elif ch == close_ch and found_open:
                depth -= 1
                if depth == 0:
                    result = open_pos + offset + col
                    return result
        offset += len(raw)
    return len(text) - 1
