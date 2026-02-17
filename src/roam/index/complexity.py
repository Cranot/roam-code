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
    # JS/TS/Java/C#/Go — needs child token check for actual boolean ops
    "binary_expression",
}
# Python "boolean_operator" is handled separately (line ~165) since it's always boolean

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

    # Control flow: +1 base, +triangular nesting penalty.
    # Triangular number depth*(depth+1)/2 models the superlinear cognitive
    # load of deeply nested code (Sweller's Cognitive Load Theory, 1988).
    # depth 0→+1, 1→+2, 2→+4, 3→+7, 4→+11 (vs linear: 1,2,3,4,5).
    if ntype in _CONTROL_FLOW:
        result["cognitive"] += 1 + depth * (depth + 1) // 2
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


# ── Halstead metrics ────────────────────────────────────────────────

# Operator node types (control flow, assignments, calls, etc.)
_OPERATOR_TYPES = {
    "if_statement", "for_statement", "while_statement", "do_statement",
    "switch_statement", "try_statement", "catch_clause", "return_statement",
    "throw_statement", "raise_statement", "break_statement", "continue_statement",
    "for_in_statement", "match_statement", "match_expression", "with_statement",
    "conditional_expression", "ternary_expression", "assignment_expression",
    "augmented_assignment", "call_expression", "new_expression",
    "yield_statement", "yield",
}

# Operand node types (identifiers, literals)
_OPERAND_TYPES = {
    "identifier", "property_identifier", "shorthand_property_identifier",
    "number", "integer", "float", "string", "template_string",
    "true", "false", "none", "null", "undefined",
}


def _compute_halstead(func_node, source: bytes) -> dict:
    """Compute Halstead complexity metrics from AST.

    Counts distinct and total operators (control flow, assignments, calls)
    and operands (identifiers, literals) to compute:
      Volume   = N * log2(n)     — information content in bits
      Difficulty = (n1/2) * (N2/n2)
      Effort   = D * V           — mental effort to understand
      Bugs     = V / 3000        — estimated delivered bugs

    Reference: Halstead (1977), "Elements of Software Science."
    """
    import math as _math

    operators = set()
    operands = set()
    total_operators = 0
    total_operands = 0

    def _walk(node):
        nonlocal total_operators, total_operands
        ntype = node.type

        if ntype in _OPERATOR_TYPES:
            operators.add(ntype)
            total_operators += 1
        elif ntype in _OPERAND_TYPES:
            text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            operands.add(text)
            total_operands += 1

        # Also count non-named operator tokens (+, -, =, ==, etc.)
        if not node.is_named and node.parent and node.parent.type in (
            "binary_expression", "unary_expression", "assignment_expression",
            "augmented_assignment", "comparison_operator", "boolean_operator",
        ):
            op_text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            if op_text.strip():
                operators.add(op_text)
                total_operators += 1

        for child in node.children:
            _walk(child)

    _walk(func_node)

    n1 = len(operators)   # distinct operators
    n2 = len(operands)    # distinct operands
    N1 = total_operators  # total operators
    N2 = total_operands   # total operands

    n = n1 + n2           # vocabulary
    N = N1 + N2           # length

    if n <= 0 or n2 <= 0:
        return {"volume": 0.0, "difficulty": 0.0, "effort": 0.0, "bugs": 0.0}

    volume = round(N * _math.log2(n), 1) if n > 1 else 0.0
    difficulty = round((n1 / 2.0) * (N2 / n2), 1) if n2 > 0 else 0.0
    effort = round(difficulty * volume, 0)
    bugs = round(volume / 3000.0, 3)

    return {
        "volume": volume,
        "difficulty": difficulty,
        "effort": effort,
        "bugs": bugs,
    }


def _find_function_node(tree, line_start: int, line_end: int):
    """Find the tree-sitter node for a function at the given line range.

    Walks the tree looking for function nodes whose line range matches
    (with 1-line tolerance for decorators).
    """
    if tree is None:
        return None
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

    # Halstead metrics: count operators and operands from AST
    halstead = _compute_halstead(func_node, source)

    # Cyclomatic density: complexity / lines (Gill & Kemerer, IEEE TSE)
    cc_density = round(metrics["cognitive"] / body_lines, 3) if body_lines > 0 else 0.0

    return {
        "cognitive_complexity": round(metrics["cognitive"], 2),
        "nesting_depth": metrics["nesting"],
        "param_count": param_count,
        "line_count": body_lines,
        "return_count": metrics["returns"],
        "bool_op_count": metrics["bool_ops"],
        "callback_depth": metrics["callback_depth"],
        "cyclomatic_density": cc_density,
        "halstead_volume": halstead["volume"],
        "halstead_difficulty": halstead["difficulty"],
        "halstead_effort": halstead["effort"],
        "halstead_bugs": halstead["bugs"],
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

    cc_density = round(cognitive / line_count, 3) if line_count > 0 else 0.0

    return {
        "cognitive_complexity": round(cognitive, 2),
        "nesting_depth": max_indent,
        "param_count": 0,  # Can't reliably count from raw source
        "line_count": line_count,
        "return_count": returns,
        "bool_op_count": bool_ops,
        "callback_depth": 0,
        "cyclomatic_density": cc_density,
        "halstead_volume": 0.0,
        "halstead_difficulty": 0.0,
        "halstead_effort": 0.0,
        "halstead_bugs": 0.0,
    }


# ── Math signal extraction (piggybacks on per-symbol AST walk) ──────

# Loop node types (cross-language)
_LOOP_NODES = {
    "for_statement", "while_statement", "do_statement",
    "for_in_statement", "enhanced_for_statement", "foreach_statement",
    "for_expression",
}

# Subscript / index access nodes
_SUBSCRIPT_NODES = {
    "subscript", "subscript_expression", "index_expression",
    "element_access_expression", "bracket_access",
}

# Comparison operator tokens
_COMPARE_OPS = {"<", ">", "<=", ">=", "==", "!=", "<>"}

# Augmented assignment node types and tokens
_AUGMENTED_ASSIGN_NODES = {"augmented_assignment"}
_AUGMENTED_ASSIGN_OPS = {"+=", "-=", "*=", "/=", "%=", "**=", "//=",
                         "&=", "|=", "^=", "<<=", ">>="}


def _extract_math_signals(func_node, source: bytes, symbol_name: str) -> dict:
    """Walk a function AST node once and extract algorithmic signals.

    Returns a dict suitable for storing in the ``math_signals`` table.
    """
    max_loop_depth = 0
    has_nested = False
    calls_in_loops: list[str] = []
    subscript_in_loops = False
    has_self_call = False
    self_call_count = 0
    loop_with_compare = False
    loop_with_accumulator = False
    str_concat_in_loop = False
    loop_invariant_calls: list[str] = []
    loop_bound_small = False

    symbol_lower = symbol_name.lower() if symbol_name else ""

    # --- Pre-scan: find variables initialized to string literals ---
    # This enables detection of quadratic string building (str += in loop)
    string_vars: set[str] = set()
    _scan_string_inits(func_node, source, string_vars)

    def _walk(node, loop_depth: int, loop_vars: set[str]):
        nonlocal max_loop_depth, has_nested, subscript_in_loops
        nonlocal has_self_call, self_call_count
        nonlocal loop_with_compare, loop_with_accumulator
        nonlocal str_concat_in_loop, loop_bound_small

        ntype = node.type

        # --- Loop entry ---
        if ntype in _LOOP_NODES:
            new_depth = loop_depth + 1
            if new_depth > max_loop_depth:
                max_loop_depth = new_depth
            if new_depth >= 2:
                has_nested = True
            # Extract loop variable names and check for bounded iteration
            lv = _extract_loop_vars(node, source)
            new_loop_vars = loop_vars | lv
            if _is_bounded_loop(node, source):
                loop_bound_small = True
            for child in node.children:
                _walk(child, new_depth, new_loop_vars)
            return

        # --- Inside a loop: check for signals ---
        if loop_depth > 0:
            # Call expressions inside loops
            if ntype in ("call_expression", "call"):
                name = _call_target_name(node, source)
                if name:
                    calls_in_loops.append(name)
                    if symbol_lower and name.lower() == symbol_lower:
                        has_self_call = True
                        self_call_count += 1
                    # Check if call is loop-invariant (args don't use loop vars)
                    if loop_vars and not _call_uses_loop_vars(node, source, loop_vars):
                        loop_invariant_calls.append(name)

            # Subscript access inside loops
            if ntype in _SUBSCRIPT_NODES:
                subscript_in_loops = True

            # Comparison inside loops
            if ntype in ("binary_expression", "comparison_operator"):
                for child in node.children:
                    if not child.is_named:
                        op = source[child.start_byte:child.end_byte].decode(
                            "utf-8", errors="replace")
                        if op in _COMPARE_OPS:
                            loop_with_compare = True
                            break

            # Augmented assignment inside loops
            if ntype in _AUGMENTED_ASSIGN_NODES:
                loop_with_accumulator = True
                # Check for string concatenation: str_var += ...
                target = _augmented_assign_target(node, source)
                if target and target in string_vars:
                    str_concat_in_loop = True
            elif ntype in ("binary_expression", "assignment_expression",
                           "expression_statement"):
                for child in node.children:
                    if not child.is_named:
                        op = source[child.start_byte:child.end_byte].decode(
                            "utf-8", errors="replace")
                        if op in _AUGMENTED_ASSIGN_OPS:
                            loop_with_accumulator = True
                            break

        # --- Self-call detection outside loops too ---
        if loop_depth == 0 and ntype in ("call_expression", "call"):
            name = _call_target_name(node, source)
            if name and symbol_lower and name.lower() == symbol_lower:
                has_self_call = True
                self_call_count += 1

        # Recurse children
        for child in node.children:
            _walk(child, loop_depth, loop_vars)

    _walk(func_node, 0, set())

    # Deduplicate calls list while preserving order
    seen: set[str] = set()
    unique_calls: list[str] = []
    for c in calls_in_loops:
        if c not in seen:
            seen.add(c)
            unique_calls.append(c)

    # Deduplicate loop-invariant calls
    seen_inv: set[str] = set()
    unique_inv: list[str] = []
    for c in loop_invariant_calls:
        if c not in seen_inv:
            seen_inv.add(c)
            unique_inv.append(c)

    return {
        "loop_depth": max_loop_depth,
        "has_nested_loops": int(has_nested),
        "calls_in_loops": unique_calls,
        "subscript_in_loops": int(subscript_in_loops),
        "has_self_call": int(has_self_call),
        "self_call_count": self_call_count,
        "loop_with_compare": int(loop_with_compare),
        "loop_with_accumulator": int(loop_with_accumulator),
        "str_concat_in_loop": int(str_concat_in_loop),
        "loop_invariant_calls": unique_inv,
        "loop_bound_small": int(loop_bound_small),
    }


def _call_target_name(node, src: bytes) -> str:
    """Best-effort extraction of the call target identifier."""
    for child in node.children:
        if child.type == "identifier":
            return src[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace")
        if child.type in ("member_expression", "attribute",
                          "field_expression"):
            # Take the last identifier (e.g. obj.method -> method)
            for sub in reversed(child.children):
                if sub.type in ("identifier", "property_identifier",
                                "field_identifier"):
                    return src[sub.start_byte:sub.end_byte].decode(
                        "utf-8", errors="replace")
    return ""


def _scan_string_inits(func_node, source: bytes, string_vars: set[str]):
    """Scan top-level assignments in a function for string initializations.

    Detects patterns like: x = "", x = '', x = str(), x = f"", x = ""\"\"\"\"\"\"
    Populates string_vars with the variable names.
    """
    body = None
    for child in func_node.children:
        if child.type in ("block", "statement_block", "compound_statement"):
            body = child
            break
    if body is None:
        return

    for stmt in body.children:
        # Python: expression_statement > assignment
        # JS/TS: variable_declaration, expression_statement > assignment_expression
        if stmt.type == "expression_statement":
            for child in stmt.children:
                if child.type == "assignment":
                    _check_string_assign(child, source, string_vars)
                elif child.type == "assignment_expression":
                    _check_string_assign(child, source, string_vars)
        elif stmt.type in ("assignment", "assignment_expression"):
            _check_string_assign(stmt, source, string_vars)
        # Stop scanning once we hit a loop — we only want pre-loop inits
        if stmt.type in _LOOP_NODES:
            break


def _check_string_assign(assign_node, source: bytes, string_vars: set[str]):
    """Check if an assignment initializes a variable to a string value."""
    children = assign_node.children
    if len(children) < 3:
        return
    lhs = children[0]
    rhs = children[-1]

    if lhs.type != "identifier":
        return

    var_name = source[lhs.start_byte:lhs.end_byte].decode("utf-8", errors="replace")

    # Check RHS for string patterns
    if rhs.type in ("string", "concatenated_string", "template_string"):
        string_vars.add(var_name)
    elif rhs.type in ("call_expression", "call"):
        # str() constructor
        fn_name = _call_target_name(rhs, source)
        if fn_name in ("str", "String", "StringBuilder", "StringBuffer"):
            string_vars.add(var_name)


def _augmented_assign_target(node, source: bytes) -> str:
    """Extract the target variable name from an augmented assignment node."""
    for child in node.children:
        if child.type == "identifier":
            return source[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace")
    return ""


def _extract_loop_vars(loop_node, source: bytes) -> set[str]:
    """Extract loop variable names from a for/foreach loop node."""
    result: set[str] = set()
    for child in loop_node.children:
        # Python: for x in ..., Go: for i, v := range ...
        if child.type in ("identifier",):
            result.add(source[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace"))
        # Python pattern_list / tuple: for x, y in ...
        elif child.type in ("pattern_list", "tuple_pattern", "pair"):
            for sub in child.children:
                if sub.type == "identifier":
                    result.add(source[sub.start_byte:sub.end_byte].decode(
                        "utf-8", errors="replace"))
        # JS: for (const x of ...) / for (let x in ...)
        elif child.type in ("variable_declaration", "lexical_declaration"):
            for sub in child.children:
                if sub.type == "variable_declarator":
                    for ssub in sub.children:
                        if ssub.type == "identifier":
                            result.add(source[ssub.start_byte:ssub.end_byte].decode(
                                "utf-8", errors="replace"))
        # Stop at the body — we only want the loop header vars
        if child.type in ("block", "statement_block", "compound_statement"):
            break
    return result


def _call_uses_loop_vars(call_node, source: bytes, loop_vars: set[str]) -> bool:
    """Check if any argument of a call expression references a loop variable."""
    # Find argument list node
    args_node = None
    for child in call_node.children:
        if child.type in ("argument_list", "arguments", "template_string"):
            args_node = child
            break
    if args_node is None:
        return False

    # Walk all identifiers in the arguments
    def _has_loop_var(node) -> bool:
        if node.type == "identifier":
            name = source[node.start_byte:node.end_byte].decode(
                "utf-8", errors="replace")
            if name in loop_vars:
                return True
        for child in node.children:
            if _has_loop_var(child):
                return True
        return False

    return _has_loop_var(args_node)


def _is_bounded_loop(loop_node, source: bytes) -> bool:
    """Check if a loop iterates over a known-small bounded range.

    Detects: range(N) where N < 100, iteration over tuple/list literals.
    """
    for child in loop_node.children:
        # range(N) with small literal
        if child.type in ("call_expression", "call"):
            fn_name = _call_target_name(child, source)
            if fn_name == "range":
                # Check for a single integer argument < 100
                for sub in child.children:
                    if sub.type in ("argument_list", "arguments"):
                        for arg in sub.children:
                            if arg.type in ("integer", "number"):
                                try:
                                    val = int(source[arg.start_byte:arg.end_byte].decode(
                                        "utf-8", errors="replace"))
                                    if val < 100:
                                        return True
                                except (ValueError, OverflowError):
                                    pass
        # Iteration over a small literal collection
        if child.type in ("list", "tuple", "set"):
            # Count elements
            elem_count = sum(1 for c in child.children if c.is_named)
            if elem_count < 20:
                return True
    return False


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
        "SELECT id, name, kind, line_start, line_end FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    metrics_batch = []
    math_batch = []
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
            metrics.get("cyclomatic_density", 0.0),
            metrics.get("halstead_volume", 0.0),
            metrics.get("halstead_difficulty", 0.0),
            metrics.get("halstead_effort", 0.0),
            metrics.get("halstead_bugs", 0.0),
        ))

        # Extract math signals from same AST node
        func_node = _find_function_node(tree, ls, le)
        if func_node is not None:
            import json as _json_mod
            msig = _extract_math_signals(func_node, source, row["name"])
            math_batch.append((
                row["id"],
                msig["loop_depth"],
                msig["has_nested_loops"],
                _json_mod.dumps(msig["calls_in_loops"]),
                msig["subscript_in_loops"],
                msig["has_self_call"],
                msig["loop_with_compare"],
                msig["loop_with_accumulator"],
                msig["self_call_count"],
                msig["str_concat_in_loop"],
                _json_mod.dumps(msig["loop_invariant_calls"]),
                msig["loop_bound_small"],
            ))

    if metrics_batch:
        conn.executemany(
            """INSERT OR REPLACE INTO symbol_metrics
               (symbol_id, cognitive_complexity, nesting_depth, param_count,
                line_count, return_count, bool_op_count, callback_depth,
                cyclomatic_density, halstead_volume, halstead_difficulty,
                halstead_effort, halstead_bugs)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            metrics_batch,
        )

    if math_batch:
        conn.executemany(
            """INSERT OR REPLACE INTO math_signals
               (symbol_id, loop_depth, has_nested_loops, calls_in_loops,
                subscript_in_loops, has_self_call, loop_with_compare,
                loop_with_accumulator, self_call_count, str_concat_in_loop,
                loop_invariant_calls, loop_bound_small)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            math_batch,
        )
