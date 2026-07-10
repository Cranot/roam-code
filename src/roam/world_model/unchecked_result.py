"""Detect inline dereferences of narrowly known optional stdlib results.

This detector reports only an attribute or subscript applied directly to a
call with a hardcoded possibly-empty return shape: ``re.match``/
``re.search``/``re.fullmatch``, a one-argument ``.get``, ``os.getenv``, or
``os.environ.get``.  It deliberately does not perform dataflow, so an inline
expression is reported even when a surrounding expression happens to guard a
separate call.  Assigned results can be checked explicitly and are not
reported.

Bare one-argument ``.get`` is necessarily a conservative shape check rather
than full type inference.  Common HTTP receiver components (``session``,
``client``, ``request``, ``response``, and ``requests``) are excluded to avoid
labeling HTTP calls as ``dict.get``.  This can produce a false negative when a
real dictionary is literally named one of those components.
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.world_model.side_effects import SideEffectClassification, classify_side_effects

_OPTIONAL_RETURNERS = {
    "re.match": "returns Optional[Match]",
    "re.search": "returns Optional[Match]",
    "re.fullmatch": "returns Optional[Match]",
    "dict.get": "returns Optional[value] without a default",
    "os.environ.get": "returns Optional[str] without a default",
    "os.getenv": "returns Optional[str]",
}
OPTIONAL_RETURNERS = _OPTIONAL_RETURNERS
_RE_MATCHERS = frozenset({"match", "search", "fullmatch"})
_HTTP_RECEIVER_NAMES = frozenset({"client", "request", "requests", "response", "session"})


@dataclass
class UncheckedResultFinding:
    """One inline dereference of a possibly-empty standard-library result."""

    symbol: str
    file: str
    kind: str = "unchecked_result"
    callee: str = ""
    access_kind: str = ""
    evidence: dict = field(default_factory=dict)
    confidence: str = "high"
    symbol_id: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "file": self.file,
            "kind": self.kind,
            "callee": self.callee,
            "access_kind": self.access_kind,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    try:
        text = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except (OSError, UnicodeError) as exc:
        log_swallowed(f"world_model.unchecked_result:body_read:{rel_path}", exc)
        return "", []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(body_text))
        return next(
            (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
            None,
        )
    except (SyntaxError, ValueError, TypeError, MemoryError) as exc:
        log_swallowed("world_model.unchecked_result:parse", exc)
        return None


def _receiver_component(node: ast.Call) -> str | None:
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    if isinstance(receiver, ast.Attribute):
        return receiver.attr
    if isinstance(receiver, ast.Name):
        return receiver.id
    return None


def _optional_callee(node: ast.Call) -> tuple[str, str] | None:
    """Return ``(callee, reason)`` only for the exact supported call shapes."""
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "re" and func.attr in _RE_MATCHERS:
            callee = f"re.{func.attr}"
            return callee, _OPTIONAL_RETURNERS[callee]
        if func.attr == "get" and len(node.args) == 1 and not node.keywords:
            if isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
                if func.value.value.id == "os" and func.value.attr == "environ":
                    callee = "os.environ.get"
                    return callee, _OPTIONAL_RETURNERS[callee]
            if _receiver_component(node) in _HTTP_RECEIVER_NAMES:
                return None
            return "dict.get", _OPTIONAL_RETURNERS["dict.get"]
        if isinstance(func.value, ast.Name) and func.value.id == "os" and func.attr == "getenv":
            if len(node.args) == 1 and not node.keywords:
                return "os.getenv", _OPTIONAL_RETURNERS["os.getenv"]
    return None


class _UncheckedResultVisitor(ast.NodeVisitor):
    """Find the first inline dereference while skipping nested scopes."""

    def __init__(self) -> None:
        self.match: tuple[str, str, ast.AST, ast.Call] | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def _visit_access(self, node: ast.Attribute | ast.Subscript) -> None:
        if self.match is not None:
            return
        if isinstance(node.value, ast.Call):
            optional = _optional_callee(node.value)
            if optional is not None:
                callee, reason = optional
                self.match = (callee, reason, node, node.value)
                return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        self._visit_access(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        self._visit_access(node)


def _classify_one(se: SideEffectClassification, body_text: str) -> UncheckedResultFinding | None:
    fn = _parse_function(body_text)
    if fn is None:
        return None
    visitor = _UncheckedResultVisitor()
    for statement in fn.body:
        visitor.visit(statement)
        if visitor.match is not None:
            break
    if visitor.match is None:
        return None
    callee, reason, node, _call = visitor.match
    line_offset = (se.line_start or 1) - 1
    node_start = getattr(node, "lineno", 1)
    node_end = getattr(node, "end_lineno", None) or node_start
    return UncheckedResultFinding(
        symbol=se.symbol,
        file=se.file,
        callee=callee,
        access_kind="attribute" if isinstance(node, ast.Attribute) else "subscript",
        evidence={"reason": reason},
        symbol_id=se.symbol_id,
        line_start=line_offset + node_start,
        line_end=line_offset + node_end,
    )


def classify_unchecked_result(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[UncheckedResultFinding]:
    """Scan indexed symbols for direct dereferences of optional results."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
        repo_root = find_project_root()
    except Exception as exc:  # noqa: BLE001 — detector failures are silent
        log_swallowed("world_model.unchecked_result:classification", exc)
        return []

    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[UncheckedResultFinding] = []
    seen: set[tuple[int, str, str]] = set()
    try:
        for file_path, items in by_file.items():
            _text, lines = _read_source(repo_root, file_path)
            if not lines:
                continue
            for se in items:
                key = (se.symbol_id, se.file, se.symbol)
                if key in seen:
                    continue
                seen.add(key)
                line_start = se.line_start or 1
                line_end = se.line_end or line_start
                body = "".join(lines[max(0, line_start - 1) : line_end])
                finding = _classify_one(se, body)
                if finding is not None:
                    out.append(finding)
    except Exception as exc:  # noqa: BLE001 — detector failures are silent
        log_swallowed("world_model.unchecked_result:classification_loop", exc)
        return []
    return out


__all__ = ["OPTIONAL_RETURNERS", "UncheckedResultFinding", "classify_unchecked_result"]
