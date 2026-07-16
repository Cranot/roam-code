"""Small deterministic JSON projection language for CLI output.

The supported subset is intentionally executable and dependency-free:

* ``.`` — identity
* ``.field`` / ``.field.nested``
* ``[N]`` / ``[-N]``
* ``[START:END]``
* mixed paths such as ``.symbols[:5]`` or ``.summary.verdict``
"""

from __future__ import annotations

import re
from typing import Any

_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_INDEX_RE = re.compile(r"-?\d+")
_SLICE_RE = re.compile(r"(-?\d*)?:(-?\d*)?")


def _parse_int(value: str) -> int | None:
    return int(value) if value not in {"", None} else None


def _tokenize(expression: str) -> tuple[list[tuple[str, Any]], str | None]:
    expression = (expression or "").strip()
    if expression == ".":
        return [], None
    if not expression.startswith("."):
        return [], "projection must start with '.'"
    tokens: list[tuple[str, Any]] = []
    index = 1
    while index < len(expression):
        if expression[index] == ".":
            index += 1
            if index >= len(expression):
                return [], "projection cannot end with '.'"
        if expression[index] == "[":
            close = expression.find("]", index + 1)
            if close < 0:
                return [], "projection contains an unclosed '['"
            raw = expression[index + 1 : close].strip()
            if _INDEX_RE.fullmatch(raw):
                tokens.append(("index", int(raw)))
            else:
                match = _SLICE_RE.fullmatch(raw)
                if match is None:
                    return [], f"unsupported array selector [{raw}]"
                tokens.append(
                    (
                        "slice",
                        (_parse_int(match.group(1)), _parse_int(match.group(2))),
                    )
                )
            index = close + 1
            continue
        match = _FIELD_RE.match(expression, index)
        if match is None:
            return [], f"unsupported projection syntax near {expression[index:]!r}"
        tokens.append(("field", match.group(0)))
        index = match.end()
        if index < len(expression) and expression[index] not in {".", "["}:
            return [], f"unsupported projection syntax near {expression[index:]!r}"
    return tokens, None


def apply_projection(value: object, expression: str) -> tuple[object, str | None]:
    """Apply one projection expression and return ``(value, error)``."""
    tokens, error = _tokenize(expression)
    if error is not None:
        return None, error
    current = value
    for kind, operand in tokens:
        if kind == "field":
            if not isinstance(current, dict):
                return None, (f"cannot read field {operand!r} from {type(current).__name__}")
            if operand not in current:
                return None, f"field {operand!r} is not present"
            current = current[operand]
            continue
        if kind == "index":
            if not isinstance(current, list):
                return None, f"cannot index {type(current).__name__}"
            try:
                current = current[operand]
            except IndexError:
                return None, (f"index {operand} is outside array length {len(current)}")
            continue
        if kind == "slice":
            if not isinstance(current, list):
                return None, f"cannot slice {type(current).__name__}"
            start, end = operand
            current = current[start:end]
            continue
        return None, f"unsupported projection token {kind!r}"
    return current, None


def project_cli_output(
    value: object,
    expressions: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Return a minimal structured projection envelope."""
    selected = [str(expression).strip() for expression in expressions if str(expression).strip()]
    command = str(value.get("command") or "") if isinstance(value, dict) else ""
    projected: list[dict[str, object]] = []
    for expression in selected:
        result, error = apply_projection(value, expression)
        if error is not None:
            return {
                "command": command,
                "summary": {
                    "verdict": f"Projection {expression!r} failed",
                    "state": "usage_error",
                    "partial_success": True,
                },
                "status": "usage_error",
                "isError": True,
                "error_code": "USAGE_ERROR",
                "error": error,
                "projection": expression,
                "supported_projection": ".field, .field.nested, [N], [START:END]",
            }
        projected.append({"expression": expression, "value": result})
    if len(projected) == 1:
        expression = str(projected[0]["expression"])
        return {
            "command": command,
            "summary": {
                "verdict": f"Projected {expression}",
                "projection": expression,
            },
            "projection": expression,
            "data": projected[0]["value"],
        }
    return {
        "command": command,
        "summary": {
            "verdict": f"Projected {len(projected)} fields",
            "projection_count": len(projected),
        },
        "projections": projected,
    }
