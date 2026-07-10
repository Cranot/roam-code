"""Fabricated-success detector — flags external-sink stubs that claim success.

Heuristic detector — false negatives expected, false positives should be rare.

This surfaces functions that declare an external operation but only return or
yield a success-shaped literal without performing any statically resolved
external effect.

The detector is deliberately narrow:

- only Python functions with statically parseable bodies are considered;
- only literal ``True``, success dictionaries, or HTTP 200/201 shapes count;
- the function name, docstring, or annotations must declare a known external
  sink (HTTP, network, DB, payment, or write);
- ``charge``/``pay``/``refund``/``capture`` names resolve to the payment sink;
- any resolved external I/O effect suppresses the finding;
- any parameter/global-to-effect causal edge suppresses the finding;
- unknown effects, missing causal graphs, and unresolved declarations are
  ignored.

The goal is precision.  If the code cannot prove the declaration, literal
success shape, and absence of an external effect statically, the detector
stays silent.
"""

from __future__ import annotations

import ast
import re
import textwrap
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.world_model.causal_graph import CausalGraph, classify_causal_graph
from roam.world_model.side_effects import SideEffectClassification, classify_side_effects

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

FABRICATED_SUCCESS_KINDS = ("fabricated_success_stub",)


@dataclass
class FabricatedSuccessFinding:
    """Per-symbol fabricated-success finding."""

    symbol: str
    file: str
    kind: str = "fabricated_success_stub"
    declared_sink: str = ""
    success_shape: str = ""
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
            "declared_sink": self.declared_sink,
            "success_shape": self.success_shape,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# ---------------------------------------------------------------------------
# Narrow static helpers
# ---------------------------------------------------------------------------

_DECLARED_SINK_TERMS = {
    "http": "http",
    "network": "network",
    "db": "db",
    "database": "db",
    "payment": "payment",
    "charge": "payment",
    "pay": "payment",
    "refund": "payment",
    "capture": "payment",
    "write": "write",
}
_EXTERNAL_EFFECT_KINDS = frozenset({"io_read", "io_write", "process"})
_FLOW_TO_EFFECT_KINDS = frozenset({"param_to_effect", "global_to_effect"})
_HTTP_SUCCESS_CODES = frozenset({200, 201})
_EXTERNAL_SINK_PREFIXES = ("io_read:", "io_write:", "process:")


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    """Read one source file, preserving empty slices for missing content."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", []
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except OSError as exc:
        log_swallowed(f"world_model.fabricated_success:body_read:{rel_path}", exc)
        return "", []


def _split_identifier_words(text: str) -> list[str]:
    """Split snake/kebab/camel identifiers into lowercase words."""
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [word.lower() for word in re.findall(r"[A-Za-z]+", camel_split)]


def _resolve_declared_sink(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str] | None:
    """Resolve a known external sink from the function declaration only."""
    sources: list[tuple[str, str]] = [("name", fn.name)]
    docstring = ast.get_docstring(fn, clean=False)
    if docstring:
        sources.append(("docstring", docstring))

    annotations = [arg.annotation for arg in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)]
    if fn.args.vararg:
        annotations.append(fn.args.vararg.annotation)
    if fn.args.kwarg:
        annotations.append(fn.args.kwarg.annotation)
    annotations.append(fn.returns)
    annotation_text = " ".join(ast.unparse(node) for node in annotations if node is not None)
    if annotation_text:
        sources.append(("annotation", annotation_text))

    for source, text in sources:
        for word in _split_identifier_words(text):
            sink = _DECLARED_SINK_TERMS.get(word)
            if sink:
                return sink, source
    return None


def _literal_http_code(node: ast.AST) -> int | None:
    """Return a literal HTTP success code, excluding booleans."""
    if isinstance(node, ast.Constant) and type(node.value) is int and node.value in _HTTP_SUCCESS_CODES:
        return node.value
    return None


def _success_shape(node: ast.AST | None) -> str | None:
    """Return the exact recognized literal-success shape for an expression."""
    if isinstance(node, ast.Constant) and node.value is True:
        return "return_true"

    if isinstance(node, ast.Dict):
        literal_items: dict[str, object] = {}
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            if isinstance(value, ast.Constant):
                literal_items[key.value.lower()] = value.value
        if literal_items.get("status") == "success":
            return "status_success"
        if literal_items.get("ok") is True:
            return "ok_true"
        status_code = literal_items.get("status_code", literal_items.get("status"))
        if type(status_code) is int and status_code in _HTTP_SUCCESS_CODES:
            return f"http_{status_code}"

    code = _literal_http_code(node) if node is not None else None
    if code is not None:
        return f"http_{code}"

    if isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            code = _literal_http_code(element)
            if code is not None:
                return f"http_{code}"

    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg not in {"status", "status_code"}:
                continue
            code = _literal_http_code(keyword.value)
            if code is not None:
                return f"http_{code}"
    return None


class _SuccessLiteralVisitor(ast.NodeVisitor):
    """Find success returns/yields while skipping nested declarations."""

    def __init__(self) -> None:
        self.shape: str | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        if self.shape is None:
            self.shape = _success_shape(node.value)

    def visit_Yield(self, node: ast.Yield) -> None:  # noqa: N802
        if self.shape is None:
            self.shape = _success_shape(node.value)


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Parse a source slice and return its outer function declaration."""
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError):
        return None
    return next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)


def _find_success_shape(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    visitor = _SuccessLiteralVisitor()
    for statement in fn.body:
        visitor.visit(statement)
        if visitor.shape is not None:
            return visitor.shape
    return None


def _has_external_effect(se: SideEffectClassification, causal: CausalGraph) -> bool:
    """Return True for any resolved external effect or input-to-effect edge."""
    if _EXTERNAL_EFFECT_KINDS.intersection(se.kinds or []):
        return True
    if any(sink.startswith(_EXTERNAL_SINK_PREFIXES) for sink in causal.sinks):
        return True
    return any(
        edge.kind in _FLOW_TO_EFFECT_KINDS and edge.sink.startswith(_EXTERNAL_SINK_PREFIXES) for edge in causal.edges
    )


def _classify_one(
    se: SideEffectClassification,
    causal: CausalGraph | None,
    body_text: str,
) -> FabricatedSuccessFinding | None:
    """Map side-effect and causal records plus source body to a finding."""
    if causal is None or "unknown" in (se.kinds or []):
        return None

    fn = _parse_function(body_text)
    if fn is None:
        return None
    declared = _resolve_declared_sink(fn)
    if declared is None:
        return None
    success_shape = _find_success_shape(fn)
    if success_shape is None or _has_external_effect(se, causal):
        return None

    declared_sink, declaration_source = declared
    return FabricatedSuccessFinding(
        symbol=se.symbol,
        file=se.file,
        declared_sink=declared_sink,
        success_shape=success_shape,
        evidence={
            "declaration_source": declaration_source,
            "side_effect_kinds": list(se.kinds),
            "causal_effect_edges": [],
        },
        confidence="high",
        symbol_id=se.symbol_id,
        line_start=se.line_start,
        line_end=se.line_end,
    )


def classify_fabricated_success(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
    causal_graphs: Optional[list[CausalGraph]] = None,
) -> list[FabricatedSuccessFinding]:
    """Scan symbols and report external-sink functions that fabricate success."""
    try:
        if side_effects is None:
            side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)
        if causal_graphs is None:
            causal_graphs = classify_causal_graph(
                conn,
                symbol_name=symbol_name,
                limit=limit,
                side_effects=side_effects,
            )
    except Exception as exc:  # noqa: BLE001 — unresolved machinery must stay silent
        log_swallowed("world_model.fabricated_success:classification", exc)
        return []

    try:
        repo_root = find_project_root()
    except OSError as exc:
        warnings.warn(
            f"find_project_root() failed in classify_fabricated_success "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    causal_by_symbol_id = {graph.symbol_id: graph for graph in causal_graphs if graph.symbol_id}
    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[FabricatedSuccessFinding] = []
    for file_path, items in by_file.items():
        text, lines = _read_source(repo_root, file_path)
        if not text or not lines:
            continue
        for se in items:
            line_start = se.line_start or 1
            line_end = se.line_end or line_start
            body = "".join(lines[max(0, line_start - 1) : line_end])
            finding = _classify_one(se, causal_by_symbol_id.get(se.symbol_id), body)
            if finding is not None:
                out.append(finding)

    return out


__all__ = [
    "FABRICATED_SUCCESS_KINDS",
    "FabricatedSuccessFinding",
    "classify_fabricated_success",
]
