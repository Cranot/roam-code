"""W1121 CI lint — block new ``@click.argument("target")`` drift in cmd_*.py.

The canonical CLI argument for the symbol-shaped concept is ``"symbol"`` —
matching the MCP wrapper alias map at ``src/roam/mcp_server.py::_PARAM_ALIASES``
(W430), which lists ``"target": "symbol"`` under the symbol cluster. Per
W1004 audit Cluster 3 the legacy ``"target"`` spelling is semantically
overloaded — some sites mean "symbol", others mean a git ref, a file path,
or a number. This lint:

1. Grandfathers every CURRENT site (renames would break shell scripts and
   agent prompts that pass positional arguments). The grandfathered set is
   split into four sub-sets by W1121-target classification so a future
   v14.0 rename pass can clear out the SYMBOL bucket first while leaving
   the permanent carve-outs untouched.
2. Blocks a NEW site from inheriting the overloaded vocabulary — new
   commands should declare ``symbol`` (when the concept is a symbol) or
   pick a domain-specific positional name (``ref`` / ``path`` / ``count``)
   for the carve-out cases.

The MCP-side alias map (W430) already normalizes inbound ``target=...``
callers onto canonical ``symbol`` with a deprecation warning. The CLI-side
lint enforces source hygiene so the divergence doesn't keep widening.

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Matches
``Decorator → Call(func=Attribute(value=Name("click"), attr="argument"))``
with a first positional arg whose ``Constant.value == "target"``.

Sibling lint: ``tests/test_w1111_click_argument_name_lint.py`` covers
the ``@click.argument("name")`` variant of the same divergence.

See: (internal memo) (W1102-RESEARCH).
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Grandfathered sites — closed set, split by W1121-target classification.
#
# Per the W1004 Cluster 3 audit + per-site read of TARGET docstrings:
#
#  - SYMBOL: positional resolves through find_symbol-style helpers; means a
#    symbol identity (sometimes with a file-fallback). Rename candidates for
#    a future v14.0 sweep — the MCP alias map already normalizes inbound
#    ``target=...`` callers onto canonical ``symbol`` so renaming the CLI
#    positional just removes the alias-rewrite hop.
#
#  - GIT_REF: positional passes to git rev-parse / branch resolver. Permanent
#    carve-out — calling it ``symbol`` would mislead.
#
#  - FILE_PATH: positional is a filesystem path (often ``type=click.Path``).
#    Permanent carve-out at this layer, BUT is a Pattern-3b candidate for
#    canonical ``"path"`` (separate lint when it lands).
#
#  - NUMBER: positional is an int / threshold / count. Permanent carve-out.
#    (Empty on the current tree — preserved as a placeholder so the four-way
#    classification stays visible to future auditors.)
# ---------------------------------------------------------------------------

# Rename candidates (v14.0): TARGET is a symbol identity (often with a file
# fallback handled by the resolver). MCP alias map already routes inbound
# ``target=...`` onto canonical ``symbol``.
_SYMBOL_TARGET: frozenset[str] = frozenset(
    {
        "cmd_affected_tests.py",  # "TARGET is a symbol name or file path"
        "cmd_annotate.py",  # "TARGET is a symbol name (resolved via find_symbol) or a file path"
        "cmd_complexity.py",  # per-symbol/file complexity
        "cmd_effects.py",  # side-effect classification per symbol
        "cmd_invariants.py",  # implicit contracts for symbols
        "cmd_metrics.py",  # "TARGET can be a file path ... or a symbol name"
        "cmd_plan.py",  # plan-generation for a symbol/file
        "cmd_preflight.py",  # preflight gate on symbol/file/staged
        "cmd_safe_zones.py",  # "TARGET is a symbol name (or file:symbol) or a file path"
        "cmd_simulate.py",  # simulate_delete: "a symbol or all symbols in a file"
        "cmd_trace.py",  # shortest path between two symbols
        "cmd_why_fail.py",  # test target → symbols
        "cmd_ws.py",  # ws_trace SOURCE→TARGET across repos
    }
)

# Permanent carve-outs: positional is a git ref, not a symbol identity.
_GIT_REF_TARGET: frozenset[str] = frozenset(
    {
        "cmd_breaking.py",  # "TARGET (default: HEAD~1)" — git ref
    }
)

# Permanent carve-outs at this layer: positional is a filesystem path.
# Pattern-3b candidate for canonical ``"path"`` (separate lint, future work).
_FILE_PATH_TARGET: frozenset[str] = frozenset(
    {
        "cmd_compare.py",  # type=click.Path(exists=True, dir_okay=False)
    }
)

# Permanent carve-outs: positional is an int/threshold/count.
# Empty on the current tree — kept as a placeholder for the four-way taxonomy.
_NUMBER_TARGET: frozenset[str] = frozenset()

_GRANDFATHERED: frozenset[str] = _SYMBOL_TARGET | _GIT_REF_TARGET | _FILE_PATH_TARGET | _NUMBER_TARGET


_COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# AST helpers — locate ``@click.argument("target")`` decorators.
# ---------------------------------------------------------------------------


def _is_click_argument_target(decorator: ast.expr) -> bool:
    """Match ``@click.argument("target", ...)`` literal decorator calls.

    Matches BOTH single and double quoted, and is robust to additional
    keyword arguments (e.g. ``required=False, default=None``,
    ``type=click.Path(...)``). Only the first positional argument's
    constant string value is inspected.
    """
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "argument":
        return False
    value = func.value
    if not isinstance(value, ast.Name) or value.id != "click":
        return False
    if not decorator.args:
        return False
    first = decorator.args[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value == "target"


def _find_click_argument_target_sites() -> set[str]:
    """Walk every ``src/roam/commands/cmd_*.py`` file and return the
    basenames of files containing ``@click.argument("target")`` decorators.

    AST-only — no module import. The lint stays runnable in environments
    where roam's optional dependencies (fastmcp, etc.) aren't installed.
    """
    found: set[str] = set()
    for path in sorted(_COMMANDS_DIR.glob("cmd_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            module = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if _is_click_argument_target(decorator):
                    found.add(path.name)
                    break
            if path.name in found:
                break
    return found


# ---------------------------------------------------------------------------
# Sanity guard: discovery must produce something on the current tree.
# A silent "zero sites found" would let the negative-path lint pass even if
# the AST helper regressed.
# ---------------------------------------------------------------------------


def test_discovery_finds_grandfathered_target_sites():
    """Discovery must surface the grandfathered set — if it returns empty
    we're not linting anything and the negative-path test below silently
    passes. Treat the grandfathered set as the lower bound."""
    found = _find_click_argument_target_sites()
    assert found, (
        "AST discovery found zero @click.argument('target') sites. "
        "Either every grandfathered site was renamed (in which case empty "
        "_GRANDFATHERED + delete this test) or "
        "_find_click_argument_target_sites is broken — fix the AST walker."
    )


# ---------------------------------------------------------------------------
# The W1121 lint — block NEW @click.argument("target") sites.
# ---------------------------------------------------------------------------


def test_no_new_click_argument_target_drift():
    """Block new ``@click.argument('target')`` sites.

    Canonical for symbol-shaped concepts is ``'symbol'`` (matches MCP
    wrapper alias map in ``src/roam/mcp_server.py::_PARAM_ALIASES``, W430).
    The ``target`` spelling is semantically overloaded — new sites should
    pick a domain-specific positional name:

      * symbol identity → ``"symbol"``
      * git ref          → ``"ref"``
      * file path        → ``"path"`` (Pattern-3b canonical)
      * int / threshold  → name the quantity (``"count"`` / ``"depth"`` /
                            ``"threshold"``)
    """
    found = _find_click_argument_target_sites()
    unexpected = found - _GRANDFATHERED
    assert not unexpected, (
        f"New @click.argument('target') site(s) detected: "
        f"{sorted(unexpected)}.\n"
        f"Canonical argument name for the symbol-shaped concept is "
        f"'symbol' (matches MCP wrapper alias map in "
        f"src/roam/mcp_server.py::_PARAM_ALIASES, W430).\n"
        f"The 'target' spelling is semantically overloaded — pick a "
        f"domain-specific positional name:\n"
        f"  * symbol identity → 'symbol'\n"
        f"  * git ref          → 'ref'\n"
        f"  * file path        → 'path' (Pattern-3b canonical)\n"
        f"  * int / threshold  → name the quantity ('count' / 'depth' / "
        f"'threshold')\n"
        f"If the new site fits one of the legitimate non-symbol carve-outs, "
        f"exempt it explicitly in _GIT_REF_TARGET / _FILE_PATH_TARGET / "
        f"_NUMBER_TARGET with a comment justifying the carve-out."
    )


def test_grandfathered_target_sites_still_exist():
    """Inverse drift guard — if a grandfathered site goes away (rename
    completed, file deleted, etc.), drop it from ``_GRANDFATHERED`` so
    the lint stays accurate.

    A stale entry hides the fact that the site has been cleaned up AND
    leaves a "ghost slot" through which a regressor could re-add the
    legacy declaration without tripping the lint.
    """
    found = _find_click_argument_target_sites()
    missing = _GRANDFATHERED - found
    assert not missing, (
        f"Grandfathered @click.argument('target') site(s) gone: "
        f"{sorted(missing)}.\nRemove from the appropriate sub-set "
        f"(_SYMBOL_TARGET / _GIT_REF_TARGET / _FILE_PATH_TARGET / "
        f"_NUMBER_TARGET) in this test file so the lint stays accurate. "
        f"Stale entries hide regressions."
    )
