"""A grammar that cannot parse a construct has not found a syntax error.

`roam verify`'s syntax check reports tree-sitter ERROR nodes as FAIL findings.
That is right when the code is genuinely malformed and wrong when the bundled
grammar simply lags the language -- and TypeScript's `import('...')` type is
exactly that case. Field reports from a production Vue/TS application showed
`roam verify --auto` returning FAIL on a tree that passed `vue-tsc`, a Vite
production build, and ~5,900 unit tests, with a valid import-type array
reported as "syntax error".

Syntax is the check a reader trusts most absolutely, so a false positive here
is expensive: it tells someone their working code is broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from roam.commands.cmd_verify import (
    _mask_import_type_calls,
    _reparse_without_import_types,
    _tree_sitter_error_violations,
)

# Every one of these is valid TypeScript that the bundled grammar cannot parse.
GRAMMAR_GAP_SOURCES = [
    b"export function f(): import('./t').W[] {\n  return []\n}\n",
    b"let a: import('./t').W[] = []\n",
    b"type A = import('./t').W[]\n",
    b"function f(p: import('./t').W[]) {}\n",
    b"let a: import('./t').W<number>\n",
    b"let a: import('./t').W<number>[] = []\n",
    b"let a: import('./t').N.W[] = []\n",
    b"let a: readonly import('./t').W[] = []\n",
]


def _violations(tmp_path: Path, source: bytes):
    path = tmp_path / "x.ts"
    path.write_bytes(source)
    tree = _reparse_without_import_types(path, "typescript")
    if tree is None:
        return None
    return _tree_sitter_error_violations("x.ts", tree)


@pytest.mark.parametrize("source", GRAMMAR_GAP_SOURCES, ids=range(len(GRAMMAR_GAP_SOURCES)))
def test_valid_import_type_syntax_is_not_reported_as_broken(tmp_path: Path, source: bytes):
    assert _violations(tmp_path, source) == []


def test_a_real_syntax_error_still_fails_at_the_right_line(tmp_path: Path):
    # The must-fire pair. The mask exists to remove one false positive, not to
    # make the syntax check permissive, and the line number has to survive it
    # or the finding points somewhere the reader cannot use.
    source = b"export function f(): import('./t').W[] {\n  return [\n}\n"
    violations = _violations(tmp_path, source)
    assert violations, "a genuine syntax error must survive the retry"
    assert violations[0]["line"] == 2
    assert violations[0]["severity"] == "FAIL"


def test_a_file_with_no_import_type_is_left_entirely_alone(tmp_path: Path):
    # No mask applies, so the retry declines and the original verdict stands.
    # This keeps the change from touching files it has no business touching.
    assert _mask_import_type_calls(b"export function f(): number[] {\n  return [\n}\n") is None
    assert _violations(tmp_path, b"export function f(): number[] {\n  return [\n}\n") is None


@pytest.mark.parametrize(
    "source",
    [
        b"const m = await import('./t')\n",
        b"let a: import('./t').W[] = []\n",
        b"// import('./t').W[] in a comment\n",
        b"const s = \"import('./t').W[]\"\n",
    ],
    ids=["dynamic-import", "type-position", "comment", "string"],
)
def test_the_mask_preserves_every_byte_offset(source: bytes):
    # Line and column numbers in a genuine error are only trustworthy on the
    # second parse if the substitution is exactly length-preserving.
    masked = _mask_import_type_calls(source)
    assert masked is not None
    assert len(masked) == len(source)
    assert masked.count(b"\n") == source.count(b"\n")
