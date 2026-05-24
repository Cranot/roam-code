"""W668 drift-guard: ``importlib.resources.as_file()`` path captured
OUTSIDE the ``with`` block is the W643 anti-pattern.

Why this lint exists -- the W643 incident.

``importlib.resources.as_file(resource)`` returns a CONTEXT MANAGER
that, on a ``MultiplexedPath`` (namespace-subpackage) backing, extracts
the resource into a *temporary* directory. The ``with`` block's
``__exit__`` cleans the temp dir up. Capturing the path target outside
the ``with`` block therefore leaves the caller pointing at a now-deleted
filesystem entry. The visible failure was tests in
``tests/test_taint.py::test_deserialization_pack_loads`` flaking with
"file not found" on the resource path the loader had just returned.

W643 fixed the symptom by adding the missing ``__init__.py`` to
``src/roam/security/taint_rules/``. W664 added the structural lint that
every package referenced by ``[tool.setuptools.package-data]`` must
contain an ``__init__.py``. That keeps the temp-extraction codepath
unreachable under the current pyproject -- but it does NOT prevent a
contributor re-introducing the original `return Path(resource_path)`
pattern in a NEW caller that subscribes to a future package-data entry
that has somehow lost its ``__init__.py``.

This W668 drift-guard pins the CALLER-side discipline: it walks every
``with as_file(...) as <target>:`` construct in ``src/roam/`` and
checks that ``<target>`` is not captured outside the ``with`` block via
``return <target>`` / ``return Path(<target>)`` / outer-scope
assignment. Companion to W664 which pins the *package-shape*
prerequisite; W668 pins the *call-site* discipline.

Companion docs: ``(internal memo)``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

# ── Module discovery ─────────────────────────────────────────────────


def _iter_src_modules() -> list[Path]:
    """Return every ``.py`` file under ``src/roam/``."""
    src_dir = repo_root() / "src" / "roam"
    return sorted(p for p in src_dir.rglob("*.py") if p.is_file())


# ── AST helpers ──────────────────────────────────────────────────────


def _call_is_as_file(node: ast.AST) -> bool:
    """Return True iff ``node`` is an ``as_file(...)`` call.

    Accepts both ``as_file(...)`` (after a direct
    ``from importlib.resources import as_file``) and
    ``importlib.resources.as_file(...)`` (attribute-form).
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "as_file":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "as_file":
        return True
    return False


def _with_item_target_name(item: ast.withitem) -> str | None:
    """Return the bound-target name of a ``with as_file(...) as X:`` item.

    Returns None if the ``with`` item does not bind a simple ``Name`` or
    its ``context_expr`` is not an ``as_file(...)`` call.
    """
    if not _call_is_as_file(item.context_expr):
        return None
    target = item.optional_vars
    if isinstance(target, ast.Name):
        return target.id
    # Tuples / starred targets aren't the W643 anti-pattern shape -- the
    # original bug captured a single Path. Skip exotic shapes.
    return None


def _references_name(node: ast.AST, name: str) -> bool:
    """Walk ``node`` and return True iff any ``Name(id=name)`` is read."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name and isinstance(sub.ctx, ast.Load):
            return True
    return False


def _is_return_escape(sub: ast.AST, target_name: str) -> bool:
    """True if ``sub`` is ``return <target_name>`` (with optional Path wrap)."""
    if not (isinstance(sub, ast.Return) and sub.value is not None):
        return False
    return _value_is_escape_of(sub.value, target_name)


def _is_assign_escape(sub: ast.AST, target_name: str) -> bool:
    """True if ``sub`` is ``<outer> = <target_name>`` (non-self-assign)."""
    if not isinstance(sub, ast.Assign):
        return False
    if not _value_is_escape_of(sub.value, target_name):
        return False
    # Skip self-assignments like ``X = X`` (rare).
    return not _all_targets_are(sub.targets, target_name)


def _stmt_escapes_target(stmt: ast.AST, target_name: str) -> bool:
    """True if any sub-node in ``stmt`` is an escape of ``target_name``."""
    for sub in ast.walk(stmt):
        if _is_return_escape(sub, target_name):
            return True
        if _is_assign_escape(sub, target_name):
            return True
    return False


def _body_escapes_target(body: list[ast.stmt], target_name: str) -> bool:
    """True if any statement in ``body`` escapes ``target_name``."""
    return any(_stmt_escapes_target(stmt, target_name) for stmt in body)


def _find_unsafe_with_blocks(tree: ast.AST) -> list[tuple[str, int]]:
    """Walk ``tree`` and flag W668-unsafe ``with as_file(...) as X:`` blocks.

    A block is UNSAFE if any of these patterns appear INSIDE the
    ``with`` body and refer to ``X``:

    * ``return <X>`` -- the captured path escapes the ``with`` scope
      via the function return value.
    * ``return Path(<X>)`` / ``return Path(str(<X>))`` -- same shape,
      wrapped in a ``Path(...)`` call. The wrapper does not copy the
      filesystem entry; it just wraps the stale reference.
    * ``<outer> = <X>`` or ``<outer> = Path(<X>)`` where ``<outer>`` is
      assigned via ``ast.Assign`` whose value tree contains ``X`` --
      this is the "store on object / outer-scope variable" escape
      route.

    A block is SAFE when ``X`` only appears as an attribute access
    (``X.read_text()``, ``X.exists()``, ``X.is_dir()``, ``X.open()``,
    etc.) inside the ``with`` body -- i.e. the path is *consumed*, not
    *captured*.

    Returns a list of ``(target_name, line_number)`` tuples for every
    unsafe block.
    """
    offenders: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            target_name = _with_item_target_name(item)
            if target_name is None:
                continue
            if _body_escapes_target(node.body, target_name):
                offenders.append((target_name, node.lineno))

    return offenders


def _value_is_escape_of(value: ast.AST, target_name: str) -> bool:
    """True iff ``value`` is an escape-route expression of ``target_name``.

    Escape routes:

    * ``target_name`` directly,
    * ``Path(target_name)`` / ``pathlib.Path(target_name)``,
    * ``Path(str(target_name))`` / ``pathlib.Path(str(target_name))``,
    * any ``Call`` whose immediate argument is ``target_name``
      (best-effort -- catches custom wrappers like
      ``copy_to_stable(target_name)``).

    The first three are the W643 anti-pattern; the fourth catches
    near-miss wrappers without false-positiving on consumer calls like
    ``target_name.read_text()`` (which would be a *method call on the
    target*, ``ast.Attribute`` value, not a positional arg).
    """
    if isinstance(value, ast.Name) and value.id == target_name:
        return True
    if isinstance(value, ast.Call):
        # Positional first-arg is the target Name?
        if value.args:
            first = value.args[0]
            if isinstance(first, ast.Name) and first.id == target_name:
                return True
            # Path(str(X)) shape -- recurse one level into str() / Path() / etc.
            if isinstance(first, ast.Call) and first.args:
                inner = first.args[0]
                if isinstance(inner, ast.Name) and inner.id == target_name:
                    return True
    return False


def _all_targets_are(targets: list[ast.expr], name: str) -> bool:
    """True iff every assign target is just ``Name(name)``."""
    return all(isinstance(t, ast.Name) and t.id == name for t in targets)


# ── The drift-guard test ─────────────────────────────────────────────


def test_no_as_file_path_captured_outside_with_block() -> None:
    """W668: ``as_file()`` paths must NOT be captured outside ``with``.

    See the module docstring + ``(internal memo)``
    for the W643 incident this guard pins.
    """
    offenders: list[str] = []

    for module_path in _iter_src_modules():
        try:
            source = module_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        try:
            tree = ast.parse(source, filename=str(module_path))
        except SyntaxError:
            continue

        for target, lineno in _find_unsafe_with_blocks(tree):
            rel = module_path.relative_to(repo_root())
            offenders.append(f"  {rel} (line {lineno}): `as_file(...) as {target}:` captured outside `with` block")

    assert offenders == [], (
        "W668: `importlib.resources.as_file(...)` returns a temp-extraction "
        "context manager. Capturing the bound path outside the `with` block "
        "is the W643 anti-pattern -- the path is invalidated by the "
        "manager's `__exit__`. Consume the path INSIDE the `with` block "
        "(e.g. `.read_text()` / `.open()`), or skip `as_file()` and read "
        "the resource via `Path(str(files(...)))` directly when the W664 "
        "drift-guard guarantees the package is a real on-disk subpackage.\n"
        "Offenders:\n" + "\n".join(offenders) + "\nSee `(internal memo)` for the fix template."
    )


# ── Self-test: the detector should fire on a known-bad sample ─────────


_KNOWN_BAD_SAMPLE = """
from importlib.resources import as_file, files
from pathlib import Path

def bad_caller():
    pkg = files("roam.templates.audit_report") / "control-mapping.yaml"
    with as_file(pkg) as resource_path:
        if resource_path.exists():
            return Path(resource_path)
    return None
"""

_KNOWN_GOOD_SAMPLE = """
from importlib.resources import as_file, files

def good_caller():
    pkg = files("roam") / "server-card.json"
    with as_file(pkg) as resource_path:
        if resource_path.is_file():
            return resource_path.read_text(encoding="utf-8")
    return None
"""


def test_self_test_detector_fires_on_known_bad_sample() -> None:
    """Self-test: the AST visitor flags the canonical W643 anti-pattern."""
    tree = ast.parse(_KNOWN_BAD_SAMPLE)
    offenders = _find_unsafe_with_blocks(tree)
    assert offenders, (
        "W668 drift-guard self-test failed: the detector should flag "
        "`return Path(resource_path)` inside the `with as_file(...)` "
        "body but did not. The detector is broken."
    )


def test_self_test_detector_quiet_on_known_good_sample() -> None:
    """Self-test: the AST visitor does NOT flag SAFE consume-inside patterns."""
    tree = ast.parse(_KNOWN_GOOD_SAMPLE)
    offenders = _find_unsafe_with_blocks(tree)
    assert offenders == [], (
        "W668 drift-guard self-test failed: the detector flagged a SAFE "
        "consume-inside-`with` pattern (`return resource_path.read_text()`). "
        "The detector is over-aggressive. False positives:\n" + "\n".join(f"  {t}@{ln}" for t, ln in offenders)
    )
