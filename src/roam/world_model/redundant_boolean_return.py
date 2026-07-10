"""Precision-first detector for redundant boolean returns.

Author: Cranot

This detector reports only an ``if`` whose two branches are exactly literal
``return True`` and ``return False`` statements, or the equivalent adjacent
return form.  The narrow AST shape keeps the detector opt-in and conservative.
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
class RedundantBooleanReturnFinding:
    """Per-symbol redundant boolean return finding."""

    symbol: str
    file: str
    kind: str = "redundant_boolean_return"
    form: str = ""
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
            "form": self.form,
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
        log_swallowed(f"world_model.redundant_boolean_return:body_read:{rel_path}", exc)
        return "", []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Parse a source slice and return its outer function declaration."""
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError):
        return None
    return next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)


def _is_return_bool(stmt: ast.stmt, want: bool) -> bool:
    return isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant) and stmt.value.value is want


def _bodies_are_bool_pair(body: list[ast.stmt], orelse: list[ast.stmt]) -> str | None:
    if len(body) != 1 or len(orelse) != 1:
        return None
    if (_is_return_bool(body[0], True) and _is_return_bool(orelse[0], False)) or (
        _is_return_bool(body[0], False) and _is_return_bool(orelse[0], True)
    ):
        return "if_else"
    return None


def _block_statement_lists(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[list[ast.stmt]]:
    blocks: list[list[ast.stmt]] = [fn.body]
    for node in ast.walk(fn):
        for attr in ("body", "orelse"):
            statements = getattr(node, attr, None)
            if isinstance(statements, list) and statements and statements not in blocks:
                blocks.append(statements)
    return blocks


def _find_redundant_boolean_returns(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.If]]:
    findings: list[tuple[str, ast.If]] = []
    elif_nodes = {
        child
        for parent in ast.walk(fn)
        if isinstance(parent, ast.If)
        for child in parent.orelse
        if isinstance(child, ast.If)
    }
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or node in elif_nodes:
            continue
        if _bodies_are_bool_pair(node.body, node.orelse) is not None:
            findings.append(("if_else", node))

    for block in _block_statement_lists(fn):
        for index, statement in enumerate(block[:-1]):
            if not isinstance(statement, ast.If) or statement.orelse or len(statement.body) != 1:
                continue
            branch = statement.body[0]
            sibling = block[index + 1]
            if _is_return_bool(branch, True) and _is_return_bool(sibling, False):
                findings.append(("if_return_sibling", statement))
            elif _is_return_bool(branch, False) and _is_return_bool(sibling, True):
                findings.append(("if_return_sibling", statement))
    return findings


def classify_redundant_boolean_return(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[RedundantBooleanReturnFinding]:
    """Scan indexed Python symbols for exact redundant boolean return shapes."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
    except Exception as exc:  # noqa: BLE001 — unresolved classification stays silent
        log_swallowed("world_model.redundant_boolean_return:classification", exc)
        return []

    try:
        repo_root = find_project_root()
    except OSError as exc:
        warnings.warn(
            f"find_project_root() failed in classify_redundant_boolean_return "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    findings: list[RedundantBooleanReturnFinding] = []
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
            for form, node in _find_redundant_boolean_returns(fn):
                findings.append(
                    RedundantBooleanReturnFinding(
                        symbol=se.symbol,
                        file=se.file,
                        form=form,
                        evidence={"form": form, "side_effect_kinds": list(se.kinds)},
                        symbol_id=se.symbol_id,
                        line_start=line_start,
                        line_end=line_end,
                    )
                )

    return findings


__all__ = ["RedundantBooleanReturnFinding", "classify_redundant_boolean_return"]
