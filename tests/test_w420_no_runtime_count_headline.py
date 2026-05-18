"""Structural drift-guard for the W420 plugin-invariance invariant.

W420 background
---------------

Roam's user-visible "command count" / "tool count" headlines (in JSON
envelope ``summary`` blocks, in ``_status_pass`` / ``_finding`` ``detail``
strings, and inside ``json_envelope(...)`` value positions) must come
from the AST source of truth — ``roam.surface_counts.cli_commands()``
for CLI commands and ``roam.surface_counts.mcp_tool_names()`` for MCP
tools. The runtime ``roam.cli._COMMANDS`` dict and the runtime
``roam.mcp_server._REGISTERED_TOOLS`` list are BOTH plugin-mutated at
discovery time and preset-filtered at process start respectively. Using
``len(_COMMANDS)`` or ``len(_REGISTERED_TOOLS)`` in a count-headline
position therefore drifts the headline between processes depending on
whether plugin loading fired and what preset is active.

The W420 cascade campaign repaired five sites:

- ``cmd_surface.py`` (root cause; the canonical "241 commands" headline)
- ``cmd_compatibility.py:85``
- ``cmd_doctor.py:409`` (CLI integrity check)
- ``cmd_capabilities.py:178+181``
- ``constitution/loader.py`` (audit-classified DISPATCH; intentional
  membership probe, not a count-headline use)

Without a structural lint, the NEXT time a contributor writes
``f"{len(_COMMANDS)} commands"`` inside a JSON envelope value or a
``_status_pass(detail=...)`` argument, the bug regresses silently. This
file is the mechanical gate.

What the lint flags
-------------------

For every ``cmd_*.py`` under ``src/roam/commands/``, AST-walk for
``Call(func=Name("len"), args=[Name("_COMMANDS" | "_REGISTERED_TOOLS")])``
expressions and check if the enclosing context is a count-headline use:

1. The expression is a value inside a ``json_envelope(...)`` keyword
   argument (any keyword, including the ``summary={...}`` literal and
   sibling kwargs).
2. The expression is a value inside an f-string assigned to a ``detail``
   key in a dict literal (``{"detail": f"... {len(_COMMANDS)} ..."}``)
   or passed as the ``detail=`` kwarg to a call.
3. The expression is the return value of (or inside an f-string returned
   from) a ``_status_pass`` / ``_status_fail`` / ``_finding`` /
   ``_status_*`` emitter helper.

Membership / iteration / parameter-lookup uses are EXPLICITLY skipped —
those are intentional dispatch over the runtime registry (e.g.
``for cmd_name in _COMMANDS:``, ``if action in _COMMANDS:``,
``sum(1 for n in _REGISTERED_TOOLS if n in _CORE_TOOLS)``).

The DISPATCH allowlist (``_W420_RUNTIME_DISPATCH_ALLOWED``) names the
sites the cascade audit classified as INTENTIONAL count-of-live-state
(e.g. ``cmd_mcp_status`` reporting the active preset's currently
registered tool count, which by design must reflect runtime — see the
``MCP ready — preset=core, N tools registered`` verdict). A sibling
test pins the allowlist to exactly the audit-classified DISPATCH set
so silent allowlist growth is a deliberate diff.

Cross-reference
---------------

This lint is the sibling structural guard to
``tests/test_changelog_phantoms.py`` (phantom dev/*.md references) and
``tests/test_doc_no_phantom_cli.py`` (phantom CLI commands in docs).
Pattern: every multi-wave campaign should ship a same-session
structural drift-guard so the next regression cannot land silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Path resolution + scope
# ---------------------------------------------------------------------------

REPO_ROOT = repo_root()
COMMANDS_DIR = REPO_ROOT / "src" / "roam" / "commands"

# Names whose ``len(...)`` is the W420 anti-pattern.
_RUNTIME_REGISTRY_NAMES: frozenset[str] = frozenset({"_COMMANDS", "_REGISTERED_TOOLS"})

# Emitter helpers whose returned dict's ``detail`` value is a
# count-headline position. The brief enumerates ``_status_pass`` /
# ``_status_fail`` / ``_finding`` and "similar emitter helpers". We
# match by canonical prefix to catch local renames (``_status_warn``,
# ``_finding_row``) without forcing the allowlist to enumerate them.
_EMITTER_HELPER_PREFIXES: tuple[str, ...] = ("_status_", "_finding")

# The keyword arg in a JSON envelope value position that carries the
# user-visible count headline.
_HEADLINE_KEYS: frozenset[str] = frozenset({"detail", "verdict", "summary", "message"})


# ---------------------------------------------------------------------------
# Allowlist — DISPATCH sites the W420 cascade audit classified as INTENTIONAL
# ---------------------------------------------------------------------------

# Each entry is a ``(filename, function_name)`` pair. The filename is the
# basename only (e.g. ``cmd_mcp_status.py``) so the allowlist is
# environment-independent. The function name is the lexically-enclosing
# Python function at the use site — looked up via ``ast.walk`` + parent
# tracking.
#
# Pre-seeded with the audit-classified DISPATCH set. Growth here must be
# deliberate: the sibling test
# ``test_allowlist_matches_canonical_dispatch_sites`` pins this set to its
# present membership so an accidental addition (silently exempting a real
# count-headline regression) surfaces as a diff in the test suite.
_W420_RUNTIME_DISPATCH_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        # cmd_mcp_status.py reports the LIVE preset's currently registered
        # tool count + the count of core-preset members currently active.
        # The verdict ``MCP ready — preset=core, N tools registered (M core)``
        # is meaningful only if those numbers reflect the running process's
        # active surface; they would be wrong if reported from the AST.
        ("cmd_mcp_status.py", "mcp_status"),
    }
)


# ---------------------------------------------------------------------------
# AST walker — flag count-headline uses of len(_COMMANDS | _REGISTERED_TOOLS)
# ---------------------------------------------------------------------------


def _is_runtime_len_call(node: ast.expr) -> str | None:
    """If ``node`` is ``len(_COMMANDS)`` or ``len(_REGISTERED_TOOLS)``,
    return the referenced name; else None.
    """
    if not isinstance(node, ast.Call):
        return None
    if not (isinstance(node.func, ast.Name) and node.func.id == "len"):
        return None
    if len(node.args) != 1:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Name) and arg.id in _RUNTIME_REGISTRY_NAMES:
        return arg.id
    return None


def _attach_parents(tree: ast.AST) -> None:
    """Stamp every node with a ``parent`` attribute for context lookup.

    ``ast.walk`` exposes nodes but not their parent; we need the parent
    chain to decide if a ``len(_COMMANDS)`` Call is the value of a
    ``detail`` dict key, inside a JoinedStr (f-string) that's then
    placed in a count-headline position, or a sibling of an emitter
    helper's return statement.
    """
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]


def _enclosing_function(node: ast.AST) -> str | None:
    """Walk up the parent chain to find the nearest enclosing FunctionDef."""
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = getattr(cur, "parent", None)
    return None


def _is_in_joinedstr(node: ast.AST) -> ast.JoinedStr | None:
    """Return the enclosing JoinedStr (f-string) if any, else None."""
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, ast.JoinedStr):
            return cur
        # Stop walking once we've left the expression — function/module
        # boundaries mean the node is not inside an f-string at all.
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return None
        cur = getattr(cur, "parent", None)
    return None


def _is_value_of_headline_dict_key(node: ast.AST) -> bool:
    """True iff ``node`` sits in a dict-literal value position whose key
    is one of ``_HEADLINE_KEYS`` (``detail`` / ``verdict`` / ``summary`` /
    ``message``).

    Handles two shapes:
    - ``{"detail": f"... {len(_COMMANDS)} ..."}`` — the JoinedStr is the
      value; the JoinedStr's parent is a Dict whose key alongside is the
      headline key.
    - ``{"detail": len(_COMMANDS)}`` — direct value (no f-string wrap).
    """
    # Walk up until we hit a Dict (the literal) or leave the expression.
    cur: ast.AST | None = node
    last: ast.AST | None = None
    while cur is not None:
        if isinstance(cur, ast.Dict):
            # Match ``last`` against the values list; the matching key
            # decides whether this is a headline position.
            for k, v in zip(cur.keys, cur.values):
                if v is last and isinstance(k, ast.Constant) and k.value in _HEADLINE_KEYS:
                    return True
            return False
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        last = cur
        cur = getattr(cur, "parent", None)
    return False


def _is_value_of_headline_kwarg(node: ast.AST) -> bool:
    """True iff ``node`` is (or is contained in) the value of a keyword
    argument whose name is in ``_HEADLINE_KEYS``.

    Catches ``_status_pass(detail=f"... {len(_COMMANDS)} ...")`` and
    ``json_envelope(... detail=len(_COMMANDS) ...)``.
    """
    cur: ast.AST | None = node
    last: ast.AST | None = None
    while cur is not None:
        if isinstance(cur, ast.keyword):
            if cur.arg in _HEADLINE_KEYS:
                return True
            return False
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        last = cur  # noqa: F841 — kept for symmetry with _is_value_of_headline_dict_key
        cur = getattr(cur, "parent", None)
    return False


def _enclosing_call_func_name(node: ast.AST) -> str | None:
    """Return the name of the function being called at the nearest
    enclosing ``Call`` site (when callable is a Name), else None.

    ``json_envelope(...)`` -> ``"json_envelope"``.
    ``_status_pass(...)`` -> ``"_status_pass"``.
    ``some.attr(...)`` -> None (we deliberately don't match attribute
    calls; headline emission goes through bare names by convention).
    """
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.Call):
            if isinstance(cur.func, ast.Name):
                return cur.func.id
            return None
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return None
        cur = getattr(cur, "parent", None)
    return None


def _is_in_json_envelope_call(node: ast.AST) -> bool:
    """True iff ``node`` is anywhere inside a ``json_envelope(...)`` call.

    Includes the ``summary={...}`` dict literal AND any sibling kwarg
    (the W420 anti-pattern landed in both positions historically).
    """
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.Call):
            if isinstance(cur.func, ast.Name) and cur.func.id == "json_envelope":
                return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        cur = getattr(cur, "parent", None)
    return False


def _is_in_emitter_helper_call(node: ast.AST) -> bool:
    """True iff ``node`` is inside a ``_status_*`` or ``_finding*`` call."""
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.Call):
            if isinstance(cur.func, ast.Name) and any(cur.func.id.startswith(p) for p in _EMITTER_HELPER_PREFIXES):
                return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        cur = getattr(cur, "parent", None)
    return False


def _is_count_headline_context(node: ast.AST) -> bool:
    """Compose the four "is this a count-headline position?" checks.

    Order matters only for short-circuit speed; any single True flags
    the call.
    """
    return (
        _is_in_json_envelope_call(node)
        or _is_in_emitter_helper_call(node)
        or _is_value_of_headline_dict_key(node)
        or _is_value_of_headline_kwarg(node)
    )


# ---------------------------------------------------------------------------
# Lint driver
# ---------------------------------------------------------------------------


def _scan_file(path: Path) -> list[tuple[str, int, str, str]]:
    """Return a list of ``(filename, lineno, ref, enclosing_function)``
    violations for one ``cmd_*.py`` file.

    Each violation is a ``len(_COMMANDS | _REGISTERED_TOOLS)`` call whose
    enclosing context is one of the count-headline positions AND whose
    ``(filename, enclosing_function)`` is NOT in the DISPATCH allowlist.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    _attach_parents(tree)

    violations: list[tuple[str, int, str, str]] = []
    for node in ast.walk(tree):
        ref = _is_runtime_len_call(node)
        if ref is None:
            continue
        if not _is_count_headline_context(node):
            continue
        enclosing = _enclosing_function(node) or "<module>"
        if (path.name, enclosing) in _W420_RUNTIME_DISPATCH_ALLOWED:
            continue
        violations.append((path.name, node.lineno, ref, enclosing))
    return violations


def _collect_all_violations() -> list[tuple[str, int, str, str]]:
    """Scan every ``cmd_*.py`` under ``src/roam/commands/``."""
    if not COMMANDS_DIR.is_dir():
        raise FileNotFoundError(f"commands dir not found: {COMMANDS_DIR}")
    out: list[tuple[str, int, str, str]] = []
    for cmd_path in sorted(COMMANDS_DIR.glob("cmd_*.py")):
        out.extend(_scan_file(cmd_path))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_runtime_commands_in_count_headline() -> None:
    """No ``cmd_*.py`` may use ``len(_COMMANDS)`` or
    ``len(_REGISTERED_TOOLS)`` in a count-headline position (inside a
    ``json_envelope(...)`` call, inside a ``_status_*`` / ``_finding*``
    emitter, or as the value of a ``detail`` / ``verdict`` / ``summary``
    / ``message`` dict-key or kwarg).

    The W420 cascade closed five sites. This drift-guard prevents the
    sixth.
    """
    violations = _collect_all_violations()
    formatted: list[str] = []
    for filename, lineno, ref, enclosing in violations:
        formatted.append(
            f"{filename}:{lineno}: runtime `{ref}` used in COUNT-HEADLINE "
            f"context (W420). Either use `roam.surface_counts.cli_commands()` "
            f"/ `mcp_tool_names()` (AST source-of-truth) OR add "
            f"({filename!r}, {enclosing!r}) to _W420_RUNTIME_DISPATCH_ALLOWED "
            f"with rationale comment."
        )
    assert not formatted, (
        "W420 plugin-invariance regression — runtime `_COMMANDS` / "
        "`_REGISTERED_TOOLS` used in count-headline position(s). The "
        "headline must come from the AST source of truth so it is "
        "plugin-invariant and preset-invariant. Offenders:\n  " + "\n  ".join(formatted)
    )


def test_allowlist_matches_canonical_dispatch_sites() -> None:
    """The DISPATCH allowlist must equal the audit-classified set EXACTLY.

    Regression-guard against silent allowlist growth. If the next agent
    adds an entry to ``_W420_RUNTIME_DISPATCH_ALLOWED`` to silence a
    failing run of the primary lint, this test surfaces the diff so the
    addition is a deliberate review item rather than an invisible
    bypass.

    To extend the canonical set:
    1. Audit the new site — confirm it must reflect runtime state
       (live preset / live plugin / live cache).
    2. Add it to BOTH ``_W420_RUNTIME_DISPATCH_ALLOWED`` AND
       ``_CANONICAL_DISPATCH_SITES`` below.
    3. Document the rationale in a comment beside the entry.
    """
    _CANONICAL_DISPATCH_SITES: frozenset[tuple[str, str]] = frozenset(
        {
            # cmd_mcp_status.py:mcp_status — reports live preset state.
            ("cmd_mcp_status.py", "mcp_status"),
        }
    )
    extra = _W420_RUNTIME_DISPATCH_ALLOWED - _CANONICAL_DISPATCH_SITES
    missing = _CANONICAL_DISPATCH_SITES - _W420_RUNTIME_DISPATCH_ALLOWED
    assert not extra and not missing, (
        "W420 DISPATCH allowlist drift — the allowlist must equal the "
        "audit-classified canonical set EXACTLY (silent additions hide "
        "regressions).\n"
        f"  unexpected entries: {sorted(extra)}\n"
        f"  missing entries:    {sorted(missing)}"
    )
