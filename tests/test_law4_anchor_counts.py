"""Pin the LAW 4 anchor-set entry counts cited in ``CLAUDE.md``.

CLAUDE.md cites numeric entry counts for the two LAW 4 anchor sets:
``src/roam/output/formatter.py:concrete_plural_terminals`` and
``tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS``. Those counts drifted
out of sync in earlier sprints — W31.1 reported "66 / 108" against the
"~95 / 91+17" wording in the doc, but the 66 figure was an artefact of a
greedy-paren regex that stopped at an embedded ``)`` inside the source
comment. The AST-based ground truth is 91 / 108 = 91 shared + 17
additions (W26.5 / W33.1).

This test pins the doc against the source so the next addition has to
update both. It also re-asserts the strict-superset invariant: every
formatter terminal must appear in the lint anchor set.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def _formatter_terminals() -> set[str]:
    """Return the ``concrete_plural_terminals`` set parsed from the
    formatter source via AST.

    The tuple is a *local* inside ``_humanize_summary_fact`` so it is not
    importable as a module attribute. Regex extraction is unreliable
    because the tuple body contains a closing ``)`` inside an inline
    comment (``# (``files_passed`` / ``symbols_failed`` / ``runs_skipped``).``)
    which truncates non-greedy patterns. AST parsing walks the actual
    syntax tree so embedded parens in comments are ignored.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "output" / "formatter.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "concrete_plural_terminals":
                    if isinstance(node.value, ast.Tuple):
                        return {
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
    raise AssertionError("concrete_plural_terminals tuple not found in formatter.py")


def _lint_anchors() -> frozenset[str]:
    spec = importlib.util.spec_from_file_location(
        "_law4_lint_module",
        Path(__file__).parent / "test_law4_lint.py",
    )
    assert spec and spec.loader, "could not load test_law4_lint.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._CONCRETE_NOUN_ANCHORS  # type: ignore[attr-defined,no-any-return]


def test_lint_is_strict_superset_of_formatter() -> None:
    """Every formatter terminal must appear in the lint anchor set, so the
    humanizer and the lint cannot disagree on a fact's pass/fail status.
    """
    formatter_terms = _formatter_terminals()
    lint_terms = set(_lint_anchors())

    formatter_only = formatter_terms - lint_terms
    assert not formatter_only, (
        f"Formatter has {len(formatter_only)} terminal(s) the lint does not: "
        f"{sorted(formatter_only)}. Add them to "
        f"tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS."
    )


def test_claude_md_cites_correct_anchor_counts() -> None:
    """CLAUDE.md cites entry counts for the LAW 4 anchor sets in the
    'Concrete-noun anchor vocabulary' subsection. The numbers must match
    the actual sources so doc-drift is caught instead of shipped.

    Reference: W31.1 found a "~95 / 91+17" wording in CLAUDE.md and
    reported it as wrong (claiming 66 / 108 via regex). W33.1 re-counted
    via AST and confirmed the doc is right at 91 / 108 — the W31.1 regex
    was truncated by an embedded ``)`` in a comment. This test pins the
    AST-derived ground truth into the doc.
    """
    formatter_count = len(_formatter_terminals())
    lint_count = len(_lint_anchors())
    overlap = len(_formatter_terminals() & set(_lint_anchors()))
    additions = lint_count - overlap

    claude_md_path = Path(__file__).parent.parent / "CLAUDE.md"
    if not claude_md_path.exists():
        import pytest

        pytest.skip(
            "CLAUDE.md is intentionally untracked on public clones / CI "
            "(removed in commit 89a338d9). The doc-drift assertions below "
            "are defence-in-depth on local dev only."
        )
    claude_md = claude_md_path.read_text(encoding="utf-8")

    # The doc has TWO sentences citing these counts (line ~57 source-of-truth
    # block, line ~65 contributor instructions). Both must agree with source.
    assert f"{formatter_count} entries" in claude_md, (
        f"CLAUDE.md should cite formatter terminal count = {formatter_count} in the LAW 4 anchor-vocabulary section."
    )
    assert f"{lint_count} entries" in claude_md, (
        f"CLAUDE.md should cite lint anchor count = {lint_count} in the LAW 4 anchor-vocabulary section."
    )
    assert f"{overlap} shared with the formatter" in claude_md, (
        f"CLAUDE.md should cite overlap = {overlap} in the LAW 4 anchor-vocabulary section."
    )
    assert f"adds {additions} SBOM/registry-domain terminals" in claude_md, (
        f"CLAUDE.md should cite additions = {additions} in the LAW 4 anchor-vocabulary section."
    )
