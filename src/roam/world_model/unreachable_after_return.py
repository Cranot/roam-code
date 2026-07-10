"""Precision detector for statements unreachable after an unconditional terminator.

Author: Cranot.
"""

from __future__ import annotations

import ast
import textwrap
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.world_model.side_effects import SideEffectClassification, classify_side_effects


@dataclass
class UnreachableAfterReturnFinding:
    """Per-symbol statement-after-terminator finding."""

    symbol: str
    file: str
    kind: str = "unreachable_after_return"
    terminator: str = ""
    dead_line: int = 0
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
            "terminator": self.terminator,
            "dead_line": self.dead_line,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    """Read one source file, preserving empty slices for missing content."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", []
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except OSError as exc:
        log_swallowed(f"world_model.unreachable_after_return:body_read:{rel_path}", exc)
        return "", []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Parse a source slice and return its outer function declaration."""
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError):
        return None
    return next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)


_TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def _terminator_name(node: ast.stmt) -> str:
    if isinstance(node, ast.Return):
        return "return"
    if isinstance(node, ast.Raise):
        return "raise"
    if isinstance(node, ast.Break):
        return "break"
    if isinstance(node, ast.Continue):
        return "continue"
    return ""


def _scan_block(stmts: list[ast.stmt]) -> tuple[str, ast.stmt, ast.stmt] | None:
    for i, stmt in enumerate(stmts):
        if isinstance(stmt, _TERMINATORS) and i < len(stmts) - 1:
            return _terminator_name(stmt), stmt, stmts[i + 1]
    return None


def _find_unreachable(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.stmt]]:
    findings: list[tuple[str, ast.stmt]] = []

    def visit_block(stmts: list[ast.stmt]) -> None:
        result = _scan_block(stmts)
        if result is not None:
            name, _terminator, dead_stmt = result
            findings.append((name, dead_stmt))
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            for field_name in ("body", "orelse", "finalbody"):
                child = getattr(stmt, field_name, None)
                if isinstance(child, list) and child and all(isinstance(item, ast.stmt) for item in child):
                    visit_block(child)
            handlers = getattr(stmt, "handlers", None)
            if handlers:
                for handler in handlers:
                    if handler.body:
                        visit_block(handler.body)
            cases = getattr(stmt, "cases", None)
            if cases:
                for case in cases:
                    if case.body:
                        visit_block(case.body)

    visit_block(fn.body)
    return findings


def classify_unreachable_after_return(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[UnreachableAfterReturnFinding]:
    """Scan indexed function bodies for provably unreachable statements."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
    except Exception as exc:  # noqa: BLE001 — unresolved machinery must stay silent
        log_swallowed("world_model.unreachable_after_return:classification", exc)
        return []

    try:
        repo_root = find_project_root()
    except OSError as exc:
        warnings.warn(
            f"find_project_root() failed in classify_unreachable_after_return "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[UnreachableAfterReturnFinding] = []
    for file_path, items in by_file.items():
        text, lines = _read_source(repo_root, file_path)
        if not text or not lines:
            continue
        for se in items:
            line_start = se.line_start or 1
            line_end = se.line_end or line_start
            body = "".join(lines[max(0, line_start - 1) : line_end])
            fn = _parse_function(body)
            if fn is None:
                continue
            for terminator, dead_stmt in _find_unreachable(fn):
                try:
                    dead_line = (se.line_start or 1) - 1 + dead_stmt.lineno
                except (AttributeError, TypeError, ValueError):
                    dead_line = se.line_start
                out.append(
                    UnreachableAfterReturnFinding(
                        symbol=se.symbol,
                        file=se.file,
                        terminator=terminator,
                        dead_line=dead_line,
                        evidence={
                            "terminator": terminator,
                            "dead_line": dead_line,
                            "side_effect_kinds": list(se.kinds),
                        },
                        symbol_id=se.symbol_id,
                        line_start=se.line_start,
                        line_end=se.line_end,
                    )
                )
    return out


__all__ = ["UnreachableAfterReturnFinding", "classify_unreachable_after_return"]
