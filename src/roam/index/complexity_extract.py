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
