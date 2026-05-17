"""Shared signature-string parsing helpers.

A leaf module (no roam-internal imports) hosting helpers that parse
``def foo(a, b=1, *args)``-style signature strings stored on
``symbols.signature``. Multiple analysis layers (taint, dataflow rules,
…) need the same depth-tracking parse, and divergent copies have caused
cross-layer metric drift in the past — see CLAUDE.md "Cross-command
metric divergence" + the `_parse_param_names` clone cluster on
roam-code itself (sim=0.984, taint.py + rules/dataflow.py).

Kept as a top-level module (`roam._signature_utils`) rather than under
`roam.analysis` or `roam.rules` so neither package owns the dependency
direction.
"""

from __future__ import annotations

import re

# Parameter names that are NEVER user-meaningful — instance/class
# receivers (``self``/``cls``/``this``) and the conventional ignored
# placeholder ``_``. Callers that want a wider ignore set should filter
# the returned list themselves rather than mutating this constant.
_IGNORED_PARAM_NAMES = frozenset({"_", "self", "cls", "this"})


def parse_param_names(signature: str | None) -> list[str]:
    """Extract concrete parameter names from a signature string.

    Handles nested generics / default values / ``*args``/``**kwargs``
    markers / type annotations. The parser tracks bracket depth so that
    a parameter like ``cb: Callable[[int, str], None]`` is treated as a
    single comma-separated entry rather than three.

    Returns ``[]`` for ``None`` / empty / unparseable signatures.
    """
    if not signature:
        return []
    m = re.search(r"\(([^)]*)\)", signature)
    if not m:
        return []
    params_str = m.group(1).strip()
    if not params_str:
        return []

    depth = 0
    current: list[str] = []
    parts: list[str] = []
    for ch in params_str:
        if ch in "([{<":
            depth += 1
            current.append(ch)
        elif ch in ")]}>":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())

    names: list[str] = []
    for part in parts:
        token = part
        while token.startswith("*"):
            token = token[1:]
        token = token.split(":", 1)[0].split("=", 1)[0].strip()
        if token and token not in _IGNORED_PARAM_NAMES:
            names.append(token)
    return names
