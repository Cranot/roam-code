"""W1121-file CI lint — block new ``@click.argument("file"|"filename"|"filepath"|"file_path")`` drift.

The canonical CLI argument for a positional filesystem path is ``"path"`` —
matching the MCP wrapper alias map at ``src/roam/mcp_server.py::_PARAM_ALIASES``
(W347), which lists ``file`` / ``file_path`` / ``filename`` / ``filepath``
under the ``path`` cluster. Per the W1004 audit Cluster 2 the legacy
spellings have already been ruled non-canonical at the MCP boundary; this
lint extends the same hygiene to the CLI positional layer.

Sibling of:
  * ``tests/test_w1111_click_argument_name_lint.py``  (``"name"`` → ``"symbol"``)
  * ``tests/test_w1121_click_argument_target_lint.py`` (``"target"`` → ``"symbol"``)

This lint:

1. Grandfathers every CURRENT site (renames would break shell scripts and
   agent prompts that pass positional arguments). The grandfathered set is
   keyed on a ``(filename, decorator_arg_name)`` tuple — one file may
   legitimately use multiple aliases, and each is a distinct drift case.
   Split by classification so a future v14.0 rename pass can clear out
   the PATH bucket while leaving any GLOB carve-outs untouched.
2. Blocks a NEW site from inheriting the legacy vocabulary — new commands
   should declare ``"path"`` (or, if the positional really is a glob /
   pattern rather than a path, pick a distinct domain name and exempt it
   in ``_GLOB_FILE`` with a comment).

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Matches
``Decorator → Call(func=Attribute(value=Name("click"), attr="argument"))``
with a first positional arg whose ``Constant.value`` is one of the four
aliases.

See: (internal memo) (W1102-RESEARCH).
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Closed set of legacy aliases tracked by this lint. Must match the keys of
# ``_PARAM_ALIASES["path"]`` in ``src/roam/mcp_server.py`` (W347).
# ---------------------------------------------------------------------------

_PATH_ALIAS_NAMES: frozenset[str] = frozenset({"file", "filename", "filepath", "file_path"})


# ---------------------------------------------------------------------------
# Grandfathered sites — closed set of ``(filename, decorator_arg_name)``
# tuples. Split by classification:
#
#  - PATH: positional resolves to a filesystem path (passed to Path(),
#    filesystem ops, or filtering by filename). Rename candidates for a
#    future v14.0 sweep — canonical is ``"path"``.
#
#  - GLOB: positional is a glob / pattern matched against filenames rather
#    than a single concrete path. Empty on the current tree; preserved as
#    a placeholder so the two-way classification stays visible to future
#    auditors (and so any future drive-by can extend it without re-deriving
#    the taxonomy from scratch).
# ---------------------------------------------------------------------------

# Rename candidates (v14.0): positional is a filesystem path.
_PATH_FILE: frozenset[tuple[str, str]] = frozenset(
    {
        # Path recorded into bundle["context_read"]["files_inspected"] —
        # a filesystem path string.
        ("cmd_pr_bundle.py", "file_path"),
        # Path passed to is_suppressed(suppressions, rule, file_path, ...)
        # for suppression-file matching — a filesystem path string.
        ("cmd_triage.py", "file_path"),
    }
)

# Permanent carve-outs: positional is a glob / pattern, not a single path.
# Empty on the current tree — kept as a placeholder for the two-way taxonomy.
_GLOB_FILE: frozenset[tuple[str, str]] = frozenset()

_GRANDFATHERED: frozenset[tuple[str, str]] = _PATH_FILE | _GLOB_FILE


_COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# AST helpers — locate ``@click.argument("<path-alias>")`` decorators.
# ---------------------------------------------------------------------------


def _click_argument_path_alias_name(decorator: ast.expr) -> str | None:
    """Return the alias name iff this is ``@click.argument("<path-alias>", ...)``.

    Matches BOTH single and double quoted, and is robust to additional
    keyword arguments (e.g. ``required=False, default=None``,
    ``type=click.Path(...)``). Only the first positional argument's
    constant string value is inspected; the returned string is the literal
    alias (``"file"`` / ``"filename"`` / ``"filepath"`` / ``"file_path"``)
    so callers can record which alias variant the site used.
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
    if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value in _PATH_ALIAS_NAMES:
        return first.value
    return None


def _find_click_argument_path_alias_sites() -> set[tuple[str, str]]:
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
                alias = _click_argument_path_alias_name(decorator)
                if alias is not None:
                    found.add((path.name, alias))
    return found


# ---------------------------------------------------------------------------
# Sanity guard: discovery must produce something on the current tree.
# A silent "zero sites found" would let the negative-path lint pass even if
# the AST helper regressed.
# ---------------------------------------------------------------------------


def test_discovery_finds_grandfathered_path_alias_sites():
    """Discovery must surface the grandfathered set — if it returns empty
    we're not linting anything and the negative-path test below silently
    passes. Treat the grandfathered set as the lower bound."""
    found = _find_click_argument_path_alias_sites()
    assert found, (
        "AST discovery found zero @click.argument path-alias sites. "
        "Either every grandfathered site was renamed (in which case empty "
        "_GRANDFATHERED + delete this test) or "
        "_find_click_argument_path_alias_sites is broken — fix the AST walker."
    )


# ---------------------------------------------------------------------------
# The W1121-file lint — block NEW @click.argument(<path-alias>) sites.
# ---------------------------------------------------------------------------


def test_no_new_click_argument_path_alias_drift():
    """Block new ``@click.argument('file'|'filename'|'filepath'|'file_path')`` sites.

    Canonical for filesystem-path positionals is ``'path'`` (matches MCP
    wrapper alias map in ``src/roam/mcp_server.py::_PARAM_ALIASES``, W347).
    """
    found = _find_click_argument_path_alias_sites()
    unexpected = found - _GRANDFATHERED
    assert not unexpected, (
        f"New @click.argument path-alias site(s) detected: "
        f"{sorted(unexpected)}.\n"
        f"Canonical argument name for a filesystem-path positional is "
        f"'path' (matches MCP wrapper alias map in "
        f"src/roam/mcp_server.py::_PARAM_ALIASES, W347).\n"
        f"Rename the positional to 'path', OR — if the positional is "
        f"genuinely a glob/pattern rather than a concrete path — pick a "
        f"distinct domain name and exempt the (filename, decorator) tuple "
        f"in _GLOB_FILE with a comment justifying the carve-out."
    )


def test_grandfathered_path_alias_sites_still_exist():
    """Inverse drift guard — if a grandfathered (filename, alias) tuple
    goes away (rename completed, file deleted, etc.), drop it from
    ``_GRANDFATHERED`` so the lint stays accurate.

    A stale entry hides the fact that the site has been cleaned up AND
    leaves a "ghost slot" through which a regressor could re-add the
    legacy declaration without tripping the lint.
    """
    found = _find_click_argument_path_alias_sites()
    missing = _GRANDFATHERED - found
    assert not missing, (
        f"Grandfathered @click.argument path-alias site(s) gone: "
        f"{sorted(missing)}.\nRemove from the appropriate sub-set "
        f"(_PATH_FILE / _GLOB_FILE) in this test file so the lint stays "
        f"accurate. Stale entries hide regressions."
    )
