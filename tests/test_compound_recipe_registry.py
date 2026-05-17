"""W1297 — CI lint blocking compound-recipe internal-command-name drift.

Pattern 5 of CLAUDE.md's "Six systemic anti-patterns to NEVER ship":

> Compound-recipe internal command-name drift. ``for_security_review``
> called internal ``roam vuln`` (should be ``vulns``). ``for_refactor``
> called ``roam complexity-report`` (should be ``complexity``).
> String-concat invocation is fragile; use a registry-key lookup at
> compound-init time. Both bugs ship silently because no test runs the
> compound end-to-end on the actual CLI registry.

This lint is the broad-coverage complement to ``tests/test_compound_registry.py``
(which gates the MCP-server ``_COMPOUND_REGISTRY``). It walks every
compound-recipe surface in the codebase, AST-extracts every string
literal that looks like an internal ``roam`` command name, and asserts
each one resolves against the live source-of-truth: the union of
``roam.cli._COMMANDS`` keys and ``roam.cli._DEPRECATED_COMMANDS`` keys.
Per CLAUDE.md Constraint 8, the source-of-truth is the live registry,
NOT an inline allowlist.

Discovery surfaces (the closed set of compound-recipe modules):

* ``src/roam/ask/recipes.py`` — every ``Recipe.commands[i][0]``
  (subprocess-dispatched via ``roam.ask.runner.run_recipe``).
* ``src/roam/commands/cmd_report.py`` — every ``PRESETS[name]["sections"][i]["command"][0]``
  (subprocess-dispatched via ``cmd_report._run_section``).
* ``src/roam/commands/cmd_audit.py`` /  ``cmd_dogfood.py`` /  ``cmd_permit.py``
  / ``cmd_postmortem.py`` / ``cmd_pr_analyze.py`` / ``cmd_pr_prep.py``
  / ``cmd_pr_replay.py`` / ``cmd_metrics_push.py`` — every literal
  ``runner.invoke(cli, ["--json", "<name>", ...])`` call.
* ``src/roam/mcp_server.py`` — every literal ``_run_roam(["<name>", ...])``
  call. Calls where the first arg is ``_cr("...")`` (registry-resolved)
  are skipped since the existing import-time gate already covers them.
* ``src/roam/ask/runner.py`` — the ``subprocess.run([sys.executable,
  "-m", "roam", "--json", cmd_name, ...])`` form. The ``cmd_name`` is
  bound from the recipe loop and resolves transitively through the
  recipe scan.

Extraction patterns (AST-only — no module import, so the lint runs in
environments where optional deps aren't installed):

1. ``Call(func=Name("_run_roam"), args=[List([Constant(str), ...]), ...])``
   → first element of the list is the command name (skip if the first
   element is a ``Call`` like ``_cr(...)``).
2. ``Call(func=Attribute(attr="invoke"), args=[<cli>, List([...]), ...])``
   → walk the list, strip a leading ``"--json"`` constant, then the next
   ``Constant(str)`` is the command name.
3. ``Recipe(commands=Tuple([Tuple([Constant(str), ...]), ...]))`` → for
   each inner tuple the first element is the command name. Extracted by
   parsing ``RECIPES`` in ``ask/recipes.py``.
4. ``PRESETS = {<preset>: {"sections": [{"command": [Constant(str), ...]}, ...]}}``
   → first element of every ``"command"`` list. Extracted by parsing
   ``cmd_report.py``.

Synthetic-break verification: see the failing-fixture test at the
bottom — patch ``cmd_for_refactor`` to call ``"complexity-report"``
and the lint must surface it.
"""

from __future__ import annotations

import ast
import difflib
import pathlib
from typing import Iterator

import pytest

# ---------------------------------------------------------------------------
# Source-of-truth: the live CLI command registry. Per CLAUDE.md Constraint 8
# (closed enumeration), this is the single set of valid command names. We do
# not inline an allowlist.
# ---------------------------------------------------------------------------
from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS  # noqa: E402

VALID_COMMAND_NAMES: frozenset[str] = frozenset(_COMMANDS.keys()) | frozenset(_DEPRECATED_COMMANDS.keys())


# ---------------------------------------------------------------------------
# Closed set of files this lint scans. Adding a new compound-recipe surface
# means adding it here (an explicit edit, not silent inheritance) — keeps the
# lint scoped and gives reviewers a deterministic blast radius for the lint.
# ---------------------------------------------------------------------------

from tests._helpers.repo_root import repo_root

_REPO_ROOT = repo_root()
_SRC = _REPO_ROOT / "src" / "roam"

# Modules that issue compound-recipe subprocess / in-process invocations
# via at least one literal command-name string. Two further compound-recipe
# files — ``src/roam/ask/runner.py`` and ``src/roam/commands/cmd_report.py``
# — are covered exclusively by the specialized parsers below, because their
# invocations are bound through a loop variable (``cmd_name`` from the
# recipe DAG / ``section["command"]`` from the preset table) and contain
# zero literal command-name strings at the call site. Treat the
# specialized parsers as their lint coverage; do NOT re-add them here.
_COMPOUND_INVOKERS: tuple[pathlib.Path, ...] = (
    _SRC / "commands" / "cmd_audit.py",
    _SRC / "commands" / "cmd_dogfood.py",
    _SRC / "commands" / "cmd_metrics_push.py",
    _SRC / "commands" / "cmd_permit.py",
    _SRC / "commands" / "cmd_postmortem.py",
    _SRC / "commands" / "cmd_pr_analyze.py",
    _SRC / "commands" / "cmd_pr_prep.py",
    _SRC / "commands" / "cmd_pr_replay.py",
    _SRC / "mcp_server.py",
)

# Specialized parsers — recipe tuples + report PRESETS. These two
# files use loop-bound dispatch so the lint coverage is the table
# of literals, not the subprocess call site.
_RECIPES_FILE = _SRC / "ask" / "recipes.py"
_REPORT_FILE = _SRC / "commands" / "cmd_report.py"


# ---------------------------------------------------------------------------
# AST helpers.
# ---------------------------------------------------------------------------


def _first_command_name_from_list(elts: list[ast.expr]) -> str | None:
    """Walk an argv-shaped list, skip any leading ``"--json"`` constant,
    return the next string constant if any.

    Returns ``None`` when no string constant precedes the first option
    flag (a token starting with ``-`` that isn't ``--json``) — in that
    case the call isn't a recognisable command invocation.
    """
    # Strip the ``sys.executable, "-m", "roam"`` prefix when present (the
    # ``subprocess.run([sys.executable, "-m", "roam", "--json", "<cmd>", ...])``
    # form used by ``cmd_report._run_section`` and ``ask/runner.run_recipe``).
    idx = 0
    if (
        len(elts) >= 3
        and isinstance(elts[0], ast.Attribute)
        and isinstance(elts[0].value, ast.Name)
        and elts[0].value.id == "sys"
        and elts[0].attr == "executable"
        and isinstance(elts[1], ast.Constant)
        and elts[1].value == "-m"
        and isinstance(elts[2], ast.Constant)
        and elts[2].value == "roam"
    ):
        idx = 3

    # Strip any leading ``--json`` (canonical JSON-mode flag at the front
    # of every compound invocation).
    while idx < len(elts) and isinstance(elts[idx], ast.Constant) and elts[idx].value == "--json":
        idx += 1

    if idx >= len(elts):
        return None
    head = elts[idx]
    # Skip Call expressions (e.g. ``_cr("complexity")`` — already gated by
    # the import-time check in mcp_server._verify_compound_registry).
    if isinstance(head, ast.Call):
        return None
    # Skip Starred (``*args``), Name (variable references — caller-supplied),
    # and non-string constants. The lint cannot enforce a value it can't
    # see at the literal source level.
    if not isinstance(head, ast.Constant) or not isinstance(head.value, str):
        return None
    value = head.value
    # If the first non-flag token starts with ``-`` it's an option, not a
    # command name (e.g. mistakenly-built argv with no command).
    if value.startswith("-"):
        return None
    return value


def _discover_wrapper_helpers(tree: ast.AST) -> set[str]:
    """Return the set of ``def <name>(args, ...)`` helpers in *tree* whose
    bodies issue a recognised compound-recipe invocation.

    Several compound-recipe modules wrap ``runner.invoke(cli, ["--json",
    *args])`` (or the ``subprocess.run([sys.executable, "-m", "roam", ...])``
    form) inside a single private helper (``_capture``, ``_run_subcommand``,
    ``_capture_json_subcommand``, ``_run_section``, ...). The literal
    command name lives at the wrapper-CALL site rather than at the
    ``runner.invoke`` site itself, so the lint must follow one indirection
    to extract it.

    Discovery is per-module — a wrapper in ``cmd_audit.py`` doesn't make
    ``_capture(...)`` calls in a different module land in the lint. That
    keeps the false-positive surface small.
    """
    helpers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Helper must take a positional ``args`` parameter — that's the
        # canonical shape across all observed wrappers.
        positional = [a.arg for a in node.args.args]
        if not positional:
            continue
        for inner in ast.walk(node):
            if inner is node or not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Attribute) and func.attr == "invoke":
                helpers.add(node.name)
                break
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id in {"subprocess", "_subprocess"}
            ):
                # Only treat ``subprocess.run`` as a wrapper-tell when the
                # body argv begins with ``sys.executable, "-m", "roam"``.
                if inner.args and isinstance(inner.args[0], ast.List):
                    elts = inner.args[0].elts
                    if (
                        len(elts) >= 3
                        and isinstance(elts[0], ast.Attribute)
                        and isinstance(elts[0].value, ast.Name)
                        and elts[0].value.id == "sys"
                        and elts[0].attr == "executable"
                        and isinstance(elts[1], ast.Constant)
                        and elts[1].value == "-m"
                        and isinstance(elts[2], ast.Constant)
                        and elts[2].value == "roam"
                    ):
                        helpers.add(node.name)
                        break
    return helpers


def _iter_invocation_calls(
    tree: ast.AST, *, wrapper_names: frozenset[str] = frozenset()
) -> Iterator[tuple[str, ast.AST]]:
    """Yield ``(call_kind, node)`` pairs for every recognised
    compound-recipe invocation in *tree*.

    ``call_kind`` is one of ``"_run_roam"``, ``"runner.invoke"``,
    ``"subprocess.run"``, ``"wrapper"``, or ``"args_assign"`` (the five
    invocation shapes we extract command-name literals from):

    * ``"_run_roam"`` — ``_run_roam([...])`` direct call.
    * ``"runner.invoke"`` — ``runner.invoke(cli, [...])`` direct call.
    * ``"subprocess.run"`` — ``subprocess.run([sys.executable, "-m",
      "roam", ...])`` direct call.
    * ``"wrapper"`` — module-local wrapper helper (``_capture([...])``)
      discovered via :func:`_discover_wrapper_helpers`.
    * ``"args_assign"`` — ``args = ["--json", "<cmd>", ...]`` assignment
      followed downstream by ``runner.invoke(cli, args)``. cmd_pr_analyze
      builds argv incrementally via ``.append()`` / ``.extend()`` after a
      literal seed; the seed is what we lint.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # ``args = ["--json", "<cmd>", ...]``
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "args"
                and isinstance(node.value, ast.List)
            ):
                elts = node.value.elts
                # Only treat as a roam-argv seed when the literal contains
                # ``"--json"`` somewhere up front (rules out unrelated
                # ``args = [...]`` assignments).
                has_json = any(isinstance(e, ast.Constant) and e.value == "--json" for e in elts[:3])
                if has_json:
                    yield ("args_assign", node)
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # ``_run_roam(...)`` — bare name.
        if isinstance(func, ast.Name) and func.id == "_run_roam":
            yield ("_run_roam", node)
            continue
        # ``runner.invoke(...)`` — attribute on a ``runner`` binding (Click
        # CliRunner). We don't care which receiver — only the ``.invoke``.
        if isinstance(func, ast.Attribute) and func.attr == "invoke":
            yield ("runner.invoke", node)
            continue
        # ``subprocess.run(...)`` — attribute on ``subprocess`` or
        # ``_subprocess`` (the latter is the alias used in cmd_pr_replay).
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and isinstance(func.value, ast.Name)
            and func.value.id in {"subprocess", "_subprocess"}
        ):
            yield ("subprocess.run", node)
            continue
        # Module-local wrapper helpers — ``_capture([...])`` and friends.
        if isinstance(func, ast.Name) and func.id in wrapper_names:
            yield ("wrapper", node)


def _extract_command_name_from_call(kind: str, node: ast.AST) -> str | None:
    """Pull the command-name string literal from a compound-invocation node.

    Returns ``None`` if the call doesn't have a literal-list argv (caller-
    supplied args, kwargs-only forms, etc.) — those are out of scope for
    the lint by construction.
    """
    if kind == "args_assign":
        # ``args = ["--json", "<cmd>", ...]`` — extract from the RHS list.
        assert isinstance(node, ast.Assign) and isinstance(node.value, ast.List)
        return _first_command_name_from_list(node.value.elts)
    assert isinstance(node, ast.Call)
    call = node
    if kind == "_run_roam":
        # _run_roam(args=[...], root=".")  — first positional is the list.
        if not call.args or not isinstance(call.args[0], ast.List):
            return None
        return _first_command_name_from_list(call.args[0].elts)
    if kind == "runner.invoke":
        # runner.invoke(cli, [...], ...) — second positional is the argv.
        if len(call.args) < 2 or not isinstance(call.args[1], ast.List):
            return None
        return _first_command_name_from_list(call.args[1].elts)
    if kind == "subprocess.run":
        # subprocess.run([...], ...) — first positional is the argv. Only
        # pick up calls whose argv begins with ``sys.executable, "-m", "roam"``
        # so we don't false-positive on ``["git", ...]`` and friends.
        if not call.args or not isinstance(call.args[0], ast.List):
            return None
        elts = call.args[0].elts
        if not (
            len(elts) >= 3
            and isinstance(elts[0], ast.Attribute)
            and isinstance(elts[0].value, ast.Name)
            and elts[0].value.id == "sys"
            and elts[0].attr == "executable"
            and isinstance(elts[1], ast.Constant)
            and elts[1].value == "-m"
            and isinstance(elts[2], ast.Constant)
            and elts[2].value == "roam"
        ):
            return None
        return _first_command_name_from_list(elts)
    if kind == "wrapper":
        # Wrapper-helper call (``_capture([...])``, ``_run_subcommand([...])``,
        # ...) — first positional is the argv. Same shape as ``_run_roam``
        # but routed through a private helper that adds ``--json`` itself.
        if not call.args or not isinstance(call.args[0], ast.List):
            return None
        return _first_command_name_from_list(call.args[0].elts)
    return None


def _scan_compound_invokers() -> list[tuple[pathlib.Path, str, int]]:
    """Return ``(path, command_name, lineno)`` triples for every literal
    compound invocation across the closed module list.

    Each module is scanned twice: first for wrapper-helper definitions
    (``def _capture(args): ... runner.invoke(cli, ["--json", *args])``),
    then for the full set of recognised invocation shapes (including
    calls to the wrappers discovered in step 1).
    """
    out: list[tuple[pathlib.Path, str, int]] = []
    for path in _COMPOUND_INVOKERS:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        wrapper_names = frozenset(_discover_wrapper_helpers(tree))
        for kind, call in _iter_invocation_calls(tree, wrapper_names=wrapper_names):
            name = _extract_command_name_from_call(kind, call)
            if name is None:
                continue
            out.append((path, name, call.lineno))
    return out


# ---------------------------------------------------------------------------
# Recipe registry — parse ``ask/recipes.py`` for ``Recipe(commands=((...,
# (...,)), ...))`` and extract the first element of every inner tuple.
# ---------------------------------------------------------------------------


def _scan_recipe_commands() -> list[tuple[pathlib.Path, str, int]]:
    """Return ``(path, command_name, lineno)`` triples for every recipe
    command in ``ask/recipes.py``."""
    out: list[tuple[pathlib.Path, str, int]] = []
    try:
        source = _RECIPES_FILE.read_text(encoding="utf-8")
    except OSError:
        return out
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        # Recipe(commands=(...,))
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Recipe"):
            continue
        for kw in node.keywords:
            if kw.arg != "commands":
                continue
            if not isinstance(kw.value, ast.Tuple):
                continue
            for inner in kw.value.elts:
                if not isinstance(inner, ast.Tuple) or not inner.elts:
                    continue
                head = inner.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    out.append((_RECIPES_FILE, head.value, head.lineno))
    return out


# ---------------------------------------------------------------------------
# Report presets — parse ``cmd_report.PRESETS`` for ``{"command": [<name>,
# ...]}`` entries and extract the first list element.
# ---------------------------------------------------------------------------


def _scan_report_presets() -> list[tuple[pathlib.Path, str, int]]:
    """Return ``(path, command_name, lineno)`` triples for every section
    command in ``cmd_report.PRESETS``."""
    out: list[tuple[pathlib.Path, str, int]] = []
    try:
        source = _REPORT_FILE.read_text(encoding="utf-8")
    except OSError:
        return out
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        # PRESETS = {<name>: {"sections": [{"command": [...]}, ...]}}
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "PRESETS"):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for preset_value in node.value.values:
            if not isinstance(preset_value, ast.Dict):
                continue
            for k, v in zip(preset_value.keys, preset_value.values):
                if not (isinstance(k, ast.Constant) and k.value == "sections"):
                    continue
                if not isinstance(v, ast.List):
                    continue
                for section in v.elts:
                    if not isinstance(section, ast.Dict):
                        continue
                    for sk, sv in zip(section.keys, section.values):
                        if not (isinstance(sk, ast.Constant) and sk.value == "command"):
                            continue
                        if not isinstance(sv, ast.List) or not sv.elts:
                            continue
                        head = sv.elts[0]
                        if isinstance(head, ast.Constant) and isinstance(head.value, str):
                            out.append((_REPORT_FILE, head.value, head.lineno))
    return out


def _all_referenced_names() -> list[tuple[pathlib.Path, str, int]]:
    """Aggregate every compound-recipe command-name reference found by the
    three extraction passes."""
    return _scan_compound_invokers() + _scan_recipe_commands() + _scan_report_presets()


# ---------------------------------------------------------------------------
# Discovery sanity: the lint is worthless if no extraction sites are found —
# a silent regression in the AST helpers would let the negative-path test
# pass while no actual checking happens.
# ---------------------------------------------------------------------------


def test_discovery_finds_compound_recipe_invocations() -> None:
    """The lint must find SOMETHING on the current tree.

    Both the ask recipe registry and the report PRESETS are guaranteed to
    contain valid command-name references; if the extractor returns empty
    the AST helpers have regressed.
    """
    refs = _all_referenced_names()
    assert refs, (
        "Compound-recipe scan found zero references — either every compound "
        "module was deleted (in which case retire this lint) or the AST "
        "extractors have regressed. Re-check _iter_invocation_calls and the "
        "two specialized parsers."
    )
    # The recipe registry alone should contribute >= 10 hits.
    recipe_hits = _scan_recipe_commands()
    assert len(recipe_hits) >= 10, (
        f"Recipe-commands extractor only found {len(recipe_hits)} entries — _scan_recipe_commands has likely regressed."
    )


# ---------------------------------------------------------------------------
# The W1297 lint — every referenced name must resolve to a live CLI command.
# ---------------------------------------------------------------------------


def test_every_compound_recipe_command_name_resolves() -> None:
    """Every literal internal-command name referenced by a compound
    recipe must be in ``cli._COMMANDS`` or ``cli._DEPRECATED_COMMANDS``.

    Catches the ``vuln``/``vulns``-class typo BEFORE it ships, per
    CLAUDE.md Pattern 5. The error message names the file + line + the
    closest valid match (difflib) so the fix is mechanical.
    """
    refs = _all_referenced_names()
    invalid: list[str] = []
    for path, name, lineno in refs:
        if name in VALID_COMMAND_NAMES:
            continue
        suggestions = difflib.get_close_matches(name, sorted(VALID_COMMAND_NAMES), n=2, cutoff=0.6)
        hint = f" Did you mean: {' or '.join(repr(s) for s in suggestions)}?" if suggestions else ""
        rel = path.relative_to(_REPO_ROOT)
        invalid.append(f"  {rel}:{lineno} references {name!r} which is not in cli._COMMANDS.{hint}")
    assert not invalid, (
        "Compound-recipe internal-command-name drift detected (CLAUDE.md "
        "Pattern 5). One or more referenced command names do not resolve "
        "to a live CLI command in roam.cli._COMMANDS or _DEPRECATED_COMMANDS:\n"
        + "\n".join(invalid)
        + "\n\nFix: change the literal to a name in _COMMANDS, OR add the "
        "missing command. Source-of-truth is the live registry (CLAUDE.md "
        "Constraint 8: closed enumeration over free string composition)."
    )


# ---------------------------------------------------------------------------
# Synthetic-break harness — verify the lint actually catches a typo.
#
# We don't mutate the real tree (the parent task forbids leaving a synthetic
# break in place). Instead we exercise the helpers against an in-memory
# AST that *looks* like a compound-invocation site with a known-bad name.
# ---------------------------------------------------------------------------


def test_lint_catches_synthetic_typo() -> None:
    """Feed a synthetic ``_run_roam(["complexity-report", "x"])`` call to
    the extractor + resolver — the lint must surface ``"complexity-report"``
    as invalid (the W1297 historical typo, now reserved as a regression
    probe).

    This is the in-source equivalent of patching a compound recipe to
    re-introduce the typo, then running the lint to confirm it fails.
    Keeping the synthetic-break inside the test process means the
    regression probe runs every time the test suite runs — no manual
    "remember to revert" step.
    """
    snippet = '_run_roam(["complexity-report", "x"], root=".")'
    tree = ast.parse(snippet, mode="eval")
    found_names: list[str] = []
    for kind, call in _iter_invocation_calls(tree):
        name = _extract_command_name_from_call(kind, call)
        if name is not None:
            found_names.append(name)
    assert found_names == ["complexity-report"], (
        f"Extractor failed to pick up the synthetic _run_roam call: got {found_names!r}"
    )
    assert "complexity-report" not in VALID_COMMAND_NAMES, (
        "'complexity-report' unexpectedly appeared in _COMMANDS — the "
        "synthetic-break harness is no longer probing a typo. Pick a new "
        "known-bad token (the W1297 dogfood corpus also names 'vuln')."
    )


def test_lint_catches_synthetic_runner_invoke_typo() -> None:
    """Companion synthetic break for the ``runner.invoke(cli, [...])`` shape —
    the other compound-invocation pattern. Exercises the second branch of
    ``_extract_command_name_from_call``."""
    snippet = 'runner.invoke(cli, ["--json", "vuln", "--sarif"])'
    tree = ast.parse(snippet, mode="eval")
    found_names: list[str] = []
    for kind, call in _iter_invocation_calls(tree):
        name = _extract_command_name_from_call(kind, call)
        if name is not None:
            found_names.append(name)
    assert found_names == ["vuln"], f"Extractor failed to pick up the synthetic runner.invoke call: got {found_names!r}"
    assert "vuln" not in VALID_COMMAND_NAMES, (
        "'vuln' unexpectedly appeared in _COMMANDS — pick a new known-bad token for the synthetic-break harness."
    )


# ---------------------------------------------------------------------------
# Coverage sanity — every closed-set invoker module must contribute at
# least one hit. If a module suddenly contributes zero, either it lost all
# its compound invocations (retire it from _COMPOUND_INVOKERS) or its
# invocation shape changed (extend the extractor).
# ---------------------------------------------------------------------------


def test_every_listed_invoker_module_has_at_least_one_hit() -> None:
    """Per-module discovery probe — keeps _COMPOUND_INVOKERS honest."""
    refs = _scan_compound_invokers()
    by_module: dict[pathlib.Path, int] = {p: 0 for p in _COMPOUND_INVOKERS}
    for path, _name, _lineno in refs:
        if path in by_module:
            by_module[path] += 1
    silent = [p.relative_to(_REPO_ROOT) for p, count in by_module.items() if count == 0]
    if silent:
        pytest.fail(
            "_COMPOUND_INVOKERS lists modules that produced ZERO compound-invocation "
            "extractions:\n  "
            + "\n  ".join(str(s) for s in silent)
            + "\nEither remove the module from _COMPOUND_INVOKERS or update the "
            "extractor to match its actual invocation shape."
        )
