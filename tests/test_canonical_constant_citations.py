"""Lints that canonical constant tuples/sets carry a citation comment with
>=1 numeric figure. Surfaced by W36.6: W6.3's JS_FAMILY_LANGUAGES carries
its dogfood metric verbatim in the docstring ("23 of 89 dead findings
in a real Vue/TS codebase (~26%)") -- self-documenting prophylactic that prevents
future contributors from "cleaning up" without seeing the regression.

Formalising the pattern: every module-level constant whose name ends in
_LANGUAGES, _EXTENSIONS, or _FAMILY and whose value is an iterable
literal (tuple / set / frozenset / list / dict) must have a leading
docstring or comment containing at least one digit.

The lint exists to CATCH NEW DRIFT. Pre-existing constants without a
citation are recorded in ``_NO_CITATION_NEEDED`` with a grandfather-clause
TODO; back-filling history is a separate task.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_ROAM_ROOT = _SRC_ROOT / "roam"
_SUFFIXES = ("_LANGUAGES", "_EXTENSIONS", "_FAMILY")
_DIGIT_RE = re.compile(r"\d")

# Allowlist for constants that legitimately don't need a citation. Each entry
# is the dotted module path + constant name (e.g.,
# ``roam.languages.registry._TS_EXTENSIONS``) so the list is grep-friendly.
# Each entry MUST carry a one-line rationale.
#
# History:
#   W37.5 removed the 3 _DOC_EXTENSIONS duplicates (file_roles, cmd_intent,
#     cmd_secrets) by consolidating onto file_roles.DOC_EXTENSIONS.
#   W38.3 back-filled citations for the remaining 11 grandfather entries
#     (SKIP_EXTENSIONS, _CONFIG/_DATA/_MINIFIABLE_EXTENSIONS,
#     REGEX_ONLY_LANGUAGES, _SUPPORTED_LANGUAGES, _SCANNABLE_EXTENSIONS,
#     _FRONTEND/_BACKEND_EXTENSIONS, _UI_EXTENSIONS, _BINARY_EXTENSIONS),
#     reducing the allowlist from 11 entries to 0. The lint is now load-bearing:
#     every new canonical iterable must ship a citation comment with a count.
_NO_CITATION_NEEDED: frozenset[str] = frozenset()


def _is_iterable_literal(value_node: ast.AST | None) -> bool:
    """Return True if the assigned value is a tuple/set/frozenset/list/dict
    literal (or ``frozenset({...})`` / ``set([...])`` / ``tuple(...)``
    constructor call). Non-iterable values (a string, int, None) are
    excluded from the lint per the W36.6 constraint -- the pattern targets
    canonical iterables, not single-value flags.
    """
    if value_node is None:
        return False
    if isinstance(value_node, (ast.Tuple, ast.Set, ast.List, ast.Dict)):
        return True
    if isinstance(value_node, ast.Call):
        # frozenset({...}) / set([...]) / tuple(...) / dict(...)
        if isinstance(value_node.func, ast.Name) and value_node.func.id in {
            "frozenset",
            "set",
            "tuple",
            "list",
            "dict",
        }:
            return True
    return False


def _find_constants_with_suffix(tree: ast.AST) -> list[tuple[str, int]]:
    """Return ``(name, lineno)`` for module-level Name targets whose id ends
    in a tracked suffix AND whose value is an iterable literal.
    """
    out: list[tuple[str, int]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            if not _is_iterable_literal(node.value):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and any(target.id.endswith(s) for s in _SUFFIXES):
                    out.append((target.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not _is_iterable_literal(node.value):
                continue
            if any(node.target.id.endswith(s) for s in _SUFFIXES):
                out.append((node.target.id, node.lineno))
    return out


def _has_citation_above(source_lines: list[str], lineno: int) -> bool:
    """Walk backwards from ``lineno - 1`` looking for a contiguous comment
    block or trailing docstring. Return True if ANY of those leading
    non-blank lines contains a digit.

    Heuristic -- looks at the 10 lines preceding the constant. A comment
    block or a triple-quoted docstring on the prior contiguous lines counts.
    """
    start = max(0, lineno - 11)  # lineno is 1-based; back off up to 10 lines
    leading = source_lines[start : lineno - 1]
    # Strip trailing blank lines, then collect from end backwards
    while leading and not leading[-1].strip():
        leading.pop()
    if not leading:
        return False
    block: list[str] = []
    in_docstring = False
    for line in reversed(leading):
        stripped = line.strip()
        if not stripped:
            break
        is_comment = stripped.startswith("#")
        is_doc_marker = (
            stripped.startswith('"""')
            or stripped.startswith("'''")
            or stripped.endswith('"""')
            or stripped.endswith("'''")
        )
        if is_comment or is_doc_marker or in_docstring:
            block.append(line)
            # Toggle docstring tracking when we see triple-quote markers
            if is_doc_marker and not in_docstring:
                in_docstring = True
            elif is_doc_marker and in_docstring:
                in_docstring = False
        else:
            # First non-comment non-doc line above the constant -> stop
            break
    return any(_DIGIT_RE.search(line) for line in block)


def _qualified_name(py_file: Path, name: str) -> str:
    """``D:/.../src/roam/languages/registry.py`` + ``JS_FAMILY_LANGUAGES``
    -> ``roam.languages.registry.JS_FAMILY_LANGUAGES``.
    """
    rel = py_file.relative_to(_SRC_ROOT)
    return f"{rel.with_suffix('').as_posix().replace('/', '.')}.{name}"


def test_canonical_constants_have_numeric_citations():
    """Every ``_LANGUAGES`` / ``_EXTENSIONS`` / ``_FAMILY`` iterable constant
    must have >=1 digit in the leading comment block or docstring.
    Self-documenting prophylactic per W36.6 finding (modelled on W6.3's
    JS_FAMILY_LANGUAGES at ``src/roam/languages/registry.py``).
    """
    missing: list[str] = []
    for py_file in _ROAM_ROOT.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(py_file))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        source_lines = text.splitlines()
        for name, lineno in _find_constants_with_suffix(tree):
            qualified = _qualified_name(py_file, name)
            if qualified in _NO_CITATION_NEEDED:
                continue
            if not _has_citation_above(source_lines, lineno):
                rel_display = py_file.relative_to(_REPO_ROOT).as_posix()
                missing.append(f"{rel_display}:{lineno} {name} ({qualified})")
    assert not missing, (
        "Canonical constants need a numeric citation in their leading comment "
        "block or docstring. Add the regression count, sprint ref, or entry "
        "count (e.g., '12 entries; W19.x dogfood: 7 of 50 FPs').\n"
        "Reference: W6.3's JS_FAMILY_LANGUAGES at src/roam/languages/registry.py.\n"
        "If the constant truly does not warrant a citation, add it to "
        "_NO_CITATION_NEEDED in tests/test_canonical_constant_citations.py "
        "with a one-line rationale.\n\n"
        "Missing:\n" + "\n".join(f"  - {m}" for m in missing)
    )


def test_allowlist_entries_still_exist():
    """Every allowlist entry must still resolve to a real constant. Catches
    drift when a constant is renamed or removed -- the allowlist would
    silently rot otherwise.
    """
    discovered: set[str] = set()
    for py_file in _ROAM_ROOT.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(py_file))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        for name, _lineno in _find_constants_with_suffix(tree):
            discovered.add(_qualified_name(py_file, name))
    stale = sorted(_NO_CITATION_NEEDED - discovered)
    assert not stale, (
        "Allowlist entries no longer correspond to real constants -- "
        "remove them from _NO_CITATION_NEEDED in "
        "tests/test_canonical_constant_citations.py:\n" + "\n".join(f"  - {s}" for s in stale)
    )
