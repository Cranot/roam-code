"""W588 drift-guard — tests/ must resolve the project root via
:func:`tests._helpers.repo_root.repo_root`, not the fragile
``Path(__file__).resolve().parents[N]`` / ``Path(__file__).parent.parent``
walk.

Why the walk is fragile
-----------------------

Agents dispatched into nested Claude-Code worktrees (the
``.claude/worktrees/.../.claude/worktrees/...`` layout) execute test code
from a tree that has a real ``.git`` link but lacks the project-root
marker files (chiefly ``CLAUDE.md``) because those are uncommitted on
``main`` or live only at the canonical top-level. Tests that compute
their root as ``Path(__file__).resolve().parents[1]`` silently break in
that environment: ``parents[1]`` lands on the worktree root, the path
check fails, and downstream assertions trip on missing content.

W572 introduced :mod:`tests._helpers.repo_root` (``git rev-parse
--show-toplevel`` first, marker-file walk second, historical
``parents[2]`` fallback last) as the single source of truth. W587 began
the migration sweep; W594 is queued to migrate the remaining sites.

This drift-guard prevents NEW occurrences after the W594 sweep
completes. It ships fail-loud today with a ``_PRE_W594_PENDING``
allowlist of the currently-known offenders so the W594 batches can
drop entries as they migrate without re-touching this file.

Mirrors :mod:`tests.test_w512_edge_kinds_drift` and
:mod:`tests.test_w547_severity_drift` — same AST-walker pattern, same
allowlist-with-rationale style.

What this drift-guard catches
-----------------------------

For every ``tests/**/*.py`` file outside the allowlists:

* ``Path(__file__).resolve().parents[N]`` (any N) — the classic
  fragile shape.
* ``Path(__file__).resolve().parents[N] / "..."`` — the same shape
  with a path suffix (no different at the AST level — the subscript is
  the thing).
* ``Path(__file__).parent.parent`` (depth >= 2, with or without an
  intervening ``.resolve()``) — the historical chain-of-``.parent``
  variant used by a handful of pre-W572 sites.

Detection uses an AST walk (string literals inside docstrings or
multi-line comments do not match — only real expression nodes that
actually reference ``__file__`` somewhere in their value chain).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

TESTS_ROOT = repo_root() / "tests"


# Sites that legitimately use ``.parents[]`` / ``.parent.parent`` for a
# reason other than resolving the project root. Keep this list small;
# every entry needs a one-line rationale.
_ALLOWLIST: dict[str, str] = {
    # The helper itself owns the canonical fallback at the bottom of
    # repo_root() — that fallback IS the fragile-walk shape on
    # purpose (last-resort branch when both git and marker walks fail).
    "_helpers/repo_root.py": (
        "canonical helper — owns the historical fallback walk by design"
    ),
    # The helper's drift-guard pins the helper itself; it goes through
    # repo_root() and does NOT use the fragile pattern.
    "test_repo_root_helper.py": (
        "pins the helper's contract — uses repo_root() directly"
    ),
}


# W594 migration backlog — every entry is a currently-known offender
# the sweep will migrate to ``from tests._helpers.repo_root import
# repo_root``. As batches land, drop the corresponding entries; the
# drift-guard goes green when this dict is empty. NEW additions are
# blocked by the lint (fail-loud).
#
# Inventory captured at W588-ship time via an AST walk of tests/.
# Re-generate with ``tests/_helpers`` tooling if drift suspected.
_PRE_W594_PENDING: dict[str, str] = {
    "test_ask.py": "W594 backlog",
    "test_atomic_io_consolidation.py": "W594 backlog",
    "test_budget_coverage_survey.py": "W594 backlog",
    "test_canonical_constant_citations.py": "W594 backlog",
    "test_canonical_demo_fixture.py": "W594 backlog",
    "test_clones.py": "W594 backlog",
    "test_competitor_site_data.py": "W594 backlog",
    "test_context_propagation.py": "W594 backlog",
    "test_demo_fixtures.py": "W594 backlog",
    "test_demo_gif_asset.py": "W594 backlog",
    "test_detail_flag_hints.py": "W594 backlog",
    "test_docker_assets.py": "W594 backlog",
    "test_docs_site_quality.py": "W594 backlog",
    "test_dogfood_dedup_check.py": "W594 backlog",
    "test_dogfood_dedup_check_e2e.py": "W594 backlog",
    "test_evidence_profiles.py": "W594 backlog",
    "test_language_corpus.py": "W594 backlog",
    "test_law4_anchor_counts.py": "W594 backlog",
    "test_loop_e2e.py": "W594 backlog",
    "test_mcp_param_names.py": "W594 backlog",
    "test_optional_imports_guarded.py": "W594 backlog",
    "test_oss_bench_harness.py": "W594 backlog",
    "test_performance.py": "W594 backlog",
    "test_plugin_dogfood_rails.py": "W594 backlog",
    "test_pr_comment_script.py": "W594 backlog",
    "test_python_extractor_docstring_safety.py": "W594 backlog",
    "test_rules_community_pack.py": "W594 backlog",
    "test_sarif_consumer_list.py": "W594 backlog",
    "test_staged_rollout_readiness.py": "W594 backlog",
    "test_user_version_discipline.py": "W594 backlog",
}


# ---------------------------------------------------------------------------
# AST detection
# ---------------------------------------------------------------------------


def _has_parents_subscript(node: ast.AST) -> bool:
    """``<expr>.parents[N]`` — an ``ast.Subscript`` whose value is an
    ``ast.Attribute`` with ``attr == 'parents'``."""
    if not isinstance(node, ast.Subscript):
        return False
    val = node.value
    if not isinstance(val, ast.Attribute):
        return False
    return val.attr == "parents"


def _has_parent_chain(node: ast.AST) -> bool:
    """``<expr>.parent.parent`` — depth >= 2 attribute chain on ``parent``.

    Catches both ``Path(__file__).parent.parent`` and
    ``Path(__file__).resolve().parent.parent``; the third-or-deeper
    ``.parent`` would still be the inner pair so the AST shape is the
    same.
    """
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != "parent":
        return False
    inner = node.value
    if not isinstance(inner, ast.Attribute):
        return False
    return inner.attr == "parent"


def _references_dunder_file(expr: ast.AST) -> bool:
    """True iff any leaf inside ``expr`` is ``Name('__file__')``.

    Walks Attribute / Subscript / Call sub-expressions. Bounded by
    object count so a pathological AST cannot hang the walker.
    """
    stack: list[ast.AST] = [expr]
    seen = 0
    while stack and seen < 200:
        seen += 1
        cur = stack.pop()
        if isinstance(cur, ast.Name) and cur.id == "__file__":
            return True
        if isinstance(cur, ast.Attribute):
            stack.append(cur.value)
        elif isinstance(cur, ast.Subscript):
            stack.append(cur.value)
            # slice is the [N] index — Constant in practice, no harm walking
            if isinstance(cur.slice, ast.AST):
                stack.append(cur.slice)
        elif isinstance(cur, ast.Call):
            if cur.func is not None:
                stack.append(cur.func)
            for a in cur.args:
                stack.append(a)
    return False


def _find_fragile_sites(path: Path) -> list[str]:
    """Return ``"<rel>:<lineno>"`` for every fragile expression in *path*."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(TESTS_ROOT).as_posix()
    hits: list[str] = []
    for node in ast.walk(tree):
        if _has_parents_subscript(node) and _references_dunder_file(node):
            hits.append(f"{rel}:{node.lineno}")
            continue
        if _has_parent_chain(node) and _references_dunder_file(node):
            hits.append(f"{rel}:{node.lineno}")
    return hits


def _iter_test_files() -> list[Path]:
    return [
        p
        for p in TESTS_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_fragile_path_pattern_in_tests() -> None:
    """No NEW ``Path(__file__).resolve().parents[N]`` / ``.parent.parent``
    site may land in ``tests/``.

    Migrate to::

        from tests._helpers.repo_root import repo_root

    See module docstring for the worktree-nesting rationale.
    """
    allowed = set(_ALLOWLIST) | set(_PRE_W594_PENDING)
    violations: list[str] = []
    for path in _iter_test_files():
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel in allowed:
            continue
        violations.extend(_find_fragile_sites(path))
    assert not violations, (
        "W588: fragile Path(__file__) walk detected in tests/ — migrate "
        "to `from tests._helpers.repo_root import repo_root` (W572). "
        "Offenders:\n  " + "\n  ".join(violations)
    )


def test_pre_w594_pending_entries_actually_exist() -> None:
    """Every ``_PRE_W594_PENDING`` entry must point at a real file.

    Stale entries (the file was deleted or renamed without updating
    this list) silently widen the allowlist and let real regressions
    through.
    """
    missing = [
        rel for rel in _PRE_W594_PENDING if not (TESTS_ROOT / rel).exists()
    ]
    assert not missing, (
        f"W588: _PRE_W594_PENDING references missing files: {missing}"
    )


def test_pre_w594_pending_entries_still_have_pattern() -> None:
    """Every ``_PRE_W594_PENDING`` entry must still contain the fragile
    pattern.

    Once a file is migrated, its entry must drop from
    ``_PRE_W594_PENDING`` — otherwise the allowlist keeps shielding a
    file that no longer needs shielding, and a future fragile-pattern
    regression in that same file would slip through silently.
    """
    stale: list[str] = []
    for rel in _PRE_W594_PENDING:
        path = TESTS_ROOT / rel
        if not path.exists():
            continue  # caught by the previous test
        if not _find_fragile_sites(path):
            stale.append(rel)
    assert not stale, (
        "W588: _PRE_W594_PENDING entries no longer contain the fragile "
        "pattern (W594 migrated them) — drop these from the dict:\n  "
        + "\n  ".join(stale)
    )


def test_allowlist_entries_actually_exist() -> None:
    """Every ``_ALLOWLIST`` entry must point at a real file."""
    missing = [rel for rel in _ALLOWLIST if not (TESTS_ROOT / rel).exists()]
    assert not missing, (
        f"W588: _ALLOWLIST references missing files: {missing}"
    )


def test_detector_catches_synthetic_offender(tmp_path: Path) -> None:
    """The AST detector flags a synthetic offender that mirrors the
    real-world fragile patterns.

    Pins the detector contract so a future refactor of the AST walker
    cannot silently regress the catch.
    """
    src = (
        "from pathlib import Path\n"
        "FOO = Path(__file__).resolve().parents[1]\n"
        "BAR = Path(__file__).parent.parent / 'src'\n"
        "BAZ = Path(__file__).resolve().parent.parent\n"
    )
    offender = tmp_path / "synthetic_offender.py"
    offender.write_text(src, encoding="utf-8")
    text = offender.read_text(encoding="utf-8")
    tree = ast.parse(text)
    flagged_lines: list[int] = []
    for node in ast.walk(tree):
        if _has_parents_subscript(node) and _references_dunder_file(node):
            flagged_lines.append(node.lineno)
            continue
        if _has_parent_chain(node) and _references_dunder_file(node):
            flagged_lines.append(node.lineno)
    # Three offender lines (parents[1], parent.parent, resolve().parent.parent).
    assert sorted(flagged_lines) == [2, 3, 4], (
        f"W588 detector should flag all three synthetic offender lines, "
        f"got {flagged_lines}"
    )


def test_detector_ignores_unrelated_parents_usage(tmp_path: Path) -> None:
    """The detector must NOT flag ``.parents[N]`` chains that have no
    ``__file__`` leaf in their value chain (e.g. resolving from an
    explicitly-passed Path argument).
    """
    src = (
        "from pathlib import Path\n"
        "def f(start):\n"
        "    return start.resolve().parents[2]\n"
        "X = Path('/tmp/x').parents[0]\n"
    )
    offender = tmp_path / "synthetic_clean.py"
    offender.write_text(src, encoding="utf-8")
    text = offender.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if _has_parents_subscript(node):
            assert not _references_dunder_file(node), (
                "W588 detector should NOT flag .parents[N] chains "
                "without an __file__ leaf"
            )


def test_detector_ignores_docstring_mentions(tmp_path: Path) -> None:
    """Docstring / string-literal mentions of the pattern must NOT be
    flagged (the AST walks expression nodes, not string contents).
    """
    src = (
        '"""Module docstring mentioning Path(__file__).resolve().parents[1]."""\n'
        "from pathlib import Path\n"
        "X = 'this string mentions Path(__file__).parent.parent too'\n"
    )
    offender = tmp_path / "synthetic_doc.py"
    offender.write_text(src, encoding="utf-8")
    text = offender.read_text(encoding="utf-8")
    tree = ast.parse(text)
    hits: list[int] = []
    for node in ast.walk(tree):
        if _has_parents_subscript(node) and _references_dunder_file(node):
            hits.append(node.lineno)
        if _has_parent_chain(node) and _references_dunder_file(node):
            hits.append(node.lineno)
    assert hits == [], (
        f"W588 detector should ignore docstring / string-literal "
        f"mentions; got hits {hits}"
    )
