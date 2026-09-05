"""Deterministic helper-extraction hints for high-complexity functions.

``roam complexity`` flags a function as too complex (a cognitive-complexity
score over the threshold). The question every developer asks next is *where*
the complexity lives and *what* to pull out — and today they have to eyeball
it. This module answers that question deterministically.

Cognitive complexity (see :mod:`roam.index.complexity`) is *additive over the
AST* with a *triangular nesting penalty*: a control-flow node at nesting depth
``d`` contributes ``1 + d*(d+1)//2`` and pushes its children one level deeper.
The practical consequence: a block buried three levels deep costs far more in
place than the same block would as the body of a fresh top-level helper (whose
nesting restarts at 0). Extracting such a block therefore *erases* its nesting
penalty — a win we can compute exactly by walking the same complexity model
twice: once in place (at the block's real depth) and once rebased to depth 0.

We enumerate the extractable control-flow blocks in a function, score each by
how much extracting it would drop the parent's score, and surface the blocks
that give a meaningful reduction for the fewest lines moved. Pure static
analysis, no LLM: same input → same suggestion. Language-agnostic because it
reuses complexity.py's cross-language node maps.

The figures are *structural estimates* from the same model that produced the
flag — they describe the effect of the move on the metric, not a promise about
the resulting code's readability, and they don't rewrite anything.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from roam.index.complexity import (
    _CONTINUATION_FLOW,
    _CONTROL_FLOW,
    _FUNCTION_NODES,
    _find_function_node,
    _walk_complexity,
)

# Control-flow node types that are valid *extraction roots* — a complete
# statement you can lift into a helper. This is ``_CONTROL_FLOW`` minus the
# clause fragments (``except_clause`` / ``catch_clause``) that can't stand
# alone, and minus the expression-level conditionals (a ternary isn't worth a
# helper). The excluded clause types still participate in depth accounting;
# they just aren't offered as roots.
_EXTRACTABLE_BLOCKS = {
    "if_statement",
    "if_expression",
    "for_statement",
    "for_in_statement",
    "enhanced_for_statement",
    "foreach_statement",
    "while_statement",
    "do_statement",
    "try_statement",
    "with_statement",
    "match_statement",
    "match_expression",
    "switch_statement",
}

# Human-readable labels for the block a hint points at.
_BLOCK_LABEL = {
    "if_statement": "if block",
    "if_expression": "if expression",
    "for_statement": "for loop",
    "for_in_statement": "for loop",
    "enhanced_for_statement": "for loop",
    "foreach_statement": "foreach loop",
    "while_statement": "while loop",
    "do_statement": "do/while loop",
    "try_statement": "try/except block",
    "with_statement": "with block",
    "match_statement": "match block",
    "match_expression": "match block",
    "switch_statement": "switch block",
}

# roam's high-severity floor (mirrors ``COMPLEXITY_FINDING_THRESHOLD`` in
# cmd_complexity — kept as a local constant to avoid importing the command
# module back into an indexing module). A hint whose estimated ``parent_after``
# drops below this "solves" the finding; those are ranked ahead of partial
# dents and, among them, we prefer the smallest helper.
_HIGH_SEVERITY_FLOOR = 15.0

_BODY_NODE_TYPES = frozenset(
    {
        "block",
        "statement_block",
        "compound_statement",
        "function_body",
        "method_body",
        "body",
    }
)
_BOUNDARY_OWNERS = frozenset(_EXTRACTABLE_BLOCKS) | frozenset(
    {
        "elif_clause",
        "else_clause",
        "case_clause",
        "switch_case",
        "match_arm",
        "except_clause",
        "catch_clause",
        "finally_clause",
    }
)
_EARLY_EXIT_TYPES = frozenset(
    {
        "return_statement",
        "break_statement",
        "continue_statement",
        "yield",
        "yield_statement",
    }
)
_ASSIGNMENT_TYPES = frozenset(
    {
        "assignment",
        "augmented_assignment",
        "assignment_expression",
        "variable_declarator",
        "variable_declaration",
        "lexical_declaration",
        "const_declaration",
        "short_var_declaration",
    }
)
_IDENTIFIER_TYPES = frozenset(
    {
        "identifier",
        "field_identifier",
        "property_identifier",
        "shorthand_property_identifier",
    }
)
_NONLOCAL_TARGET_TYPES = frozenset(
    {
        "attribute",
        "subscript",
        "member_expression",
        "field_expression",
        "index_expression",
    }
)
_MUTATING_METHODS = frozenset(
    {
        "add",
        "append",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "setdefault",
        "sort",
        "update",
    }
)
_VERBS = frozenset(
    {
        "add",
        "append",
        "apply",
        "audit",
        "build",
        "calculate",
        "check",
        "collect",
        "compute",
        "create",
        "emit",
        "fetch",
        "filter",
        "find",
        "format",
        "load",
        "normalize",
        "notify",
        "parse",
        "process",
        "publish",
        "read",
        "render",
        "resolve",
        "save",
        "send",
        "transform",
        "update",
        "validate",
        "write",
    }
)
_NAME_STOP_WORDS = frozenset(
    {
        "and",
        "args",
        "body",
        "else",
        "for",
        "from",
        "if",
        "item",
        "items",
        "kwargs",
        "self",
        "the",
        "this",
        "try",
        "value",
        "values",
        "while",
        "with",
    }
)
_NAME_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|\d+")


@dataclass
class ExtractionHint:
    """One candidate block to extract into a helper, with estimated effect.

    All complexity figures come from the same deterministic model
    ``roam complexity`` uses to score the function — they describe the
    *structural* effect of the move on the metric, computed from the current
    on-disk source.
    """

    block_type: str  # raw tree-sitter node type
    label: str  # human label, e.g. "for loop"
    line_start: int  # 1-indexed, inclusive
    line_end: int  # 1-indexed, inclusive
    line_count: int
    depth: int  # nesting depth of the block within the function
    reduction: float  # cognitive complexity the parent sheds
    parent_after: float  # estimated parent score after extraction
    helper_cc: float  # estimated cognitive complexity of the new helper


@dataclass
class _BoundaryCandidate:
    node: object
    label: str
    line_start: int
    line_end: int
    suggested_name: str
    expected_delta: float
    residual_score: float
    auto_fixable: bool
    reason: str | None


def _walk_nodes(node):
    yield node
    for child in node.children:
        yield from _walk_nodes(child)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _identifier_leaves(node, source: bytes) -> list[str]:
    return [_node_text(part, source) for part in _walk_nodes(node) if part.type in _IDENTIFIER_TYPES]


def _body_for_owner(node):
    for field in ("body", "consequence"):
        child = node.child_by_field_name(field)
        if child is not None and child.type in _BODY_NODE_TYPES:
            return child
    for child in node.children:
        if child.type in _BODY_NODE_TYPES:
            return child
    return None


def _boundary_label(owner_type: str) -> str:
    if owner_type in {"for_statement", "for_in_statement", "enhanced_for_statement", "foreach_statement"}:
        return "loop_body"
    if owner_type in {"while_statement", "do_statement"}:
        return "loop_body"
    if owner_type in {"if_statement", "if_expression", "elif_clause", "else_clause"}:
        return "if_arm"
    if owner_type in {"try_statement", "except_clause", "catch_clause", "finally_clause"}:
        return "try_body"
    if owner_type in {"match_statement", "match_expression", "match_arm", "case_clause", "switch_case"}:
        return "match_arm"
    return "block"


def _collect_boundaries(func_node) -> list[tuple[object, str]]:
    """Enumerate maximal contiguous bodies and nested functions in *func_node*."""
    found: list[tuple[object, str]] = []
    seen: set[tuple[int, int]] = set()

    def add(node, label: str) -> None:
        key = (node.start_byte, node.end_byte)
        if key not in seen and node.end_byte > node.start_byte:
            seen.add(key)
            found.append((node, label))

    def rec(node) -> None:
        if node is not func_node and node.type in _FUNCTION_NODES:
            add(node, "nested_function")
            return
        if node.type in _BOUNDARY_OWNERS:
            body = _body_for_owner(node)
            if body is not None:
                add(body, _boundary_label(node.type))
        for child in node.children:
            rec(child)

    rec(func_node)
    return found


def _infer_language(func_node, source: bytes) -> str | None:
    prefix = _node_text(func_node, source).lstrip()[:40]
    if func_node.type == "function_definition" and prefix.startswith(("def ", "async def ")):
        return "python"
    if func_node.type in {"function_item"}:
        return "rust"
    if func_node.type == "method" and prefix.startswith("def "):
        return "ruby"
    if func_node.type in {"function_declaration", "method_definition", "arrow_function"}:
        return "javascript"
    if func_node.type in {"method_declaration", "constructor_declaration"}:
        return "java"
    return None


def _replacement_call(node, suggested_name: str, source: bytes) -> bytes:
    original = source[node.start_byte : node.end_byte].strip()
    call = suggested_name.encode("utf-8") + b"()"
    if original.startswith(b"{") and original.endswith(b"}"):
        return b"{ " + call + b"; }"
    if original.endswith(b";"):
        return call + b";"
    return call


def _walk_nodes_near_row(node, row: int):
    """Keep preorder while pruning spans outside the matcher's one-row window.

    A descendant cannot start inside the window if its entire parent span is
    outside it. An explicit stack also avoids Python recursion limits when a
    valid function is nested deeply in the parsed tree.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.start_point[0] > row + 1 or current.end_point[0] < row - 1:
            continue
        yield current
        stack.extend(reversed(current.children))


def _matching_function(tree, original, source: bytes):
    original_start = original.start_point[0]
    original_name = original.child_by_field_name("name")
    name_text = _node_text(original_name, source) if original_name is not None else None
    matches = []
    for node in _walk_nodes_near_row(tree.root_node, original_start):
        if node.type not in _FUNCTION_NODES or abs(node.start_point[0] - original_start) > 1:
            continue
        if name_text:
            candidate_name = node.child_by_field_name("name")
            if candidate_name is not None and _node_text(candidate_name, source) != name_text:
                continue
        matches.append(node)
    return min(matches, key=lambda node: abs(node.start_point[0] - original_start)) if matches else None


def _recomputed_parent_score(
    func_node,
    boundary,
    source: bytes,
    suggested_name: str,
    *,
    language: str | None,
) -> float | None:
    from roam.commands.changed_files import parse_source_with_grammar

    language = language or _infer_language(func_node, source)
    if not language:
        return None
    replacement = _replacement_call(boundary, suggested_name, source)
    rewritten = source[: boundary.start_byte] + replacement + source[boundary.end_byte :]
    tree, parsed, _ = parse_source_with_grammar(rewritten, language)
    if tree is None or parsed is None:
        return None
    rewritten_func = _matching_function(tree, func_node, parsed)
    if rewritten_func is None:
        return None
    if any(node.type == "ERROR" for node in _walk_nodes(rewritten_func)):
        return None
    return float(_walk_complexity(rewritten_func, parsed, 0)["cognitive"])


def _split_name(value: str) -> list[str]:
    words: list[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", value):
        words.extend(token.lower() for token in _NAME_SPLIT_RE.findall(part) if token)
    return words


def _suggested_name(node, source: bytes, label: str) -> str:
    verbs: Counter[str] = Counter()
    subjects: Counter[str] = Counter()
    for part in _walk_nodes(node):
        if part.type not in {"call", "call_expression", "invocation_expression"}:
            continue
        callee = part.child_by_field_name("function") or part.child_by_field_name("name")
        if callee is None and part.children:
            callee = part.children[0]
        if callee is None:
            continue
        identifiers = _identifier_leaves(callee, source)
        if not identifiers:
            continue
        call_words = _split_name(identifiers[-1])
        if call_words:
            verb = call_words[0]
            if verb in _VERBS:
                verbs[verb] += 1
        for identifier in identifiers[:-1]:
            for token in _split_name(identifier):
                if len(token) >= 3 and token not in _NAME_STOP_WORDS and token not in _VERBS:
                    subjects[token] += 1
        for argument in part.children[1:]:
            for identifier in _identifier_leaves(argument, source):
                for token in _split_name(identifier):
                    if len(token) >= 3 and token not in _NAME_STOP_WORDS and token not in _VERBS:
                        subjects[token] += 1
    if verbs:
        verb = sorted(verbs.items(), key=lambda item: (-item[1], item[0]))[0][0]
        if subjects:
            subject = sorted(subjects.items(), key=lambda item: (-item[1], item[0]))[0][0]
            return f"{verb}_{subject}"
        return f"{verb}_{label}"
    return f"extract_{label}_l{node.start_point[0] + 1}"


def _assignment_target(node):
    for field in ("left", "name", "declarator", "pattern"):
        target = node.child_by_field_name(field)
        if target is not None:
            return target
    return next((child for child in node.children if child.is_named), None)


def _safety_for_boundary(func_node, boundary, source: bytes) -> tuple[bool, str | None]:
    early = sorted({node.type for node in _walk_nodes(boundary) if node.type in _EARLY_EXIT_TYPES})
    if early:
        return False, f"control flow crosses the span ({', '.join(early)})"
    if any(node.type in {"global_statement", "nonlocal_statement"} for node in _walk_nodes(boundary)):
        return False, "the span declares global or nonlocal writes"

    writes: set[str] = set()
    for node in _walk_nodes(boundary):
        if node.type in _ASSIGNMENT_TYPES:
            target = _assignment_target(node)
            if target is None:
                continue
            if any(part.type in _NONLOCAL_TARGET_TYPES for part in _walk_nodes(target)):
                return False, "the span writes through an attribute or indexed value"
            writes.update(_identifier_leaves(target, source))
        if node.type in {"call", "call_expression", "invocation_expression"}:
            callee = node.child_by_field_name("function") or node.child_by_field_name("name")
            if callee is None and node.children:
                callee = node.children[0]
            identifiers = _identifier_leaves(callee, source) if callee is not None else []
            if (
                len(identifiers) >= 2
                and _split_name(identifiers[-1])[-1:]
                and _split_name(identifiers[-1])[-1] in _MUTATING_METHODS
            ):
                writes.add(identifiers[-2])

    after_reads = {
        _node_text(node, source)
        for node in _walk_nodes(func_node)
        if node.type in _IDENTIFIER_TYPES and node.start_byte >= boundary.end_byte
    }
    escaping = sorted(writes & after_reads)
    if escaping:
        names = ", ".join(f"`{name}`" for name in escaping[:3])
        return False, f"writes {names} used after the span"
    return True, None


def _candidate_payload(candidate: _BoundaryCandidate) -> dict:
    payload = {
        "span": {"start_line": candidate.line_start, "end_line": candidate.line_end},
        "suggested_name": candidate.suggested_name,
        "expected_delta": candidate.expected_delta,
        "residual_score": candidate.residual_score,
        "auto_fixable": candidate.auto_fixable,
    }
    if candidate.reason:
        payload["reason"] = candidate.reason
    return payload


def build_complexity_fix_hint(
    func_node,
    source: bytes,
    *,
    threshold: float = _HIGH_SEVERITY_FLOOR,
    language: str | None = None,
) -> dict | None:
    """Return the best extraction whose delta is recomputed from a rewritten AST.

    Candidate boundaries are maximal contiguous loop bodies, if arms, try
    bodies, match arms, and nested functions. Each candidate is replaced by a
    syntactically valid helper call, reparsed with the source grammar, and
    scored again with :func:`_walk_complexity`; ``expected_delta`` is therefore
    arithmetic over the rewritten AST rather than a nesting estimate.
    """
    total_score = float(_walk_complexity(func_node, source, 0)["cognitive"])
    if total_score < float(threshold):
        return None

    candidates: list[_BoundaryCandidate] = []
    for boundary, label in _collect_boundaries(func_node):
        suggested_name = _suggested_name(boundary, source, label)
        residual = _recomputed_parent_score(
            func_node,
            boundary,
            source,
            suggested_name,
            language=language,
        )
        if residual is None or residual >= total_score:
            continue
        auto_fixable, reason = _safety_for_boundary(func_node, boundary, source)
        candidates.append(
            _BoundaryCandidate(
                node=boundary,
                label=label,
                line_start=boundary.start_point[0] + 1,
                line_end=boundary.end_point[0] + 1,
                suggested_name=suggested_name,
                expected_delta=round(total_score - residual, 2),
                residual_score=round(residual, 2),
                auto_fixable=auto_fixable,
                reason=reason,
            )
        )
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -item.expected_delta,
            item.line_end - item.line_start + 1,
            item.line_start,
            item.suggested_name,
        )
    )
    best = candidates[0]
    hint = {"kind": "extract", **_candidate_payload(best)}
    if best.residual_score >= float(threshold):
        hint["iterative"] = True
        hint["iteration_reason"] = (
            f"no single block reduces the residual below threshold {threshold:g}; extraction must be iterative"
        )
        hint["candidate_spans"] = [_candidate_payload(candidate) for candidate in candidates[:2]]
    return hint


def _collect_blocks(func_node, source) -> list[tuple]:
    """Return ``[(node, depth), ...]`` for each extractable control-flow block.

    Mirrors :func:`roam.index.complexity._walk_complexity`'s depth accounting
    exactly so our in-place scores line up with the score the command reports:

      * a ``_CONTROL_FLOW`` node increments depth for its children;
      * a ``_CONTINUATION_FLOW`` node (elif/else/case) keeps the same depth;
      * we do not descend into nested functions/closures (extracting a block
        from inside a callback is a different, riskier refactor — the closure's
        complexity still counts toward the parent, we just don't offer its
        internals as roots).
    """
    found: list[tuple] = []

    def rec(node, depth: int) -> None:
        ntype = node.type
        if ntype in _FUNCTION_NODES and node is not func_node:
            return  # opaque: don't offer closure internals as extraction roots
        if ntype in _CONTROL_FLOW:
            if ntype in _EXTRACTABLE_BLOCKS:
                found.append((node, depth))
            for child in node.children:
                rec(child, depth + 1)
            return
        if ntype in _CONTINUATION_FLOW:
            for child in node.children:
                rec(child, depth)
            return
        for child in node.children:
            rec(child, depth)

    rec(func_node, 0)
    return found


def suggest_extractions(
    func_node,
    source: bytes,
    *,
    total_cc: float | None = None,
    max_hints: int = 3,
    min_lines: int = 3,
    min_reduction: float = 3.0,
    max_line_ratio: float = 0.7,
) -> list[ExtractionHint]:
    """Return up to *max_hints* :class:`ExtractionHint`\\ s for *func_node*, best
    first.

    Empty when no single block gives a meaningful reduction — that happens when
    complexity is *diffuse* (many flat branches / boolean conditions) rather
    than concentrated in a deeply-nested block, and the honest answer is "no
    single extraction helps; simplify the conditionals instead."

    ``total_cc`` defaults to a fresh walk of *func_node* so the arithmetic
    (``parent_after = total - reduction``) is internally consistent with the
    current source even if the stored index is stale. ``max_line_ratio`` caps a
    candidate at that fraction of the function's own line span: a block covering
    almost the whole body is a *rename*, not a decomposition, and dropping the
    parent to near-zero by lifting 90% of it out is a degenerate suggestion.
    """
    if total_cc is None:
        total_cc = _walk_complexity(func_node, source, 0)["cognitive"]

    func_span = (func_node.end_point[0] - func_node.start_point[0]) + 1

    hints: list[ExtractionHint] = []
    for node, depth in _collect_blocks(func_node, source):
        # In place: the block at its real depth — what the parent loses.
        in_place = _walk_complexity(node, source, depth)["cognitive"]
        # Rebased to depth 0: what it costs as the body of a fresh helper.
        rebased = _walk_complexity(node, source, 0)["cognitive"]

        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        line_count = line_end - line_start + 1

        if line_count < min_lines or in_place < min_reduction:
            continue
        # A block that IS essentially the whole function body isn't an
        # extraction, it's a rename — require the parent to retain something,
        # both structurally (complexity) and physically (line span).
        if in_place >= total_cc:
            continue
        if func_span > 0 and line_count > max_line_ratio * func_span:
            continue

        # The call that replaces the block adds nothing to cognitive
        # complexity (a bare call is not a control-flow node), so the parent
        # simply sheds the block's in-place contribution.
        parent_after = max(0.0, total_cc - in_place)
        hints.append(
            ExtractionHint(
                block_type=node.type,
                label=_BLOCK_LABEL.get(node.type, "block"),
                line_start=line_start,
                line_end=line_end,
                line_count=line_count,
                depth=depth,
                reduction=round(float(in_place), 1),
                parent_after=round(float(parent_after), 1),
                helper_cc=round(float(rebased), 1),
            )
        )

    # Rank: blocks that bring the parent under the high-severity floor "solve"
    # the finding — surface those first and, among them, prefer the SMALLEST
    # helper (fewest lines moved). Blocks that only dent it rank after, by
    # largest dent. Stable tie-break on source position.
    def _key(h: ExtractionHint) -> tuple:
        solves = 0 if h.parent_after < _HIGH_SEVERITY_FLOOR else 1
        # solvers: small-helper-first; non-solvers: big-dent-first.
        secondary = h.line_count if solves == 0 else -h.reduction
        return (solves, secondary, h.line_start)

    hints.sort(key=_key)

    # Drop strictly-worse nested duplicates: a candidate fully contained in an
    # already-kept block that sheds at least as much is a subset with no
    # advantage. (A smaller inner block that reduces *more per line* still wins
    # because it sorts ahead and is kept first.)
    kept: list[ExtractionHint] = []
    for h in hints:
        if any(k.line_start <= h.line_start and h.line_end <= k.line_end and k.reduction >= h.reduction for k in kept):
            continue
        kept.append(h)
        if len(kept) >= max_hints:
            break
    return kept


def hints_for_symbol(
    path: str,
    line_start: int,
    line_end: int,
    *,
    source: bytes | None = None,
    **kwargs,
) -> list[ExtractionHint]:
    """Parse *path*, locate the function spanning ``[line_start, line_end]``, and
    return its extraction hints.

    Returns ``[]`` (never raises) when the file can't be read, the language
    isn't parseable, or the function node can't be located — the caller treats
    "no hints" and "couldn't analyze" identically. ``source`` may be supplied to
    analyze an in-memory buffer instead of re-reading the file.
    """
    from roam.commands.changed_files import parse_source_with_grammar
    from roam.index.parser import detect_language

    if source is None:
        try:
            with open(path, "rb") as handle:
                source = handle.read()
        except OSError:
            return []

    language = detect_language(path)
    if not language:
        return []

    tree, parsed_source, _ = parse_source_with_grammar(source, language)
    if tree is None or parsed_source is None:
        return []

    func_node = _find_function_node(tree, line_start, line_end)
    if func_node is None:
        return []

    return suggest_extractions(func_node, parsed_source, **kwargs)


def fix_hint_for_symbol(
    path: str,
    line_start: int,
    line_end: int,
    *,
    threshold: float = _HIGH_SEVERITY_FLOOR,
    source: bytes | None = None,
) -> dict | None:
    """Parse one indexed symbol and return its machine-readable fix hint.

    Unsupported grammars, unreadable files, stale line ranges, and functions
    without a score-reducing contiguous boundary return ``None``. Callers omit
    the additive field in that case, preserving legacy envelopes byte-for-byte.
    """
    from roam.commands.changed_files import parse_source_with_grammar
    from roam.index.parser import detect_language

    if source is None:
        try:
            with open(path, "rb") as handle:
                source = handle.read()
        except OSError:
            return None
    language = detect_language(path)
    if not language:
        return None
    tree, parsed_source, _ = parse_source_with_grammar(source, language)
    if tree is None or parsed_source is None:
        return None
    func_node = _find_function_node(tree, line_start, line_end)
    if func_node is None:
        return None
    try:
        return build_complexity_fix_hint(
            func_node,
            parsed_source,
            threshold=threshold,
            language=language,
        )
    except Exception as exc:  # noqa: BLE001 — additive enrichment preserves callers
        from roam.observability import log_swallowed

        log_swallowed("complexity_extract:fix_hint_for_symbol", exc)
        return None
