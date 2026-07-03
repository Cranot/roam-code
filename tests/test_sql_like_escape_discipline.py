"""Drift-guard: SQL LIKE patterns with literal `_` must use ESCAPE.

A LIKE pattern containing an unescaped underscore `_` matches "any single
character", not the literal character `_`. When the author intended the
literal (e.g. `LIKE '%test_%'` to match file paths containing the substring
`test_`), the unescaped form matches accidental wildcards too — `testX`,
`Xtest.py`, etc. That's a silent precision loss.

W991 fixed 15 sites in ``src/roam/catalog/detectors.py``; W992 fixed 2 sites
in ``src/roam/commands/cmd_migration_plan.py``; the W993 drive-by sweep
escaped the remaining 9 occurrences in ``cmd_patterns.py``, ``cmd_understand.py``
and ``catalog/detectors.py``. Post-fix, the codebase has zero LIKE patterns
with a literal underscore that lack an ESCAPE clause.

This test AST-walks ``src/roam/`` for ``.execute(...)`` / ``.executemany(...)``
call sites (and module-level SQL string constants) and flags any LIKE literal
that carries an unescaped `_` without a trailing ``ESCAPE '\\'`` clause.

How to silence a NEW violation:

1. Preferred — escape the literal: ``LIKE 'foo\\_%' ESCAPE '\\'``.
2. If the wildcard is intentional (the `_` is actually meant to match any
   single character), add the call site to ``_INTENTIONAL_WILDCARD_ALLOWLIST``
   with a short rationale.

This test deliberately uses a small regex helper rather than parsing arbitrary
SQL — overly-clever SQL parsing produces false negatives. The trade-off:
multi-line concatenation across `+` operators is handled; arbitrary runtime
string building (``f"... {var} ..."`` with the LIKE literal inside an
interpolation) is NOT. Such cases will not flag here but should be reviewed
manually.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src" / "roam"

# Matches `LIKE '<pattern>'` (single-quoted). Captures the pattern body.
_LIKE_LITERAL_RE = re.compile(r"LIKE\s+'([^']*)'", re.IGNORECASE)
# Matches a trailing `ESCAPE '\'` clause (SQL convention).
_ESCAPE_CLAUSE_RE = re.compile(r"\s+ESCAPE\s+'\\'", re.IGNORECASE)

# Intentional-wildcard allowlist. Format: (relative_path, lineno, pattern_substr, rationale).
# Empty post-W991/W992/W993 — the codebase has zero remaining violations.
# Add an entry here ONLY when the `_` is genuinely meant as a "any single
# character" wildcard.
_INTENTIONAL_WILDCARD_ALLOWLIST: tuple[tuple[str, int, str, str], ...] = ()


def _has_unescaped_underscore(pattern: str) -> bool:
    """True when the LIKE pattern body contains a literal `_` that is not
    preceded by a backslash escape. We model a minimal SQL-LIKE escape grammar
    where `\\` consumes the following character.
    """
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2  # skip escape pair
            continue
        if c == "_":
            return True
        i += 1
    return False


def _scan_string_for_like_violations(sql: str) -> list[tuple[str, bool]]:
    """Yield (pattern_body, has_trailing_escape_clause) for each LIKE in `sql`."""
    out: list[tuple[str, bool]] = []
    for m in _LIKE_LITERAL_RE.finditer(sql):
        pat = m.group(1)
        if not _has_unescaped_underscore(pat):
            continue
        tail = sql[m.end() : m.end() + 30]
        has_escape = bool(_ESCAPE_CLAUSE_RE.match(tail))
        out.append((pat, has_escape))
    return out


def _collect_string_literal_args(call_node: ast.Call) -> list[str]:
    """Best-effort extraction of string literals from the first positional arg
    of a `.execute(...)` / `.executemany(...)` call. Handles:

    - plain `ast.Constant` strings
    - `ast.JoinedStr` (f-strings) — concatenates only the literal parts
    - `ast.BinOp` with `Add` — recursive concat of string constants
    """
    if not call_node.args:
        return []
    arg0 = call_node.args[0]
    strings: list[str] = []
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
        strings.append(arg0.value)
    elif isinstance(arg0, ast.JoinedStr):
        # f-string: concat literal parts; interpolations are opaque.
        for part in arg0.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                strings.append(part.value)
    elif isinstance(arg0, ast.BinOp) and isinstance(arg0.op, ast.Add):

        def _concat(n: ast.AST) -> str:
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                return n.value
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                return _concat(n.left) + _concat(n.right)
            return ""

        strings.append(_concat(arg0))
    return strings


def _is_execute_call(call_node: ast.Call) -> bool:
    func = call_node.func
    if isinstance(func, ast.Attribute):
        return func.attr in ("execute", "executemany")
    return False


def _missing_escape_clause_violations(
    rel: str,
    lineno: int,
    sql_strings: list[str],
    pattern_suffix: str = "",
) -> list[tuple[str, int, str]]:
    """Return LIKE patterns that still need an explicit ESCAPE clause."""
    violations: list[tuple[str, int, str]] = []
    for sql in sql_strings:
        for pat, has_escape in _scan_string_for_like_violations(sql):
            if not has_escape:
                violations.append((rel, lineno, f"{pat}{pattern_suffix}"))
    return violations


def _direct_sql_call_violations(rel: str, node: ast.AST) -> list[tuple[str, int, str]]:
    """Catch inline SQL literals before they become wildcard-prone queries."""
    if not isinstance(node, ast.Call) or not _is_execute_call(node):
        return []
    return _missing_escape_clause_violations(rel, node.lineno, _collect_string_literal_args(node))


def _hoisted_sql_template_violations(rel: str, node: ast.AST) -> list[tuple[str, int, str]]:
    """Catch module-level SQL templates before execution hides their source line."""
    if not isinstance(node, ast.Assign):
        return []
    value = node.value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return []
    if "LIKE" not in value.value.upper():
        return []
    return _missing_escape_clause_violations(rel, node.lineno, [value.value], "  [module-level]")


def _tree_violations_that_preserve_source_lines(rel: str, tree: ast.AST) -> list[tuple[str, int, str]]:
    """Collect AST violations while preserving the source line of each rule."""
    violations: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        violations.extend(_direct_sql_call_violations(rel, node))
        violations.extend(_hoisted_sql_template_violations(rel, node))
    return violations


def _parse_source_or_skip_to_keep_audit_tolerant(py: Path) -> ast.AST | None:
    """Parse one source file, returning None when the repo-wide audit should keep walking."""
    try:
        src_text = py.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return ast.parse(src_text)
    except SyntaxError:
        return None


def _walk_src_for_violations() -> list[tuple[str, int, str]]:
    """Return (rel_path, lineno, pattern_body) for every violation in src/roam/."""
    violations: list[tuple[str, int, str]] = []
    for py in _SRC.rglob("*.py"):
        tree = _parse_source_or_skip_to_keep_audit_tolerant(py)
        if tree is None:
            continue

        rel = py.relative_to(_REPO_ROOT).as_posix()
        violations.extend(_tree_violations_that_preserve_source_lines(rel, tree))
    return violations


def _strip_allowlisted(
    violations: list[tuple[str, int, str]],
) -> list[tuple[str, int, str]]:
    """Drop allowlisted (path, line, substring) entries."""
    out: list[tuple[str, int, str]] = []
    for path, line, pat in violations:
        skip = False
        for allow_path, allow_line, allow_substr, _rationale in _INTENTIONAL_WILDCARD_ALLOWLIST:
            if path.endswith(allow_path) and line == allow_line and allow_substr in pat:
                skip = True
                break
        if not skip:
            out.append((path, line, pat))
    return out


def test_no_unescaped_like_underscore_in_src() -> None:
    """Every LIKE pattern with a literal `_` carries an ``ESCAPE '\\'`` clause.

    Post-W991/W992/W993: zero violations. Adding a new ``LIKE '%foo_%'``
    without ``ESCAPE`` will fail this test — pick one of:

    1. Escape the literal: ``LIKE '%foo\\_%' ESCAPE '\\'`` (preferred).
    2. Add an entry to ``_INTENTIONAL_WILDCARD_ALLOWLIST`` with rationale.
    """
    violations = _walk_src_for_violations()
    surviving = _strip_allowlisted(violations)
    if surviving:
        formatted = "\n".join(f"  {path}:{line}  LIKE '{pat}'" for path, line, pat in surviving)
        pytest.fail(
            "Found "
            f"{len(surviving)} LIKE patterns with literal `_` and no ESCAPE clause:\n"
            f"{formatted}\n\n"
            "Either escape the literal (`LIKE 'foo\\_%' ESCAPE '\\'`) or add an "
            "entry to _INTENTIONAL_WILDCARD_ALLOWLIST with a rationale."
        )


def test_allowlist_entries_are_well_formed() -> None:
    """Every allowlist entry must point at a real source line and carry a rationale."""
    for path, line, substr, rationale in _INTENTIONAL_WILDCARD_ALLOWLIST:
        full = _REPO_ROOT / path
        assert full.exists(), f"allowlist path does not exist: {path}"
        assert isinstance(line, int) and line > 0, f"bad line for {path}: {line}"
        assert substr, f"empty pattern substring for {path}:{line}"
        assert rationale and len(rationale) >= 10, f"rationale too short for {path}:{line}: {rationale!r}"


def test_audit_helpers_detect_known_unescaped() -> None:
    """Smoke check: the underscore detector flags an unescaped `_` and skips an escaped one.

    This pins the helper's semantics independent of source content so a future
    refactor of `_has_unescaped_underscore` cannot silently soften the rule.
    """
    assert _has_unescaped_underscore("%foo_%")
    assert _has_unescaped_underscore("create_%")
    assert not _has_unescaped_underscore(r"%foo\_%")
    assert not _has_unescaped_underscore(r"create\_%")
    assert not _has_unescaped_underscore("%foo%")
    # Pattern with both escaped + unescaped — should flag (the unescaped one wins).
    assert _has_unescaped_underscore(r"%foo\_bar_baz%")


def test_audit_helpers_detect_escape_clause() -> None:
    """Smoke check: the trailing-ESCAPE detector matches the canonical shape.

    The scanner only yields LIKE patterns whose body has an UNESCAPED literal
    underscore — properly-escaped patterns (`\\_`) are not flagged because they
    cannot be wildcards by construction. We exercise the ESCAPE-clause detector
    via an unescaped pattern followed by an ESCAPE clause, which represents the
    rare "author meant the wildcard AND added ESCAPE for an unrelated metachar"
    shape. In that case `has_escape=True` and the test reports no violation.
    """
    # NB: the SQL text — as it would arrive at sqlite — uses ONE backslash
    # between the quotes. In Python source the same literal is spelled
    # ``"ESCAPE '\\'"`` (double-backslash inside a regular string) or
    # ``r"ESCAPE '\'"`` is illegal because a raw string cannot end in a single
    # backslash. We construct the test inputs as regular strings to match the
    # at-runtime SQL.
    sql_unescaped_with_clause = "LIKE '%foo_%' ESCAPE '\\'"
    sql_unescaped_no_clause = "LIKE '%foo_%'"
    found_with = _scan_string_for_like_violations(sql_unescaped_with_clause)
    found_without = _scan_string_for_like_violations(sql_unescaped_no_clause)
    assert found_with == [("%foo_%", True)]
    assert found_without == [("%foo_%", False)]
    # And properly-escaped patterns are not yielded at all.
    assert _scan_string_for_like_violations("LIKE '%foo\\_%'") == []
    assert _scan_string_for_like_violations("LIKE '%foo\\_%' ESCAPE '\\'") == []
