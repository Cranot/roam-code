"""None-equality comparison detector. Author: Cranot."""

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
class NoneEqComparisonFinding:
    """Equality comparison against ``None`` that should use identity."""

    symbol: str
    file: str
    kind: str = "none_eq_comparison"
    operator: str = ""
    operand_text: str = ""
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
            "operator": self.operator,
            "operand_text": self.operand_text,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", []
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return text, text.splitlines(keepends=True)
    except OSError as exc:
        log_swallowed(f"world_model.none_eq_comparison:body_read:{rel_path}", exc)
        return rel_path, []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError, TypeError):
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _is_none_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _find_none_eq_comparisons(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        for index, (op, right) in enumerate(zip(node.ops, node.comparators)):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                left = right
                continue
            left_is_none = _is_none_literal(left)
            right_is_none = _is_none_literal(right)
            if left_is_none != right_is_none:
                operand = right if left_is_none else left
                findings.append(("==" if isinstance(op, ast.Eq) else "!=", ast.unparse(operand)))
            left = right
    return findings


def classify_none_eq_comparison(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[NoneEqComparisonFinding]:
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
    except Exception as exc:  # noqa: BLE001 - detector is best effort
        log_swallowed("world_model.none_eq_comparison:classification", exc)
        return []

    try:
        repo_root = find_project_root()
    except OSError as exc:
        warnings.warn(
            f"find_project_root() failed in classify_none_eq_comparison "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[NoneEqComparisonFinding] = []
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
            for operator, operand_text in _find_none_eq_comparisons(fn):
                out.append(
                    NoneEqComparisonFinding(
                        symbol=se.symbol,
                        file=se.file,
                        operator=operator,
                        operand_text=operand_text,
                        evidence={"side_effect_kinds": list(se.kinds)},
                        symbol_id=se.symbol_id,
                        line_start=se.line_start,
                        line_end=se.line_end,
                    )
                )
    return out


__all__ = ["NoneEqComparisonFinding", "classify_none_eq_comparison"]
