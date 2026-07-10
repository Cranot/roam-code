"""Precision-first detector for unreachable ordered ``except`` clauses.

Only a small, explicit set of builtin superclass relationships is considered
proven.  Unknown and custom exception classes stay silent so that a possible
ordering problem never becomes a speculative finding.  Any internal failure
also stays silent and is recorded through :func:`log_swallowed`.
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

_BUILTIN_SUPERTYPES: dict[str, set[str]] = {
    "BaseException": {
        "Exception",
        "GeneratorExit",
        "KeyboardInterrupt",
        "SystemExit",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "OSError",
        "IOError",
        "RuntimeError",
        "ArithmeticError",
        "LookupError",
        "ImportError",
        "ModuleNotFoundError",
        "FileNotFoundError",
        "ZeroDivisionError",
        "UnicodeError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "BrokenPipeError",
        "PermissionError",
        "IsADirectoryError",
        "NotADirectoryError",
        "FileExistsError",
        "OverflowError",
        "FloatingPointError",
    },
    "Exception": {
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "OSError",
        "IOError",
        "RuntimeError",
        "ArithmeticError",
        "LookupError",
        "ImportError",
        "FileNotFoundError",
        "ZeroDivisionError",
        "UnicodeError",
        "ConnectionError",
        "ModuleNotFoundError",
    },
    "OSError": {
        "FileNotFoundError",
        "PermissionError",
        "IsADirectoryError",
        "NotADirectoryError",
        "FileExistsError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "BrokenPipeError",
    },
    "ArithmeticError": {"ZeroDivisionError", "OverflowError", "FloatingPointError"},
    "LookupError": {"KeyError", "IndexError"},
    "ConnectionError": {"ConnectionResetError", "ConnectionRefusedError", "BrokenPipeError"},
    "ImportError": {"ModuleNotFoundError"},
    "ValueError": {"UnicodeError"},
}
_KNOWN_BUILTINS = set(_BUILTIN_SUPERTYPES) | {
    subclass for subclasses in _BUILTIN_SUPERTYPES.values() for subclass in subclasses
}
UNREACHABLE_EXCEPT_KINDS = ("unreachable_except",)


@dataclass
class UnreachableExceptFinding:
    """One later exception handler shadowed by an earlier builtin handler."""

    symbol: str
    file: str
    kind: str = "unreachable_except"
    shadowing_type: str = ""
    shadowed_type: str = ""
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
            "shadowing_type": self.shadowing_type,
            "shadowed_type": self.shadowed_type,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "symbol_id": self.symbol_id,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    try:
        text = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except Exception as exc:  # noqa: BLE001 — detector must fail open
        log_swallowed(f"world_model.unreachable_except:body_read:{rel_path}", exc)
        return "", []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except Exception as exc:  # noqa: BLE001 — malformed slices stay silent
        log_swallowed("world_model.unreachable_except:parse", exc)
        return None
    return next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )


def _caught_type_names(node: ast.AST | None) -> set[str]:
    """Resolve only builtin names, including ``builtins.X`` attributes."""
    if isinstance(node, ast.Name):
        return {node.id} if node.id in _KNOWN_BUILTINS else set()
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "builtins" and node.attr in _KNOWN_BUILTINS:
            return {node.attr}
        return set()
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for element in node.elts:
            names.update(_caught_type_names(element))
        return names
    return set()


def _shadowed_pair(handlers: list[ast.ExceptHandler]) -> tuple[ast.ExceptHandler, str, str] | None:
    for later_index, later in enumerate(handlers):
        later_types = _caught_type_names(later.type)
        if not later_types:
            continue
        for earlier in handlers[:later_index]:
            for shadowing_type in _caught_type_names(earlier.type):
                for shadowed_type in later_types:
                    if shadowing_type != shadowed_type and shadowed_type in _BUILTIN_SUPERTYPES.get(
                        shadowing_type, set()
                    ):
                        return later, shadowing_type, shadowed_type
    return None


class _TryVisitor(ast.NodeVisitor):
    """Find the first source-order shadowed handler, excluding nested scopes."""

    def __init__(self) -> None:
        self.result: tuple[ast.ExceptHandler, str, str] | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        if self.result is not None:
            return
        self.result = _shadowed_pair(node.handlers)
        if self.result is None:
            self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:  # noqa: N802
        """Apply the same proven ordering rule to ``except*`` handlers."""
        if self.result is not None:
            return
        self.result = _shadowed_pair(node.handlers)
        if self.result is None:
            self.generic_visit(node)


def classify_unreachable_except(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[UnreachableExceptFinding]:
    """Find one proven unreachable later ``except`` clause per symbol."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
        repo_root = find_project_root()
    except Exception as exc:  # noqa: BLE001 — detector must fail open
        log_swallowed("world_model.unreachable_except:classification", exc)
        return []

    by_file: dict[str, list[SideEffectClassification]] = {}
    for side_effect in side_effects:
        by_file.setdefault(side_effect.file, []).append(side_effect)

    findings: list[UnreachableExceptFinding] = []
    seen_symbols: set[tuple[int, str]] = set()
    for file_path, items in by_file.items():
        try:
            _, lines = _read_source(repo_root, file_path)
            if not lines:
                continue
            for side_effect in items:
                symbol_key = (side_effect.symbol_id, side_effect.symbol)
                if symbol_key in seen_symbols:
                    continue
                seen_symbols.add(symbol_key)
                line_start = side_effect.line_start or 1
                line_end = side_effect.line_end or line_start
                function = _parse_function("".join(lines[max(0, line_start - 1) : line_end]))
                if function is None:
                    continue
                visitor = _TryVisitor()
                for statement in function.body:
                    visitor.visit(statement)
                    if visitor.result is not None:
                        break
                if visitor.result is None:
                    continue
                handler, shadowing_type, shadowed_type = visitor.result
                # handler.lineno is relative to the parsed slice (which starts at
                # the function's absolute line_start); offset it back to file lines.
                handler_start = (handler.lineno or 1) + line_start - 1
                handler_end = (handler.end_lineno or handler.lineno or 1) + line_start - 1
                findings.append(
                    UnreachableExceptFinding(
                        symbol=side_effect.symbol,
                        file=side_effect.file,
                        shadowing_type=shadowing_type,
                        shadowed_type=shadowed_type,
                        evidence={
                            "shadowing_type": shadowing_type,
                            "shadowed_type": shadowed_type,
                        },
                        symbol_id=side_effect.symbol_id,
                        line_start=handler_start,
                        line_end=handler_end,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — each symbol/file fails open
            log_swallowed(f"world_model.unreachable_except:{file_path}", exc)
    return findings


__all__ = ["UNREACHABLE_EXCEPT_KINDS", "UnreachableExceptFinding", "classify_unreachable_except"]
