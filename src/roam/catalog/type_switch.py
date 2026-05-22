"""Fowler "Type-Switch" / "Polymorphism Rejection" detector (W852).

When a function dispatches on the runtime type of one of its inputs via
a chain of ``isinstance(x, T)`` / ``type(x) is T`` checks (or a Python
``match x:`` whose case patterns are concrete classes), every new
subclass forces an edit to that function. This is the canonical
Open/Closed Principle violation — Strategy, Visitor, double-dispatch,
or ``functools.singledispatch`` are the polymorphic alternatives.

Algorithm
---------
1. For every Python file recorded in ``files``, read the source through
   the workspace root (``find_project_root()`` walks up for ``.git``).
   Files that can't be read are skipped silently — this matches the
   discipline used by ``detect_switch_statement`` and friends.
   W1301: the read + ``ast.parse`` go through the shared per-run AST
   cache (``smells._read_and_parse``) so when ``roam smells`` and this
   detector run in the same process each Python file is parsed once and
   reused. ``ast.parse`` is pure for a fixed source, so the finding set
   is byte-identical to the old inline ``read_text`` + ``ast.parse``.
2. Skip files whose path is rooted at ``tests/``, ``test/``, or whose
   basename is ``conftest.py``. Type-switches in test code are
   legitimate parametric setup (pytest fixtures, mocked classes,
   property-based shrinkers).
3. Walk every ``FunctionDef`` /
   ``AsyncFunctionDef`` and count, per function body:
     * ``isinstance(x, T)`` calls discriminating on the SAME variable
       against ``>= min_class_arms`` distinct concrete classes.
     * ``type(x) is T`` / ``type(x) == T`` checks, ditto.
     * ``match x: case ClassName(...):`` patterns with
       ``>= min_class_arms`` distinct concrete ``MatchClass`` arms on a
       single subject ``Name``.
4. Concrete classes are detected by the heuristic "the dotted name's
   terminal segment starts with an uppercase letter AND is NOT in the
   primitive allowlist". This catches ``Cat`` / ``models.Dog`` /
   ``Bird`` while excluding ``int`` / ``str`` / ``bool`` / etc.
5. Carve-out for explicit polymorphic dispatch: any call inside the
   function body that targets ``.register(...)`` on a name ending in
   ``dispatch`` (``singledispatch.register``,
   ``my_dispatcher.register``), or directly references the
   ``__instancecheck__`` / ``__subclasscheck__`` dunder, removes the
   function from the finding set — the author IS using the polymorphic
   alternative.

Confidence tier: ``structural`` — pure AST analysis, no name-based
guesswork beyond the ``Capitalised`` heuristic for "is this a class".
Severity: ``warning`` — type-switches are well-established OCP smells
worth a refactor decision; they are not always defects (e.g., a
boundary parser legitimately fans out on a closed AST node set).

LAW 4: descriptions end on the concrete-noun terminal ``arms``.

Findings shape mirrors ``roam.catalog.smells._finding`` plus an
``evidence`` sidecar carrying ``discriminator``, ``class_arms``,
``check_kind`` for downstream rendering.
"""

from __future__ import annotations

import ast
import logging
import sqlite3

from roam.catalog._shared import enclosing_symbol as _enclosing_symbol
from roam.catalog._shared import find_workspace_root as _find_workspace_root
from roam.catalog._shared import is_test_path as _file_is_test
from roam.catalog._shared import loc as _loc
from roam.catalog._shared import make_smell_finding as _finding

log = logging.getLogger(__name__)


# Detector identity constants — `smells.py` consumes both.
TYPE_SWITCH_DETECTOR = "type-switch"
TYPE_SWITCH_DETECTOR_VERSION = 1


# Python primitive / builtin container types that are duck-type guards,
# NOT OCP violations. ``isinstance(x, int)`` is a sentinel check, not
# a switch on a subclass hierarchy. ``NoneType`` is treated via the
# ``type(None)`` callsite shape — see ``_classname_from_node``.
_PRIMITIVE_TYPES: frozenset[str] = frozenset(
    {
        "bool",
        "int",
        "float",
        "complex",
        "str",
        "bytes",
        "bytearray",
        "list",
        "tuple",
        "dict",
        "set",
        "frozenset",
        "object",
        "type",
        "None",
        "NoneType",
        "Iterable",
        "Iterator",
        "Sequence",
        "Mapping",
        "MutableMapping",
        "Set",
        "MutableSet",
        "Collection",
        "Container",
        "Sized",
        "Hashable",
        "Callable",
        "Generator",
        "Coroutine",
        "AsyncIterable",
        "AsyncIterator",
        "Number",
        "Real",
        "Integral",
        "Rational",
        "Any",
        "Optional",
        "Union",
    }
)


def _classname_from_node(node: ast.AST) -> str | None:
    """Render a class reference as its dotted-name string.

    Returns ``None`` when the node is not a class-name shape we want to
    count (e.g. a literal, a subscripted generic, a function call).
    Accepts ``Name`` (``Cat``), ``Attribute`` (``models.Cat``), and
    handles the ``type(None)`` callsite by mapping it to ``"NoneType"``.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = [node.attr]
        cur: ast.AST = node.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        return None
    if isinstance(node, ast.Call):
        # ``type(None)`` -> the NoneType sentinel — primitive, not a class arm.
        func = node.func
        if isinstance(func, ast.Name) and func.id == "type" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and arg.value is None:
                return "NoneType"
        return None
    return None


def _is_concrete_class_name(rendered: str | None) -> bool:
    """A rendered name is a "concrete class arm" candidate when:

    * It's not ``None``.
    * Its terminal segment is NOT in ``_PRIMITIVE_TYPES``.
    * Its terminal segment starts with an uppercase letter (PascalCase
      convention; matches Python community practice + the rendering
      used by Fowler's original definition).
    """
    if not rendered:
        return False
    terminal = rendered.rsplit(".", 1)[-1]
    if terminal in _PRIMITIVE_TYPES:
        return False
    if not terminal:
        return False
    return terminal[0].isupper()


def _classes_from_isinstance(call: ast.Call) -> list[str]:
    """Extract the class-arm names from an ``isinstance(x, T_or_tuple)`` call.

    Returns ``[]`` when the call is not a syntactic ``isinstance(...)`` or
    when the 2nd arg is not a class / tuple-of-classes. Concrete-class
    filtering happens at the caller — we return the raw rendered names
    so the primitive allowlist can still see them.
    """
    if not (isinstance(call.func, ast.Name) and call.func.id == "isinstance"):
        return []
    if len(call.args) < 2:
        return []
    type_arg = call.args[1]
    if isinstance(type_arg, ast.Tuple):
        names: list[str] = []
        for elt in type_arg.elts:
            rendered = _classname_from_node(elt)
            if rendered:
                names.append(rendered)
        return names
    rendered = _classname_from_node(type_arg)
    return [rendered] if rendered else []


def _discriminator_from_node(node: ast.AST) -> str | None:
    """Render the discriminator side of an ``isinstance`` / ``type(...)`` check.

    For now we only count ``Name`` discriminators (``x``,
    ``self.value`` → first segment ``self``). Attribute-rooted
    discriminators (``self.value``) are rendered as the full dotted
    path so ``isinstance(self.value, A)`` and ``isinstance(x, A)`` are
    NOT conflated. Subscripts, calls, and other complex expressions
    return ``None`` (we cannot count them as a stable discriminator).
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = [node.attr]
        cur: ast.AST = node.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        return None
    return None


def _classes_from_type_eq(test: ast.AST) -> tuple[str | None, list[str]]:
    """If ``test`` is ``type(x) is T`` / ``type(x) == T``, return ``(x_repr, [T])``.

    Handles both the ``Compare`` shape (``type(x) is T``) and the
    chained ``type(x) is T or type(x) is U`` form via the caller's
    BoolOp walk.
    """
    if not isinstance(test, ast.Compare):
        return None, []
    if not (
        isinstance(test.left, ast.Call)
        and isinstance(test.left.func, ast.Name)
        and test.left.func.id == "type"
        and len(test.left.args) == 1
    ):
        return None, []
    if len(test.ops) != 1 or not isinstance(test.ops[0], (ast.Is, ast.Eq)):
        return None, []
    discriminator = _discriminator_from_node(test.left.args[0])
    if discriminator is None:
        return None, []
    classes: list[str] = []
    for comparator in test.comparators:
        rendered = _classname_from_node(comparator)
        if rendered:
            classes.append(rendered)
    return discriminator, classes


def _decorator_names(func: ast.AST) -> set[str]:
    """Render the decorator names on a function as a set of terminal-segment strings.

    ``@singledispatch`` -> ``{"singledispatch"}``.
    ``@functools.singledispatch`` -> ``{"singledispatch"}``.
    ``@my_dispatch.register`` -> ``{"register"}``.
    ``@cache(maxsize=128)`` -> ``{"cache"}`` (decorator call's func.id).
    """
    out: set[str] = set()
    decorators = getattr(func, "decorator_list", ()) or ()
    for dec in decorators:
        if isinstance(dec, ast.Name):
            out.add(dec.id)
        elif isinstance(dec, ast.Attribute):
            out.add(dec.attr)
        elif isinstance(dec, ast.Call):
            target = dec.func
            if isinstance(target, ast.Name):
                out.add(target.id)
            elif isinstance(target, ast.Attribute):
                out.add(target.attr)
    return out


def _module_has_singledispatch(tree: ast.AST) -> bool:
    """Cheap module-wide signal: any function decorated with ``singledispatch``
    or ``singledispatchmethod`` indicates the polymorphic alternative is in
    use. Subsequent ``foo.register(Type)`` calls anywhere in the module are
    then registration plumbing, not OCP smells.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = _decorator_names(node)
            if "singledispatch" in names or "singledispatchmethod" in names:
                return True
    return False


def _function_has_dispatch_optout(func: ast.AST, *, module_has_singledispatch: bool = False) -> bool:
    """Carve-out: the function explicitly uses a polymorphic alternative.

    Triggers when:
      * The enclosing module has a ``@singledispatch`` /
        ``@singledispatchmethod`` decorator anywhere (passed in via the
        ``module_has_singledispatch`` flag — singledispatch registers
        type arms through ``.register(Type)`` plumbing, which is the
        polymorphic FIX).
      * The function itself is decorated with ``@singledispatch`` /
        ``@singledispatchmethod`` / ``@<name>.register``.
      * Any descendant is ``<x>.register(...)`` where ``<x>`` ends with
        ``dispatch`` (custom dispatcher naming convention).
      * Any descendant references ``__instancecheck__`` /
        ``__subclasscheck__`` (the dunder hook is the polymorphic
        alternative for type membership).
    """
    if module_has_singledispatch:
        return True
    decorators = _decorator_names(func)
    if decorators & {"singledispatch", "singledispatchmethod", "register"}:
        return True
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "register":
                receiver = node.func.value
                terminal: str | None = None
                if isinstance(receiver, ast.Name):
                    terminal = receiver.id
                elif isinstance(receiver, ast.Attribute):
                    terminal = receiver.attr
                if terminal and terminal.lower().endswith("dispatch"):
                    return True
        if isinstance(node, ast.Attribute) and node.attr in {
            "__instancecheck__",
            "__subclasscheck__",
        }:
            return True
        if isinstance(node, ast.Name) and node.id in {
            "__instancecheck__",
            "__subclasscheck__",
        }:
            return True
    return False


# W923: the canonical ``_finding`` envelope builder is imported at the
# top of this module from ``roam.catalog._shared.make_smell_finding``.
# Historical local def (a self-confessed mirror of the canonical
# ``smells._finding`` shape) has been replaced by the import alias.
# The post-mutation idiom at the call-site (``finding["evidence"] =
# ...; finding["confidence"] = ...; finding["detector_version"] =
# ...``) keeps working — ``make_smell_finding`` returns a plain dict.


def _collect_arms_for_function(
    func: ast.AST,
) -> dict[tuple[str, str], set[str]]:
    """Walk one function body and bucket class arms by (discriminator, kind).

    Returns ``{(discriminator, check_kind): {class_name, ...}}`` where
    ``check_kind`` is one of ``"isinstance"`` / ``"type_eq"`` /
    ``"match_case"``. The set values are PRE-filter — primitives are
    still in there. The caller filters via ``_is_concrete_class_name``.
    """
    buckets: dict[tuple[str, str], set[str]] = {}

    for node in ast.walk(func):
        # isinstance(x, T) / isinstance(x, (A, B, C))
        if isinstance(node, ast.Call):
            classes = _classes_from_isinstance(node)
            if classes and len(node.args) >= 1:
                disc = _discriminator_from_node(node.args[0])
                if disc is not None:
                    key = (disc, "isinstance")
                    buckets.setdefault(key, set()).update(classes)
                continue

        # type(x) is T / type(x) == T   (as a standalone Compare test)
        if isinstance(node, ast.Compare):
            disc, classes = _classes_from_type_eq(node)
            if disc is not None and classes:
                key = (disc, "type_eq")
                buckets.setdefault(key, set()).update(classes)

        # match x: case Cat(...): ...
        if isinstance(node, ast.Match):
            if not isinstance(node.subject, ast.Name):
                continue
            disc = node.subject.id
            for case in node.cases:
                pattern = case.pattern
                # match Cat(): -> MatchClass with cls=Name('Cat')
                if isinstance(pattern, ast.MatchClass):
                    rendered = _classname_from_node(pattern.cls)
                    if rendered:
                        key = (disc, "match_case")
                        buckets.setdefault(key, set()).add(rendered)
                # match _ if isinstance(x, Cat): ... — caught by walk
                # already via the ast.Call branch above.

    return buckets


def detect_type_switch(
    conn: sqlite3.Connection,
    *,
    min_class_arms: int = 3,
) -> list[dict]:
    """Detect functions that switch on runtime type against >=N concrete classes.

    Parameters
    ----------
    conn:
        SQLite connection (``row_factory == sqlite3.Row`` recommended).
    min_class_arms:
        Threshold of distinct concrete-class arms on the same
        discriminator to flag a finding. Default ``3`` — Fowler's
        canonical "tier-2 if-else-if" boundary.

    Returns
    -------
    list[dict]
        One finding per (function, discriminator, check_kind) triple
        meeting the threshold. Findings carry ``evidence`` with
        ``discriminator``, ``class_arms`` (sorted), ``check_kind``.
        Empty list when the DB has no python files or no function
        crosses the threshold.
    """
    try:
        files = conn.execute("SELECT id, path FROM files WHERE language = 'python'").fetchall()
    except sqlite3.OperationalError:
        return []

    # W1301: lazy import of the shared per-run AST cache. ``smells.py``
    # imports this module at its top (``from roam.catalog.type_switch
    # import detect_type_switch``) BEFORE ``_read_and_parse`` is defined
    # further down — a verified circular edge (smells -> type_switch ->
    # smells). A module-level import here would hit a partially-
    # initialized ``smells`` and raise ImportError. Deferring to call
    # time is correct: by the time ``detect_type_switch`` runs,
    # ``smells`` is fully initialized. ``_read_and_parse`` reads with
    # ``encoding="utf-8", errors="replace"`` and a bare ``ast.parse`` —
    # byte-identical to this detector's former inline parse.
    from roam.catalog.smells import _read_and_parse

    workspace = _find_workspace_root()
    results: list[dict] = []

    for f in files:
        file_id = f["id"]
        rel_path = f["path"]
        if _file_is_test(rel_path):
            continue
        # Shared cache: parses each file at most once per process and
        # self-invalidates on (mtime_ns, size). Returns ``None`` on any
        # read / stat / parse failure — same skip-on-failure control flow
        # as the former ``(OSError, ValueError)`` + ``SyntaxError`` guards.
        tree = _read_and_parse(workspace, rel_path)
        if tree is None:
            continue

        module_singledispatch = _module_has_singledispatch(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _function_has_dispatch_optout(node, module_has_singledispatch=module_singledispatch):
                continue

            buckets = _collect_arms_for_function(node)
            if not buckets:
                continue

            # Emit one finding per (discriminator, check_kind) bucket
            # that crosses the threshold AFTER filtering out primitives.
            for (discriminator, check_kind), arm_set in buckets.items():
                concrete_arms = sorted(a for a in arm_set if _is_concrete_class_name(a))
                if len(concrete_arms) < min_class_arms:
                    continue

                func_line = getattr(node, "lineno", 1)
                # Prefer the indexer's enclosing-symbol record (correct
                # method-class name); fall back to the AST function
                # name when the index hasn't catalogued it.
                enc_name, enc_kind, _enc_line = _enclosing_symbol(conn, file_id, func_line)
                func_name = node.name
                func_kind = (
                    enc_kind
                    if enc_name == func_name
                    else (
                        "method"
                        if any(isinstance(a, ast.arg) and a.arg in {"self", "cls"} for a in node.args.args[:1])
                        else "function"
                    )
                )

                check_label = {
                    "isinstance": "isinstance arms",
                    "type_eq": "type() == T arms",
                    "match_case": "match-case class arms",
                }[check_kind]
                class_list = ", ".join(concrete_arms)
                description = (
                    f"Type-switch on `{discriminator}` in `{func_name}`: "
                    f"{len(concrete_arms)} {check_label} ({class_list}) — "
                    f"consider Strategy / Visitor / functools.singledispatch "
                    f"to absorb the arms."
                )

                finding = _finding(
                    TYPE_SWITCH_DETECTOR,
                    "warning",
                    func_name,
                    func_kind,
                    _loc(rel_path, func_line),
                    len(concrete_arms),
                    min_class_arms,
                    description,
                )
                finding["evidence"] = {
                    "discriminator": discriminator,
                    "class_arms": concrete_arms,
                    "check_kind": check_kind,
                }
                finding["confidence"] = "structural"
                finding["detector_version"] = TYPE_SWITCH_DETECTOR_VERSION
                results.append(finding)

    return results


__all__ = [
    "TYPE_SWITCH_DETECTOR",
    "TYPE_SWITCH_DETECTOR_VERSION",
    "detect_type_switch",
]
