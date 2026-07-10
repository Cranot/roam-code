"""Detect exception-swallowing control flow in ``finally`` blocks.

Heuristic detector — false negatives expected, false positives should be rare.
The goal is precision.

Only literal Python AST structure is inspected.  A ``return``, ``break``, or
``continue`` in a ``finally`` block can discard an exception propagating from
the corresponding ``try`` statement, so this detector reports the first such
statement in each symbol.
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.world_model.fabricated_success import _read_source
from roam.world_model.side_effects import SideEffectClassification, classify_side_effects

RETURN_IN_FINALLY_KINDS = ("return_in_finally",)


@dataclass
class ReturnInFinallyFinding:
    """Per-symbol return-in-finally finding."""

    symbol: str
    file: str
    kind: str = "return_in_finally"
    statement_kind: str = "return"
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
            "statement_kind": self.statement_kind,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "symbol_id": self.symbol_id,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


class _FinallyControlFlowVisitor(ast.NodeVisitor):
    """Find control flow that exits the enclosing ``finally`` block."""

    def __init__(self) -> None:
        self.offender: ast.Return | ast.Break | ast.Continue | None = None
        self._loop_depth = 0

    def _record(self, node: ast.Return | ast.Break | ast.Continue) -> None:
        if self.offender is None:
            self.offender = node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        self._record(node)

    def visit_Break(self, node: ast.Break) -> None:  # noqa: N802
        if self._loop_depth == 0:
            self._record(node)

    def visit_Continue(self, node: ast.Continue) -> None:  # noqa: N802
        if self._loop_depth == 0:
            self._record(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        # A break/continue in ``orelse`` belongs to an outer loop, not this
        # loop.  Returns still count everywhere in the loop subtree.
        for statement in node.body:
            self._loop_depth += 1
            self.visit(statement)
            self._loop_depth -= 1
            if self.offender is not None:
                return
        for statement in node.orelse:
            self.visit(statement)
            if self.offender is not None:
                return

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self._visit_loop(node)


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError, TypeError):
        return None
    return next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )


class _TryFinder(ast.NodeVisitor):
    """Visit try statements in one function, excluding nested scopes."""

    def __init__(self) -> None:
        self.offender: ast.Return | ast.Break | ast.Continue | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        if self.offender is not None:
            return
        if node.finalbody:
            visitor = _FinallyControlFlowVisitor()
            for statement in node.finalbody:
                visitor.visit(statement)
                if visitor.offender is not None:
                    self.offender = visitor.offender
                    return
        self.generic_visit(node)


def _first_offender(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Return | ast.Break | ast.Continue | None:
    """Return the first offending statement in source order."""
    finder = _TryFinder()
    for statement in fn.body:
        finder.visit(statement)
        if finder.offender is not None:
            return finder.offender
    return None


def _classify_one(se: SideEffectClassification, body_text: str) -> ReturnInFinallyFinding | None:
    fn = _parse_function(body_text)
    if fn is None:
        return None
    offender = _first_offender(fn)
    if offender is None:
        return None
    statement_kind = {ast.Return: "return", ast.Break: "break", ast.Continue: "continue"}[type(offender)]
    source_offset = (se.line_start or 1) - 1
    end_lineno = offender.end_lineno or offender.lineno
    return ReturnInFinallyFinding(
        symbol=se.symbol,
        file=se.file,
        statement_kind=statement_kind,
        evidence={"statement_line": source_offset + offender.lineno},
        symbol_id=se.symbol_id,
        line_start=source_offset + offender.lineno,
        line_end=source_offset + end_lineno,
    )


def classify_return_in_finally(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[ReturnInFinallyFinding]:
    """Find exception-swallowing control flow in function ``finally`` blocks."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
        repo_root = find_project_root()
        by_file: dict[str, list[SideEffectClassification]] = {}
        for se in side_effects:
            by_file.setdefault(se.file, []).append(se)

        out: list[ReturnInFinallyFinding] = []
        for file_path, items in by_file.items():
            text, lines = _read_source(repo_root, file_path)
            if not text or not lines:
                continue
            for se in items:
                line_start = se.line_start or 1
                line_end = se.line_end or line_start
                body = "".join(lines[max(0, line_start - 1) : line_end])
                finding = _classify_one(se, body)
                if finding is not None:
                    out.append(finding)
        return out
    except Exception as exc:  # noqa: BLE001 — detector failures stay silent
        log_swallowed("world_model.return_in_finally:classification", exc)
        return []


__all__ = ["RETURN_IN_FINALLY_KINDS", "ReturnInFinallyFinding", "classify_return_in_finally"]
