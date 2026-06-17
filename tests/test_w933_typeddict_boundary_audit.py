"""W933-extension audit: pin "Don't TypedDict a boundary you don't validate".

Discovery-time audit (2026-05-18) found ZERO live violations of the
W933 / W966 discipline (CLAUDE.md, "Don't TypedDict a boundary you
don't validate — the W966 discipline rule", line ~1045+).

This test is the regression guard. It enumerates every ``TypedDict``
subclass in ``src/roam/`` and every function returning a tight-typed
``dict[str, <ClassName>]`` / ``dict[int, <ClassName>]`` shape. For each
hit, it confirms the source pattern is on the audit-approved allowlist
(controlled construction OR explicit at-load validation).

The test FAILS the day someone:

1. Adds a new ``TypedDict`` subclass that isn't in the allowlist, OR
2. Adds a new tight-typed dict return whose body has the W933
   anti-pattern fingerprint (``yaml.safe_load`` / ``json.loads`` /
   ``dict.update(<external>)`` populating the typed shape without an
   intermediate validator).

The allowlist captures the audit's positive findings — extending it
means deliberately accepting a new entry into the sealed set, NOT
silencing a violation.

See ``(internal memo)`` for the audit memo.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROAM_SRC = Path(__file__).resolve().parent.parent / "src" / "roam"


# ---------------------------------------------------------------------------
# Allowlist — entries proven OK by the 2026-05-18 audit
# ---------------------------------------------------------------------------

# Every ``class Foo(TypedDict)`` site currently in src/roam. Each entry
# names the rationale from the audit memo. Adding a new TypedDict means
# extending this set AND documenting the rationale in the audit memo.
APPROVED_TYPEDDICTS: dict[str, str] = {
    # File-relative path:class_name -> rationale
    "commands/cmd_alerts.py:AlertThreshold": (
        "hand-written _DEFAULT_THRESHOLDS literal; W974 op closed-enum "
        "+ W969 level closed-enum at boundary (sealed W933)"
    ),
    "commands/cmd_alerts.py:_AlertBase": (
        "constructed only by _make_alert from validated inputs; W973 "
        "assert on level + W959 cast-narrow after assert (sealed W959)"
    ),
    "commands/cmd_alerts.py:Alert": (
        "Alert(_AlertBase, total=False) — same construction path as "
        "_AlertBase via _make_alert (sealed W959); inherits W973 assert "
        "and W959 cast-narrow discipline"
    ),
}

# Every tight-typed ``dict[..., <ClassName>]`` return currently in
# src/roam where ClassName is NOT Any/str/int/float/bool/etc. Each
# entry must name the source-pattern rationale.
APPROVED_TIGHT_DICT_RETURNS: dict[str, str] = {
    "analysis/taint.py:compute_all_summaries": ("constructs TaintSummary(...) programmatically; dataclass"),
    "modes/policy.py:list_modes": (
        "constructs ModePolicy(...) per VALID_MODES closed enum; boundary-validated upstream"
    ),
    "plugins/__init__.py:get_plugin_commands": (
        "populated only by register_command(...) which constructs CommandTarget itself"
    ),
    "plugins/__init__.py:get_plugin_framework_profiles": (
        "populated only by register_framework_profile(profile) which receives a fully-typed FrameworkProfile dataclass"
    ),
    "plugins/registry.py:get_framework_profiles": ("same registry as get_plugin_framework_profiles (delegated)"),
    "commands/cmd_pr_replay.py:_default_rehearsal_paths": (
        "internal str->Path map of rehearsal artifact locations under internal/; not an "
        "external/parsed boundary — paths are constructed locally, never deserialized"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_py_files() -> list[Path]:
    """Yield every .py file under src/roam."""
    return sorted(p for p in ROAM_SRC.rglob("*.py") if p.is_file())


def _rel(path: Path) -> str:
    """Return the path relative to src/roam as forward-slash string."""
    return path.relative_to(ROAM_SRC).as_posix()


def _is_typeddict_class(node: ast.ClassDef) -> bool:
    """Return True if *node* inherits from ``TypedDict`` (direct base)."""
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "TypedDict":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "TypedDict":
            return True
        # ``class Alert(_AlertBase, total=False)`` style — _AlertBase is
        # itself a TypedDict; treat the subclass as a TypedDict-shape too.
        if isinstance(base, ast.Name) and base.id in {"_AlertBase"}:
            return True
    return False


def _tight_dict_return(ret: ast.AST) -> str | None:
    """If *ret* annotates ``dict[<k>, <SomeClass>]`` where SomeClass is
    a capitalised identifier (not Any/built-in), return ``<SomeClass>``.

    Returns ``None`` for honest shapes (``dict[str, Any]``,
    ``dict[str, str]``, ``Mapping[str, Any]``, etc.).
    """
    if not isinstance(ret, ast.Subscript):
        return None
    # Container — accept dict/Dict/Mapping
    container = ret.value
    container_name = None
    if isinstance(container, ast.Name):
        container_name = container.id
    elif isinstance(container, ast.Attribute):
        container_name = container.attr
    if container_name not in {"dict", "Dict", "Mapping", "MutableMapping"}:
        return None

    slice_node = ret.slice
    # In 3.9+, slice is the tuple directly; in 3.8 it would be ast.Index
    if isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2:
        value_anno = slice_node.elts[1]
    else:
        return None

    # We only care about a bare Name as value annotation
    if not isinstance(value_anno, ast.Name):
        return None
    name = value_anno.id
    # Reject the honest cases
    if name in {
        "Any",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "object",
        "None",
        "list",
        "dict",
        "tuple",
        "set",
        "frozenset",
    }:
        return None
    # Reject lowercase-only identifiers (likely a TypeVar or generic)
    if not name[0].isupper():
        return None
    return name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_typeddict_inventory_matches_allowlist() -> None:
    """Every ``class Foo(TypedDict)`` in src/roam must be in the
    approved-typeddicts allowlist with documented rationale.

    Failure mode: a new TypedDict was added without an audit entry.
    Resolution: read CLAUDE.md "Don't TypedDict a boundary you don't
    validate" + audit memo, decide if the new TypedDict is OK
    (controlled construction or boundary-validated), then add the
    entry to ``APPROVED_TYPEDDICTS`` with rationale.
    """
    discovered: dict[str, str] = {}
    for py_file in _iter_py_files():
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_typeddict_class(node):
                key = f"{_rel(py_file)}:{node.name}"
                discovered[key] = py_file.read_text(encoding="utf-8")[: node.lineno]

    unapproved = set(discovered) - set(APPROVED_TYPEDDICTS)
    stale_approved = set(APPROVED_TYPEDDICTS) - set(discovered)

    assert not unapproved, (
        f"W933 audit drift — new TypedDict subclass(es) found without "
        f"audit entries: {sorted(unapproved)}. Read CLAUDE.md 'Don't "
        f"TypedDict a boundary you don't validate' + dev/W933-EXTENSION-"
        f"the audit notes. Confirm the TypedDict is populated via "
        f"controlled construction OR validated boundary input, then add "
        f"an entry to APPROVED_TYPEDDICTS with rationale."
    )
    assert not stale_approved, (
        f"W933 audit allowlist references TypedDict(s) that no longer "
        f"exist in src/roam: {sorted(stale_approved)}. Remove from "
        f"APPROVED_TYPEDDICTS."
    )


def test_tight_dict_returns_match_allowlist() -> None:
    """Every function returning ``dict[<k>, <SomeClass>]`` where SomeClass
    is a capitalised identifier (not a built-in / Any) must be in the
    approved-returns allowlist.

    Failure mode: a new boundary loader returned a tight dict shape
    without going through the validation discipline.
    Resolution: either (a) loosen the return annotation to
    ``dict[str, Any]`` / ``Mapping[str, Any]`` (W933 honest path), or
    (b) confirm the body validates per-field at the boundary before
    constructing the typed value, then add the entry to
    ``APPROVED_TIGHT_DICT_RETURNS`` with rationale.
    """
    discovered: set[str] = set()
    for py_file in _iter_py_files():
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.returns is None:
                continue
            value_class = _tight_dict_return(node.returns)
            if value_class is None:
                continue
            # Skip TypeVar-style names (e.g. T, K, V) — already filtered by
            # the single-char-upper-only heuristic, but be explicit.
            if len(value_class) <= 2:
                continue
            key = f"{_rel(py_file)}:{node.name}"
            discovered.add(key)

    unapproved = discovered - set(APPROVED_TIGHT_DICT_RETURNS)
    stale_approved = set(APPROVED_TIGHT_DICT_RETURNS) - discovered

    assert not unapproved, (
        f"W933 audit drift — new function(s) return a tight-typed "
        f"dict[..., <Class>] shape without audit entries: "
        f"{sorted(unapproved)}. Read CLAUDE.md 'Don't TypedDict a "
        f"boundary you don't validate' + dev/W933-EXTENSION-AUDIT-"
        f"2026-05-18.md. Either (a) loosen the annotation to dict[..., "
        f"Any] (W933 honest path), or (b) confirm boundary-validation "
        f"discipline and add to APPROVED_TIGHT_DICT_RETURNS with "
        f"rationale."
    )
    assert not stale_approved, (
        f"W933 audit allowlist references function(s) that no longer "
        f"return a tight-typed dict: {sorted(stale_approved)}. Remove "
        f"from APPROVED_TIGHT_DICT_RETURNS."
    )


def test_no_safe_load_then_typeddict_cast() -> None:
    """Block the exact anti-pattern shape: ``yaml.safe_load`` / ``json.loads``
    immediately cast to a TypedDict-typed slot in the same function.

    This is the strongest form of the W933 violation: an uncontrolled
    parse feeds directly into a tight type without an intermediate
    validator. The audit confirmed ZERO live cases in src/roam at
    2026-05-18; this test pins the discovery.
    """
    offenders: list[str] = []
    for py_file in _iter_py_files():
        src = py_file.read_text(encoding="utf-8")
        if "safe_load" not in src and "json.loads" not in src:
            continue
        if "TypedDict" not in src and "cast(" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # cast(SomeTypedDictName, yaml.safe_load(...)) / cast(..., json.loads(...))
            if not (isinstance(func, ast.Name) and func.id == "cast"):
                continue
            if len(node.args) < 2:
                continue
            second = node.args[1]
            if not isinstance(second, ast.Call):
                continue
            inner_func = second.func
            inner_name = None
            if isinstance(inner_func, ast.Attribute):
                inner_name = f"{getattr(inner_func.value, 'id', '?')}.{inner_func.attr}"
            if inner_name in {"yaml.safe_load", "json.loads", "json.load"}:
                offenders.append(f"{_rel(py_file)}:line {node.lineno}")

    assert not offenders, (
        f"W933 violation — `cast(SomeType, yaml.safe_load(...))` / "
        f"`cast(SomeType, json.loads(...))` skips validation. Either "
        f"validate at the boundary before casting, or keep the type "
        f"honest as Mapping[str, Any]. Offenders: {offenders}"
    )


@pytest.mark.parametrize(
    "approved_key,rationale",
    sorted(APPROVED_TYPEDDICTS.items()),
)
def test_approved_typeddict_rationale_is_non_empty(approved_key: str, rationale: str) -> None:
    """Drift guard on the allowlist itself — every entry must carry a
    non-trivial rationale string. Prevents future maintainers from
    silently adding an entry with empty rationale to bypass the audit.
    """
    assert len(rationale) >= 20, (
        f"W933 allowlist rationale for {approved_key!r} is too short "
        f"({len(rationale)} chars). Spell out WHY the TypedDict is OK "
        f"(controlled construction? boundary-validated? sealed in test X?)."
    )


@pytest.mark.parametrize(
    "approved_key,rationale",
    sorted(APPROVED_TIGHT_DICT_RETURNS.items()),
)
def test_approved_tight_return_rationale_is_non_empty(approved_key: str, rationale: str) -> None:
    """Companion of the typeddict rationale test for tight dict returns."""
    assert len(rationale) >= 20, (
        f"W933 allowlist rationale for {approved_key!r} is too short "
        f"({len(rationale)} chars). Spell out WHY the tight return is "
        f"OK (controlled construction? boundary-validated?)."
    )
