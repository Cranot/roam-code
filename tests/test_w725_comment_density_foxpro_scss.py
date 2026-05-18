"""W725: comment-density coverage for FoxPro (``&&`` / ``*``) and SCSS.

Wave W605 shipped the TODO/FIXME/XXX/HACK comment-density smell.
Wave W650 added block-comment support for C/Java/JS.
Wave W705 unified per-language comment syntax behind ``_CommentSyntax``.
Wave W720 added hcl + apex.

W725 closes the two remaining canonical-language gaps:

* **FoxPro** — line-start ``*`` and ``&&`` markers; no block syntax.
  Reference: ``src/roam/languages/foxpro_lang.py:_preprocess`` (line 139
  uses ``stripped.startswith("*")`` for full-line comments; line 144 +
  ``_strip_inline_comment`` handle ``&&``).  The line-prefix detector
  model only catches left-justified ``&& TODO: ...`` form, which is the
  case fixtures and most hand-authored markers use.

* **SCSS** — ``//`` line + ``/* ... */`` block (same shape as JS/TS).
  Coverage existed since W705 (`smells.py` line ~2720); these tests
  pin the live behaviour against future regressions and confirm the
  language-id ``"scss"`` matches what ``parser.LANGUAGE_BY_EXT`` stores
  for ``.scss`` files (see ``src/roam/index/parser.py:73``).

The drift-guard at ``tests/test_w703_comment_syntax_coverage.py`` keeps
the disjointness invariant: ``foxpro`` must now appear in
``_COMMENT_SYNTAX_BY_LANG`` AND must NOT appear in
``_COMMENT_DENSITY_NO_SUPPORT``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from roam.catalog.smells import (
    _COMMENT_DENSITY_NO_SUPPORT,
    _COMMENT_SYNTAX_BY_LANG,
    detect_comment_density,
)
from tests._helpers.repo_root import repo_root

# Touch the repo-root helper so the test file honours the project-wide
# convention even when no path is dereferenced directly: future fixture
# expansions (e.g. anchoring to ``templates/`` examples) reuse it.
_REPO_ROOT = repo_root()
assert _REPO_ROOT.is_dir()


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        """
    )
    conn.commit()
    return conn


def _wire_file(
    tmp_path: Path,
    conn: sqlite3.Connection,
    rel_path: str,
    source: str,
    language: str,
) -> None:
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(source, encoding="utf-8")
    conn.execute("DELETE FROM symbols")
    conn.execute("DELETE FROM files")
    conn.execute(
        "INSERT INTO files (id, path, language) VALUES (1, ?, ?)",
        (rel_path, language),
    )
    conn.commit()
    (tmp_path / ".git").mkdir(exist_ok=True)


def _run(tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        return detect_comment_density(conn)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# FoxPro
# ---------------------------------------------------------------------------


class TestCommentDensityFoxPro:
    """W725: VFP files participate in the comment-density scan."""

    def test_foxpro_present_in_syntax_map(self) -> None:
        """``foxpro`` must be modelled in ``_COMMENT_SYNTAX_BY_LANG``."""
        assert "foxpro" in _COMMENT_SYNTAX_BY_LANG, (
            "W725: foxpro is missing from _COMMENT_SYNTAX_BY_LANG — the "
            "comment-density detector will silently skip VFP files."
        )

    def test_foxpro_removed_from_skip_set(self) -> None:
        """W703 disjointness: ``foxpro`` is covered, not skipped."""
        assert "foxpro" not in _COMMENT_DENSITY_NO_SUPPORT, (
            "W725: foxpro is BOTH covered and skipped — drift-guard tests/test_w703_*.py will fail on disjointness."
        )

    def test_foxpro_syntax_markers_match_extractor(self) -> None:
        """Syntax tuple must match the FoxPro extractor's comment forms.

        ``foxpro_lang.py`` line 139 treats ``*`` at line-start as a
        comment; ``_strip_inline_comment`` recognises ``&&``. The
        detector inherits both as line prefixes.
        """
        syntax = _COMMENT_SYNTAX_BY_LANG["foxpro"]
        assert "*" in syntax.line, "VFP line-start ``*`` marker missing"
        assert "&&" in syntax.line, "VFP ``&&`` marker missing"
        assert syntax.block == (), "VFP has no canonical block comment"

    def test_foxpro_star_prefix_counts_markers(self, tmp_path: Path) -> None:
        """3 line-start ``*`` markers in a 20-line file (15% rate) -> finding."""
        conn = _make_db(tmp_path)
        body_lines = ["x = 1"] * 17
        src = (
            "\n".join(
                [
                    "* TODO: refactor this proc",
                    "* FIXME: cursor leak in error path",
                    "* HACK: legacy workaround for VFP 6",
                    *body_lines,
                ]
            )
            + "\n"
        )
        _wire_file(tmp_path, conn, "src/m.prg", src, language="foxpro")
        results = _run(tmp_path, conn)
        assert len(results) == 1, results
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["kind"] == "file"
        assert f["symbol_name"] == "src/m.prg"
        assert f["metric_value"] == 3
        conn.close()

    def test_foxpro_double_amp_prefix_counts_markers(self, tmp_path: Path) -> None:
        """3 left-justified ``&&`` markers in a 20-line file -> finding.

        The line-prefix model only catches left-justified ``&& TODO``
        markers (the case fixtures and many hand-authored debug
        comments use); mid-line inline comments are out of scope.
        """
        conn = _make_db(tmp_path)
        body_lines = ["x = 1"] * 17
        src = (
            "\n".join(
                [
                    "&& TODO: rewrite cursor block",
                    "&& FIXME: race condition on save",
                    "&& XXX: revisit after VFP 9 upgrade",
                    *body_lines,
                ]
            )
            + "\n"
        )
        _wire_file(tmp_path, conn, "src/legacy.prg", src, language="foxpro")
        results = _run(tmp_path, conn)
        assert len(results) == 1, results
        assert results[0]["metric_value"] == 3
        conn.close()


# ---------------------------------------------------------------------------
# SCSS
# ---------------------------------------------------------------------------


class TestCommentDensityScss:
    """W725: SCSS files participate in the comment-density scan.

    Coverage existed since W705; these tests pin the contract so the
    entry cannot be silently removed and confirm the language-id
    ``"scss"`` matches what ``parser.LANGUAGE_BY_EXT`` stores.
    """

    def test_scss_present_in_syntax_map(self) -> None:
        """Pin the SCSS entry against future-removal regressions."""
        assert "scss" in _COMMENT_SYNTAX_BY_LANG
        syntax = _COMMENT_SYNTAX_BY_LANG["scss"]
        assert "//" in syntax.line
        assert ("/*", "*/") in syntax.block

    def test_scss_line_comments_count(self, tmp_path: Path) -> None:
        """3 ``//`` markers in a 20-line SCSS file -> finding."""
        conn = _make_db(tmp_path)
        body_lines = [".x { color: red; }"] * 17
        src = (
            "\n".join(
                [
                    "// TODO: rewrite variable cascade",
                    "// FIXME: dark-mode override leaks",
                    "// HACK: ie11 grid fallback",
                    *body_lines,
                ]
            )
            + "\n"
        )
        _wire_file(tmp_path, conn, "src/m.scss", src, language="scss")
        results = _run(tmp_path, conn)
        assert len(results) == 1, results
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["symbol_name"] == "src/m.scss"
        assert f["metric_value"] == 3
        conn.close()

    def test_scss_block_comments_count(self, tmp_path: Path) -> None:
        """A ``/* TODO ... FIXME ... */`` block contributes 2 markers."""
        conn = _make_db(tmp_path)
        body_lines = [".x { color: red; }"] * 17
        src = (
            "\n".join(
                [
                    "/* TODO: rewrite",
                    "   FIXME: race",
                    "   HACK: hack */",
                    *body_lines,
                ]
            )
            + "\n"
        )
        _wire_file(tmp_path, conn, "src/m.scss", src, language="scss")
        results = _run(tmp_path, conn)
        assert len(results) == 1, results
        assert results[0]["metric_value"] == 3
        conn.close()


# ---------------------------------------------------------------------------
# Drift-guard parity
# ---------------------------------------------------------------------------


def test_w703_drift_guards_still_pass_with_foxpro_added() -> None:
    """Re-assert the W703 invariants with foxpro now in the covered set.

    Mirrors the four guard tests in ``tests/test_w703_*.py`` so a
    regression here surfaces alongside W725 rather than only via the
    sibling test module.
    """
    from roam.languages.registry import _SUPPORTED_LANGUAGES

    covered = set(_COMMENT_SYNTAX_BY_LANG.keys())
    skipped = set(_COMMENT_DENSITY_NO_SUPPORT)
    canonical = set(_SUPPORTED_LANGUAGES)

    # foxpro must be in covered, not in skipped, AND in the canonical
    # registry — the three-way pin that previously held only for
    # apex / hcl.
    assert "foxpro" in covered
    assert "foxpro" not in skipped
    assert "foxpro" in canonical

    # Disjointness still holds globally.
    assert not (covered & skipped)

    # No canonical language is silently absent.
    gap = canonical - covered - skipped
    assert not gap, f"W725 widened coverage but a gap remains: {sorted(gap)}"


if __name__ == "__main__":  # pragma: no cover - convenience runner
    pytest.main([__file__, "-x", "-v"])
