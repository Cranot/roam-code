"""W1456: the case-fold resolution fallback is gated on source language.

``_resolve_standard`` resolves a reference in three steps: qualified name,
simple name, then a *case-insensitive* match. Step 3 exists for Visual
FoxPro, whose identifiers genuinely fold case (``MyProc`` and ``myproc``
name the same routine). It used to run for every reference in every file,
regardless of language.

The damage is not marginal mis-resolution. Steps 1-2 fail for any name that
is not defined in the repo at all -- stdlib, third-party, builtins -- and
step 3 then bound those names to whatever indexed symbol happened to
case-fold equal. On roam-code itself, ``Path`` (from ``pathlib``) alone
produced ~3.4k fabricated edges into an unrelated ``PATH`` constant in a
dev script, which in turn made ``roam impact`` report a repo-wide blast
radius for that constant.

Two things are asserted here and they pull in opposite directions:

* the fallback must NOT fire for a case-sensitive source (the fix), and
* it must STILL fire for a case-insensitive source (the negative control).

The control is load-bearing: without it, "delete the fallback entirely"
passes every other test in the suite while silently breaking the only
language the fallback was ever written for.
"""

from __future__ import annotations

from pathlib import Path

from roam.index.relations import resolve_references
from roam.languages.registry import get_extractor, get_supported_languages, is_case_insensitive_language


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


class TestCaseFoldFallbackIsNotTakenForCaseSensitiveSources:
    """The fix: a Python source never reaches step 3."""

    def test_python_reference_to_undefined_name_does_not_bind_to_casefold_twin(self, tmp_path):
        """The exact production shape: ``Path`` (stdlib, not indexed) must
        not resolve to an unrelated ``PATH`` constant in a dev script."""
        project = _build_project(
            tmp_path,
            {
                "src/consumer.py": "from pathlib import Path\n\ndef load():\n    return Path('x')\n",
                "dev/scoreboard.py": "PATH = 'out.json'\n",
            },
        )
        symbols = [
            _sym(1, "load", file_path="src/consumer.py", line_start=3, line_end=4),
            _sym(2, "PATH", file_path="dev/scoreboard.py", kind="constant"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "load",
                "target_name": "Path",
                "kind": "call",
                "line": 4,
                "source_file": "src/consumer.py",
            },
        ]
        edges = resolve_references(refs, symbols_by_name, files_by_path, project_root=str(project))
        assert [e for e in edges if e["target_id"] == 2] == [], (
            "case-fold fallback fired for a Python source: a reference to stdlib "
            f"`Path` was bound to the unrelated `PATH` constant. edges={edges}"
        )
        assert edges == [], f"no edge should be emitted at all for an unresolvable name: {edges}"

    def test_python_type_ref_does_not_bind_to_casefold_twin(self, tmp_path):
        """Same guard on ``type_ref`` -- the second-largest producer of
        fabricated case-fold edges after ``call``."""
        project = _build_project(
            tmp_path,
            {
                "src/models.py": "from pathlib import Path\n\nclass Cfg:\n    root: Path\n",
                "dev/scoreboard.py": "PATH = 'out.json'\n",
            },
        )
        symbols = [
            _sym(1, "Cfg", file_path="src/models.py", kind="class", line_start=3, line_end=4),
            _sym(2, "PATH", file_path="dev/scoreboard.py", kind="constant"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "Cfg",
                "target_name": "Path",
                "kind": "type_ref",
                "line": 4,
                "source_file": "src/models.py",
            },
        ]
        edges = resolve_references(refs, symbols_by_name, files_by_path, project_root=str(project))
        assert edges == [], f"case-fold fallback fired for a Python type_ref: {edges}"

    def test_exact_case_match_still_resolves_for_python(self, tmp_path):
        """Sanity rail: the guard must not disturb steps 1-2. An
        exact-case match in a Python file still resolves."""
        project = _build_project(
            tmp_path,
            {
                "src/consumer.py": "from .helpers import helper\n\ndef load():\n    return helper()\n",
                "src/helpers.py": "def helper():\n    return 1\n",
            },
        )
        symbols = [
            _sym(1, "load", file_path="src/consumer.py", line_start=3, line_end=4),
            _sym(2, "helper", file_path="src/helpers.py"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "load",
                "target_name": "helper",
                "kind": "call",
                "line": 4,
                "source_file": "src/consumer.py",
            },
        ]
        edges = resolve_references(refs, symbols_by_name, files_by_path, project_root=str(project))
        assert [e["target_id"] for e in edges] == [2], edges


class TestCaseFoldFallbackIsStillTakenForCaseInsensitiveSources:
    """NEGATIVE CONTROL -- deleting the fallback must break these.

    There is no other FoxPro resolver coverage in the suite, so these two
    tests are the entire proof that the language the fallback was written
    for still works.
    """

    def test_prg_source_resolves_lowercase_call_to_uppercase_definition(self, tmp_path):
        project = _build_project(
            tmp_path,
            {
                "forms/caller.prg": "PROCEDURE DoWork\n    myproc()\nENDPROC\n",
                "lib/util.prg": "PROCEDURE MyProc\n    RETURN\nENDPROC\n",
            },
        )
        symbols = [
            _sym(1, "DoWork", file_path="forms/caller.prg", line_start=1, line_end=3),
            _sym(2, "MyProc", file_path="lib/util.prg", kind="function", line_start=1, line_end=3),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "DoWork",
                "target_name": "myproc",
                "kind": "call",
                "line": 2,
                "source_file": "forms/caller.prg",
            },
        ]
        edges = resolve_references(refs, symbols_by_name, files_by_path, project_root=str(project))
        assert [e["target_id"] for e in edges] == [2], (
            "the case-fold fallback no longer fires for a FoxPro source -- "
            f"VFP identifier folding is broken. edges={edges}"
        )

    def test_scx_source_resolves_uppercase_call_to_mixedcase_definition(self, tmp_path):
        """The second registered FoxPro extension, and the opposite fold
        direction (reference upper, definition mixed)."""
        project = _build_project(
            tmp_path,
            {
                "forms/order.scx": "",
                "lib/util.prg": "PROCEDURE MyProc\n    RETURN\nENDPROC\n",
            },
        )
        symbols = [
            _sym(1, "order", file_path="forms/order.scx", kind="class", line_start=1, line_end=1),
            _sym(2, "MyProc", file_path="lib/util.prg", line_start=1, line_end=3),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "order",
                "target_name": "MYPROC",
                "kind": "call",
                "line": 1,
                "source_file": "forms/order.scx",
            },
        ]
        edges = resolve_references(refs, symbols_by_name, files_by_path, project_root=str(project))
        assert [e["target_id"] for e in edges] == [2], edges


class TestCaseInsensitivityIsALanguageProperty:
    """The guard reads a registry property, not a hardcoded extension list,
    so adding a case-insensitive language does not require editing the
    relation resolver."""

    def test_foxpro_declares_case_insensitive_identifiers(self):
        assert is_case_insensitive_language("foxpro") is True
        assert get_extractor("foxpro").case_insensitive_identifiers is True

    def test_case_sensitive_languages_are_the_default(self):
        for language in ("python", "typescript", "go", "rust", "java"):
            assert is_case_insensitive_language(language) is False, language

    def test_foxpro_is_the_only_declared_case_insensitive_language(self):
        """Drift guard: if another language starts declaring case-folded
        identifiers, that is a graph-shape change and this test should be
        updated deliberately, not discovered from a blast-radius report."""
        declared = {lang for lang in get_supported_languages() if is_case_insensitive_language(lang)}
        assert declared == {"foxpro"}, declared
