"""W1136 CI lint — block new ``@click.option(..., "<input-path-alias>", ...)`` dest drift.

Sibling of the W1121-input-path *argument-side* lint at
``tests/test_w1121_click_argument_input_path_lint.py``. Where the W1121
sibling covers ``@click.argument("rules_file" | "rules_path" |
"statement_path" | "envelope_path")``, this one covers the matching
``@click.option`` form:

    @click.option("--rules", "rules_file", type=click.Path(), ...)
                   ^^^^^^^^  ^^^^^^^^^^^^
                   flag      dest (legacy alias)

The canonical Click ``dest`` for a single-input-file option is
``"input_path"`` — matching the MCP wrapper alias map at
``src/roam/mcp_server.py::_PARAM_ALIASES`` (W332), which lists the four
legacy spellings (``rules_path`` / ``rules_file`` / ``statement_path`` /
``envelope_path``) under the ``input_path`` cluster. Per the CLAUDE.md
Pattern-3b note the ``input_path`` cluster is the only alias family that
*still* has ZERO MCP-side normalization — agents calling
``roam_audit_trail_verify`` with the wrong-name silent-fail today. This
lint enforces source hygiene on the CLI option side so the divergence
doesn't keep widening while the MCP-side W332 normalization lands.

Why a separate lint from W1121-input-path's ``@click.argument`` walker:
``@click.option`` decorators take the dest at ``args[1]`` (the second
positional) rather than ``args[0]`` (which is the ``--flag`` spelling).
The matcher logic differs enough that overloading the W1121-input-path
walker would hurt readability.

This lint:

1. Grandfathers every CURRENT legacy-dest site (renames would break shell
   scripts and agent prompts that pass ``--rules <path>``). The
   grandfathered set is keyed on a ``(filename, dest)`` tuple — one file
   could legitimately use multiple aliases, and each is a distinct drift
   case. Both grandfathered sites today resolve to a filesystem path
   passed to ``click.Path(...)``, so they share a single PATH
   classification.
2. Tracks the positive corpus of CURRENT canonical-dest sites — if any
   of those ever get renamed back to a legacy alias the lint catches the
   regression at the canonical-corpus assertion.
3. Blocks a NEW site from inheriting the legacy vocabulary — new commands
   should declare ``"input_path"`` so callers (CLI + MCP) converge on
   one spelling.

Discovery: AST-only walk over ``src/roam/commands/cmd_*.py``. Matches
``Decorator -> Call(func=Attribute(value=Name("click"), attr="option"))``
with a second positional arg whose ``Constant.value`` is either the
canonical ``"input_path"`` (positive corpus) or one of the four legacy
aliases (negative corpus). Only the dest is inspected — the ``--flag``
string (first positional) is irrelevant to canonicalisation.

Out of scope (argument-side): ``@click.argument("rules_file", ...)`` is
handled by ``tests/test_w1121_click_argument_input_path_lint.py``. Each
lint stays a one-pattern check.

See: (internal memo) (W1102-RESEARCH).
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Closed set of legacy aliases tracked by this lint. Must match the keys of
# ``_PARAM_ALIASES["input_path"]`` in ``src/roam/mcp_server.py`` (W332).
# Mirrors the W1121-input-path sibling lint's ``_INPUT_PATH_ALIAS_NAMES``.
# ---------------------------------------------------------------------------

_INPUT_PATH_ALIAS_NAMES: frozenset[str] = frozenset({"rules_file", "rules_path", "statement_path", "envelope_path"})


# Canonical Click ``dest`` for a single-input-file option.
_CANONICAL_INPUT_PATH_DEST: str = "input_path"


# ---------------------------------------------------------------------------
# Positive corpus — current ``@click.option(..., "input_path", ...)`` sites.
#
# These are the 6 sites that ALREADY use the canonical dest. Tracking them
# explicitly means a renamer (e.g. someone moving back to ``"rules_file"``
# to "match the help text") trips a clear lint failure naming the lost
# canonical site, not a silent regression that only surfaces when the next
# alias-cluster audit runs.
# ---------------------------------------------------------------------------

_CANONICAL_INPUT_PATH_DEST_SITES: frozenset[str] = frozenset(
    {
        "cmd_audit_trail_conformance.py",
        "cmd_audit_trail_export.py",
        "cmd_audit_trail_verify.py",
        "cmd_critique.py",
        "cmd_oracle.py",
        "cmd_suppress.py",
    }
)


# ---------------------------------------------------------------------------
# Grandfathered legacy-dest sites — closed set of ``(filename, dest)``
# tuples. Currently single classification:
#
#  - PATH: dest resolves to a filesystem path (passed to ``click.Path()``
#    and read via ``Path(...).read_text(...)`` or similar). Rename
#    candidates for a future v14.0 sweep — canonical is ``"input_path"``.
#
# No other classifications are populated today. If a future grandfather
# entry is added that ISN'T a single-input-file path (e.g. the dest is
# genuinely a different domain concept), introduce a second sub-set with
# a comment justifying why ``input_path`` is wrong for that site.
# ---------------------------------------------------------------------------

# Rename candidates (v14.0): dest holds a single-input-file path.
_PATH_INPUT: frozenset[tuple[str, str]] = frozenset(
    {
        # @dogfood.command(...): --rules / rules_file is the rules.yml file
        # passed through to pr-analyze (auto-detects .roam/rules.yml when
        # None). Single-input-file shape.
        ("cmd_dogfood.py", "rules_file"),
        # @pr_analyze.command(...): --rules / rules_file is the rules.yml
        # file path passed through click.Path(). Single-input-file shape.
        ("cmd_pr_analyze.py", "rules_file"),
    }
)


_LEGACY_INPUT_PATH_DEST_GRANDFATHERED: frozenset[tuple[str, str]] = _PATH_INPUT


_COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


# ---------------------------------------------------------------------------
# AST helpers — locate ``@click.option("--flag", "<dest>", ...)`` decorators.
# ---------------------------------------------------------------------------


def _click_option_dest_if_tracked(decorator: ast.expr) -> str | None:
    """Return the dest iff this is ``@click.option("--flag", "<tracked-dest>", ...)``.

    ``<tracked-dest>`` is either the canonical ``"input_path"`` (positive
    corpus) OR one of the four legacy aliases (negative corpus). Returns
    ``None`` for every other ``@click.option`` call (or non-option
    decorators), so callers can filter cheaply.

    Matcher rules:

    * ``decorator`` is ``ast.Call`` with ``func`` of shape
      ``ast.Attribute(value=ast.Name("click"), attr="option")``.
    * The dest is ``args[1]`` (second positional). Click also accepts
      ``@click.option("--flag")`` with no explicit dest (Click then infers
      ``flag`` automatically) — we deliberately do NOT match the inferred
      case. Inferred destinations cannot be ``"input_path"`` (would
      require ``--input-path`` flag) nor any of the four legacy aliases
      (would require e.g. ``--rules-file`` AND no second positional —
      but every current legacy site DOES pass an explicit ``rules_file``
      second positional, so the inferred case has no in-tree examples to
      lint). If a future site uses inferred dest with a legacy spelling,
      the W1121 argument-side and the option-flag spelling cluster will
      need separate guards.
    * Only the second positional argument's constant string value is
      inspected; the returned string is the literal dest so callers can
      record which spelling each site used.
    """
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr != "option":
        return None
    value = func.value
    if not isinstance(value, ast.Name) or value.id != "click":
        return None
    if len(decorator.args) < 2:
        return None
    second = decorator.args[1]
    if not (isinstance(second, ast.Constant) and isinstance(second.value, str)):
        return None
    dest = second.value
    if dest == _CANONICAL_INPUT_PATH_DEST or dest in _INPUT_PATH_ALIAS_NAMES:
        return dest
    return None


def _find_click_option_input_path_dest_sites() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Walk every ``src/roam/commands/cmd_*.py`` file and return
    ``(canonical_sites, legacy_sites)``.

    * ``canonical_sites``: tuples ``(filename, "input_path")`` for every
      ``@click.option(..., "input_path", ...)`` decorator found.
    * ``legacy_sites``: tuples ``(filename, "<legacy-alias>")`` for every
      ``@click.option(..., "<rules_file|rules_path|statement_path|envelope_path>", ...)``
      decorator found.

    AST-only — no module import. The lint stays runnable in environments
    where roam's optional dependencies (fastmcp, etc.) aren't installed.

    One file may contribute multiple tuples if it declares multiple
    distinct dests — each is tracked separately so the grandfathered
    set stays precise about which spelling appears where.
    """
    canonical: set[tuple[str, str]] = set()
    legacy: set[tuple[str, str]] = set()
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
                dest = _click_option_dest_if_tracked(decorator)
                if dest is None:
                    continue
                site = (path.name, dest)
                if dest == _CANONICAL_INPUT_PATH_DEST:
                    canonical.add(site)
                else:
                    legacy.add(site)
    return canonical, legacy


# ---------------------------------------------------------------------------
# Sanity guard: discovery must produce non-empty positive AND negative
# corpora on the current tree. A silent "zero sites found" on either side
# would let the corresponding lint pass even if the AST helper regressed.
# ---------------------------------------------------------------------------


def test_discovery_finds_canonical_and_legacy_option_dest_sites():
    """Discovery must surface BOTH the canonical positive corpus AND the
    grandfathered legacy set — if either returns empty we're not linting
    the matching direction and that test silently passes. Treat the two
    sets as the lower bound on each side."""
    canonical, legacy = _find_click_option_input_path_dest_sites()
    assert canonical, (
        "AST discovery found zero @click.option('input_path') sites. "
        "Either every canonical site was removed (in which case empty "
        "_CANONICAL_INPUT_PATH_DEST_SITES + delete this assertion) or "
        "_find_click_option_input_path_dest_sites is broken — fix the "
        "AST walker."
    )
    assert legacy, (
        "AST discovery found zero @click.option('<legacy-alias>') sites. "
        "Either every legacy site was renamed (in which case empty "
        "_LEGACY_INPUT_PATH_DEST_GRANDFATHERED + delete this assertion) "
        "or _find_click_option_input_path_dest_sites is broken — fix "
        "the AST walker."
    )


# ---------------------------------------------------------------------------
# Positive-corpus assertion — N>=6 canonical sites currently expected.
# A drop below 6 means a canonical site got renamed; surface it loudly.
# ---------------------------------------------------------------------------


def test_canonical_input_path_dest_corpus_intact():
    """The 6 canonical ``@click.option(..., 'input_path', ...)`` sites must
    still exist. If a canonical site is renamed back to a legacy alias
    the W1136 negative-path test below catches the alias but doesn't
    name the lost canonical site. This test names the missing
    canonical filename directly so the diff is obvious in CI output.
    """
    canonical, _ = _find_click_option_input_path_dest_sites()
    canonical_files = {filename for filename, _ in canonical}
    missing = _CANONICAL_INPUT_PATH_DEST_SITES - canonical_files
    assert not missing, (
        f"Canonical @click.option('input_path') site(s) gone: "
        f"{sorted(missing)}.\n"
        f"Either the file was deleted (drop from "
        f"_CANONICAL_INPUT_PATH_DEST_SITES in this test) or the dest "
        f"was renamed back to a legacy alias (revert the rename — "
        f"canonical is 'input_path', matching MCP wrapper alias map at "
        f"src/roam/mcp_server.py::_PARAM_ALIASES, W332)."
    )


# ---------------------------------------------------------------------------
# The W1136 lint — block NEW @click.option(..., "<legacy-alias>", ...) sites.
# ---------------------------------------------------------------------------


def test_no_new_legacy_input_path_dest_drift():
    """Block new ``@click.option('--flag', 'rules_file'|'rules_path'|'statement_path'|'envelope_path', ...)`` sites.

    Canonical Click ``dest`` for a single-input-file option is
    ``'input_path'`` (matches MCP wrapper alias map in
    ``src/roam/mcp_server.py::_PARAM_ALIASES``, W332).
    """
    _, legacy = _find_click_option_input_path_dest_sites()
    unexpected = legacy - _LEGACY_INPUT_PATH_DEST_GRANDFATHERED
    assert not unexpected, (
        f"New @click.option legacy-input-path-dest site(s) detected: "
        f"{sorted(unexpected)}.\n"
        f"Canonical Click dest for a single-input-file option is "
        f"'input_path' (matches MCP wrapper alias map in "
        f"src/roam/mcp_server.py::_PARAM_ALIASES, W332).\n"
        f"Rename the dest to 'input_path' (the --flag spelling stays "
        f"whatever it is — only the second positional changes), OR — "
        f"if the dest is genuinely a different domain concept rather "
        f"than a single input file — pick a distinct domain name and "
        f"exempt the (filename, dest) tuple in a new sub-set in this "
        f"lint with a comment justifying the carve-out."
    )


def test_grandfathered_legacy_input_path_dest_sites_still_exist():
    """Inverse drift guard — if a grandfathered ``(filename, dest)`` tuple
    goes away (rename completed, file deleted, etc.), drop it from
    ``_LEGACY_INPUT_PATH_DEST_GRANDFATHERED`` so the lint stays accurate.

    A stale entry hides the fact that the site has been cleaned up AND
    leaves a "ghost slot" through which a regressor could re-add the
    legacy declaration without tripping the lint.
    """
    _, legacy = _find_click_option_input_path_dest_sites()
    missing = _LEGACY_INPUT_PATH_DEST_GRANDFATHERED - legacy
    assert not missing, (
        f"Grandfathered @click.option legacy-input-path-dest site(s) "
        f"gone: {sorted(missing)}.\nRemove from _PATH_INPUT in this "
        f"test file so the lint stays accurate. Stale entries hide "
        f"regressions."
    )
