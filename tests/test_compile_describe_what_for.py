"""Telemetry-driven (2026-06-04): "what is X.py for" / "what's X for" is a
describe-the-file intent that the anchored ``_DESCRIBE_FILE_RE`` missed (it
required "what is in / the purpose of / the role of"). It fell to empty-prefetch
``freeform_explore`` in production. Now routes to ``describe_file`` — but ONLY
when a concrete file PATH is present (the probe needs the file), so abstract
"what is this for" stays freeform, and the structural procedures (blast / callers
/ compare) still win on their shapes even when a path is mentioned.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import _classify

# Bare-filename describe ("what is cmd_verify.py for") was a deferred gap: the
# cwd-less classifier can't DB-resolve a basename, so `_extract_describe_file`
# only saw slash-paths. Closed 2026-06-05 — it now also accepts a filename-SHAPED
# token (`_BARE_FILE_RE`); the probe resolves it to a real repo path via
# `_resolve_bare_filenames` at dispatch time. Same wave added API-surface
# phrasings ("what's exported from X", "public API of X") to the describe verb —
# the file_skeleton (top-level def/class list) IS the export answer.
_POSITIVE = [
    "what's src/roam/plan/compiler.py for",
    "what is tests/test_index.py for again",
    "what is src/roam/commands/cmd_verify.py for",
    # bare filenames (no slash) — the closed gap
    "what is cmd_verify.py for",
    "what's parser.py for",
    # API-surface phrasings → describe_file (skeleton = export list)
    "what's exported from cmd_verify.py",
    "what's exported from src/roam/plan/compiler.py",
    "audit the public API of src/roam/cli.py",
]

_NEGATIVE = [
    # structural shapes that mention a path but are NOT describe-the-file
    "what is the blast radius for cmd_verify.py",
    "compare cmd_verify.py vs cmd_compile.py",
    "who calls greet for app.py",
    # no path at all → must stay out of describe_file (needs a file to probe)
    "what is this for",
    "what is the auth flow for",
]


@pytest.mark.parametrize("task", _POSITIVE)
def test_what_is_x_for_routes_to_describe_file(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc == "describe_file", f"expected describe_file, got {proc!r} for {task!r}"


@pytest.mark.parametrize("task", _NEGATIVE)
def test_negatives_not_stolen_by_describe_file(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "describe_file", f"unexpected describe_file for {task!r}"
