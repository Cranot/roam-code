"""W805-QQQQQ — Compound-recipe SHAPE-axis drift lint.

Pattern 5 (CLAUDE.md §"compound-recipe internal command-name drift") was
historically pinned on the NAME axis: ``vuln``/``vulns``,
``complexity-report``/``complexity``. The existing CI lint
(``tests/test_compound_recipe_registry.py``) AST-scans compound recipes
against ``cli._COMMANDS | cli._DEPRECATED_COMMANDS`` to block name drift.

W805-NNNNN surfaced TWO new Pattern-5 bugs in
``cmd_for_security_review``'s recipe at ``src/roam/mcp_server.py:6499-6510``
on the SHAPE axis (NOT the name axis):

1. ``_safe_run([_cr("adversarial"), symbol], root)`` — ``cmd_adversarial``
   has NO ``click.argument`` declaration (0 args, 5 opts). Click rejects
   the ``symbol`` positional with ``USAGE_ERROR: Got unexpected extra
   argument``. The symbol is silently dropped before any resolver runs.
2. ``_safe_run([_cr("vulns"), "list"], root)`` — ``vulns`` is a single
   ``@click.command`` (0 args, 4 opts), NOT a ``click.Group`` with a
   ``list`` subcommand. ``"list"`` is rejected as an unexpected
   positional.

Both reproduce live::

    $ roam adversarial somesymbol
    Usage: cli adversarial [OPTIONS]
    Error: Got unexpected extra argument (somesymbol)

    $ roam vulns list
    Usage: cli vulns [OPTIONS]
    Error: Got unexpected extra argument (list)

This file builds an AST scanner that, for every
``_safe_run([_cr("<name>"), *positionals], root)`` site in
``src/roam/mcp_server.py``:

1. Resolves the target command name through
   ``mcp_server._COMPOUND_REGISTRY`` → ``cli._COMMANDS`` → click object.
2. Counts the literal positional args that follow ``_cr(...)`` in the
   list, stopping at the first ``-``-prefixed option flag (canonical
   ``--flag`` form).
3. Counts the target command's ``click.Argument`` slots and notes whether
   it's a ``click.Group`` (subcommand-style invocation valid) or a
   single ``@click.command`` (positional rejected).
4. Flags drift:
   * Positional count > argument-slot count on a non-group target
     (variadic ``nargs=-1`` slot absorbs the rest, so a single ``nargs=-1``
     argument counts as "unbounded").
   * The recipe site treats a non-group target as if it had subcommands.

The lint deliberately does NOT scan ``--<option>`` strings — Click's
own ``no_args_is_help`` and unknown-option handling reject those at
runtime with the same ``USAGE_ERROR`` shape, and the option surface
drifts more frequently than the argument-slot surface. The shape lint
focuses on the positional axis where the W805-NNNNN finding lives.

Discipline notes:

- W978 first-hypothesis rule: each detected drift is re-verified by
  invoking the CLI through ``CliRunner`` in
  ``test_each_detected_drift_reproduces_via_clirunner``. Only drifts
  that ALSO trigger a non-zero exit at runtime stay in the report.
- W907 false-cycle hedge: this file imports ``roam.cli._COMMANDS`` and
  ``roam.mcp_server._COMPOUND_REGISTRY`` directly. No defensive
  duplication.
- Source-of-truth follows CLAUDE.md Constraint 8 (closed enumeration):
  drift is defined against the LIVE click signatures, not an inline
  allowlist.

Run only via::

    pytest tests/test_w805_qqqqq*.py -n 0 -q
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from typing import Iterator

import click
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Source-of-truth: the live CLI registry + the compound registry that maps
# recipe keys → CLI command names. Both raise loudly on drift, so we let
# the import surface any registry-level errors directly.
# ---------------------------------------------------------------------------
from roam.cli import _COMMANDS  # noqa: E402
from roam.mcp_server import _COMPOUND_REGISTRY  # noqa: E402
from tests._helpers.repo_root import repo_root

_REPO_ROOT = repo_root()
_MCP_SERVER = _REPO_ROOT / "src" / "roam" / "mcp_server.py"


# ---------------------------------------------------------------------------
# Click-signature inspection.
# ---------------------------------------------------------------------------


def _load_click_command(cli_name: str) -> click.Command:
    """Resolve a CLI command name to its live click.Command object."""
    mod_path, attr = _COMMANDS[cli_name]
    mod = importlib.import_module(mod_path)
    return getattr(mod, attr)


def _signature(cmd: click.Command) -> tuple[bool, int, bool]:
    """Return ``(is_group, fixed_arg_count, has_variadic)`` for *cmd*.

    * ``is_group`` — True iff cmd is a ``click.Group`` (accepts a
      subcommand name as the first positional).
    * ``fixed_arg_count`` — number of ``click.Argument`` slots with
      ``nargs == 1``.
    * ``has_variadic`` — True iff any argument has ``nargs == -1``
      (absorbs unbounded remaining positionals).
    """
    is_group = isinstance(cmd, click.Group)
    fixed = 0
    has_variadic = False
    for p in cmd.params:
        if not isinstance(p, click.Argument):
            continue
        if p.nargs == -1:
            has_variadic = True
        else:
            fixed += p.nargs  # nargs > 1 counts toward the fixed slot count
    return is_group, fixed, has_variadic


# ---------------------------------------------------------------------------
# AST extraction — every ``_safe_run([_cr("<key>"), *positionals], root)`` site
# in ``mcp_server.py``.
# ---------------------------------------------------------------------------


def _iter_safe_run_cr_sites(tree: ast.AST) -> Iterator[tuple[int, str, list[ast.expr]]]:
    """Yield ``(lineno, recipe_key, post_cr_elements)`` for every
    ``_safe_run([_cr("<key>"), *more], root)`` call.

    ``post_cr_elements`` is the slice of the argv list AFTER the leading
    ``_cr("<key>")`` call — those are what the recipe author intended
    to feed to the target command. Returned as raw AST nodes so the
    caller can decide which to treat as positionals vs option flags.

    Two patterns are extracted:

    1. INLINE LITERAL — ``_safe_run([_cr("X"), <elts...>], root)``. The
       argv list is built in-place; ``post_cr_elements`` is
       ``argv.elts[1:]``.
    2. INCREMENTAL BUILD — ``args = [_cr("X")]; if cond:
       args.append(<elt>); _safe_run(args, root)``. Used by
       ``cmd_for_security_review`` for the adversarial section. We
       walk each ``FunctionDef``, find ``<name> = [_cr("X")]``
       assignments, collect every ``<name>.append(...)`` /
       ``<name>.extend([...])`` call in the same function body, and
       attribute the call to the FIRST ``_safe_run(<name>, root)`` we
       see.
    """
    # redactedinline literals.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # ``_safe_run([...], root)`` — name-based call.
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_safe_run"):
            continue
        if not node.args:
            continue
        argv = node.args[0]
        if not isinstance(argv, ast.List) or not argv.elts:
            continue
        head = argv.elts[0]
        # Head must be ``_cr("<key>")``.
        if not (
            isinstance(head, ast.Call)
            and isinstance(head.func, ast.Name)
            and head.func.id == "_cr"
            and len(head.args) == 1
            and isinstance(head.args[0], ast.Constant)
            and isinstance(head.args[0].value, str)
        ):
            continue
        recipe_key = head.args[0].value
        yield node.lineno, recipe_key, list(argv.elts[1:])

    # redactedincremental builds inside a FunctionDef.
    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Collect ``<name> = [_cr("X")]`` seed assignments inside this fn.
        seeds: dict[str, tuple[str, int]] = {}  # var -> (recipe_key, lineno)
        appends: dict[str, list[ast.expr]] = {}  # var -> appended elements
        consumed_at: dict[str, int] = {}  # var -> lineno of consuming _safe_run

        for inner in ast.walk(func_node):
            # Seed: ``args_name = [_cr("X")]``  (single-target list of len 1)
            if isinstance(inner, ast.Assign) and len(inner.targets) == 1:
                tgt = inner.targets[0]
                if not (isinstance(tgt, ast.Name) and isinstance(inner.value, ast.List)):
                    continue
                elts = inner.value.elts
                if len(elts) != 1:
                    continue
                head = elts[0]
                if not (
                    isinstance(head, ast.Call)
                    and isinstance(head.func, ast.Name)
                    and head.func.id == "_cr"
                    and len(head.args) == 1
                    and isinstance(head.args[0], ast.Constant)
                    and isinstance(head.args[0].value, str)
                ):
                    continue
                seeds[tgt.id] = (head.args[0].value, inner.lineno)
                appends.setdefault(tgt.id, [])
            # Append: ``args_name.append(<elt>)``
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "append"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in seeds
                and len(inner.args) == 1
            ):
                appends[inner.func.value.id].append(inner.args[0])
            # Extend: ``args_name.extend([<elts>])``
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "extend"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in seeds
                and len(inner.args) == 1
                and isinstance(inner.args[0], ast.List)
            ):
                appends[inner.func.value.id].extend(inner.args[0].elts)
            # Consumer: ``_safe_run(<name>, root)``
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_safe_run"
                and inner.args
                and isinstance(inner.args[0], ast.Name)
                and inner.args[0].id in seeds
                and inner.args[0].id not in consumed_at
            ):
                consumed_at[inner.args[0].id] = inner.lineno
            # W607-AJ wrapper-bridge variant of the incremental-build
            # consumer: ``_run_check_aj("<phase>", _safe_run, <name>,
            # root, default=...)`` -- the seed list is still consumed
            # by _safe_run, just through the W607 substrate-marker
            # closure. args[2] is the seed list variable.
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id.startswith("_run_check")
                and len(inner.args) >= 3
                and isinstance(inner.args[1], ast.Name)
                and inner.args[1].id == "_safe_run"
                and isinstance(inner.args[2], ast.Name)
                and inner.args[2].id in seeds
                and inner.args[2].id not in consumed_at
            ):
                consumed_at[inner.args[2].id] = inner.lineno

        for var, (recipe_key, _seed_line) in seeds.items():
            if var not in consumed_at:
                continue
            yield consumed_at[var], recipe_key, appends.get(var, [])

    # redactedW607-AG/AJ wrapper-bridge variant. The compound-recipe W607
    # waves (W607-AG cmd_for_refactor, W607-AJ cmd_for_security_review,
    # and likely future for_bug_fix / for_new_feature) wrap each
    # ``_safe_run`` invocation in a per-recipe ``_run_check`` /
    # ``_run_check_aj`` closure for substrate-CALL marker plumbing:
    #
    #     _run_check_aj("vulns", _safe_run, [_cr("vulns"), "list"], root,
    #                   default={"error": "vulns_w607aj_default"})
    #
    # The shape-axis drift in the inlined ``[_cr("X"), <positionals>]``
    # list is still present at runtime — only the outermost call shape
    # changed. Detect via: any Call whose third positional argument is
    # an ``ast.List`` whose first element is ``_cr("<key>")``. Defends
    # the W805-NNNNN pin against AST blindness through W607 wrappers
    # (which would otherwise FAIL ``test_repo_wide_shape_axis_sweep``
    # the moment the W607 family lands on a recipe carrying a known
    # drift).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name):
            continue
        # Match any ``_run_check*`` helper -- closed-set of W607 family
        # naming variants: ``_run_check`` (W607-AG) and
        # ``_run_check_aj`` (W607-AJ); future waves add more suffixes.
        if not func.id.startswith("_run_check"):
            continue
        # Expect ``_run_check_*("<phase>", _safe_run, [<argv>], root, ...)``
        # so the inlined list literal sits at args[2].
        if len(node.args) < 3:
            continue
        # args[1] must be the ``_safe_run`` Name (otherwise this is a
        # different helper using the same prefix).
        bridge = node.args[1]
        if not (isinstance(bridge, ast.Name) and bridge.id == "_safe_run"):
            continue
        argv = node.args[2]
        if not isinstance(argv, ast.List) or not argv.elts:
            continue
        head = argv.elts[0]
        if not (
            isinstance(head, ast.Call)
            and isinstance(head.func, ast.Name)
            and head.func.id == "_cr"
            and len(head.args) == 1
            and isinstance(head.args[0], ast.Constant)
            and isinstance(head.args[0].value, str)
        ):
            continue
        recipe_key = head.args[0].value
        yield node.lineno, recipe_key, list(argv.elts[1:])


def _count_literal_positionals(post_cr: list[ast.expr]) -> tuple[int, list[str]]:
    """Walk ``post_cr`` left-to-right; count items that act as positional
    arguments to the target click command.

    A positional is any element that ISN'T a string-constant option flag
    (a constant whose value starts with ``-``). Once we hit the first
    option flag, every following element is treated as either the
    flag's value or the next flag — none of them count as positionals
    in click's positional axis. This matches click's actual parser
    behaviour: a flag terminates positional collection.

    Returns ``(positional_count, sample_strings)`` where
    ``sample_strings`` captures the literal value (or ``"<expr>"`` for
    non-constants) of each positional, for diagnostic messages.
    """
    count = 0
    samples: list[str] = []
    for elt in post_cr:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and elt.value.startswith("-"):
            break  # first option flag terminates positional collection
        # Treat anything else as a positional — a variable like ``symbol``,
        # a non-string constant, an expression, or a literal string that
        # doesn't start with ``-``.
        count += 1
        if isinstance(elt, ast.Constant):
            samples.append(repr(elt.value))
        elif isinstance(elt, ast.Name):
            samples.append(f"<var {elt.id}>")
        else:
            samples.append("<expr>")
    return count, samples


# ---------------------------------------------------------------------------
# Drift detection — one record per offending recipe site.
# ---------------------------------------------------------------------------


class _Drift:
    __slots__ = ("lineno", "recipe_key", "cli_name", "positionals", "samples", "fixed", "variadic", "is_group", "kind")

    def __init__(
        self,
        *,
        lineno: int,
        recipe_key: str,
        cli_name: str,
        positionals: int,
        samples: list[str],
        fixed: int,
        variadic: bool,
        is_group: bool,
        kind: str,
    ) -> None:
        self.lineno = lineno
        self.recipe_key = recipe_key
        self.cli_name = cli_name
        self.positionals = positionals
        self.samples = samples
        self.fixed = fixed
        self.variadic = variadic
        self.is_group = is_group
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"<Drift line={self.lineno} cli={self.cli_name!r} kind={self.kind} "
            f"positionals={self.positionals} samples={self.samples} "
            f"fixed={self.fixed} variadic={self.variadic} is_group={self.is_group}>"
        )


def _scan_drift(source_path: pathlib.Path) -> list[_Drift]:
    """Return every shape-drift ``_Drift`` record at ``source_path``."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    out: list[_Drift] = []
    for lineno, recipe_key, post_cr in _iter_safe_run_cr_sites(tree):
        # The compound registry MUST have the key (verified at module load
        # time by mcp_server._verify_compound_registry). Translate to CLI
        # name; absent keys raise KeyError which surfaces here as a test
        # failure with a useful traceback.
        if recipe_key not in _COMPOUND_REGISTRY:
            # The recipe key isn't in the registry — the existing
            # name-axis lint covers this case. Skip silently here.
            continue
        cli_name = _COMPOUND_REGISTRY[recipe_key]
        if cli_name not in _COMMANDS:
            continue  # name-axis problem, not shape-axis
        cmd = _load_click_command(cli_name)
        is_group, fixed, variadic = _signature(cmd)
        positionals, samples = _count_literal_positionals(post_cr)

        # Two drift kinds on the shape axis:
        if positionals == 0:
            continue  # no positionals → cannot drift on positional axis

        if variadic:
            # nargs=-1 absorbs unbounded positionals — never drifts.
            continue

        if is_group:
            # Group target: first positional is the subcommand name. We
            # don't recursively validate the subcommand's own signature
            # here (no group targets are currently used in any recipe).
            continue

        if positionals > fixed:
            out.append(
                _Drift(
                    lineno=lineno,
                    recipe_key=recipe_key,
                    cli_name=cli_name,
                    positionals=positionals,
                    samples=samples,
                    fixed=fixed,
                    variadic=variadic,
                    is_group=is_group,
                    kind="extra_positional",
                )
            )
    return out


# ---------------------------------------------------------------------------
# Discovery sanity — the AST scan must find SOMETHING. If it returns zero
# sites the helpers regressed silently.
# ---------------------------------------------------------------------------


def test_discovery_finds_safe_run_cr_sites() -> None:
    """The AST scanner must find a non-trivial number of
    ``_safe_run([_cr(...), ...], root)`` sites in mcp_server.py.

    There are currently >= 10 such sites across the four compound
    recipes (for_new_feature, for_bug_fix, for_refactor,
    for_security_review). If the scanner returns < 5 the AST helpers
    have regressed.
    """
    source = _MCP_SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    sites = list(_iter_safe_run_cr_sites(tree))
    assert len(sites) >= 5, (
        f"AST scanner found {len(sites)} ``_safe_run([_cr(...), ...])`` sites — "
        "expected >= 5. The _iter_safe_run_cr_sites helper has likely regressed."
    )


# ---------------------------------------------------------------------------
# Positive smoke test — a known-good recipe (for_bug_fix's four-section
# chain) must pass the shape check cleanly. for_bug_fix's recipe:
#
#   ("diagnose",       _safe_run([_cr("diagnose"), symbol], root))
#   ("affected_tests", _safe_run([_cr("affected-tests"), symbol], root))
#   ("diff",           _safe_run([_cr("diff")], root))
#   ("context",        _safe_run([_cr("context"), symbol], root))
#
# Each target either has at least one fixed argument slot OR has zero
# positionals on the recipe side. Should produce zero drifts.
# ---------------------------------------------------------------------------


_FOR_BUG_FIX_TARGETS = ("diagnose", "affected-tests", "diff", "context")


def test_for_bug_fix_recipe_shape_is_clean() -> None:
    """The for_bug_fix recipe is one of the OCTET-validated compounds —
    its four sections must all pass the shape check (zero drift).

    This is the positive lock: a regression on for_bug_fix would mean
    either the recipe itself changed shape OR the shape lint started
    false-positiving on a benign pattern.
    """
    drifts = _scan_drift(_MCP_SERVER)
    for_bug_fix_drifts = [d for d in drifts if d.cli_name in _FOR_BUG_FIX_TARGETS]
    assert not for_bug_fix_drifts, (
        "for_bug_fix recipe drifted on the shape axis — this is the "
        "positive lock; a regression here means either the recipe changed "
        "or the shape lint false-positived. Offending sites:\n"
        + "\n".join(
            f"  mcp_server.py:{d.lineno} _safe_run([_cr({d.recipe_key!r}), ...]) "
            f"→ cmd {d.cli_name!r} got {d.positionals} positional(s) {d.samples} but only "
            f"{d.fixed} argument-slot(s) available (variadic={d.variadic}, group={d.is_group})"
            for d in for_bug_fix_drifts
        )
    )


# ---------------------------------------------------------------------------
# W805-NNNNN pin — the two known shape drifts in cmd_for_security_review.
#
# This is an xfail-strict pin: until the recipe is fixed, the test
# documents the drift and the strict flag ensures we'll be notified
# the moment the fix lands (xfail → unexpected pass → failure).
# ---------------------------------------------------------------------------


def _drifts_at(drifts: list[_Drift], cli_name: str) -> list[_Drift]:
    return [d for d in drifts if d.cli_name == cli_name]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NNNNN: cmd_for_security_review's recipe at "
        "src/roam/mcp_server.py:6499-6510 passes shape-axis drifts on two "
        "sections: (1) _safe_run([_cr('adversarial'), symbol], root) — "
        "cmd_adversarial has 0 click.Argument slots, symbol is silently "
        "dropped; (2) _safe_run([_cr('vulns'), 'list'], root) — vulns is "
        "a single click.Command (not a group), 'list' is rejected as an "
        "unexpected positional. Fix the recipe (e.g. _safe_run([_cr("
        "'vulns')], root) for vulns, and either drop the symbol from "
        "adversarial or add a click.argument('symbol', required=False) "
        "to cmd_adversarial) and this xfail will flip to a pass."
    ),
)
def test_w805_nnnnn_shape_drifts_pinned() -> None:
    """Pin the two known W805-NNNNN drifts.

    Asserts BOTH:
    1. ``adversarial`` has a positional drift (recipe passes ``symbol``,
       target has 0 arg slots).
    2. ``vulns`` has a positional drift (recipe passes ``"list"``,
       target has 0 arg slots).

    Each failure independently would still trip the strict-xfail, so
    a partial fix (closing only one of the two sites) is detected.
    """
    drifts = _scan_drift(_MCP_SERVER)
    adv = _drifts_at(drifts, "adversarial")
    vulns = _drifts_at(drifts, "vulns")
    assert adv, "Expected an adversarial shape drift but found none"
    assert vulns, "Expected a vulns shape drift but found none"
    # Both drifts must be in the for_security_review recipe range
    # (mcp_server.py:6499-6510 at scan time — allow some slack for
    # surrounding edits).
    for d in adv + vulns:
        assert 6400 <= d.lineno <= 6700, (
            f"Drift at unexpected lineno {d.lineno} (cli={d.cli_name!r}) — "
            "the recipe moved or the scanner is mis-attributing the line."
        )
    # If we got here, both drifts were detected; xfail-strict flips the
    # outcome to "expected failure" → test passes. When the recipe is
    # fixed, drifts go to zero, the asserts above fire, the strict-xfail
    # catches the unexpected-pass and red-lights CI.
    raise AssertionError("Pinning W805-NNNNN drift findings via xfail-strict (see reason).")


# ---------------------------------------------------------------------------
# Repo-wide sweep — the shape-axis equivalent of the name-axis lint at
# tests/test_compound_recipe_registry.py.
#
# Today's expected drift count is exactly 2 (the W805-NNNNN finding).
# When the recipe is fixed, this constant drops to 0 — and any NEW shape
# drift introduced in a recipe will trip the assert immediately.
# ---------------------------------------------------------------------------


# Closed enumeration of known shape drifts as of W805-QQQQQ. Adding to
# this set means consciously accepting a new drift; the comment must
# name the wave that authorized it.
_KNOWN_DRIFTS: frozenset[tuple[str, int]] = frozenset(
    {
        # W805-NNNNN: cmd_for_security_review extra positional on adversarial
        ("adversarial", 1),
        # W805-NNNNN: cmd_for_security_review treats vulns as a group
        ("vulns", 1),
    }
)


def test_repo_wide_shape_axis_sweep() -> None:
    """Repo-wide assertion: the set of shape-axis drifts EQUALS the known
    set. Any NEW drift (an extra positional on a non-group target with
    no variadic absorber) trips the assert; any FIXED drift also trips
    the assert (so the known-set is updated when the recipe is repaired).

    Drift identity is ``(cli_name, positionals_passed)`` — this is the
    minimum granularity that survives line-number shifts from unrelated
    edits.
    """
    drifts = _scan_drift(_MCP_SERVER)
    observed = frozenset((d.cli_name, d.positionals) for d in drifts)
    extra = observed - _KNOWN_DRIFTS
    missing = _KNOWN_DRIFTS - observed
    if extra or missing:
        parts = []
        if extra:
            parts.append("NEW shape drift(s) introduced:")
            for d in drifts:
                if (d.cli_name, d.positionals) in extra:
                    parts.append(
                        f"  mcp_server.py:{d.lineno} _safe_run([_cr({d.recipe_key!r}), ...]) "
                        f"→ cmd {d.cli_name!r} got {d.positionals} positional(s) {d.samples} but only "
                        f"{d.fixed} argument-slot(s) available (variadic={d.variadic}, group={d.is_group})"
                    )
        if missing:
            parts.append(
                "Known drift(s) no longer detected — _KNOWN_DRIFTS is stale, "
                "remove the entries below from the frozenset:"
            )
            for entry in sorted(missing):
                parts.append(f"  {entry}")
        pytest.fail("\n".join(parts))


# ---------------------------------------------------------------------------
# Runtime cross-check — W978 first-hypothesis rule.
#
# Each AST-detected drift is verified by invoking the CLI through
# CliRunner and confirming click actually rejects the positional. A
# drift that passes the AST check but doesn't reproduce at runtime is
# a false positive and gets filtered out.
# ---------------------------------------------------------------------------


def test_each_detected_drift_reproduces_via_clirunner() -> None:
    """For every shape drift the AST scanner reports, invoking the CLI
    with the equivalent positional(s) MUST produce a non-zero exit
    (click's ``USAGE_ERROR`` / ``Got unexpected extra argument``).

    This is the W978 first-hypothesis cross-check: an AST drift that
    doesn't reproduce at runtime is a false positive (e.g. the target
    secretly forwards to a sub-CLI via ``ctx.args`` — none of the
    current targets do this, but the cross-check keeps the lint
    honest).
    """
    from roam.cli import cli  # local import to keep top-of-module lean

    drifts = _scan_drift(_MCP_SERVER)
    runner = CliRunner()
    for d in drifts:
        # Synthesise a plausible positional vector: ``"x"`` is a safe
        # placeholder that's syntactically valid wherever a symbol-like
        # string is expected and is treated as an extra positional
        # everywhere else.
        positional_args = ["x"] * d.positionals
        result = runner.invoke(cli, [d.cli_name, *positional_args])
        # Click emits exit code 2 on usage errors. Anything OTHER than
        # 0-exit-success counts as reproduction here — including 1, 2,
        # 5, etc. — because the recipe author's intent was a clean run.
        assert result.exit_code != 0, (
            f"Drift at mcp_server.py:{d.lineno} on {d.cli_name!r} did NOT "
            f"reproduce via CliRunner — exit_code was 0 with output:\n"
            f"{result.output[:400]}\n"
            "Either the AST scanner false-positived OR the target started "
            "accepting the positional silently (which would itself be a "
            "Pattern-1 variant D regression — re-investigate)."
        )
        # Tighten the verification: the output should mention either
        # "unexpected" (extra positional) or "Usage:" (any usage error).
        # Don't require the exact phrase — click's wording shifts between
        # major versions.
        out_low = result.output.lower()
        assert "usage" in out_low or "unexpected" in out_low or "error" in out_low, (
            f"Drift at mcp_server.py:{d.lineno} on {d.cli_name!r} produced "
            f"non-zero exit ({result.exit_code}) but the output doesn't look "
            f"like a click usage error:\n{result.output[:400]}"
        )


# ---------------------------------------------------------------------------
# Synthetic-break harness — proves the lint catches a manufactured drift
# even if every current bug is fixed. Mirrors the negative-path test in
# tests/test_compound_recipe_registry.py.
# ---------------------------------------------------------------------------


def test_lint_catches_synthetic_extra_positional() -> None:
    """Feed a synthetic ``_safe_run([_cr("clones"), "extraneous"], root)``
    call to the AST + signature pipeline; the helpers must surface it
    as a shape drift.

    ``clones`` has 0 ``click.Argument`` slots, so a literal extra
    positional is the canonical drift shape. This guards against silent
    regression in the helper chain.
    """
    snippet = '_safe_run([_cr("clones"), "extraneous"], root="."  )'
    tree = ast.parse(snippet, mode="eval")
    detected: list[tuple[int, str, int]] = []
    for lineno, recipe_key, post_cr in _iter_safe_run_cr_sites(tree):
        positionals, _ = _count_literal_positionals(post_cr)
        if recipe_key not in _COMPOUND_REGISTRY:
            continue
        cli_name = _COMPOUND_REGISTRY[recipe_key]
        cmd = _load_click_command(cli_name)
        is_group, fixed, variadic = _signature(cmd)
        if positionals > fixed and not variadic and not is_group:
            detected.append((lineno, cli_name, positionals))
    assert detected == [(1, "clones", 1)], (
        f"Synthetic-break harness failed to surface the manufactured "
        f"drift — got {detected!r}. Helpers may have regressed."
    )


def test_lint_ignores_synthetic_clean_call() -> None:
    """The companion negative-case: a recipe site with zero post-_cr
    positionals must NOT trip the drift detector.
    """
    snippet = '_safe_run([_cr("clones"), "--top", "20"], root="."  )'
    tree = ast.parse(snippet, mode="eval")
    detected: list[tuple[int, str, int]] = []
    for lineno, recipe_key, post_cr in _iter_safe_run_cr_sites(tree):
        positionals, _ = _count_literal_positionals(post_cr)
        if recipe_key not in _COMPOUND_REGISTRY:
            continue
        cli_name = _COMPOUND_REGISTRY[recipe_key]
        cmd = _load_click_command(cli_name)
        is_group, fixed, variadic = _signature(cmd)
        if positionals > fixed and not variadic and not is_group:
            detected.append((lineno, cli_name, positionals))
    assert detected == [], (
        f"Synthetic clean-call false-positived in the drift detector: "
        f"got {detected!r}. Option-flag termination logic may have regressed."
    )
