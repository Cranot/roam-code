"""W512 drift-guard — every ``edges.kind`` filter must source its
vocabulary from :mod:`roam.db.edge_kinds`, not inline tuples.

The W493 / W499 / W511 / W522 / W524 bug family was four (then five,
then seven) silent-no-op bugs spread across the codebase: every site
that filtered ``edges.kind`` was inlining its own ``('call', 'calls',
'reference', 'references')`` tuple, and a single typo (``'calls'``
instead of ``'call'`` against a writer that emits singular) returned
zero rows with no error.

W512 (this drift-guard) consolidates the vocabulary into one named
module and lints the source tree to prove no new site re-introduces an
inline tuple literal.

What this test asserts
----------------------

For every ``src/roam/**/*.py`` file outside the allowlist:

1. **No inline ``kind IN (... 'call' ...)`` tuples.** If a site filters
   ``kind`` against the canonical call/reference vocabulary, it must
   import ``CALL_OR_REF_KINDS`` / ``CALL_EDGE_KINDS`` /
   :func:`call_or_ref_in_clause` from :mod:`roam.db.edge_kinds`.

2. **No inline ``kind = 'call'`` equality.** Singular equality misses
   plural plugin variants; the W493 fix moved those to ``IN``. The
   drift-guard now blocks the regression.

The allowlist captures sites whose edge-kind universe is intentionally
WIDER than the canonical call/reference set (e.g. ``cmd_hover.py``
mixes in ``'inherits'`` / ``'import'`` / ``'imports'``). Those stay
inline; the test lists them explicitly so future audits know they
were considered.

Implementation: AST-walk the source tree and inspect every string
constant. Docstrings are excluded so prose references like
``edges.kind = 'call'`` in a docstring do not trip the lint. This is
robust against the line-counting false-positives a regex-only scan
suffers on multi-line triple-quoted strings.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"

# Sites that intentionally extend the canonical call/reference vocabulary
# with additional edge kinds. The drift-guard treats these as a
# deliberate-design allowlist; revisit if the edge-kind universe shifts.
_ALLOWLIST: dict[str, str] = {
    # cmd_hover mixes call + inheritance + import edges to find the
    # most-relevant neighbour. Wider than CALL_OR_REF_KINDS by design.
    "commands/cmd_hover.py": "intentionally unions call + inherits + import",
    # side_effects extends CALL_OR_REF_KINDS with phantom 'invokes' /
    # 'uses' kinds defensively for plugin extractors. The constant IS
    # imported; the inline extension is the deliberate part.
    "world_model/side_effects.py": "extends CALL_OR_REF_KINDS with phantom plugin kinds",
    # The canonical module itself defines the literals.
    "db/edge_kinds.py": "canonical source of truth",
}

# Match kind IN ('call', ...) and variants. Catches singular and plural
# forms — both are part of the W493 family vocabulary. We anchor on a
# non-identifier left boundary so Python ``edge_kind in (...)``
# membership checks don't match, and we require uppercase IN to keep
# SQL distinct from Python's lowercase ``in`` operator.
_INLINE_IN_PATTERN = re.compile(
    r"""(?<![A-Za-z_])kind\s+IN\s*\(\s*['\"](?:call|calls|reference|references)['\"]""",
)

# Match kind = 'call' / kind = 'calls' / kind = 'reference' / kind =
# 'references' singular equality INSIDE SQL strings. SQL fragments
# embedded in Python use single quotes for string literals (because
# the outer Python string is usually double-quoted); Python writer
# call-sites that pass ``kind="call"`` as a keyword argument use
# double quotes. We require single quotes here so the lint matches
# SQL filters only, not the writer-side constructors.
_INLINE_EQ_PATTERN = re.compile(
    r"""(?<![A-Za-z_])kind\s*=\s*'(?:call|calls|reference|references)'""",
)


def _iter_source_files() -> list[Path]:
    return [
        p
        for p in SRC_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Return ``id(node)`` for every string node that is a docstring.

    Module / function / class / async-function docstrings are the
    first statement of the body and an :class:`ast.Expr` wrapping a
    constant string.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _find_string_violations(path: Path, pattern: re.Pattern) -> list[str]:
    """AST-walk *path*, return ``rel:line: snippet`` for every non-docstring
    string node whose value matches *pattern*.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        # Unparseable — skip rather than mask real syntax errors.
        return []
    docstring_ids = _docstring_ids(tree)
    rel = path.relative_to(SRC_ROOT).as_posix()
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        if id(node) in docstring_ids:
            continue
        if pattern.search(node.value):
            snippet = node.value.strip().splitlines()[0][:120] if node.value.strip() else ""
            violations.append(f"{rel}:{node.lineno}: {snippet}")
    return violations


def test_no_inline_edge_kind_in_tuples() -> None:
    """Every ``kind IN (...)`` site must use roam.db.edge_kinds."""
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        violations.extend(_find_string_violations(path, _INLINE_IN_PATTERN))
    assert not violations, (
        "W512: inline edges.kind IN-tuple — import from "
        "roam.db.edge_kinds instead:\n  " + "\n  ".join(violations)
    )


def test_no_inline_edge_kind_equality() -> None:
    """Every ``kind = '<call>'`` equality must use roam.db.edge_kinds.

    Singular equality silently misses plural plugin variants. The W493
    family fix is to use ``kind IN (...)`` over the canonical set.
    """
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        violations.extend(_find_string_violations(path, _INLINE_EQ_PATTERN))
    assert not violations, (
        "W512: inline edges.kind = '<value>' equality — use IN-clause "
        "from roam.db.edge_kinds instead:\n  " + "\n  ".join(violations)
    )


def test_canonical_module_imports_consistent() -> None:
    """The canonical constants are the only allowed entry point.

    Asserts the module surface stays stable: CALL_EDGE_KINDS,
    REFERENCE_EDGE_KINDS, CALL_OR_REF_KINDS, plus the two helpers.
    """
    from roam.db import edge_kinds

    assert edge_kinds.CALL_EDGE_KINDS == ("call", "calls")
    assert edge_kinds.REFERENCE_EDGE_KINDS == ("reference", "references")
    assert edge_kinds.CALL_OR_REF_KINDS == (
        "call",
        "calls",
        "reference",
        "references",
    )
    # Helpers exist + return the expected literal shape.
    assert "kind IN" in edge_kinds.call_or_ref_in_clause()
    assert edge_kinds.call_or_ref_placeholders().count("?") == 4


def test_allowlist_entries_actually_exist() -> None:
    """Every allowlist entry must point at a real file (no stale rows)."""
    missing = [
        rel for rel in _ALLOWLIST if not (SRC_ROOT / rel).exists()
    ]
    assert not missing, f"W512 allowlist references missing files: {missing}"


@pytest.mark.parametrize(
    "rel,expected_marker",
    [
        ("critique/checks.py", "call_or_ref_in_clause"),
        ("world_model/side_effects.py", "CALL_OR_REF_KINDS"),
        ("security/taint_engine.py", "call_or_ref_in_clause"),
        ("commands/cmd_oracle.py", "call_or_ref_in_clause"),
        ("commands/cmd_taint.py", "call_or_ref_in_clause"),
        ("commands/cmd_risk.py", "call_or_ref_in_clause"),
        ("commands/cmd_patterns.py", "CALL_EDGE_KINDS"),
        ("commands/cmd_dead.py", "CALL_EDGE_KINDS"),
        ("analysis/taint.py", "CALL_EDGE_KINDS"),
        ("rules/dataflow.py", "CALL_EDGE_KINDS"),
        ("catalog/detectors.py", "CALL_EDGE_KINDS"),
        ("catalog/python_idioms.py", "CALL_EDGE_KINDS"),
    ],
)
def test_migrated_sites_import_canonical_module(
    rel: str, expected_marker: str
) -> None:
    """The known migration targets each import from roam.db.edge_kinds."""
    path = SRC_ROOT / rel
    text = path.read_text(encoding="utf-8")
    assert "roam.db.edge_kinds" in text, (
        f"W512: {rel} should import from roam.db.edge_kinds"
    )
    assert expected_marker in text, (
        f"W512: {rel} should reference {expected_marker}"
    )
