"""Self-comparison detector for non-name operands."""

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
class SelfComparisonFinding:
    """Per-symbol self-comparison finding."""

    symbol: str
    file: str
    kind: str = "self_comparison"
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
    """Read one source file, preserving empty slices for missing content."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", []
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except OSError as exc:
        log_swallowed(f"world_model.self_comparison:body_read:{rel_path}", exc)
        return "", []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Parse a source slice and return its outer function declaration."""
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError):
        return None
    return next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)


def _find_self_comparisons(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str]]:
    """Find comparisons whose non-name left operand is repeated on the right."""
    operator_symbols = {
        "Eq": "==",
        "NotEq": "!=",
        "Lt": "<",
        "Gt": ">",
        "LtE": "<=",
        "GtE": ">=",
        "Is": "is",
        "IsNot": "is not",
    }
    findings: list[tuple[str, str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not isinstance(left, (ast.Attribute, ast.Subscript, ast.Call)):
            continue
        left_text = ast.unparse(left)
        for op, comparator in zip(node.ops, node.comparators):
            if left_text == ast.unparse(comparator):
                operator = operator_symbols.get(type(op).__name__)
                if operator is not None:
                    findings.append((operator, left_text))
    return findings


def classify_self_comparison(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[SelfComparisonFinding]:
    """Scan indexed symbols and report repeated non-name comparison operands."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
    except Exception as exc:  # noqa: BLE001 — unresolved machinery must stay silent
        log_swallowed("world_model.self_comparison:classification", exc)
        return []

    try:
        repo_root = find_project_root()
    except OSError as exc:
        warnings.warn(
            f"find_project_root() failed in classify_self_comparison "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[SelfComparisonFinding] = []
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
            for operator, operand_text in _find_self_comparisons(fn):
                out.append(
                    SelfComparisonFinding(
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


__all__ = ["SelfComparisonFinding", "classify_self_comparison"]
