"""Small, shared guards for parsing untrusted bounded JSON.

Byte limits belong at each I/O boundary.  This module supplies the other
independent bound: syntactic nesting depth.  CPython's JSON decoder is
recursive, so a small payload containing hundreds of nested arrays can raise
``RecursionError`` even when its byte size is acceptable.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_MAX_JSON_DEPTH = 128


class JsonNestingError(ValueError):
    """The JSON payload exceeds the caller's structural depth budget."""


class DuplicateJsonKeyError(ValueError):
    """One object contains an ambiguous repeated key."""


def strict_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting last-key-wins ambiguity."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def ensure_bounded_json_nesting(value: str, *, max_depth: int = DEFAULT_MAX_JSON_DEPTH) -> None:
    """Reject JSON text whose ``[]``/``{}`` nesting exceeds ``max_depth``.

    Brackets inside strings are ignored and escaped quotes are handled.  Full
    syntax validation remains the JSON decoder's job.
    """

    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    depth = 0
    in_string = False
    escaped = False
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise JsonNestingError(f"JSON nesting exceeds {max_depth}")
        elif char in "]}":
            depth = max(0, depth - 1)


def loads_bounded(
    value: str | bytes | bytearray,
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    **kwargs: Any,
) -> Any:
    """Run ``json.loads`` only after enforcing a non-recursive depth scan."""

    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8")
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError("bounded JSON input must be str, bytes, or bytearray")
    ensure_bounded_json_nesting(text, max_depth=max_depth)
    try:
        return json.loads(text, **kwargs)
    except RecursionError as exc:
        # Defensive normalization in case a decoder implementation reaches a
        # recursion boundary below our conservative syntactic cap.
        raise JsonNestingError("JSON decoder recursion limit reached") from exc
