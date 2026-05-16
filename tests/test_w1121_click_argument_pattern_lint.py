"""W1121-pattern CI lint — block new ``@click.argument("pattern")`` drift.

The canonical CLI argument for the search-query concept is ``"query"`` —
matching the MCP wrapper alias map at ``src/roam/mcp_server.py::_PARAM_ALIASES``
(W1121-pattern, see ``"query": {"pattern": "query"}``). Per the W1004 audit
Cluster 1 ("query cluster") the ``"pattern"`` spelling has been ruled
non-canonical at the MCP boundary; this lint extends the same hygiene to
the CLI positional layer.

Sibling of:
  * ``tests/test_w1111_click_argument_name_lint.py``  (``"name"`` → ``"symbol"``)
  * ``tests/test_w1121_click_argument_target_lint.py`` (``"target"`` → ``"symbol"``)
  * ``tests/test_w1121_click_argument_file_lint.py``   (``"file"``/... → ``"path"``)

Important nuance — ``"pattern"`` has TWO legitimate meanings:

  - **QUERY concept** — a search query string passed to FTS5 / retrieve /
    name search. Canonical is ``"query"`` and the MCP alias map already
    normalizes inbound ``pattern=...`` callers. CLI rename candidates.
  - **REGEX concept** — an actual regex pattern compiled via ``re.compile``
    / ``re.search`` / passed to ripgrep. This is a semantically distinct
    concept from a search query; legitimate sites should use a name that
    discloses regex semantics (``"regex"`` is the current canonical — see
    ``cmd_grep`` which uses ``--regex`` for its filter). Permanent
    carve-out bucket so a future v14.0 rename pass over the QUERY bucket
    doesn't accidentally rename regex-meaning sites.

This lint:

1. Grandfathers every CURRENT site (renames would break shell scripts and
   agent prompts that pass positional arguments). The grandfathered set is
   keyed on a ``(filename, decorator_arg_name)`` tuple — matching the
   W1121-file convention — so one file may legitimately contribute multiple
   tuples and each is tracked separately. Split by QUERY / REGEX
   classification so a future v14.0 rename pass can clear out the QUERY
   bucket while leaving any REGEX carve-outs untouched.
2. Blocks a NEW site from inheriting the legacy vocabulary — new commands
   should declare ``"query"`` (when the concept is a search query) or pick
   a regex-specific name (e.g. ``"regex"``) and exempt the
   ``(filename, decorator)`` tuple in ``_REGEX_PATTERN`` with a comment
   justifying the carve-out.

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Matches
``Decorator → Call(func=Attribute(value=Name("click"), attr="argument"))``
with a first positional arg whose ``Constant.value == "pattern"``.

See: (internal memo) (W1102-RESEARCH).
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Grandfathered sites — closed set of ``(filename, decorator_arg_name)``
# tuples. Split by W1121-pattern classification:
#
#  - QUERY: positional is a search query string (FTS5 / name-search /
#    retrieve). The MCP alias map already routes inbound ``pattern=...``
#    onto canonical ``query`` — renaming the CLI positional just removes
#    the alias-rewrite hop. Rename candidates for a future v14.0 sweep.
#
#  - REGEX: positional is an actual regex pattern (compiled via re or
#    passed to ripgrep). Permanent carve-out — calling it ``"query"`` would
#    mislead callers about the match semantics. Use ``"regex"`` instead.
#    Empty on the current tree (``cmd_grep`` uses ``"positional"`` for
#    its argument and ``--regex`` for its flag-style regex input) —
#    preserved as a placeholder so the two-way classification stays
#    visible to future auditors.
# ---------------------------------------------------------------------------

# Rename candidates (v14.0): PATTERN is a search query string. MCP alias
# map already routes inbound ``pattern=...`` onto canonical ``query``.
_QUERY_PATTERN: frozenset[tuple[str, str]] = frozenset(
    {
        # FTS5-backed symbol-name search. ``pattern`` is matched against
        # the symbol-name corpus in substring/regex/exact modes — but the
        # positional carries a search *query*, not a regex. Regex is just
        # one of three match modes selected via ``--mode regex``.
        ("cmd_search.py", "pattern"),
    }
)

# Permanent carve-outs: positional is a real regex (not a search query).
# Empty on the current tree — kept as a placeholder for the two-way
# taxonomy so future regex-meaning sites can be added without re-deriving
# the classification.
_REGEX_PATTERN: frozenset[tuple[str, str]] = frozenset()

_GRANDFATHERED: frozenset[tuple[str, str]] = _QUERY_PATTERN | _REGEX_PATTERN


_COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# AST helpers — locate ``@click.argument("pattern")`` decorators.
# ---------------------------------------------------------------------------


def _is_click_argument_pattern(decorator: ast.expr) -> bool:
    """Match ``@click.argument("pattern", ...)`` literal decorator calls.

    Matches BOTH single and double quoted, and is robust to additional
    keyword arguments (e.g. ``required=False, default=None``,
    ``nargs=-1``). Only the first positional argument's constant string
    value is inspected.
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
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value == "pattern"


def _find_click_argument_pattern_sites() -> set[tuple[str, str]]:
    """Walk every ``src/roam/commands/cmd_*.py`` file and return the set of
    ``(filename, decorator_arg_name)`` tuples for matching decorators.

    AST-only — no module import. The lint stays runnable in environments
    where roam's optional dependencies (fastmcp, etc.) aren't installed.

    The tuple shape mirrors ``test_w1121_click_argument_file_lint.py`` so
    a future regex-meaning addition in the same file as a query-meaning
    declaration would be tracked as a distinct entry.
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
                if _is_click_argument_pattern(decorator):
                    found.add((path.name, "pattern"))
    return found


# ---------------------------------------------------------------------------
# Sanity guard: discovery must produce something on the current tree.
# A silent "zero sites found" would let the negative-path lint pass even if
# the AST helper regressed.
# ---------------------------------------------------------------------------


def test_discovery_finds_grandfathered_pattern_sites():
    """Discovery must surface the grandfathered set — if it returns empty
    we're not linting anything and the negative-path test below silently
    passes. Treat the grandfathered set as the lower bound."""
    found = _find_click_argument_pattern_sites()
    assert found, (
        "AST discovery found zero @click.argument('pattern') sites. "
        "Either every grandfathered site was renamed (in which case empty "
        "_GRANDFATHERED + delete this test) or "
        "_find_click_argument_pattern_sites is broken — fix the AST walker."
    )


# ---------------------------------------------------------------------------
# The W1121-pattern lint — block NEW @click.argument("pattern") sites.
# ---------------------------------------------------------------------------


def test_no_new_click_argument_pattern_drift():
    """Block new ``@click.argument('pattern')`` sites.

    Canonical for search-query positionals is ``'query'`` (matches MCP
    wrapper alias map in ``src/roam/mcp_server.py::_PARAM_ALIASES`` —
    ``"query": {"pattern": "query"}``).

    If the new site is genuinely a regex (compiled via ``re`` or passed to
    ripgrep) rather than a search query, use ``'regex'`` and exempt the
    ``(filename, decorator)`` tuple in ``_REGEX_PATTERN`` with a comment
    justifying the carve-out.
    """
    found = _find_click_argument_pattern_sites()
    unexpected = found - _GRANDFATHERED
    assert not unexpected, (
        f"New @click.argument('pattern') site(s) detected: "
        f"{sorted(unexpected)}.\n"
        f"Canonical argument name for a search-query positional is "
        f"'query' (matches MCP wrapper alias map in "
        f"src/roam/mcp_server.py::_PARAM_ALIASES — "
        f'"query": {{"pattern": "query"}}).\n'
        f"Rename the positional to 'query', OR — if the positional is "
        f"genuinely a regex (compiled via re or passed to ripgrep) "
        f"rather than a search query — rename to 'regex' and exempt the "
        f"(filename, decorator) tuple in _REGEX_PATTERN with a comment "
        f"justifying the carve-out."
    )


def test_grandfathered_pattern_sites_still_exist():
    """Inverse drift guard — if a grandfathered (filename, alias) tuple
    goes away (rename completed, file deleted, etc.), drop it from
    ``_GRANDFATHERED`` so the lint stays accurate.

    A stale entry hides the fact that the site has been cleaned up AND
    leaves a "ghost slot" through which a regressor could re-add the
    legacy declaration without tripping the lint.
    """
    found = _find_click_argument_pattern_sites()
    missing = _GRANDFATHERED - found
    assert not missing, (
        f"Grandfathered @click.argument('pattern') site(s) gone: "
        f"{sorted(missing)}.\nRemove from the appropriate sub-set "
        f"(_QUERY_PATTERN / _REGEX_PATTERN) in this test file so the "
        f"lint stays accurate. Stale entries hide regressions."
    )
