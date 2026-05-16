"""W1111 CI lint — block new ``@click.argument("name")`` drift in cmd_*.py.

The canonical CLI argument for the symbol-shaped concept is ``"symbol"`` —
matching the MCP wrapper alias map at ``src/roam/mcp_server.py::_PARAM_ALIASES``
(W430). Six commands historically diverged on ``@click.argument("name")``;
the W1102 audit captured drive-by additions (cmd_diagnose / cmd_impact /
cmd_oracle / cmd_explain_command / cmd_plugins / cmd_testmap / cmd_closure).
Per W1102-RESEARCH Strategy D this lint:

1. Grandfathers every CURRENT site (preserved for backward compat — renames
   would break shell scripts and agent prompts that pass ``name=...``).
2. Blocks a 7th site from inheriting the divergent vocabulary.

The MCP-side alias map (W430) already normalizes inbound ``name=...``,
``target=...``, ``symbol=...``, ``subject=...`` callers onto canonical
``symbol`` with a deprecation warning. The CLI-side lint enforces source
hygiene so the divergence doesn't keep widening.

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Matches
``Decorator → Call(func=Attribute(value=Name("click"), attr="argument"))``
with a first positional arg whose ``Constant.value == "name"``.

See: (internal memo) (W1102-RESEARCH).
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Grandfathered sites — closed set. Every CURRENT cmd_*.py file that
# declares ``@click.argument("name")`` lives here. The strategy memo's
# "default: grandfather ALL EXISTING sites" rule covers the W1102 drive-by
# captures (cmd_diagnose / cmd_impact / cmd_oracle / cmd_explain_command /
# cmd_plugins / cmd_testmap / cmd_closure). W1106/W1107/W1109 reclassified
# cmd_impact / cmd_oracle / cmd_diagnose as SYMBOL-CONCEPT (see W1132).
#
# An entry here means: "this file already has @click.argument('name'); new
# additions to the SAME file are still blocked by this lint." Drop the
# entry when the site is renamed away from ``name`` (W1004/W1102 follow-up).
# ---------------------------------------------------------------------------
# Per the W1118+W1119+W1108+W1120+W1106+W1107+W1109 reclassification audit:
#
# SYMBOL-CONCEPT (rename to "symbol" in v14.0, MCP alias map normalizes today):
#   cmd_disambiguate / cmd_guard / cmd_safe_delete / cmd_symbol /
#   cmd_test_scaffold / cmd_uses / cmd_closure / cmd_testmap /
#   cmd_impact / cmd_diagnose / cmd_oracle (4 subcommand sites —
#   symbol_exists / is_test_only / is_reachable / is_clone_of)
#
# DOMAIN-DISTINCT (permanent grandfather — different concept than symbol):
#   cmd_explain_command (CLI command name)
#   cmd_plugins         (plugin name)
_GRANDFATHERED: frozenset[str] = frozenset(
    {
        # W1004 audit — original 6 symbol-concept sites:
        "cmd_disambiguate.py",
        "cmd_guard.py",
        "cmd_safe_delete.py",
        "cmd_symbol.py",
        "cmd_test_scaffold.py",
        "cmd_uses.py",
        # W1102 drive-by captures (W1106/W1107/W1109 reclassified as SYMBOL-CONCEPT):
        "cmd_diagnose.py",
        "cmd_impact.py",
        "cmd_oracle.py",
        "cmd_closure.py",
        "cmd_testmap.py",
        # Non-symbol "name" concepts (legitimate carve-outs — would be exempt
        # per W1108 even if reclassified):
        # - cmd_explain_command.py: ``name`` is a CLI-command name, NOT a symbol id.
        # - cmd_plugins.py:        ``name`` is a plugin name, NOT a symbol id.
        "cmd_explain_command.py",
        "cmd_plugins.py",
    }
)


_COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# AST helpers — locate ``@click.argument("name")`` decorators.
# ---------------------------------------------------------------------------


def _is_click_argument_name(decorator: ast.expr) -> bool:
    """Match ``@click.argument("name", ...)`` literal decorator calls.

    Matches BOTH single and double quoted, and is robust to additional
    keyword arguments (e.g. ``required=False, default=None``). Only the
    first positional argument's constant string value is inspected.
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
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value == "name"


def _find_click_argument_name_sites() -> set[str]:
    """Walk every ``src/roam/commands/cmd_*.py`` file and return the
    basenames of files containing ``@click.argument("name")`` decorators.

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
                if _is_click_argument_name(decorator):
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


def test_discovery_finds_grandfathered_sites():
    """Discovery must surface the grandfathered set — if it returns empty
    we're not linting anything and the negative-path test below silently
    passes. Treat the grandfathered set as the lower bound."""
    found = _find_click_argument_name_sites()
    assert found, (
        "AST discovery found zero @click.argument('name') sites. "
        "Either every grandfathered site was renamed (in which case empty "
        "_GRANDFATHERED + delete this test) or _find_click_argument_name_sites "
        "is broken — fix the AST walker."
    )


# ---------------------------------------------------------------------------
# The W1111 lint — block NEW @click.argument("name") sites.
# ---------------------------------------------------------------------------


def test_no_new_click_argument_name_drift():
    """Block new ``@click.argument('name')`` sites; canonical is ``'symbol'``
    per W1004 audit + W1102 strategy.

    The MCP wrapper alias map in ``src/roam/mcp_server.py::_PARAM_ALIASES``
    rewrites legacy ``name=...`` callers onto canonical ``symbol`` with a
    deprecation warning. New CLI commands should declare ``symbol`` directly
    so the wrapper doesn't have to alias-rewrite on every call.
    """
    found = _find_click_argument_name_sites()
    unexpected = found - _GRANDFATHERED
    assert not unexpected, (
        f"New @click.argument('name') site(s) detected: {sorted(unexpected)}.\n"
        f"Canonical argument name is 'symbol' (matches MCP wrapper alias map "
        f"in src/roam/mcp_server.py::_PARAM_ALIASES, W430).\n"
        f"See (internal memo) for Strategy D "
        f"rationale.\n"
        f"If the new site is a legitimate non-symbol 'name' concept (e.g. "
        f"CLI-command name, plugin name), exempt it explicitly in "
        f"_GRANDFATHERED with a comment justifying the carve-out."
    )


def test_grandfathered_sites_still_exist():
    """Inverse drift guard — if a grandfathered site goes away (rename
    completed, file deleted, etc.), drop it from ``_GRANDFATHERED`` so
    the lint stays accurate.

    A stale entry hides the fact that the site has been cleaned up AND
    leaves a "ghost slot" through which a regressor could re-add the
    legacy declaration without tripping the lint.
    """
    found = _find_click_argument_name_sites()
    missing = _GRANDFATHERED - found
    assert not missing, (
        f"Grandfathered @click.argument('name') site(s) gone: "
        f"{sorted(missing)}.\nRemove from _GRANDFATHERED in this test file "
        f"so the lint stays accurate. Stale entries hide regressions."
    )
