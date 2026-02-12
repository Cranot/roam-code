"""Per-symbol cognitive complexity analysis using tree-sitter ASTs.

Computes multi-factor complexity metrics for functions/methods:
- cognitive_complexity: weighted composite score (SonarSource-inspired)
- nesting_depth: max control-flow nesting
- param_count: number of parameters
- line_count: lines of code in the body
- return_count: return/throw statements
- bool_op_count: boolean operator count (&&/||/and/or)
- callback_depth: max depth of nested function/lambda definitions
"""

from __future__ import annotations

import sqlite3


# ── Node-type mappings per language family ────────────────────────────

# Control flow nodes that increment complexity AND increase nesting
_CONTROL_FLOW = {
    # Python
    "if_statement", "for_statement", "while_statement",
    "try_statement", "except_clause", "with_statement",
    "match_statement",
    # JS/TS
    "if_statement", "for_statement", "for_in_statement",
    "while_statement", "do_statement", "switch_statement",
    "try_statement", "catch_clause",
    # Java/C#/Go/Rust
    "for_statement", "enhanced_for_statement", "foreach_statement",
    "while_statement", "do_statement", "if_expression",
    "match_expression",
    # General
    "conditional_expression", "ternary_expression",
}

# Continuation nodes: +1 for flow break but NO nesting increment.
# Per SonarSource cognitive complexity spec: elif/else/case are continuations
# of the same mental model, not new nesting levels.
_CONTINUATION_FLOW = {
    "elif_clause", "else_clause",       # Python
    "case_clause",                       # Python match/case
    "switch_case",                       # JS/TS case
    "match_arm",                         # Rust
}

# Nodes that only increment complexity (no extra nesting)
_FLOW_BREAK = {
    "break_statement", "continue_statement",
    "goto_statement",
}

# Boolean operators
_BOOL_OPS = {
    # Python
    "boolean_operator",
    # JS/TS/Java/C#/Go
    "binary_expression",  # need to check operator
}

_BOOL_OP_TOKENS = {"&&", "||", "and", "or", "??"}

# Return/throw nodes
_RETURN_NODES = {
    "return_statement", "throw_statement", "raise_statement",
    "yield", "yield_statement",
}

# Function/lambda definition nodes (for callback depth)
_FUNCTION_NODES = {
    "function_definition", "function_declaration",
    "method_definition", "method_declaration",
    "arrow_function", "lambda", "lambda_expression",
    "anonymous_function", "closure_expression",
    "function_expression", "generator_function_declaration",
}

# Parameter list nodes
_PARAM_NODES = {
    "parameters", "formal_parameters", "parameter_list",
    "function_parameters", "type_parameters",
}


def _count_params(node) -> int:
    """Count parameters from a function node's parameter list."""
    for child in node.children:
        if child.type in _PARAM_NODES:
            # Count named children that are actual parameters
            # (skip commas, parens, etc.)
            count = 0
            for p in child.children:
                if p.is_named and p.type not in (
                    "(", ")", ",", "comment", "block_comment",
                    "type_annotation", "type",
                ):
                    count += 1
            return count
    return 0


def _walk_complexity(node, source: bytes, depth: int = 0) -> dict:
    """Recursively walk an AST subtree and accumulate complexity factors.

    Returns a dict with:
        cognitive: int, nesting: int, returns: int,
        bool_ops: int, callback_depth: int
    """
    result = {
        "cognitive": 0,
        "nesting": 0,
        "returns": 0,
        "bool_ops": 0,
        "callback_depth": 0,
    }

    ntype = node.type

    # Control flow: +1 base, +depth for nesting, children at depth+1
    if ntype in _CONTROL_FLOW:
        result["cognitive"] += 1 + depth
        result["nesting"] = max(result["nesting"], depth + 1)
        for child in node.children:
            child_r = _walk_complexity(child, source, depth + 1)
            _merge(result, child_r)
        return result

    # Continuation flow (elif/else/case): +1 flat, NO nesting penalty.
    # Per SonarSource spec, these are continuations of the parent structure.
    # Children stay at the same depth — not depth+1.
    if ntype in _CONTINUATION_FLOW:
        result["cognitive"] += 1
        for child in node.children:
            child_r = _walk_complexity(child, source, depth)
            _merge(result, child_r)
        return result

    # Flow breaks: +1, no nesting increase
    if ntype in _FLOW_BREAK:
        result["cognitive"] += 1
        return result

    # Return/throw
    if ntype in _RETURN_NODES:
        result["returns"] += 1

    # Boolean operators
    if ntype in _BOOL_OPS:
        # Check if it's actually a boolean op (not arithmetic)
        for child in node.children:
            if not child.is_named:
                op_text = source[child.start_byte:child.end_byte].decode(
                    "utf-8", errors="replace"
                )
                if op_text in _BOOL_OP_TOKENS:
                    result["bool_ops"] += 1
                    result["cognitive"] += 1
                    break

    # Python boolean_operator is always boolean
    if ntype == "boolean_operator":
        result["bool_ops"] += 1
        result["cognitive"] += 1

    # Nested function/lambda (callback depth)
    if ntype in _FUNCTION_NODES and depth > 0:
        result["callback_depth"] = max(result["callback_depth"], 1)
        # Recurse inside the nested function, resetting depth for its own
        # nesting but tracking callback depth
        for child in node.children:
            child_r = _walk_complexity(child, source, depth + 1)
            _merge(result, child_r)
            result["callback_depth"] = max(
                result["callback_depth"], child_r["callback_depth"] + 1
            )
        return result

    # Default: recurse children at same depth
    for child in node.children:
        child_r = _walk_complexity(child, source, depth)
        _merge(result, child_r)

    return result


def _merge(target: dict, source_r: dict):
    """Merge child complexity results into parent."""
    target["cognitive"] += source_r["cognitive"]
    target["nesting"] = max(target["nesting"], source_r["nesting"])
    target["returns"] += source_r["returns"]
    target["bool_ops"] += source_r["bool_ops"]
    target["callback_depth"] = max(
        target["callback_depth"], source_r["callback_depth"]
    )


def _find_function_node(tree, line_start: int, line_end: int):
    """Find the tree-sitter node for a function at the given line range.

    Walks the tree looking for function nodes whose line range matches
    (with 1-line tolerance for decorators).
    """
    root = tree.root_node

    def _search(node):
        # tree-sitter lines are 0-indexed, our line_start/end are 1-indexed
        node_start = node.start_point[0] + 1
        node_end = node.end_point[0] + 1

        if node.type in _FUNCTION_NODES:
            # Allow 1-3 lines tolerance for decorators/annotations
            if (abs(node_start - line_start) <= 3 and
                    abs(node_end - line_end) <= 1):
                return node

        for child in node.children:
            # Skip children that are entirely outside our range
            child_start = child.start_point[0] + 1
            child_end = child.end_point[0] + 1
            if child_end < line_start - 3 or child_start > line_end + 1:
                continue
            found = _search(child)
            if found:
                return found
        return None

    return _search(root)


def compute_symbol_complexity(
    tree, source: bytes, line_start: int, line_end: int
) -> dict:
    """Compute complexity metrics for a single symbol.

    Args:
        tree: tree-sitter parse tree
        source: raw source bytes
        line_start: 1-indexed start line of the symbol
        line_end: 1-indexed end line of the symbol

    Returns dict with all metric fields, or None if symbol node not found.
    """
    func_node = _find_function_node(tree, line_start, line_end)
    if func_node is None:
        # Fall back: compute from source line range
        return _complexity_from_source(source, line_start, line_end)

    # Params
    param_count = _count_params(func_node)

    # Line count (body only)
    body_lines = (func_node.end_point[0] - func_node.start_point[0]) + 1

    # Walk AST for cognitive complexity
    metrics = _walk_complexity(func_node, source, depth=0)

    return {
        "cognitive_complexity": round(metrics["cognitive"], 2),
        "nesting_depth": metrics["nesting"],
        "param_count": param_count,
        "line_count": body_lines,
        "return_count": metrics["returns"],
        "bool_op_count": metrics["bool_ops"],
        "callback_depth": metrics["callback_depth"],
    }


def _complexity_from_source(
    source: bytes, line_start: int, line_end: int
) -> dict:
    """Fallback: estimate complexity from raw source when AST node not found."""
    lines = source.split(b"\n")
    start_idx = max(0, line_start - 1)
    end_idx = min(len(lines), line_end)
    body = lines[start_idx:end_idx]

    # Simple heuristics
    max_indent = 0
    returns = 0
    bool_ops = 0

    for line in body:
        expanded = line.expandtabs(4)
        stripped = expanded.lstrip()
        if not stripped:
            continue
        indent = (len(expanded) - len(stripped)) // 4
        max_indent = max(max_indent, indent)

        text = stripped.decode("utf-8", errors="replace")
        # Count returns
        if text.startswith(("return ", "return;", "throw ", "raise ")):
            returns += 1
        # Count boolean ops
        for op in (" and ", " or ", " && ", " || "):
            bool_ops += text.count(op)

    line_count = end_idx - start_idx
    # Rough cognitive complexity estimate
    cognitive = max_indent * 2 + bool_ops + max(returns - 1, 0)

    return {
        "cognitive_complexity": round(cognitive, 2),
        "nesting_depth": max_indent,
        "param_count": 0,  # Can't reliably count from raw source
        "line_count": line_count,
        "return_count": returns,
        "bool_op_count": bool_ops,
        "callback_depth": 0,
    }


# ── Batch computation + storage ──────────────────────────────────────

def compute_and_store(
    conn: sqlite3.Connection,
    file_id: int,
    tree,
    source: bytes,
):
    """Compute complexity metrics for all function/method symbols in a file
    and store them in the symbol_metrics table.

    Only processes symbols with kind in ('function', 'method', 'generator',
    'constructor', 'property') — classes/modules are skipped.
    """
    CALLABLE_KINDS = (
        "function", "method", "generator", "constructor",
        "property", "closure", "lambda",
    )

    rows = conn.execute(
        "SELECT id, kind, line_start, line_end FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    metrics_batch = []
    for row in rows:
        kind = row["kind"] or ""
        if kind not in CALLABLE_KINDS:
            continue
        ls = row["line_start"]
        le = row["line_end"]
        if ls is None or le is None:
            continue

        metrics = compute_symbol_complexity(tree, source, ls, le)
        if metrics is None:
            continue

        metrics_batch.append((
            row["id"],
            metrics["cognitive_complexity"],
            metrics["nesting_depth"],
            metrics["param_count"],
            metrics["line_count"],
            metrics["return_count"],
            metrics["bool_op_count"],
            metrics["callback_depth"],
        ))

    if metrics_batch:
        conn.executemany(
            """INSERT OR REPLACE INTO symbol_metrics
               (symbol_id, cognitive_complexity, nesting_depth, param_count,
                line_count, return_count, bool_op_count, callback_depth)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            metrics_batch,
        )
