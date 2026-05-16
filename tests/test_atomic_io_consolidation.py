"""W17.1 consolidation pin — no duplicate atomic-write helpers in src/roam/.

After the W17.1 cleanup, ``src/roam/atomic_io.py`` is the only module
permitted to *define* a function whose name matches ``atomic_write_*``.
Every other site that needs the temp-file + ``os.replace`` idiom must
import from :mod:`roam.atomic_io` (it may keep a thin private adapter
wrapper, but the wrapper's body must delegate — not re-implement).

This test walks ``src/roam/`` with the ``ast`` module and asserts that
no other file declares such a function with more than a trivial body
(``trivial`` = up to a handful of statements, since the surviving
``_atomic_write_bundle`` wrapper and ``cmd_stale_refs._atomic_write_text``
adapter ARE expected — they just call into ``atomic_io``).

Why pin this
============

The 212-eval dogfood corpus surfaced that small idioms (tempfile +
rename, JSON-parse-on-empty, vocabulary mismatch) tend to re-spawn
inline once the original author moves on. A grep-style structural
guard catches the regression at PR time, not in production.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Project root resolution: tests/<this>.py → ../src/roam/
SRC_ROAM = Path(__file__).resolve().parent.parent / "src" / "roam"
CANONICAL_HOME = SRC_ROAM / "atomic_io.py"

# Max statement count for a tolerated wrapper. The shared helper is ~10
# statements; a delegating wrapper is typically 2-5. Anything taller is
# almost certainly a re-implementation.
WRAPPER_MAX_STATEMENTS = 8


def _delegates_to_atomic_io(func: ast.FunctionDef) -> bool:
    """Return True if *func* contains a Call to a roam.atomic_io symbol.

    We match either ``atomic_write_*(...)`` or ``_shared_write(...)`` etc.
    by scanning every ``ast.Call`` inside the function body for a name
    that begins with ``atomic_write_``. The wrapper renames don't matter
    — only that the body actually invokes the shared helper.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name.startswith("atomic_write_"):
                return True
        # An ``import ... as _shared_write`` followed by a call to
        # ``_shared_write`` also counts as delegation; we detect by the
        # presence of an ImportFrom from roam.atomic_io anywhere inside
        # the function.
        if isinstance(node, ast.ImportFrom) and node.module == "roam.atomic_io":
            return True
    return False


def test_no_duplicate_atomic_write_helpers_remain():
    """After W17.1, only ``atomic_io.py`` may host an atomic-write definition.

    All other files may keep a *thin adapter* (name starts with
    ``atomic_write_`` or ``_atomic_write_``) — but the body MUST
    delegate to :mod:`roam.atomic_io`. A re-implementation (no
    delegation, or a long body) is forbidden and this test will fail
    with the offending file + function name.
    """
    offenders: list[str] = []

    for py_path in SRC_ROAM.rglob("*.py"):
        if py_path == CANONICAL_HOME:
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            # Don't let an unrelated parse error mask a legitimate
            # offender elsewhere; just skip the broken file.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            name = node.name
            if not (name.startswith("atomic_write_") or name.startswith("_atomic_write_")):
                continue
            # Allow short delegating wrappers. Flag if either (a) the
            # body is too long (looks like a re-implementation) or (b)
            # it doesn't reference the canonical module at all.
            body_len = len(node.body)
            delegates = _delegates_to_atomic_io(node)
            if body_len > WRAPPER_MAX_STATEMENTS or not delegates:
                offenders.append(
                    f"{py_path.relative_to(SRC_ROAM.parent.parent)}::{name} "
                    f"(body_statements={body_len}, delegates={delegates})"
                )

    assert not offenders, (
        "W17.1 regression — inline atomic-write helpers found in src/roam/.\n"
        "Each of the following functions must either delegate to "
        "roam.atomic_io or be deleted:\n  - " + "\n  - ".join(offenders)
    )


def test_canonical_atomic_io_module_exists():
    """Sanity — the consolidation target file must exist with the public API."""
    assert CANONICAL_HOME.is_file(), f"missing canonical module: {CANONICAL_HOME}"
    src = CANONICAL_HOME.read_text(encoding="utf-8")
    for fn in ("atomic_write_text", "atomic_write_bytes", "atomic_write_json"):
        assert f"def {fn}(" in src, f"roam.atomic_io is missing {fn}"
