"""W1121-input-path CI lint — block new ``@click.argument("rules_file" / "rules_path" / "statement_path" / "envelope_path")`` drift.

The canonical CLI argument for a single-input-file positional is
``"input_path"`` — matching the MCP wrapper alias map at
``src/roam/mcp_server.py::_PARAM_ALIASES`` (W332), which lists the four
legacy spellings (``rules_path`` / ``rules_file`` / ``statement_path`` /
``envelope_path``) under the ``input_path`` cluster. Per the CLAUDE.md
Pattern-3b note the ``input_path`` cluster is the only alias family that
*still* has ZERO MCP-side normalization — agents calling
``roam_audit_trail_verify`` with the wrong-name silent-fail today. This
lint enforces source hygiene on the CLI side so the divergence doesn't
keep widening while the MCP-side W332 normalization lands.

Sibling of:
  * ``tests/test_w1111_click_argument_name_lint.py``     (``"name"`` → ``"symbol"``)
  * ``tests/test_w1121_click_argument_target_lint.py``   (``"target"`` → ``"symbol"``)
  * ``tests/test_w1121_click_argument_file_lint.py``     (``"file"`` / ``"filename"`` / ``"filepath"`` / ``"file_path"`` → ``"path"``)
  * ``tests/test_w1121_click_argument_pattern_lint.py``  (pattern-cluster carve-outs)

This lint:

1. Grandfathers every CURRENT site (renames would break shell scripts and
   agent prompts that pass positional arguments). The grandfathered set is
   keyed on a ``(filename, decorator_arg_name)`` tuple — one file could
   legitimately use multiple aliases, and each is a distinct drift case.
   Currently both grandfathered sites resolve to a filesystem path passed
   to ``click.Path(...)``, so they share a single PATH classification.
2. Blocks a NEW site from inheriting the legacy vocabulary — new commands
   should declare ``"input_path"`` so callers (CLI + MCP) converge on one
   spelling.

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Matches
``Decorator → Call(func=Attribute(value=Name("click"), attr="argument"))``
with a first positional arg whose ``Constant.value`` is one of the four
legacy aliases.

Out of scope (option-side / dest-side): ``@click.option("--rules-file",
"rules_file", ...)`` style sites (where the legacy spelling appears as
the option *dest* rather than as a ``@click.argument`` positional) are
NOT covered by this lint. The audit at W1121-input-path landing time
found 2 such option-dest sites (``cmd_dogfood.py``, ``cmd_pr_analyze.py``)
that warrant a separate sibling lint — see "Recommended follow-up" in
the W1121-input-path landing report. This lint deliberately stays
narrow so the AST matcher remains a one-pattern check.

See: (internal memo) (W1102-RESEARCH).
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Closed set of legacy aliases tracked by this lint. Must match the keys of
# ``_PARAM_ALIASES["input_path"]`` in ``src/roam/mcp_server.py`` (W332).
# ---------------------------------------------------------------------------

_INPUT_PATH_ALIAS_NAMES: frozenset[str] = frozenset({"rules_file", "rules_path", "statement_path", "envelope_path"})


# ---------------------------------------------------------------------------
# Grandfathered sites — closed set of ``(filename, decorator_arg_name)``
# tuples. Currently single classification:
#
#  - PATH: positional resolves to a filesystem path (passed to
#    ``click.Path()`` and read via ``Path(...).read_text(...)`` or
#    similar). Rename candidates for a future v14.0 sweep — canonical
#    is ``"input_path"``.
#
# No other classifications are populated today. If a future grandfather
# entry is added that ISN'T a single-input-file path (e.g. the positional
# is genuinely a different domain concept), introduce a second sub-set
# with a comment justifying why ``input_path`` is wrong for that site.
# ---------------------------------------------------------------------------

# Rename candidates (v14.0): positional is a single-input-file path.
_PATH_INPUT: frozenset[tuple[str, str]] = frozenset(
    {
        # @cga.command("verify"): statement_path is the CGA statement file
        # passed through click.Path(exists=True) and read via
        # Path(statement_path).read_text(...). Single-input-file shape.
        ("cmd_cga.py", "statement_path"),
        # @click.command("rules-validate"): rules_path is the .roam/rules.yml
        # file passed through click.Path() with a default and loaded via
        # _load_rules_yaml(Path(rules_path)). Single-input-file shape.
        ("cmd_rules_validate.py", "rules_path"),
    }
)

_GRANDFATHERED: frozenset[tuple[str, str]] = _PATH_INPUT


_COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# AST helpers — locate ``@click.argument("<input-path-alias>")`` decorators.
# ---------------------------------------------------------------------------


def _click_argument_input_path_alias_name(decorator: ast.expr) -> str | None:
    """Return the alias name iff this is ``@click.argument("<alias>", ...)``.

    Matches BOTH single and double quoted, and is robust to additional
    keyword arguments (e.g. ``type=click.Path(...)``, ``default=...``,
    ``required=...``). Also robust to multi-line ``@click.argument(\\n
    "alias",\\n type=...\\n)`` formatting because ``ast.Call`` matching
    is line-independent. Only the first positional argument's constant
    string value is inspected; the returned string is the literal alias
    so callers can record which spelling each site used.
    """
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr != "argument":
        return None
    value = func.value
    if not isinstance(value, ast.Name) or value.id != "click":
        return None
    if not decorator.args:
        return None
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value in _INPUT_PATH_ALIAS_NAMES:
        return first.value
    return None


def _find_click_argument_input_path_alias_sites() -> set[tuple[str, str]]:
    """Walk every ``src/roam/commands/cmd_*.py`` file and return the set of
    ``(filename, decorator_arg_name)`` tuples for matching decorators.

    AST-only — no module import. The lint stays runnable in environments
    where roam's optional dependencies (fastmcp, etc.) aren't installed.

    One file may contribute multiple tuples if it declares multiple distinct
    aliases — each is tracked separately so the grandfathered set stays
    precise about which spelling appears where.
    """
    found: set[tuple[str, str]] = set()
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
                alias = _click_argument_input_path_alias_name(decorator)
                if alias is not None:
                    found.add((path.name, alias))
    return found


# ---------------------------------------------------------------------------
# Sanity guard: discovery must produce something on the current tree.
# A silent "zero sites found" would let the negative-path lint pass even if
# the AST helper regressed.
# ---------------------------------------------------------------------------


def test_discovery_finds_grandfathered_input_path_alias_sites():
    """Discovery must surface the grandfathered set — if it returns empty
    we're not linting anything and the negative-path test below silently
    passes. Treat the grandfathered set as the lower bound."""
    found = _find_click_argument_input_path_alias_sites()
    assert found, (
        "AST discovery found zero @click.argument input-path-alias sites. "
        "Either every grandfathered site was renamed (in which case empty "
        "_GRANDFATHERED + delete this test) or "
        "_find_click_argument_input_path_alias_sites is broken — fix the "
        "AST walker."
    )


# ---------------------------------------------------------------------------
# The W1121-input-path lint — block NEW @click.argument(<alias>) sites.
# ---------------------------------------------------------------------------


def test_no_new_click_argument_input_path_alias_drift():
    """Block new ``@click.argument('rules_file'|'rules_path'|'statement_path'|'envelope_path')`` sites.

    Canonical for a single-input-file positional is ``'input_path'``
    (matches MCP wrapper alias map in
    ``src/roam/mcp_server.py::_PARAM_ALIASES``, W332).
    """
    found = _find_click_argument_input_path_alias_sites()
    unexpected = found - _GRANDFATHERED
    assert not unexpected, (
        f"New @click.argument input-path-alias site(s) detected: "
        f"{sorted(unexpected)}.\n"
        f"Canonical argument name for a single-input-file positional is "
        f"'input_path' (matches MCP wrapper alias map in "
        f"src/roam/mcp_server.py::_PARAM_ALIASES, W332).\n"
        f"Rename the positional to 'input_path', OR — if the positional "
        f"is genuinely a different domain concept rather than a single "
        f"input file — pick a distinct domain name and exempt the "
        f"(filename, decorator) tuple in a new sub-set in this lint with "
        f"a comment justifying the carve-out."
    )


def test_grandfathered_input_path_alias_sites_still_exist():
    """Inverse drift guard — if a grandfathered (filename, alias) tuple
    goes away (rename completed, file deleted, etc.), drop it from
    ``_GRANDFATHERED`` so the lint stays accurate.

    A stale entry hides the fact that the site has been cleaned up AND
    leaves a "ghost slot" through which a regressor could re-add the
    legacy declaration without tripping the lint.
    """
    found = _find_click_argument_input_path_alias_sites()
    missing = _GRANDFATHERED - found
    assert not missing, (
        f"Grandfathered @click.argument input-path-alias site(s) gone: "
        f"{sorted(missing)}.\nRemove from _PATH_INPUT in this test file so "
        f"the lint stays accurate. Stale entries hide regressions."
    )
