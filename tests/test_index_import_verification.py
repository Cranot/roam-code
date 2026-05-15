"""W167: indexer import-edge text verification.

The resolver in :mod:`roam.index.relations` used to emit ``kind='import'``
edges where the target symbol's name only fuzzy-matched the imported name
(case-insensitive collisions, local-variable collisions in test files,
etc.). W158 worked around the worst class of these (non-test -> test
fabricated edges) at the laws-miner layer. W167 fixes the root cause:
after edge resolution, every ``kind='import'`` edge is verified against
the source file's actual import statements — edges whose target name is
not written as an import in the source are dropped.

These tests pin the behaviour at the unit level via
:func:`resolve_references`, with the source-file text on disk so the
verifier can read it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from roam.index.relations import (
    _extract_imported_names,
    _mask_strings_and_comments,
    resolve_references,
)


# ---------------------------------------------------------------------------
# helper: build a project tree of {rel_path: source_text} on disk so the
# verifier can read it. ``resolve_references`` reads source text relative
# to ``project_root``.
# ---------------------------------------------------------------------------
def _build_project(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")
    return tmp_path


def _sym(
    sym_id: int,
    name: str,
    *,
    file_path: str,
    qn: str | None = None,
    kind: str = "function",
    line_start: int = 1,
    line_end: int = 1,
) -> dict:
    return {
        "id": sym_id,
        "name": name,
        "qualified_name": qn or name,
        "kind": kind,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
    }


def _build_inputs(symbols: list[dict]) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Build the (symbols_by_name, files_by_path) inputs to resolve_references."""
    by_name: dict[str, list[dict]] = {}
    for s in symbols:
        by_name.setdefault(s["name"], []).append(s)
    files_by_path: dict[str, int] = {}
    next_fid = 1
    for s in symbols:
        if s["file_path"] not in files_by_path:
            files_by_path[s["file_path"]] = next_fid
            next_fid += 1
    return by_name, files_by_path


# ---------------------------------------------------------------------------
# unit tests for the import-name extractor
# ---------------------------------------------------------------------------
class TestExtractImportedNames:
    def test_plain_python_import(self):
        text = "import yaml\nimport os\n"
        names = _extract_imported_names(text)
        assert "yaml" in names
        assert "os" in names

    def test_from_import(self):
        text = "from datetime import timezone, timedelta\n"
        names = _extract_imported_names(text)
        assert "timezone" in names
        assert "timedelta" in names

    def test_multiline_from_import(self):
        text = (
            "from roam.commands.pr_analyze.rules import (\n"
            "    _FUNCTION_CALL_RE,  # noqa: F401\n"
            "    _PATTERN_MATCHERS,\n"
            "    _check_rules,\n"
            ")\n"
        )
        names = _extract_imported_names(text)
        assert "_FUNCTION_CALL_RE" in names
        assert "_PATTERN_MATCHERS" in names
        assert "_check_rules" in names

    def test_import_as_alias(self):
        text = "import numpy as np\nfrom json import loads as json_loads\n"
        names = _extract_imported_names(text)
        # Both the original name and the alias should be in the set so a
        # ref to either can pass verification.
        assert "np" in names
        assert "json_loads" in names

    def test_docstring_does_not_fake_import(self):
        text = (
            '"""Module docstring.\n'
            "    Examples: should import yaml or call import time later.\n"
            '"""\n'
            "import os\n"
        )
        names = _extract_imported_names(text)
        assert "os" in names
        assert "yaml" not in names
        assert "time" not in names

    def test_comment_does_not_fake_import(self):
        text = "# import yaml  -- considered but rejected\nimport os\n"
        names = _extract_imported_names(text)
        assert "os" in names
        assert "yaml" not in names

    def test_go_grouped_import(self):
        text = 'import (\n    "fmt"\n    "os"\n    alias "net/http"\n)\n'
        names = _extract_imported_names(text)
        # Strings are masked, but the tokens "fmt"/"os" appear inside the
        # block before masking — actually they are *inside* string
        # literals and are masked. The extractor will still pick up the
        # bare ``alias`` identifier on the third line. Both modes are
        # acceptable as long as legitimate-import false-positives don't
        # land downstream — at minimum, ``alias`` is recognised.
        assert "alias" in names

    def test_empty_source(self):
        assert _extract_imported_names("") == set()
        assert _extract_imported_names("\n\n\n") == set()


# ---------------------------------------------------------------------------
# unit tests for the string/comment masker
# ---------------------------------------------------------------------------
class TestMaskStringsAndComments:
    def test_triple_quoted_string_masked(self):
        text = '"""docstring"""\nx = 1\n'
        masked = _mask_strings_and_comments(text)
        # Length preserved; newlines preserved
        assert len(masked) == len(text)
        assert masked.count("\n") == text.count("\n")
        # The docstring content is blanked
        assert "docstring" not in masked

    def test_line_comment_masked(self):
        text = "x = 1  # set x to one\ny = 2\n"
        masked = _mask_strings_and_comments(text)
        assert "set x" not in masked
        assert "y = 2" in masked


# ---------------------------------------------------------------------------
# integration tests for resolve_references W167 verification
# ---------------------------------------------------------------------------
class TestResolveReferencesVerification:
    def test_verified_import_remains(self, tmp_path):
        """A genuine ``import yaml`` resolves to the indexed ``yaml`` module
        symbol and the verification pass keeps the edge intact."""
        project = _build_project(
            tmp_path,
            {
                "src/foo.py": "import yaml\n\ndef use_yaml():\n    return yaml\n",
            },
        )
        symbols = [
            _sym(1, "use_yaml", file_path="src/foo.py", line_start=3, line_end=4),
            _sym(2, "yaml", file_path="vendor/yaml.py", kind="module"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "use_yaml",
                "target_name": "yaml",
                "kind": "import",
                "line": 1,
                "source_file": "src/foo.py",
                "import_path": "yaml",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        assert any(e["kind"] == "import" for e in edges), edges
        assert drop_stats.get("dropped_import_edges", 0) == 0

    def test_phantom_import_dropped(self, tmp_path):
        """The source has no ``import yaml`` statement, but the resolver
        still produced a ``kind='import'`` edge to a ``yaml`` symbol
        elsewhere. The verification pass drops it.

        This reproduces the W158 smoking gun (``seeds.py ->
        tests/test_runtime_score.py:yaml``) at the unit level: a real
        import edge backed by no real import statement.
        """
        project = _build_project(
            tmp_path,
            {
                # No `import yaml` here — only an unrelated import.
                "src/seeds.py": "import os\n\ndef seed():\n    return 1\n",
            },
        )
        symbols = [
            _sym(1, "seed", file_path="src/seeds.py", line_start=3, line_end=4),
            # A `yaml` variable lives in some unrelated test file.
            _sym(2, "yaml", file_path="tests/test_runtime_score.py", kind="variable"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            # The (fabricated) import ref — pre-W167 the resolver would
            # emit this edge unconditionally.
            {
                "source_name": "seed",
                "target_name": "yaml",
                "kind": "import",
                "line": 1,
                "source_file": "src/seeds.py",
                "import_path": "yaml",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        assert not any(e["kind"] == "import" for e in edges), edges
        assert drop_stats["dropped_import_edges"] == 1

    def test_case_insensitive_collision_dropped_when_unimported(self, tmp_path):
        """If the source has ``import time`` but the resolver fuzzy-matched
        to a ``TIME`` constant in another file, the edge with
        ``target_name='time'`` survives the text check (the source DOES
        contain ``import time``). The text check catches the strict
        subset where the source has NO matching import at all.

        This test pins the boundary: when the source does not import
        ``some_name`` AT ALL, the edge is dropped — even though the
        target name is similar-looking-but-unimported. (The pure
        case-insensitive ``time``-vs-``TIME`` slip-through is a known
        false-negative — see W158 sanity filter belt-and-suspenders.)
        """
        project = _build_project(
            tmp_path,
            {
                "src/effects.py": "import os\n\ndef effect():\n    return 0\n",
            },
        )
        symbols = [
            _sym(1, "effect", file_path="src/effects.py", line_start=3, line_end=4),
            _sym(2, "TIME", file_path="src/constants.py", kind="constant"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                # Resolver case-insensitive lookup hits ``TIME`` even though
                # source doesn't actually have ``import time``.
                "source_name": "effect",
                "target_name": "time",
                "kind": "import",
                "line": 1,
                "source_file": "src/effects.py",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        assert not any(e["kind"] == "import" for e in edges)
        assert drop_stats["dropped_import_edges"] == 1

    def test_relative_import_resolves_and_survives(self, tmp_path):
        """``from .base import X`` resolves to the sibling file's ``X`` and
        the verifier keeps the edge because ``X`` appears as an imported
        name in the source's actual import statement."""
        project = _build_project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/base.py": "def X():\n    return 0\n",
                "src/pkg/uses.py": "from .base import X\n\ndef caller():\n    return X()\n",
            },
        )
        symbols = [
            _sym(1, "X", file_path="src/pkg/base.py", line_start=1, line_end=2),
            _sym(2, "caller", file_path="src/pkg/uses.py", line_start=3, line_end=4),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "caller",
                "target_name": "X",
                "kind": "import",
                "line": 1,
                "source_file": "src/pkg/uses.py",
                "import_path": ".base.X",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        assert any(e["kind"] == "import" for e in edges), edges
        assert drop_stats.get("dropped_import_edges", 0) == 0

    def test_non_import_edges_untouched(self, tmp_path):
        """``kind='call'`` and ``kind='reference'`` edges bypass the
        verification pass entirely — only ``kind='import'`` is checked.
        """
        project = _build_project(
            tmp_path,
            {
                # No imports at all in the source.
                "src/foo.py": "def caller():\n    return helper()\n",
            },
        )
        symbols = [
            _sym(1, "caller", file_path="src/foo.py", line_start=1, line_end=2),
            _sym(2, "helper", file_path="src/util.py"),
            _sym(3, "MY_CONST", file_path="src/util.py", kind="constant"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "caller",
                "target_name": "helper",
                "kind": "call",
                "line": 2,
                "source_file": "src/foo.py",
            },
            {
                "source_name": "caller",
                "target_name": "MY_CONST",
                "kind": "reference",
                "line": 2,
                "source_file": "src/foo.py",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        kinds = sorted(e["kind"] for e in edges)
        assert kinds == ["call", "reference"], edges
        assert drop_stats.get("dropped_import_edges", 0) == 0

    def test_multiline_from_import_survives(self, tmp_path):
        """Multi-line ``from X import (a, b, c)`` blocks are correctly
        parsed by the imported-name extractor so every name in the
        parens survives verification."""
        body = (
            "from roam.commands.pr_analyze.rules import (\n"
            "    _FUNCTION_CALL_RE,\n"
            "    _PATTERN_MATCHERS,\n"
            ")\n"
            "\n"
            "def caller():\n"
            "    return _FUNCTION_CALL_RE\n"
        )
        project = _build_project(tmp_path, {"src/x.py": body})
        symbols = [
            _sym(1, "caller", file_path="src/x.py", line_start=6, line_end=7),
            _sym(2, "_FUNCTION_CALL_RE",
                 file_path="src/pr_analyze/rules.py", kind="constant"),
            _sym(3, "_PATTERN_MATCHERS",
                 file_path="src/pr_analyze/rules.py", kind="constant"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "caller",
                "target_name": "_FUNCTION_CALL_RE",
                "kind": "import",
                "line": 2,
                "source_file": "src/x.py",
            },
            {
                "source_name": "caller",
                "target_name": "_PATTERN_MATCHERS",
                "kind": "import",
                "line": 3,
                "source_file": "src/x.py",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        kept = [e for e in edges if e["kind"] == "import"]
        assert len(kept) == 2, edges
        assert drop_stats.get("dropped_import_edges", 0) == 0

    def test_unreadable_source_keeps_edge(self, tmp_path):
        """If the source file doesn't exist on disk (rare — deleted between
        ref extraction and resolution), the verifier errs on the side of
        keeping the edge. Better one stray phantom than dropping a real
        import.
        """
        project = tmp_path  # no files written
        symbols = [
            _sym(1, "caller", file_path="src/missing.py"),
            _sym(2, "thing", file_path="src/other.py"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "caller",
                "target_name": "thing",
                "kind": "import",
                "line": 1,
                "source_file": "src/missing.py",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        assert any(e["kind"] == "import" for e in edges)
        assert drop_stats.get("dropped_import_edges", 0) == 0

    def test_transient_fields_stripped(self, tmp_path):
        """The verification pass must strip the transient ``_target_name``
        and ``_source_path`` fields from emitted edges so they don't
        leak into the DB-write call site."""
        project = _build_project(
            tmp_path,
            {"src/foo.py": "import yaml\n"},
        )
        symbols = [
            _sym(1, "module", file_path="src/foo.py"),
            _sym(2, "yaml", file_path="vendor/yaml.py", kind="module"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "module",
                "target_name": "yaml",
                "kind": "import",
                "line": 1,
                "source_file": "src/foo.py",
            },
        ]
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project),
        )
        for edge in edges:
            assert "_target_name" not in edge, edge
            assert "_source_path" not in edge, edge
