"""Bounded, non-executing input partitions and static outcomes for case-table."""

from __future__ import annotations

import ast
import itertools
import math
from dataclasses import dataclass

UNKNOWN = {"kind": "unknown"}
OTHER = {"kind": "other"}


class StrictEq(ast.Eq):
    """JavaScript equality without coercion."""


class StrictNotEq(ast.NotEq):
    """JavaScript inequality without coercion."""


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def expression(node):
    """Translate the small JS expression subset into Python AST, without eval."""
    if node is None:
        return ast.Name(id="__unknown")
    text = node.text.decode()
    if node.type == "undefined":
        return ast.Name(id="undefined")
    if node.type in ("identifier", "property_identifier"):
        return ast.Name(id=text)
    if node.type in ("true", "false", "null"):
        return ast.Constant(value={"true": True, "false": False, "null": None}[node.type])
    if node.type in ("string", "number"):
        try:
            return ast.Constant(value=ast.literal_eval(text))
        except (ValueError, SyntaxError):
            return ast.Name(id="__unknown")
    if node.type == "member_expression":
        return ast.Attribute(
            value=expression(node.child_by_field_name("object")),
            attr=node.child_by_field_name("property").text.decode(),
        )
    if node.type == "parenthesized_expression":
        return expression(node.named_children[0])
    if node.type == "unary_expression":
        op = node.child_by_field_name("operator")
        unary_ops = {b"!": ast.Not, b"-": ast.USub, b"+": ast.UAdd}
        if op and op.text in unary_ops:
            return ast.UnaryOp(op=unary_ops[op.text](), operand=expression(node.child_by_field_name("argument")))
    if node.type == "binary_expression":
        left = expression(node.child_by_field_name("left"))
        right = expression(node.child_by_field_name("right"))
        op = node.child_by_field_name("operator").text.decode()
        if op in ("&&", "||"):
            return ast.BoolOp(op=ast.And() if op == "&&" else ast.Or(), values=[left, right])
        operators = {
            "===": StrictEq,
            "!==": StrictNotEq,
            "<": ast.Lt,
            ">": ast.Gt,
            "<=": ast.LtE,
            ">=": ast.GtE,
        }
        if op in operators:
            return ast.Compare(left=left, ops=[operators[op]()], comparators=[right])
    return ast.Name(id="__unknown")


def evaluate(node, values):
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return UNKNOWN
    if isinstance(node, ast.Name):
        if node.id == "undefined":
            return {"kind": "undefined"}
        return values.get(node.id, UNKNOWN)
    if isinstance(node, ast.Attribute):
        # Enum members are symbolic identities; no object access is performed.
        if isinstance(node.value, ast.Name) and node.value.id not in values and node.value.id[:1].isupper():
            return f"{node.value.id}.{node.attr}"
        return UNKNOWN
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = evaluate(node.operand, values)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value if isinstance(node.op, ast.USub) else value
        return UNKNOWN
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = evaluate(node.operand, values)
        return not value if not isinstance(value, dict) else UNKNOWN
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bool"
        and node.func.id not in values
        and len(node.args) == 1
    ):
        value = evaluate(node.args[0], values)
        return bool(value) if not isinstance(value, dict) else UNKNOWN
    if isinstance(node, ast.BoolOp):
        for value_node in node.values:
            value = evaluate(value_node, values)
            if isinstance(value, dict):
                return UNKNOWN
            if isinstance(node.op, ast.And) and not value:
                return value
            if isinstance(node.op, ast.Or) and value:
                return value
        return value
    if isinstance(node, ast.Compare):
        left = evaluate(node.left, values)
        for op, rhs in zip(node.ops, node.comparators):
            right = evaluate(rhs, values)
            unresolved = [v for v in (left, right) if isinstance(v, dict) and v.get("kind") != "undefined"]
            if unresolved:
                return UNKNOWN
            try:
                strict_equal = left == right and (
                    type(left) is type(right)
                    or (
                        isinstance(left, (int, float))
                        and not isinstance(left, bool)
                        and isinstance(right, (int, float))
                        and not isinstance(right, bool)
                    )
                )
                if (
                    isinstance(op, (ast.Is, ast.IsNot))
                    and type(left) is type(right)
                    and left is not None
                    and not isinstance(left, bool)
                ):
                    return UNKNOWN
                if isinstance(op, (ast.Eq, ast.Is)):
                    result = (
                        strict_equal
                        if isinstance(op, StrictEq)
                        else (type(left) is type(right) and left == right)
                        if isinstance(op, ast.Is)
                        else left == right
                    )
                elif isinstance(op, (ast.NotEq, ast.IsNot)):
                    result = not (
                        strict_equal
                        if isinstance(op, StrictNotEq)
                        else (type(left) is type(right) and left == right)
                        if isinstance(op, ast.IsNot)
                        else left == right
                    )
                elif isinstance(op, ast.Lt):
                    result = left < right
                elif isinstance(op, ast.Gt):
                    result = left > right
                elif isinstance(op, ast.LtE):
                    result = left <= right
                elif isinstance(op, ast.GtE):
                    result = left >= right
                elif isinstance(op, ast.In):
                    result = left in right
                elif isinstance(op, ast.NotIn):
                    result = left not in right
                else:
                    return UNKNOWN
            except (TypeError, ValueError):
                return UNKNOWN
            if not result:
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        vals = [evaluate(v, values) for v in node.elts]
        return vals if all(not isinstance(v, dict) for v in vals) else UNKNOWN
    return UNKNOWN


@dataclass
class Model:
    parameters: list
    defaults: dict
    domains: dict
    body: list
    calls: list


def python_model(source, line):
    tree = ast.parse(source)
    function = min(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and min([n.lineno] + [d.lineno for d in n.decorator_list]) <= line <= n.body[0].lineno
        ),
        key=lambda n: (n.end_lineno or n.lineno) - n.lineno,
        default=None,
    )
    if function is None:
        raise ValueError("Indexed function could not be located in current source")
    args = function.args
    params = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs if a.arg not in ("self", "cls")]
    if args.vararg:
        params.append("*" + args.vararg.arg)
    if args.kwarg:
        params.append("**" + args.kwarg.arg)
    defaults = {
        a.arg: evaluate(v, {})
        for a, v in zip(
            args.posonlyargs + args.args,
            [None] * (len(args.posonlyargs + args.args) - len(args.defaults)) + args.defaults,
        )
        if v is not None
    }
    defaults.update({a.arg: evaluate(v, {}) for a, v in zip(args.kwonlyargs, args.kw_defaults) if v is not None})
    enums = {
        n.name: [
            f"{n.name}.{s.targets[0].id}"
            for s in n.body
            if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name)
        ]
        for n in tree.body
        if isinstance(n, ast.ClassDef)
        and any(isinstance(b, ast.Name) and b.id in ("Enum", "IntEnum", "StrEnum") for b in n.bases)
    }
    domains = {}
    for arg in args.posonlyargs + args.args + args.kwonlyargs:
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        if annotation == "bool":
            domains[arg.arg] = [False, True]
        elif annotation in enums:
            domains[arg.arg] = enums[annotation]
        elif annotation.startswith("Literal[") and isinstance(arg.annotation, ast.Subscript):
            elts = arg.annotation.slice.elts if isinstance(arg.annotation.slice, ast.Tuple) else [arg.annotation.slice]
            domains[arg.arg] = [evaluate(v, {}) for v in elts]

    def statements(nodes):
        out = []
        for n in nodes:
            if isinstance(n, ast.If):
                out.append(("if", n.lineno, n.test, statements(n.body), statements(n.orelse)))
            elif isinstance(n, ast.Match):
                fallback = []
                for case in reversed(n.cases):
                    pattern = case.pattern
                    if isinstance(pattern, ast.MatchAs) and pattern.pattern is None:
                        if pattern.name is None and case.guard is None:
                            fallback = statements(case.body)
                        else:
                            fallback = [("unknown", pattern.lineno, ast.Name(id="__unknown"))]
                        continue
                    if isinstance(pattern, ast.MatchValue):
                        rhs = pattern.value
                    elif isinstance(pattern, ast.MatchSingleton):
                        rhs = ast.Constant(value=pattern.value)
                    else:
                        fallback = [("unknown", pattern.lineno, ast.Name(id="__unknown"))]
                        continue
                    condition = ast.Compare(left=n.subject, ops=[ast.Eq()], comparators=[rhs])
                    if case.guard:
                        condition = ast.BoolOp(op=ast.And(), values=[condition, case.guard])
                    fallback = [("if", pattern.lineno, condition, statements(case.body), fallback)]
                out.extend(fallback)
            elif isinstance(n, ast.Return):
                out.append(("return", n.lineno, n.value or ast.Constant(value=None)))
            elif isinstance(n, ast.Raise):
                out.append(("raise", n.lineno, ast.Constant(value=ast.unparse(n.exc) if n.exc else "reraise")))
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
                continue
            elif isinstance(n, ast.Pass):
                continue
            else:
                out.append(("unknown", n.lineno, ast.Name(id="__unknown")))
        return out

    def scope_nodes(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                yield from scope_nodes(child)

    calls = []
    for n in (node for statement in function.body for node in scope_nodes(statement)):
        if isinstance(n, ast.Call):
            name = (
                n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else ""
            )
            calls.append(
                (
                    name,
                    n.lineno,
                    [evaluate(a, {}) for a in n.args],
                    {k.arg: evaluate(k.value, {}) for k in n.keywords if k.arg},
                    ast.unparse(n.func),
                )
            )
    return Model(params, defaults, domains, statements(function.body), calls)


def js_model(source, line, language):
    from roam.parser_pack import get_parser

    tree = get_parser("typescript" if language == "typescript" else language).parse(source.encode())
    if tree.root_node.has_error:
        raise ValueError("Current source contains parse errors")
    nodes = list(walk(tree.root_node))
    functions = [
        n
        for n in nodes
        if n.type in ("function_declaration", "arrow_function", "method_definition", "function_expression")
        and n.start_point.row + 1 <= line <= n.end_point.row + 1
    ]
    if not functions:
        raise ValueError("Indexed function could not be located in current source")
    function = min(functions, key=lambda n: n.end_byte - n.start_byte)
    enums = {}
    for node in nodes:
        if node.type == "enum_declaration":
            name = node.child_by_field_name("name").text.decode()
            body = node.child_by_field_name("body")
            enums[name] = [f"{name}.{(c.child_by_field_name('name') or c).text.decode()}" for c in body.named_children]
    params, defaults, domains = [], {}, {}
    parameters = function.child_by_field_name("parameters")
    if parameters is None:
        parameters = function.child_by_field_name("parameter")
    for param in parameters.named_children if parameters and parameters.type == "formal_parameters" else [parameters]:
        if param is None:
            continue
        pattern = param.child_by_field_name("pattern") or param.child_by_field_name("left") or param
        name = pattern.text.decode()
        if pattern.type != "identifier":
            params.append(name)
            continue
        params.append(name)
        value = param.child_by_field_name("value") or param.child_by_field_name("right")
        if value:
            defaults[name] = evaluate(expression(value), {})
        annotation = param.child_by_field_name("type")
        if annotation:
            text = annotation.text.decode().lstrip(": ").strip()
            if text == "boolean":
                domains[name] = [False, True]
            elif text in enums:
                domains[name] = enums[text]
            else:
                literals = [
                    evaluate(expression(n.named_children[0]), {})
                    for n in walk(annotation)
                    if n.type == "literal_type" and n.named_children
                ]
                if literals and not any(n.type == "predefined_type" for n in walk(annotation)):
                    domains[name] = literals

    def statements(node):
        if node is None:
            return []
        if node.type in ("statement_block", "else_clause"):
            return [s for c in node.named_children for s in statements(c)]
        line = node.start_point.row + 1
        if node.type == "if_statement":
            return [
                (
                    "if",
                    line,
                    expression(node.child_by_field_name("condition")),
                    statements(node.child_by_field_name("consequence")),
                    statements(node.child_by_field_name("alternative")),
                )
            ]
        if node.type == "switch_statement":
            value = expression(node.child_by_field_name("value"))
            body = node.child_by_field_name("body")
            fallback = []
            cases = []
            for case in body.named_children:
                test = case.child_by_field_name("value")
                statements_nodes = [c for c in case.named_children if c != test]
                result = [s for c in statements_nodes for s in statements(c)]
                if test is None:
                    fallback = result
                else:
                    cases.append((case.start_point.row + 1, expression(test), result))
            for case_line, test, result in reversed(cases):
                condition = ast.Compare(left=value, ops=[StrictEq()], comparators=[test])
                if not result or all(s[0] == "if" for s in result):
                    result = [("unknown", case_line, ast.Name(id="__unknown"))]
                fallback = [("if", case_line, condition, result, fallback)]
            return fallback
        if node.type in ("return_statement", "throw_statement"):
            kind = "return" if node.type == "return_statement" else "throw"
            expr = expression(node.named_children[0]) if node.named_children else ast.Constant(value=None)
            if kind == "throw":
                expr = ast.Constant(value=node.text.decode().rstrip(";"))
            return [(kind, line, expr)]
        return [("unknown", line, ast.Name(id="__unknown"))]

    def scope_nodes(node):
        yield node
        for child in node.named_children:
            if child.type not in ("function_declaration", "arrow_function", "method_definition", "function_expression"):
                yield from scope_nodes(child)

    calls = []
    for node in scope_nodes(function.child_by_field_name("body")):
        if node.type == "call_expression":
            callee = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if callee and args:
                calls.append(
                    (
                        callee.text.decode().split(".")[-1],
                        node.start_point.row + 1,
                        [evaluate(expression(a), {}) for a in args.named_children],
                        {},
                        callee.text.decode(),
                    )
                )
    body_node = function.child_by_field_name("body")
    body = (
        statements(body_node)
        if body_node.type == "statement_block"
        else [("return", body_node.start_point.row + 1, expression(body_node))]
    )
    return Model(params, defaults, domains, body, calls)


def dimensions(model):
    domains = {p: list(model.domains.get(p, [])) for p in model.parameters}
    bounded = set(model.domains)
    conditions = []

    def scan(body):
        for s in body:
            if s[0] == "if":
                conditions.append(s[2])
                scan(s[3])
                scan(s[4])
            elif s[0] == "return" and isinstance(s[2], (ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Call)):
                conditions.append(s[2])

    scan(model.body)
    for cond in conditions:
        for node in ast.walk(cond):
            if isinstance(node, ast.Compare):
                terms = [node.left] + node.comparators
                for left, right in zip(terms, terms[1:]):
                    if isinstance(left, ast.Name) and left.id in domains:
                        value = evaluate(right, {})
                        if isinstance(right, (ast.List, ast.Tuple, ast.Set)) and isinstance(value, list):
                            domains[left.id].extend(value)
                        elif value != UNKNOWN:
                            domains[left.id].append(value)
                    elif isinstance(right, ast.Name) and right.id in domains:
                        value = evaluate(left, {})
                        if value != UNKNOWN:
                            domains[right.id].append(value)

        def boolean_names(expr):
            if isinstance(expr, ast.Name) and expr.id in domains:
                domains[expr.id].extend([False, True])
                bounded.add(expr.id)
            elif isinstance(expr, ast.BoolOp):
                for value in expr.values:
                    boolean_names(value)
            elif isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
                boolean_names(expr.operand)
            elif (
                isinstance(expr, ast.Call)
                and isinstance(expr.func, ast.Name)
                and expr.func.id == "bool"
                and len(expr.args) == 1
            ):
                boolean_names(expr.args[0])

        boolean_names(cond)
    result = []
    for p, vals in domains.items():
        if p in model.defaults and model.defaults[p] != UNKNOWN:
            vals.append(model.defaults[p])
        unique = []
        for v in vals:
            if not any(type(v) is type(u) and v == u for u in unique):
                unique.append(v)
        if p not in bounded:
            unique.append(OTHER if unique else UNKNOWN)
        result.append(
            {"name": p, "values": unique, "source": "annotation_and_branch_partitions", "exhaustive": p in bounded}
        )
    return result


def outcome(body, values):
    branches = []

    def run(statements):
        for s in statements:
            if s[0] == "if":
                decision = evaluate(s[2], values)
                if isinstance(decision, dict):
                    return {"kind": "unknown", "line": s[1]}
                branches.append({"line": s[1], "taken": bool(decision)})
                result = run(s[3] if decision else s[4])
                if result:
                    return result
            else:
                return {"kind": s[0], "line": s[1], "value": evaluate(s[2], values)}
        return None

    result = run(body) or {"kind": "fallthrough", "value": None}
    result["branches"] = branches
    return result


def combinations(dims, cap, pairwise):
    sizes = [len(d["values"]) for d in dims]
    total = math.prod(sizes)
    if not pairwise or len(dims) < 2:
        return list(itertools.islice(itertools.product(*(range(s) for s in sizes)), cap)), total, None
    # Construct rows from uncovered pairs; never materialize the cross-product.
    pairs = {
        (i, a, j, b)
        for i in range(len(sizes))
        for j in range(i + 1, len(sizes))
        for a in range(sizes[i])
        for b in range(sizes[j])
    }
    rows = []
    while pairs and len(rows) < cap:
        i, a, j, b = min(pairs)
        row = {i: a, j: b}
        for k, size in enumerate(sizes):
            if k not in row:

                def score(v):
                    return sum(
                        (min(k, x), v if k < x else y, max(k, x), y if k < x else v) in pairs for x, y in row.items()
                    )

                row[k] = max(range(size), key=score)
        values = tuple(row[k] for k in range(len(sizes)))
        rows.append(values)
        pairs.difference_update(
            (i, values[i], j, values[j]) for i in range(len(sizes)) for j in range(i + 1, len(sizes))
        )
    return rows, total, not pairs


def bind_call(model, positional, keywords):
    values = dict(model.defaults)
    values.update(zip(model.parameters, positional))
    values.update(keywords)
    return {p: values.get(p, UNKNOWN) for p in model.parameters}
