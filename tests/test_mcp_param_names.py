"""W332 CI lint — closed-set wrapper-param-name lint at the MCP boundary.

This test fails if any ``@_tool``-decorated wrapper in
``src/roam/mcp_server.py`` declares a parameter name that the W332 fix
deprecated as a wrapper-side declaration. Existing callers that pass
those legacy names still work via the alias machinery in
``_PARAM_ALIASES``; this lint targets the WRAPPER DECLARATION side so a
future divergent param-name family is caught the moment it lands.

Deterministic + fast: the test parametrizes over a closed set
(``_W332_DEPRECATED_INPUT_PATH_PARAMS``) and a small frozen mirror of
the legacy aliases for symbol / path / query. Discovery is AST-only
(no ``mcp_server`` import) so the lint stays runnable in environments
where ``fastmcp`` isn't installed.

Why the lint targets WRAPPER DECLARATIONS, not callers:
* Callers using the legacy names get a deprecation warning via
  ``summary.alias_warnings`` (see ``_normalize_aliases``). That's the
  migration nudge — non-breaking, observable.
* Wrappers that DECLARE the legacy name as their canonical short-
  circuit the alias machinery (because ``accepted`` is computed from
  the wrapper's signature). The legacy name becomes "canonical for
  this one tool," and Pattern 3b silent-fail leaks back in.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


# ---------------------------------------------------------------------------
# Closed-set legacy-name table (mirrored from ``_PARAM_ALIASES`` via a
# drift guard below).
# ---------------------------------------------------------------------------

# W332 input_path family
_W332_LEGACY_NAMES: frozenset[str] = frozenset(
    {"rules_path", "rules_file", "statement_path", "envelope_path"}
)

# Pre-W332 families (symbol / path / query) — kept here so the lint is
# uniformly enforced across all of Pattern 3b, not just the W332 set.
_PRE_W332_LEGACY_NAMES: frozenset[str] = frozenset(
    {"name", "target", "file", "pattern"}
)

# W347 — extends the symbol / path-cluster alias coverage. ``file_path``
# / ``filename`` / ``filepath`` collapse to canonical ``path``;
# ``subject`` is a reserved future symbol-shaped alias. The matching
# lint case is parametrized below in the same shape as the pre-W332
# sweep.
_W347_LEGACY_NAMES: frozenset[str] = frozenset(
    {"file_path", "filename", "filepath", "subject"}
)


# ---------------------------------------------------------------------------
# AST-only tool-wrapper discovery (mirrors ``surface_counts.mcp_tool_names``).
#
# Each ``@_tool(name=...)`` decorator in ``mcp_server.py`` marks the
# function below it as a registered MCP tool wrapper. We walk the AST
# once and return ``[(tool_name, param_names), ...]`` for every such
# wrapper. No ``import roam.mcp_server`` -> the lint stays runnable in
# environments where ``fastmcp`` isn't installed.
# ---------------------------------------------------------------------------


_MCP_SERVER_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "src" / "roam" / "mcp_server.py"
)


def _is_tool_decorator(node: ast.expr) -> bool:
    """Match the ``@_tool(...)`` decorator call literal."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_tool"
    )


def _tool_name_kwarg(decorator: ast.Call) -> str | None:
    for kw in decorator.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _param_names_from_def(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """All declared parameter names on a function definition (positional,
    keyword-only, posonly). Excludes *args / **kwargs."""
    out: list[str] = []
    args = func.args
    for grp in (args.posonlyargs, args.args, args.kwonlyargs):
        for a in grp:
            out.append(a.arg)
    return out


def _discover_tool_wrappers() -> list[tuple[str, tuple[str, ...]]]:
    """Walk the AST of ``mcp_server.py`` and return one entry per
    ``@_tool``-decorated function: ``(tool_name, declared_param_names)``.

    The discovery is order-stable (source order) so test IDs are
    deterministic across runs.
    """
    source = _MCP_SERVER_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue
            tool_name = _tool_name_kwarg(decorator)
            if tool_name is None:
                continue
            out.append((tool_name, tuple(_param_names_from_def(node))))
            break  # one @_tool per wrapper
    return out


_TOOL_WRAPPERS: list[tuple[str, tuple[str, ...]]] = _discover_tool_wrappers()


# ---------------------------------------------------------------------------
# Sanity guard: discovery returns SOMETHING. If this fails we're not
# linting anything and the rest of the test is a silent pass.
# ---------------------------------------------------------------------------


def test_tool_discovery_finds_wrappers():
    """The lint must not silently pass when discovery yields nothing."""
    assert len(_TOOL_WRAPPERS) >= 50, (
        f"AST discovery exposed {len(_TOOL_WRAPPERS)} @_tool wrappers; "
        f"expected >=50 (roam typically registers 150+). The W332 lint "
        f"below would silently pass — fix discovery in this test file."
    )


# ---------------------------------------------------------------------------
# The W332 lint — parametrized over every (tool, legacy_name) pair
# ---------------------------------------------------------------------------


def _w332_cases() -> list[tuple[str, tuple[str, ...], str]]:
    cases: list[tuple[str, tuple[str, ...], str]] = []
    for tool_name, params in _TOOL_WRAPPERS:
        for legacy in sorted(_W332_LEGACY_NAMES):
            cases.append((tool_name, params, legacy))
    return cases


@pytest.mark.parametrize(
    "tool_name,declared_params,legacy_name",
    _w332_cases(),
    ids=[f"{t}::{lg}" for t, _, lg in _w332_cases()],
)
def test_no_wrapper_declares_w332_legacy_param(tool_name, declared_params, legacy_name):
    """No wrapper may declare a W332-deprecated input-path-family param.

    The four legacy names (``rules_path`` / ``rules_file`` /
    ``statement_path`` / ``envelope_path``) collapse to the canonical
    ``input_path`` under W332. Wrappers that still declare them as
    their canonical short-circuit the alias machinery (Pattern 3b
    silent-fail leaks back in).
    """
    assert legacy_name not in declared_params, (
        f"Tool '{tool_name}' declares legacy W332 param '{legacy_name}' "
        f"as its canonical. Rename to 'input_path' — the alias machinery "
        f"in mcp_server._PARAM_ALIASES will keep '{legacy_name}=' callers "
        f"working with a deprecation warning. See W332 design note in "
        f"src/roam/mcp_server.py near _PARAM_ALIASES."
    )


# ---------------------------------------------------------------------------
# Drift guard — alias map vs lint-enumeration must stay in sync
# ---------------------------------------------------------------------------


def _load_param_aliases_via_ast() -> dict[str, frozenset[str]]:
    """Extract the keys-of-each-canonical from ``_PARAM_ALIASES`` without
    importing ``mcp_server``. We parse the literal dict-of-dict assignment.
    """
    source = _MCP_SERVER_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    out: dict[str, frozenset[str]] = {}
    for node in ast.walk(module):
        if not isinstance(node, ast.AnnAssign) and not isinstance(node, ast.Assign):
            continue
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        )
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "_PARAM_ALIASES":
                value = node.value
                if not isinstance(value, ast.Dict):
                    continue
                for k, v in zip(value.keys, value.values):
                    if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                        continue
                    if not isinstance(v, ast.Dict):
                        continue
                    aliases: set[str] = set()
                    for ak in v.keys:
                        if isinstance(ak, ast.Constant) and isinstance(ak.value, str):
                            aliases.add(ak.value)
                    out[k.value] = frozenset(aliases)
                return out
    return out


def _load_w332_deprecated_set_via_ast() -> frozenset[str]:
    """Read the ``_W332_DEPRECATED_INPUT_PATH_PARAMS`` constant via AST."""
    source = _MCP_SERVER_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in ast.walk(module):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "_W332_DEPRECATED_INPUT_PATH_PARAMS":
            continue
        # value should be frozenset({...})
        value = node.value
        # Pattern: ``frozenset({...})``
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            if value.args and isinstance(value.args[0], (ast.Set, ast.List)):
                names = {
                    elt.value
                    for elt in value.args[0].elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
                return frozenset(names)
    return frozenset()


def test_w332_legacy_names_match_param_aliases_map():
    """W332 legacy names declared above must match what ``_PARAM_ALIASES``
    actually maps to ``input_path``.

    If a future change adds a new alias to ``_PARAM_ALIASES["input_path"]``
    without extending ``_W332_LEGACY_NAMES`` here, the lint silently
    stops covering the new alias — this drift guard catches that.
    """
    alias_map = _load_param_aliases_via_ast()
    input_path_aliases = alias_map.get("input_path", frozenset())
    actual_legacies = frozenset(
        alias for alias in input_path_aliases if alias != "input_path"
    )
    assert actual_legacies == _W332_LEGACY_NAMES, (
        f"Drift: _PARAM_ALIASES['input_path'] aliases {sorted(actual_legacies)} "
        f"differ from _W332_LEGACY_NAMES {sorted(_W332_LEGACY_NAMES)}. "
        f"Update _W332_LEGACY_NAMES in this test file so the lint covers "
        f"the new alias."
    )


def test_w332_deprecated_set_matches_module_constant():
    """The lint set must equal ``mcp_server._W332_DEPRECATED_INPUT_PATH_PARAMS``.

    The module constant is the source of truth referenced by docs and
    the design note. Mirror divergence -> drift guard trips.
    """
    module_constant = _load_w332_deprecated_set_via_ast()
    assert module_constant, (
        "mcp_server._W332_DEPRECATED_INPUT_PATH_PARAMS missing — the "
        "module-level constant is the source of truth for the W332 "
        "deprecated set."
    )
    assert module_constant == _W332_LEGACY_NAMES, (
        f"Drift: module constant {sorted(module_constant)} != test "
        f"enumeration {sorted(_W332_LEGACY_NAMES)}."
    )


# ---------------------------------------------------------------------------
# Pre-W332 sweep — symbol/path/query Pattern 3b lint
#
# Same shape as the W332 lint, with a hand-maintained exemption table
# for tools whose ``target`` / ``file`` / ``pattern`` parameters mean
# something semantically distinct from the canonical concept.
# ---------------------------------------------------------------------------

# Hand-maintained exemption: ``(tool_name, legacy_name) -> reason``.
#
# These are PRE-W332 wrapper declarations the W332 task explicitly chose
# NOT to refactor (the task scope is the input_path family). The lint
# records each one so a future sweep can address them as a batch. New
# entries should NOT be added without a deliberate decision — the lint
# fails when a NEW legacy declaration lands without an exemption row.
_PRE_W332_EXEMPT: dict[tuple[str, str], str] = {
    # Semantically-distinct ``target`` — git ref / non-symbol identifier.
    ("roam_breaking_changes", "target"): "git ref, not a symbol id",
    # redacted renamed the 9 ``target``-as-symbol declarations
    # (roam_prepare_change / roam_trace / roam_affected_tests /
    # roam_annotate_symbol / roam_get_annotations / roam_generate_plan /
    # roam_get_invariants / roam_why_fail / roam_metrics) to the
    # canonical ``symbol``. Legacy ``target=`` callers still work via
    # ``_PARAM_ALIASES["symbol"]["target"]`` -> alias deprecation warning.
    # ``file`` on verify_imports — pre-W332 path divergence.
    ("roam_verify_imports", "file"): "pre-W332 path param; future Fix-D extension",
    # ``pattern`` on grep / history_grep / patterns — semantically distinct
    # from the Fix D ``query`` canonical (these are regex patterns, not
    # free-text queries).
    ("roam_grep", "pattern"): "regex pattern, not free-text query",
    ("roam_history_grep", "pattern"): "regex pattern, not free-text query",
    ("roam_patterns", "pattern"): "literal pattern name, not free-text query",
}


def _pre_w332_cases() -> list[tuple[str, tuple[str, ...], str]]:
    cases: list[tuple[str, tuple[str, ...], str]] = []
    for tool_name, params in _TOOL_WRAPPERS:
        for legacy in sorted(_PRE_W332_LEGACY_NAMES):
            cases.append((tool_name, params, legacy))
    return cases


@pytest.mark.parametrize(
    "tool_name,declared_params,legacy_name",
    _pre_w332_cases(),
    ids=[f"{t}::{lg}" for t, _, lg in _pre_w332_cases()],
)
def test_no_wrapper_declares_pre_w332_legacy_param(tool_name, declared_params, legacy_name):
    """No wrapper may declare a pre-W332 Pattern 3b legacy param as canonical.

    The four pre-W332 legacy names (``name`` / ``target`` / ``file`` /
    ``pattern``) collapse under Fix D to ``symbol`` / ``path`` /
    ``query``. Wrappers that declare them as canonical re-introduce
    the Pattern 3b vocabulary mismatch the alias mechanism was built
    to fix.

    Known exemptions (NOT a wrapper-declaration regression):
    see ``_PRE_W332_EXEMPT`` for the closed list + rationale.
    """
    if (tool_name, legacy_name) in _PRE_W332_EXEMPT:
        pytest.skip(
            f"{tool_name}.{legacy_name} exempt: "
            f"{_PRE_W332_EXEMPT[(tool_name, legacy_name)]}"
        )
    assert legacy_name not in declared_params, (
        f"Tool '{tool_name}' declares legacy Pattern-3b param "
        f"'{legacy_name}' as its canonical. Rename to the canonical "
        f"(name->symbol, target->symbol, file->path, pattern->query). "
        f"If '{legacy_name}' means something semantically distinct on "
        f"this tool, add it to ``_PRE_W332_EXEMPT`` in this test with "
        f"a one-line reason."
    )


# ---------------------------------------------------------------------------
# W347 sweep — file-path / subject cluster Pattern 3b lint
#
# ``file_path`` / ``filename`` / ``filepath`` collapse to canonical
# ``path``; ``subject`` is a reserved future symbol-shaped alias. The
# sweep targets wrapper DECLARATIONS the same way the W332 / pre-W332
# sweeps do — call sites that pass the legacy spelling still work via
# the alias machinery with a deprecation warning.
# ---------------------------------------------------------------------------

# Hand-maintained exemption table for W347. Empty by default: the
# refactor that introduced this lint also renamed the two wrappers
# that previously declared ``file_path`` (``roam_generate_plan`` /
# ``roam_plan``) so they now use ``path``. Add a row here ONLY when
# a legacy spelling has semantically distinct meaning on a specific
# wrapper (mirror the pre-W332 exemption pattern).
_W347_EXEMPT: dict[tuple[str, str], str] = {}


def _w347_cases() -> list[tuple[str, tuple[str, ...], str]]:
    cases: list[tuple[str, tuple[str, ...], str]] = []
    for tool_name, params in _TOOL_WRAPPERS:
        for legacy in sorted(_W347_LEGACY_NAMES):
            cases.append((tool_name, params, legacy))
    return cases


@pytest.mark.parametrize(
    "tool_name,declared_params,legacy_name",
    _w347_cases(),
    ids=[f"{t}::{lg}" for t, _, lg in _w347_cases()],
)
def test_no_wrapper_declares_w347_legacy_param(tool_name, declared_params, legacy_name):
    """No wrapper may declare a W347 Pattern-3b legacy param as canonical.

    The four W347 legacy names (``file_path`` / ``filename`` /
    ``filepath`` / ``subject``) collapse under Fix D to ``path`` /
    ``symbol``. Wrappers that still declare them as canonical
    short-circuit the alias machinery and re-introduce Pattern 3b
    silent-fail.

    Known exemptions (NOT a wrapper-declaration regression):
    see ``_W347_EXEMPT`` for the closed list + rationale.
    """
    if (tool_name, legacy_name) in _W347_EXEMPT:
        pytest.skip(
            f"{tool_name}.{legacy_name} exempt: "
            f"{_W347_EXEMPT[(tool_name, legacy_name)]}"
        )
    assert legacy_name not in declared_params, (
        f"Tool '{tool_name}' declares legacy W347 param "
        f"'{legacy_name}' as its canonical. Rename to the canonical "
        f"(file_path/filename/filepath -> path; subject -> symbol). "
        f"The alias machinery in mcp_server._PARAM_ALIASES will keep "
        f"'{legacy_name}=' callers working with a deprecation warning. "
        f"If '{legacy_name}' means something semantically distinct on "
        f"this tool, add it to ``_W347_EXEMPT`` in this test with a "
        f"one-line reason."
    )


# ---------------------------------------------------------------------------
# W347 drift guards — keep the lint enumeration in sync with the
# ``_PARAM_ALIASES`` map and the module-level deprecated constant.
# ---------------------------------------------------------------------------


def _load_w347_deprecated_set_via_ast() -> frozenset[str]:
    """Read the ``_W347_DEPRECATED_PARAMS`` constant via AST."""
    source = _MCP_SERVER_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in ast.walk(module):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "_W347_DEPRECATED_PARAMS":
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            if value.args and isinstance(value.args[0], (ast.Set, ast.List)):
                names = {
                    elt.value
                    for elt in value.args[0].elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
                return frozenset(names)
    return frozenset()


def test_w347_legacy_names_match_param_aliases_map():
    """W347 legacy names declared above must match the aliases that
    actually collapse to ``path`` / ``symbol`` via ``_PARAM_ALIASES``.

    Specifically: ``file_path`` / ``filename`` / ``filepath`` must be
    aliases under ``path``, and ``subject`` must be an alias under
    ``symbol``. The drift guard fails if either map drifts.
    """
    alias_map = _load_param_aliases_via_ast()
    path_aliases = alias_map.get("path", frozenset())
    symbol_aliases = alias_map.get("symbol", frozenset())

    expected_path_aliases = {"file_path", "filename", "filepath"}
    missing_path = expected_path_aliases - path_aliases
    assert not missing_path, (
        f"_PARAM_ALIASES['path'] missing W347 aliases: {sorted(missing_path)}. "
        f"Currently has: {sorted(path_aliases)}. Update mcp_server."
    )

    assert "subject" in symbol_aliases, (
        f"_PARAM_ALIASES['symbol'] missing W347 alias 'subject'. "
        f"Currently has: {sorted(symbol_aliases)}."
    )


def test_w347_deprecated_set_matches_module_constant():
    """The lint set ``_W347_LEGACY_NAMES`` must equal the module
    constant ``mcp_server._W347_DEPRECATED_PARAMS``. The module
    constant is the source of truth referenced by docs."""
    module_constant = _load_w347_deprecated_set_via_ast()
    assert module_constant, (
        "mcp_server._W347_DEPRECATED_PARAMS missing — the module-level "
        "constant is the source of truth for the W347 deprecated set."
    )
    assert module_constant == _W347_LEGACY_NAMES, (
        f"Drift: module constant {sorted(module_constant)} != test "
        f"enumeration {sorted(_W347_LEGACY_NAMES)}."
    )


# ---------------------------------------------------------------------------
# W347 alias-acceptance tests — assert wrappers in each cluster accept
# BOTH alias and canonical names through the alias machinery.
# ---------------------------------------------------------------------------


def _import_mcp_server():
    """Import ``roam.mcp_server`` lazily; skip if fastmcp isn't installed."""
    try:
        import roam.mcp_server as mcp_server  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"mcp_server import failed (fastmcp not installed?): {exc}")
    return mcp_server


@pytest.mark.parametrize(
    "canonical,alias",
    [
        ("path", "file"),
        ("path", "file_path"),
        ("path", "filename"),
        ("path", "filepath"),
        ("symbol", "subject"),
    ],
)
def test_param_alias_resolves_to_canonical(canonical, alias):
    """``_normalize_aliases`` must rewrite each W347 alias to its
    canonical when the wrapper accepts the canonical."""
    mcp_server = _import_mcp_server()
    kwargs = {alias: "x"}
    accepted = {canonical}
    out, warnings = mcp_server._normalize_aliases("roam_test", kwargs, accepted)
    assert canonical in out, (
        f"Expected '{alias}' to be rewritten to canonical '{canonical}'; "
        f"got kwargs={out}."
    )
    assert alias not in out, (
        f"Expected alias '{alias}' to be removed after rewrite; got kwargs={out}."
    )
    assert warnings, (
        f"Expected a deprecation warning when alias '{alias}' was used; got none."
    )


def test_param_alias_canonical_wins_when_both_supplied():
    """If both alias and canonical are supplied, canonical wins and
    the alias is dropped loudly (per ``_normalize_aliases`` rule 2)."""
    mcp_server = _import_mcp_server()
    kwargs = {"path": "canon", "file_path": "alias"}
    accepted = {"path"}
    out, warnings = mcp_server._normalize_aliases("roam_test", kwargs, accepted)
    assert out.get("path") == "canon"
    assert "file_path" not in out
    assert any("ignoring" in w for w in warnings)


# ---------------------------------------------------------------------------
# W606 lint — canonical-with-alias required-positional followed by another
# required positional-or-keyword param.
#
# Why this lint exists:
#   ``_wrap_with_alias_normalization`` synthesises a merged signature for
#   every ``@_tool`` wrapper. Any CANONICAL param that has at least one
#   alias is demoted to ``default=""`` so FastMCP/Pydantic schema generation
#   does not reject calls that supply only the legacy alias. That demotion
#   violates Python's ``inspect.Signature`` rule 1 (a positional-or-keyword
#   param with a default cannot be followed by a positional-or-keyword
#   param WITHOUT a default) the moment ANY subsequent positional-or-keyword
#   param is still required.
#
#   W595 sealed the runtime symptom: a canonical-with-alias that triggers
#   this pattern is now promoted to ``KEYWORD_ONLY`` (instead of staying
#   positional-or-keyword) when the wrapper builder detects a subsequent
#   required positional. Module import succeeds.
#
#   W606 adds the AST-time guard. The lint walks every ``@_tool`` wrapper
#   AND ALSO imports ``mcp_server`` to verify the wrapper builder's W595
#   promotion logic still applies to the matching wrappers. If a future
#   change reverts the runtime fix or breaks the promotion path, this
#   lint surfaces it at PR time rather than at module-import time on
#   the next agent invocation.
# ---------------------------------------------------------------------------


def _load_canonicals_with_alias_via_ast() -> frozenset[str]:
    """Return the set of canonicals in ``_PARAM_ALIASES`` that have at
    least one alias whose key != canonical (i.e. that would be demoted
    to ``default=""`` by the wrapper builder).

    AST-only; no ``mcp_server`` import.
    """
    alias_map = _load_param_aliases_via_ast()
    out: set[str] = set()
    for canonical, aliases in alias_map.items():
        if any(alias != canonical for alias in aliases):
            out.add(canonical)
    return frozenset(out)


_CANONICALS_WITH_ALIAS: frozenset[str] = _load_canonicals_with_alias_via_ast()


def _params_with_kind_and_default(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, str, bool]]:
    """Return ``[(name, kind, has_default), ...]`` for each declared param.

    ``kind`` is one of ``"posonly"`` / ``"pos_or_kw"`` / ``"kw_only"``.
    Excludes ``*args`` / ``**kwargs``.

    ``has_default`` reflects Python's per-group default-alignment: in
    ``args.args`` the trailing N entries have defaults (one default per
    trailing arg, right-aligned). ``args.kwonlyargs`` defaults align
    1:1 with ``args.kw_defaults`` (a ``None`` literal in ``kw_defaults``
    means "no default").
    """
    a = func.args
    out: list[tuple[str, str, bool]] = []

    # posonly: defaults shared with args.args from args.defaults
    posonly = list(a.posonlyargs)
    pos_or_kw = list(a.args)
    # args.defaults right-aligns across posonlyargs + args.
    n_pos = len(posonly) + len(pos_or_kw)
    n_defaults = len(a.defaults)
    default_start = n_pos - n_defaults

    for idx, p in enumerate(posonly):
        has_default = idx >= default_start
        out.append((p.arg, "posonly", has_default))
    for idx, p in enumerate(pos_or_kw):
        full_idx = len(posonly) + idx
        has_default = full_idx >= default_start
        out.append((p.arg, "pos_or_kw", has_default))

    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        # kw_defaults: None entry means "no default", else an expression node.
        out.append((p.arg, "kw_only", d is not None))
    return out


def _discover_w606_pattern_wrappers() -> list[tuple[str, str, str]]:
    """Walk ``mcp_server.py`` and return one entry per ``@_tool`` wrapper
    that matches the W606 trigger pattern:

      (canonical-with-alias as required positional-or-keyword)
      followed by
      (any required positional-or-keyword)

    Returns ``[(tool_name, canonical_name, blocker_name), ...]`` in
    source order — deterministic for parametrize IDs.
    """
    source = _MCP_SERVER_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    out: list[tuple[str, str, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_tool_decorator(d) for d in node.decorator_list):
            continue
        tool_name: str | None = None
        for d in node.decorator_list:
            if _is_tool_decorator(d):
                tool_name = _tool_name_kwarg(d)
                break
        if tool_name is None:
            continue

        params = _params_with_kind_and_default(node)
        # Scan only the positional-or-keyword (and posonly) slice.
        pos_slice = [
            (name, kind, has_default)
            for name, kind, has_default in params
            if kind in ("posonly", "pos_or_kw")
        ]
        for i, (name, _kind, has_default) in enumerate(pos_slice):
            if name not in _CANONICALS_WITH_ALIAS:
                continue
            if has_default:
                # Already has a default — wrapper builder leaves it alone.
                continue
            # Look for any subsequent pos_or_kw req param.
            for q_name, _qkind, q_has_default in pos_slice[i + 1 :]:
                if not q_has_default:
                    out.append((tool_name, name, q_name))
                    break
    return out


_W606_PATTERN_WRAPPERS: list[tuple[str, str, str]] = _discover_w606_pattern_wrappers()


def test_w606_discovery_finds_known_case():
    """Sanity guard: the W606 discovery must find ``annotate_symbol``
    on the current main tree. ``annotate_symbol(symbol, content, ...)`` is
    the wrapper W595 ships the runtime fix for — if discovery returns
    empty, the lint below silently passes."""
    matches = {tool_name for tool_name, _, _ in _W606_PATTERN_WRAPPERS}
    assert "roam_annotate_symbol" in matches, (
        f"Expected ``roam_annotate_symbol`` to be discovered as a W606 "
        f"pattern match (canonical ``symbol`` followed by required "
        f"``content``). Discovered set: {sorted(matches)}. AST discovery "
        f"is broken — fix _discover_w606_pattern_wrappers."
    )


def test_w606_module_import_succeeds():
    """Module-import sanity: ``import roam.mcp_server`` must succeed.

    The W595 crash class manifests at module-import time as::

        ValueError: non-default argument follows default argument

    raised from inside ``_wrap_with_alias_normalization`` while applying
    ``@_tool`` to a wrapper that matches the W606 trigger pattern.
    Simply being able to import the module means every existing W606
    pattern wrapper had its canonical-with-alias param successfully
    converted (W595 ``must_promote_to_kwonly`` path took effect).

    NOTE: this test does NOT use ``_import_mcp_server`` (which skips on
    failure) — a W606 regression that crashes module import is a HARD
    failure, not a "fastmcp not installed" environment skip.
    """
    try:
        import roam.mcp_server as mcp_server  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        # ``fastmcp`` itself missing is an env-skip; ``roam`` missing is hard.
        if "fastmcp" in str(exc).lower():
            pytest.skip(f"fastmcp not installed: {exc}")
        raise
    except ValueError as exc:
        pytest.fail(
            f"W606 regression: importing roam.mcp_server raised "
            f"ValueError: {exc}. This is the pre-W595 crash class — a "
            f"canonical-with-alias param ({sorted(_CANONICALS_WITH_ALIAS)}) "
            f"was followed by a required positional in some @_tool "
            f"wrapper, and the must_promote_to_kwonly path in "
            f"src/roam/mcp_server.py:_wrap_with_alias_normalization "
            f"failed to convert it to KEYWORD_ONLY."
        )
    assert mcp_server is not None


@pytest.mark.parametrize(
    "tool_name,canonical_name,blocker_name",
    _W606_PATTERN_WRAPPERS,
    ids=[
        f"{t}::{canon}+{blocker}"
        for t, canon, blocker in _W606_PATTERN_WRAPPERS
    ],
)
def test_w606_pattern_wrapper_builds_directly(
    tool_name, canonical_name, blocker_name
):
    """For every wrapper matching the W606 trigger pattern, applying
    ``_wrap_with_alias_normalization`` to the **inner Python function**
    (taken from ``mcp_server`` module attribute via ``__wrapped__``)
    MUST NOT raise.

    This is a direct exercise of the W595 promotion path. We resolve the
    original undecorated function by walking ``__wrapped__`` to the
    innermost level, then re-run the wrapper builder. Same code path
    that ran at module-import time — but parametrized over every
    matching wrapper, so a future ``@_tool`` whose author forgot the
    promotion contract is caught here rather than in an obscure
    import-time traceback.
    """
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    # Resolve the original undecorated inner function by walking
    # ``__wrapped__`` to the innermost level. The module attribute is
    # the outermost wrapped fn (FastMCP / handle-off / guard / alias),
    # but ``functools.wraps`` keeps the chain reachable.
    py_name = tool_name[len("roam_"):] if tool_name.startswith("roam_") else tool_name
    outer = getattr(mcp_server, py_name, None)
    if outer is None:
        pytest.skip(f"Module attribute '{py_name}' not found.")

    inner = outer
    while hasattr(inner, "__wrapped__"):
        inner = inner.__wrapped__

    # Sanity: the innermost should declare the canonical param.
    inner_sig = _inspect.signature(inner)
    assert canonical_name in inner_sig.parameters, (
        f"Innermost function for '{tool_name}' is missing canonical "
        f"'{canonical_name}'. AST/runtime view diverged — recheck "
        f"the discovery."
    )

    # Re-run the wrapper builder. Must not raise — that's the W595 fix.
    try:
        rewrapped = mcp_server._wrap_with_alias_normalization(tool_name, inner)
    except ValueError as exc:
        pytest.fail(
            f"W606 regression: _wrap_with_alias_normalization raised "
            f"ValueError on '{tool_name}' (canonical={canonical_name}, "
            f"first required blocker={blocker_name}): {exc}. The "
            f"must_promote_to_kwonly path in "
            f"src/roam/mcp_server.py:_wrap_with_alias_normalization is "
            f"broken."
        )

    # Verify the merged signature promoted the canonical to KEYWORD_ONLY.
    rewrapped_sig = getattr(rewrapped, "__signature__", None)
    if rewrapped_sig is None:
        # Inner had no aliases for any canonical — builder returned fn
        # unchanged. That's a no-op case; not a W606 trigger.
        return
    canonical_param = rewrapped_sig.parameters.get(canonical_name)
    assert canonical_param is not None, (
        f"Re-wrapped signature for '{tool_name}' dropped canonical "
        f"'{canonical_name}'."
    )
    assert canonical_param.kind == _inspect.Parameter.KEYWORD_ONLY, (
        f"W606 regression: '{tool_name}' has canonical-with-alias "
        f"'{canonical_name}' as kind={canonical_param.kind.name} but "
        f"required positional '{blocker_name}' follows it. The W595 "
        f"must_promote_to_kwonly path should have made "
        f"'{canonical_name}' KEYWORD_ONLY."
    )


def test_w606_synthetic_regression_caught_by_wrapper_builder():
    """Synthetic regression: build a fake wrapper with the W606 trigger
    shape (canonical-with-alias 'symbol' followed by required 'content')
    and run it through ``_wrap_with_alias_normalization``. The wrapper
    builder MUST succeed (no ValueError) AND emit a signature where
    'symbol' is KEYWORD_ONLY.

    This proves the W595 fix sees the offending shape and converts it,
    rather than silently relying on luck-of-the-source-tree where every
    canonical happens to be the last positional param.
    """
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    # Synthetic wrapper inner fn — same shape as annotate_symbol.
    def _fake_inner(symbol: str, content: str, tag: str = "", author: str = ""):
        return {"symbol": symbol, "content": content, "tag": tag, "author": author}

    # Run the wrapper builder. Should NOT raise — that's the W595 fix.
    try:
        wrapped = mcp_server._wrap_with_alias_normalization(
            "roam_synthetic_w606", _fake_inner
        )
    except ValueError as exc:
        pytest.fail(
            f"W595 regression detected: _wrap_with_alias_normalization "
            f"raised ValueError on the canonical-with-alias + required-"
            f"positional pattern: {exc}. The must_promote_to_kwonly path "
            f"in src/roam/mcp_server.py is broken."
        )

    sig = _inspect.signature(wrapped)
    symbol_param = sig.parameters["symbol"]
    content_param = sig.parameters["content"]
    assert symbol_param.kind == _inspect.Parameter.KEYWORD_ONLY, (
        f"Synthetic regression: 'symbol' should have been promoted to "
        f"KEYWORD_ONLY (W595 must_promote_to_kwonly path); got "
        f"kind={symbol_param.kind.name}."
    )
    # 'content' may stay POSITIONAL_OR_KEYWORD or become KEYWORD_ONLY
    # depending on wrapper-builder ordering — either is fine as long as
    # the signature is valid. Just assert it is still present and
    # required.
    assert content_param.default is _inspect.Parameter.empty, (
        f"'content' lost its required status (default={content_param.default!r})."
    )


# ---------------------------------------------------------------------------
# W607 unit tests — the three pure helpers extracted from
# ``_wrap_with_alias_normalization``. Each helper is testable in isolation:
# pass a synthetic ``inspect.Signature`` (or ``__annotations__`` dict) and
# assert on the return value. No FastMCP / module side effects.
# ---------------------------------------------------------------------------


def test_w607_collect_alias_candidates_no_canonical_returns_empty():
    """A signature that declares zero ``_PARAM_ALIASES`` canonicals returns
    ``(set(), [])`` — short-circuit path; the wrapper builder uses this to
    bail out and return the original fn unchanged."""
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    def fn(unrelated: str, other: int = 0) -> None: ...

    accepted, aliases = mcp_server._collect_alias_candidates(_inspect.signature(fn))
    assert accepted == set()
    assert aliases == []


def test_w607_collect_alias_candidates_symbol_and_path():
    """When a signature declares ``symbol`` AND ``path`` canonicals, every
    legacy alias for both must appear in the alias list, with no duplicates
    and no canonical-name leakage."""
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    def fn(symbol: str, path: str = "") -> None: ...

    accepted, aliases = mcp_server._collect_alias_candidates(_inspect.signature(fn))
    assert accepted == {"symbol", "path"}
    # Canonicals never appear in the alias list (identity entries skipped).
    assert "symbol" not in aliases
    assert "path" not in aliases
    # Every non-identity alias from _PARAM_ALIASES is present.
    expected_symbol_aliases = {
        a for a in mcp_server._PARAM_ALIASES["symbol"] if a != "symbol"
    }
    expected_path_aliases = {
        a for a in mcp_server._PARAM_ALIASES["path"] if a != "path"
    }
    assert expected_symbol_aliases.issubset(set(aliases))
    assert expected_path_aliases.issubset(set(aliases))
    # No duplicates.
    assert len(aliases) == len(set(aliases))


def test_w607_collect_alias_candidates_declared_alias_skipped():
    """If a wrapper itself declares one of the alias names (e.g. ``file``),
    that alias must NOT be appended — the wrapper already owns the name and
    rebinding it would shadow the wrapper's own param."""
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    # Synthetic wrapper that declares ``path`` AND ``file`` (which is an
    # alias of ``path``). The alias collector must skip ``file``.
    def fn(path: str = "", file: str = "") -> None: ...

    accepted, aliases = mcp_server._collect_alias_candidates(_inspect.signature(fn))
    assert "path" in accepted
    assert "file" not in aliases, (
        f"Alias 'file' must be skipped when the wrapper declares it directly. "
        f"Got aliases={aliases}."
    )


def test_w607_build_merged_signature_promotes_canonical_to_kwonly():
    """W595 fix path: a canonical-with-alias positional-or-keyword param
    followed by a required positional MUST become KEYWORD_ONLY in the
    merged signature."""
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    def fn(symbol: str, content: str) -> None: ...

    sig = _inspect.signature(fn)
    accepted, aliases = mcp_server._collect_alias_candidates(sig)
    merged = mcp_server._build_merged_signature(sig, accepted, aliases)
    symbol_param = merged.parameters["symbol"]
    content_param = merged.parameters["content"]
    assert symbol_param.kind == _inspect.Parameter.KEYWORD_ONLY
    assert symbol_param.default == ""
    # Required positional 'content' stays required.
    assert content_param.default is _inspect.Parameter.empty


def test_w607_build_merged_signature_demotes_canonical_when_safe():
    """When NO subsequent positional-or-keyword param is required, the
    canonical-with-alias stays POSITIONAL_OR_KEYWORD and is demoted to
    ``default=""`` (the non-W595 path)."""
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    # Only ``symbol`` is required — no trailing required positional.
    def fn(symbol: str, optional_arg: int = 0) -> None: ...

    sig = _inspect.signature(fn)
    accepted, aliases = mcp_server._collect_alias_candidates(sig)
    merged = mcp_server._build_merged_signature(sig, accepted, aliases)
    symbol_param = merged.parameters["symbol"]
    assert symbol_param.kind == _inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert symbol_param.default == ""


def test_w607_build_merged_signature_appends_aliases_as_kwonly():
    """All alias params land as KEYWORD_ONLY with ``default=None`` and
    ``annotation=str``. ``**kwargs`` (when present) stays at the tail."""
    mcp_server = _import_mcp_server()
    import inspect as _inspect

    def fn(symbol: str = "", **extra) -> None: ...

    sig = _inspect.signature(fn)
    accepted, aliases = mcp_server._collect_alias_candidates(sig)
    merged = mcp_server._build_merged_signature(sig, accepted, aliases)

    # Every alias is keyword-only, default None, annotation str.
    for alias_name in aliases:
        p = merged.parameters[alias_name]
        assert p.kind == _inspect.Parameter.KEYWORD_ONLY, (
            f"Alias '{alias_name}' should be KEYWORD_ONLY; got {p.kind.name}"
        )
        assert p.default is None
        assert p.annotation is str

    # **extra survives at the tail.
    tail = list(merged.parameters.values())[-1]
    assert tail.kind == _inspect.Parameter.VAR_KEYWORD
    assert tail.name == "extra"


def test_w607_build_merged_annotations_adds_alias_str_types():
    """``_build_merged_annotations`` copies the wrapped fn's annotations
    and adds ``str`` for every alias name. The original fn's annotations
    dict is NOT mutated (defensive copy).

    Note: this test file uses ``from __future__ import annotations``, so
    annotations on the local ``fn`` here are strings (PEP 563). We assert
    on the original-entry preservation and the alias-entries' literal
    ``str`` type separately — the alias path is the one the helper sets
    unconditionally to the runtime ``str`` type.
    """
    mcp_server = _import_mcp_server()

    def fn(symbol: str, count: int = 0) -> dict: ...

    original_annotations = dict(fn.__annotations__)
    aliases = ["name", "target", "subject"]
    merged = mcp_server._build_merged_annotations(fn, aliases)
    # Original entries preserved (under PEP 563 these are strings; that's
    # still what FastMCP sees and resolves via ``typing.get_type_hints``).
    assert "symbol" in merged
    assert "count" in merged
    assert "return" in merged
    assert merged["symbol"] == fn.__annotations__["symbol"]
    # Alias entries added — these are the helper's responsibility and
    # the helper hard-codes the runtime ``str`` type (not a string).
    for alias_name in aliases:
        assert merged[alias_name] is str, (
            f"Alias '{alias_name}' annotation should be the runtime "
            f"``str`` type; got {merged[alias_name]!r}."
        )
    # Defensive copy: caller can mutate without affecting fn.
    merged["symbol"] = bool
    assert fn.__annotations__ == original_annotations
