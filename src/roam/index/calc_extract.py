"""Deterministic extraction of computed-numeric ("calculation") assignments.

roam's symbol model records declarations and references but never descends into
an assignment's right-hand side, so a statement like ``$vat = round($base *
$rate / 100, 2)`` is invisible to it — neither a declaration nor a call target
it records. This module fills that gap: it walks the tree-sitter parse tree and
extracts every assignment whose value is a *calculation* (contains an arithmetic
operator, or is an accumulation, or calls a rounding/math function), recording
the target, the normalized formula text, its operands and numeric literals, and
any rounding function detected.

It is deliberately language-general: any tree-sitter grammar in roam's roster
that uses the C-family / Python node kinds below is covered (PHP, JS, TS, Go,
Java, C, C#, Ruby, Rust, Python, ...). Pure and side-effect-free apart from an
optional file read; a missing grammar or a parse error yields ``[]`` (fail-open,
never raises), matching the rest of the analysis layer.

Consumers: ``roam calc-inventory`` (surface + divergence detection) and, later,
the ``calc_equivalence`` verify oracle and the compile-envelope calc fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---- tree-sitter node kinds, cross-language -------------------------------
# Assignment forms: (left_field, right_field) — resolved via child_by_field_name.
_ASSIGN_NODES: dict[str, tuple[str, str]] = {
    "assignment_expression": ("left", "right"),  # php, js, ts, go, java, c, c#, ...
    "assignment": ("left", "right"),  # python (tree-sitter grammar)
}
_AUGMENTED_NODES: dict[str, tuple[str, str]] = {
    "augmented_assignment_expression": ("left", "right"),  # php, js, ts
    "augmented_assignment": ("left", "right"),  # python
}
_DECLARATOR_NODES: dict[str, tuple[str, str]] = {
    "variable_declarator": ("name", "value"),  # js, ts (const/let x = ...)
    "init_declarator": ("declarator", "value"),  # c, c++
}
_BINARY_NODES = frozenset({"binary_expression", "binary_operator"})  # binary_operator = python
_CALL_NODES = frozenset({"function_call_expression", "call_expression", "call", "method_invocation"})
_IDENT_NODES = frozenset({"identifier", "variable_name"})
# RHS node kinds that are function/closure *definitions*, not computed scalars —
# ``round2 = (n) => Math.round(...)`` assigns a function, not a value. Skip these
# as calc targets; genuine calcs inside their bodies are still caught by the walk.
_FUNC_RHS_NODES = frozenset(
    {
        "arrow_function",
        "function_expression",
        "function",
        "function_definition",
        "anonymous_function_creation_expression",
        "closure_expression",
        "lambda",
        "method_declaration",
    }
)
_ARITH_OPS = frozenset({"+", "-", "*", "/", "%", "**", "//"})
_AUG_ARITH_OPS = frozenset({"+=", "-=", "*=", "/=", "%=", "**=", "//="})
# Rounding / precision / money-shaping calls, matched on the bare function name
# (last segment of a member/static access — Math.round -> round, bcmul, etc.).
_ROUND_FUNCS = frozenset(
    {
        "round",
        "floor",
        "ceil",
        "trunc",
        "truncate",
        "number_format",
        "bcadd",
        "bcsub",
        "bcmul",
        "bcdiv",
        "bcmod",
        "intdiv",
        "intval",
        "floatval",
        "tofixed",
        "toprecision",
        "quantize",
    }
)


_MODE_CONST_RE = re.compile(
    r"\b(PHP_ROUND_HALF_(?:UP|DOWN|EVEN|ODD)|ROUND_(?:HALF_(?:UP|DOWN|EVEN)|CEILING|FLOOR|UP|DOWN|05UP))\b"
)
# Explicit rounding-mode constants -> tie/direction semantics. PHP's HALF_UP and
# decimal's ROUND_HALF_UP both mean half-AWAY-from-zero (not toward +inf).
_MODE_SEMANTICS: dict[str, str] = {
    "PHP_ROUND_HALF_UP": "half_away_from_zero",
    "PHP_ROUND_HALF_DOWN": "half_toward_zero",
    "PHP_ROUND_HALF_EVEN": "half_to_even",
    "PHP_ROUND_HALF_ODD": "half_to_odd",
    "ROUND_HALF_UP": "half_away_from_zero",
    "ROUND_HALF_DOWN": "half_toward_zero",
    "ROUND_HALF_EVEN": "half_to_even",
    "ROUND_CEILING": "toward_positive",
    "ROUND_FLOOR": "toward_negative",
    "ROUND_UP": "away_from_zero",
    "ROUND_DOWN": "truncate",
    "ROUND_05UP": "round_05up",
}


@dataclass(frozen=True)
class Calc:
    """One computed-numeric assignment extracted from source."""

    target: str  # left-hand side text (e.g. "$vat", "this.total", "$obj->net")
    formula: str  # right-hand side text, whitespace-collapsed
    operands: tuple[str, ...]  # identifiers/variables referenced in the RHS
    literals: tuple[str, ...]  # numeric literals in the RHS
    rounding: str | None  # bare name of a rounding/precision call, if any
    line: int  # 1-indexed line of the assignment
    kind: str  # "assign" | "augmented" | "declarator"
    language: str = ""
    file: str = ""
    rounding_mode: str | None = None  # explicit mode constant (e.g. PHP_ROUND_HALF_EVEN), if present


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _collapse(s: str) -> str:
    return " ".join(s.split())


def _func_name(call, src: bytes) -> str:
    """Bare (lower-cased, last-segment) name of a call's function."""
    fn = call.child_by_field_name("function")
    if fn is None and call.children:
        fn = call.children[0]
    if fn is None:
        return ""
    name = _text(fn, src).strip().lower()
    # Math.round -> round; Foo::bar -> bar; a->b -> b
    for sep in (".", "::", "->"):
        if sep in name:
            name = name.split(sep)[-1]
    return name.strip()


def _analyze_rhs(node, src: bytes, round_funcs: frozenset[str]) -> tuple[bool, str | None, str | None]:
    """(is_calculation, rounding_fn, rounding_mode) for a right-hand-side subtree.

    A calculation = contains an arithmetic binary operator, or calls a
    rounding/math function. ``round_funcs`` is passed in (not read from a module
    global) so a per-call ``--round-funcs`` widening cannot leak across calls in
    the long-running MCP server.

    ``rounding_mode`` is the explicit mode constant when the rounding call
    carries one (e.g. ``round($x, 2, PHP_ROUND_HALF_EVEN)`` or
    ``quantize(..., rounding=ROUND_HALF_UP)``) — the argument that flips the tie
    behaviour away from the language default. Only *recognized constants* are
    captured; a variable mode argument remains invisible (documented bound —
    the base per-language label then still applies as an approximation).
    """
    is_calc = False
    rounding: str | None = None
    rounding_mode: str | None = None
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _BINARY_NODES:
            op = n.child_by_field_name("operator")
            op_txt = _text(op, src) if op is not None else ""
            if not op_txt:
                for c in n.children:
                    if _text(c, src) in _ARITH_OPS:
                        op_txt = _text(c, src)
                        break
            if op_txt in _ARITH_OPS:
                is_calc = True
        elif n.type in _CALL_NODES:
            name = _func_name(n, src)
            if name in round_funcs:
                is_calc = True
                if rounding is None:
                    rounding = name
                    mode_match = _MODE_CONST_RE.search(_text(n, src))
                    if mode_match:
                        rounding_mode = mode_match.group(1)
        stack.extend(n.children)
    return is_calc, rounding, rounding_mode


def _operands_and_literals(node, src: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Identifiers (excluding call function names) and numeric literals in a subtree."""
    func_names: set[str] = set()
    scan = [node]
    while scan:
        n = scan.pop()
        if n.type in _CALL_NODES:
            fn = n.child_by_field_name("function")
            if fn is not None:
                func_names.add(_text(fn, src))
        scan.extend(n.children)
    ids: set[str] = set()
    nums: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _IDENT_NODES:
            tx = _text(n, src)
            if tx not in func_names and tx.lower() != "math":
                ids.add(tx)
        elif "number" in n.type or "integer" in n.type or n.type.startswith("float") or n.type.endswith("_literal"):
            t = _text(n, src)
            if t and (t[0].isdigit() or (len(t) > 1 and t[0] in "+-." and t[1].isdigit())):
                nums.add(t)
        stack.extend(n.children)
    return tuple(sorted(ids)), tuple(sorted(nums))


def _aug_operator(node, src: bytes) -> str:
    for c in node.children:
        t = _text(c, src)
        if t in _AUG_ARITH_OPS:
            return t
    return ""


def extract_calcs(language: str, source: bytes, extra_round_funcs: frozenset[str] = frozenset()) -> list[Calc]:
    """Extract computed-numeric assignments from ``source`` in ``language``.

    ``language`` is a roam grammar name (php, javascript, typescript, python,
    ...). ``extra_round_funcs`` widens the recognized rounding wrappers for this
    call only (e.g. a project's ``r`` / ``round2`` / ``money`` helper). Returns
    ``[]`` on missing grammar or parse failure — never raises.
    """
    round_funcs = _ROUND_FUNCS | extra_round_funcs
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return []
    from roam.index.parser import GRAMMAR_ALIASES

    try:
        parser = get_parser(GRAMMAR_ALIASES.get(language, language))
        tree = parser.parse(source)
    except (LookupError, TypeError, ValueError):
        return []

    out: list[Calc] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        kind = spec = None
        if n.type in _ASSIGN_NODES:
            kind, spec = "assign", _ASSIGN_NODES[n.type]
        elif n.type in _AUGMENTED_NODES:
            kind, spec = "augmented", _AUGMENTED_NODES[n.type]
        elif n.type in _DECLARATOR_NODES:
            kind, spec = "declarator", _DECLARATOR_NODES[n.type]
        if spec:
            lf, rf = spec
            lhs = n.child_by_field_name(lf)
            rhs = n.child_by_field_name(rf)
            if lhs is not None and rhs is not None and rhs.type not in _FUNC_RHS_NODES:
                is_calc, rounding, rounding_mode = _analyze_rhs(rhs, source, round_funcs)
                aug_op = _aug_operator(n, source) if kind == "augmented" else ""
                if aug_op:
                    is_calc = True
                if is_calc:
                    operands, literals = _operands_and_literals(rhs, source)
                    formula = _collapse(_text(rhs, source))
                    if aug_op:
                        # surface the accumulation as target OP= rhs so the
                        # formula reads as the operation it performs
                        formula = f"{_collapse(_text(lhs, source))} {aug_op[0]} {formula}"
                    out.append(
                        Calc(
                            target=_collapse(_text(lhs, source)),
                            formula=formula,
                            operands=operands,
                            literals=literals,
                            rounding=rounding,
                            line=n.start_point[0] + 1,
                            kind=kind,
                            language=language,
                            rounding_mode=rounding_mode,
                        )
                    )
        stack.extend(n.children)
    out.sort(key=lambda c: c.line)
    return out


def extract_calcs_from_file(path: str | Path, extra_round_funcs: frozenset[str] = frozenset()) -> list[Calc]:
    """Detect language, read, and extract. ``[]`` on any I/O or grammar miss."""
    from roam.languages.registry import get_language_for_file

    p = Path(path)
    language = get_language_for_file(str(p)) or ""
    if not language:
        return []
    try:
        source = p.read_bytes()
    except OSError:
        return []
    calcs = extract_calcs(language, source, extra_round_funcs)
    return [Calc(**{**c.__dict__, "file": str(p)}) for c in calcs]


# Language-aware rounding semantics for the SAME bare function name — the subtle
# cross-implementation bug a formula diff alone misses: PHP ``round()`` is
# half-away-from-zero, JS ``Math.round`` is half-up-toward-+inf (diverges on
# negative half-cents / credit notes), Python ``round()`` is banker's
# (half-to-even). A field computed with "round" in two languages can silently
# disagree on ties even when the formula text matches.
_ROUNDING_SEMANTICS: dict[tuple[str, str], str] = {
    ("php", "round"): "half_away_from_zero",
    ("python", "round"): "half_to_even",
    ("javascript", "round"): "half_up_toward_positive",
    ("typescript", "round"): "half_up_toward_positive",
    ("php", "number_format"): "half_away_from_zero",
    ("php", "bcadd"): "truncate",
    ("php", "bcsub"): "truncate",
    ("php", "bcmul"): "truncate",
    ("php", "bcdiv"): "truncate",
    ("php", "intval"): "truncate",
    ("php", "floor"): "toward_negative",
    ("javascript", "floor"): "toward_negative",
    ("php", "ceil"): "toward_positive",
    ("javascript", "ceil"): "toward_positive",
    ("javascript", "tofixed"): "half_up_toward_positive",
}


def rounding_semantic(language: str, func: str | None, mode: str | None = None) -> str | None:
    """Documented tie/direction semantics of a rounding fn in a language, if known.

    Lets divergence detection flag two implementations that call the *same*
    rounding name but with *different* semantics (e.g. PHP half-away vs JS
    half-up) — a to-the-cent bug that a formula-text comparison alone misses.

    ``mode`` is an explicit mode constant captured from the call (e.g.
    ``PHP_ROUND_HALF_EVEN``); it OVERRIDES the per-language default — without
    this, ``round($x, 2, PHP_ROUND_HALF_EVEN)`` would be mislabeled half-away
    when it is actually banker's. An unrecognized mode returns ``None``
    (honest-unknown beats confident-wrong).

    Two documented approximation bounds remain: a *variable* mode argument is
    invisible (base label still applies), and float-representation tie effects
    (e.g. JS ``toFixed(2.675) == "2.67"``) are not expressible in any static
    label — only an empirical differential probe resolves those.
    """
    if mode:
        return _MODE_SEMANTICS.get(mode)
    if not func:
        return None
    return _ROUNDING_SEMANTICS.get((language, func))


def normalize_target(target: str) -> str:
    """Base field name for grouping divergent implementations.

    ``$this->vatAmount`` / ``self.vat_amount`` / ``const vatAmount`` all reduce to
    ``vatamount`` so the same conceptual field computed in two places (e.g. a PHP
    backend and a JS frontend) groups together.
    """
    t = target.strip()
    for sep in ("->", "::", "."):
        if sep in t:
            t = t.split(sep)[-1]
    t = t.lstrip("$").strip()
    return t.lower()


def normalize_formula(formula: str) -> str:
    """Whitespace/paren-insensitive formula key for divergence comparison."""
    return "".join(formula.split()).replace("(", "").replace(")", "")
