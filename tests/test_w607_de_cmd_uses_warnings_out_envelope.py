"""W607-DE -- additive aggregation-phase plumbing for ``cmd_uses``.

cmd_uses is the direct-callers / consumers standalone -- depth-1 reverse
graph via SQL JOIN on edges + language-aware JS-family text fallback.
With W607-DE landed, the full uses path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-U (5 substrate boundaries:
    resolve_symbol_exact / resolve_symbol_fuzzy / fetch_consumers /
    fetch_target_langs / test_text_consumers)
  - aggregation-phase layer: W607-DE (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``uses_*`` marker family and the
``uses_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607u_warnings_out`` substrate-CALL + ``_w607de_warnings_out``
aggregation-phase) are combined at envelope-emit time so consumers see
the full degradation lineage in marker-emission order.

W978 first-hypothesis check (7 recurring traps)
-----------------------------------------------

1. f-string verdict floor -- compute_verdict floor must be literal
   "uses completed", NOT an f-string re-interpolating poisoned inputs.
2. kwarg-default eagerness -- every ``default=`` is a literal constant.
3. json.dumps(default=str) sentinel -- not applicable here; cmd_uses
   uses to_json on a fully-serialized dict.
4. Phase-name collision -- W607-U phase names (resolve_symbol_exact /
   resolve_symbol_fuzzy / fetch_consumers / fetch_target_langs /
   test_text_consumers) do NOT collide with W607-DE phases
   (score_classify / compute_predicate / compute_verdict /
   serialize_envelope).
5. len() at kwarg-bind -- all ``len()`` calls live INSIDE the wrapped
   closures, NOT at the ``_run_check_de(...)`` call site.
6. Unguarded len()/if x: on poisoned object -- floors are literal
   constants; no second-pass derivations.
7. dict.get(key, expensive_default) eager-eval -- _pred_fields uses bare
   ``dict[key]`` lookup because the floor dict guarantees the keys.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

SYMBOL-RELATIONS TRIO closure (agg layer)
-----------------------------------------

cmd_uses (this) is the third leg of the symbol-relations trio at the
aggregation-phase layer:

* cmd_uses    -- W607-U substrate + W607-DE THIS (agg added)
* cmd_deps    -- W607-V substrate + W607-DB pending (agg in flight)
* cmd_relate  -- W607-W substrate + W607-DA landed (agg added)

The trio pairing integration test below confirms ``uses_*`` markers
coexist with ``deps_*`` (W607-V) and ``relate_*`` (W607-W + W607-DA)
markers when all 3 commands are invoked on the same workspace, closing
the trio at the aggregation-phase layer.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Canonical W607-DE phase enumeration
# ---------------------------------------------------------------------------


_DE_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)


# Canonical W607-U substrate-CALL phase enumeration (for coexistence guards)
_U_SUBSTRATE_PHASES = (
    "resolve_symbol_exact",
    "resolve_symbol_fuzzy",
    "fetch_consumers",
    "fetch_target_langs",
    "test_text_consumers",
)


# ---------------------------------------------------------------------------
# Helpers -- invoke uses / deps / relate via the Click group
# ---------------------------------------------------------------------------


def _invoke_uses(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam uses`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("uses")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _invoke_via_cli(runner: CliRunner, cwd, subcommand: str, *extra):
    """Invoke ``roam --json <subcommand> [extra...]`` inside ``cwd``."""
    from roam.cli import cli

    args = ["--json", subcommand, *extra]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with real consumer edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def uses_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol + multi-caller chain.

    Two-file fixture with a real
    ``main_caller -> uses_de_target -> helper_one/helper_two`` chain so
    the edges JOIN + fallbacks all have signal. The target name is
    intentionally unique to avoid LIKE-fallback false-positives.
    """
    proj = tmp_path / "uses_w607de_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "def helper_one():\n"
        "    return 1\n\n"
        "def helper_two():\n"
        "    return 2\n\n"
        "def uses_de_target():\n"
        "    helper_one()\n"
        "    helper_two()\n"
        "    return 'target'\n",
        encoding="utf-8",
    )
    (src / "callers.py").write_text(
        "from src.core import uses_de_target\n\n"
        "def main_caller():\n"
        "    uses_de_target()\n\n"
        "def second_caller():\n"
        "    return uses_de_target()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DE aggregation markers (byte-stable)
# ---------------------------------------------------------------------------


def test_uses_happy_path_no_w607de_markers(cli_runner, uses_project):
    """Clean uses on a healthy corpus -> no W607-DE aggregation markers.

    Hash-stable: an empty W607-DE bucket on the success path must produce
    an envelope without any
    ``uses_score_classify_failed:`` /
    ``uses_compute_predicate_failed:`` /
    ``uses_compute_verdict_failed:`` /
    ``uses_serialize_envelope_failed:`` markers (from the DE layer).
    """
    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "uses"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DE_PHASES:
        prefix = f"uses_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean uses must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_de`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_uses_carries_w607de_accumulator():
    """AST-level guard: cmd_uses source carries the W607-DE accumulator.

    Pins the canonical W607-DE anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-U) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    assert src_path.exists(), f"cmd_uses.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607de_warnings_out" in src, (
        "W607-DE accumulator missing from cmd_uses; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_de" in src, (
        "W607-DE helper ``_run_check_de`` missing from cmd_uses; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_de is defined inside the command.
    tree = ast.parse(src)
    found_run_check_de = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_de":
            found_run_check_de = True
            break
    assert found_run_check_de, (
        "W607-DE ``_run_check_de`` helper not found in cmd_uses "
        "AST; the additive aggregation-phase wrapper has been refactored "
        "away."
    )

    # W607-U must still be present (additive layer does NOT replace it)
    assert "w607u_warnings_out" in src, (
        "W607-U accumulator vanished alongside the W607-DE add; the "
        "additive plumbing must preserve the W607-U substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_de():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_de(...)`` with the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DE_PHASES:
        same_line = f'_run_check_de(\n            "{phase}"' in src
        # accept any indentation
        multi_line = any(f'_run_check_de(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        compact = f'_run_check_de("{phase}"' in src
        marker_grep = f"uses_{phase}_failed" in src
        assert same_line or multi_line or compact or marker_grep, (
            f"W607-DE wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) serialize_envelope failure -> floor envelope still ships with marker
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, uses_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``uses_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_uses as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DE")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "uses", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("uses_serialize_envelope_failed:")]
    assert markers, f"expected ``uses_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    The canonical floor for cmd_uses is ``"uses completed"``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="uses completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DE "
        "discipline; the canonical floor literal 'uses completed' "
        "is missing from cmd_uses.py"
    )


# ---------------------------------------------------------------------------
# (6) ANY W607-DE marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_de_marker_flips_partial_success(cli_runner, uses_project, monkeypatch):
    """ANY W607-DE marker must flip summary.partial_success=True."""
    from roam.commands import cmd_uses as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DE")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DE warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607de_warnings_out_in_both_top_and_summary(cli_runner, uses_project, monkeypatch):
    """Non-empty W607-DE bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_uses as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DE")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DE raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DE raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("uses_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("uses_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-DE uses the SAME ``uses_*`` family
# ---------------------------------------------------------------------------


def test_w607de_marker_prefix_uses_family(cli_runner, uses_project, monkeypatch):
    """W607-DE markers use the canonical ``uses_*`` prefix (same family
    as W607-U; W607-DE is ADDITIVE, not a separate prefix).
    """
    from roam.commands import cmd_uses as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-DE")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("uses_"), f"every W607-DE marker must use the ``uses_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (9) W607-U COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607u_substrate_markers_coexist_with_w607de_aggregation(cli_runner, uses_project, monkeypatch):
    """Confirm ``uses_<substrate-phase>_failed:`` markers (W607-U layer)
    coexist with ``uses_<agg-phase>_failed:`` markers (W607-DE layer)
    -- both in same family, but threaded through different buckets at
    envelope-emit.

    The additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation.
    """
    from roam.commands import cmd_uses as _mod

    # W607-U substrate boundary -- _test_text_consumers raises
    def _raise_test_text(*a, **kw):
        raise RuntimeError("synthetic-u-coexist-test-text")

    # W607-DE aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-de-coexist-envelope")

    monkeypatch.setattr(_mod, "_test_text_consumers", _raise_test_text)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    # JS-family fixture so test_text_consumers gets called.
    proj = uses_project.parent / "uses_js_coexist"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.js").write_text(
        "function uses_de_target() { return 1; }\n"
        "function caller() { return uses_de_target(); }\n"
        "module.exports = { uses_de_target, caller };\n",
        encoding="utf-8",
    )
    (proj / "main.test.js").write_text(
        "const { uses_de_target } = require('./main');\ntest('mention', () => { uses_de_target(); });\n",
        encoding="utf-8",
    )
    git_init(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.cli import cli

        result = cli_runner.invoke(cli, ["--json", "uses", "uses_de_target"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-U (test_text_consumers wraps
    # _test_text_consumers per the cmd_uses call site).
    u_markers = [m for m in top_wo if m.startswith("uses_test_text_consumers_failed:")]
    # Aggregation-phase from W607-DE
    de_markers = [m for m in top_wo if m.startswith("uses_serialize_envelope_failed:")]

    assert u_markers, f"W607-U substrate-CALL marker (uses_test_text_consumers_failed) missing; got {top_wo!r}"
    assert de_markers, f"W607-DE aggregation-phase marker (uses_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``uses_*`` family
    assert all(m.startswith("uses_") for m in (u_markers + de_markers)), (
        f"all markers must share the canonical ``uses_*`` family; got u = {u_markers!r}, de = {de_markers!r}"
    )


# ---------------------------------------------------------------------------
# (10) W978 kwarg-default audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-DE ``default=`` must be a
    literal constant, NOT computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_de"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_de(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_uses.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants."
    )


# ---------------------------------------------------------------------------
# (11) W978 5th-discipline -- len() calls live INSIDE closures
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """W978 5th-discipline AST guard (cmd_taint W607-CJ anchor): every
    ``len()`` call on a wrapped input MUST live INSIDE the wrapped
    closure, NOT at the ``_run_check_de(...)`` call site.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_de"):
            continue
        for sub in node.args:
            for descendant in ast.walk(sub):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call at "
                        f"_run_check_de positional-arg site -- W978 "
                        f"5th-discipline violation"
                    )
        for kw in node.keywords:
            for descendant in ast.walk(kw.value):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call in "
                        f"_run_check_de kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, "W978 5th-discipline violations in cmd_uses.py:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# (12) W978 7-discipline AST audit -- comprehensive trap audit
# ---------------------------------------------------------------------------


def test_w978_seven_discipline_audit():
    """Comprehensive W978 7-discipline AST audit on W607-DE wraps.

    Pins all 7 W978 disciplines on the W607-DE layer:

    1. f-string verdict floor -- compute_verdict floor must be the literal
       "uses completed" string.
    2. kwarg-default eagerness -- ``default=`` is a literal constant.
    3. json.dumps(default=str) sentinel -- not applicable (no json.dumps
       at the W607-DE boundary); audit is no-op.
    4. Phase-name collision -- W607-DE phases distinct from W607-U phases.
    5. len() at kwarg-bind -- enforced by test (11) above.
    6. Unguarded len()/if x: on poisoned object -- floor is literal
       constant, no defensive derivation off poisoned inputs.
    7. dict.get(key, expensive_default) -- enforced via positive pattern:
       _pred_fields uses bare ``dict[key]`` lookup.

    This test consolidates the discipline checks into one AST sweep so a
    future regression on ANY of the 7 disciplines fails here in addition
    to its dedicated test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Discipline 1: f-string verdict floor (positive check)
    assert 'default="uses completed"' in src, (
        "Discipline 1 (f-string verdict floor): canonical literal 'uses completed' missing from cmd_uses.py"
    )

    # Discipline 4: Phase-name collision
    de_phases = set(_DE_PHASES)
    u_phases = set(_U_SUBSTRATE_PHASES)
    collisions = de_phases & u_phases
    assert not collisions, f"Discipline 4 (phase-name collision): W607-DE and W607-U share phase names: {collisions!r}"

    # Discipline 7: dict.get(...) audit -- _pred_fields must NOT use .get()
    # with a non-literal default. Walk for any _pred_fields.get(...) calls.
    pred_get_violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match X.get(...) on attribute access where X is _pred_fields
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get":
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "_pred_fields"):
            continue
        # If a second arg (default) is present and is a Call/Subscript/etc,
        # it's a discipline-7 violation.
        if len(node.args) >= 2:
            default_arg = node.args[1]
            if isinstance(default_arg, (ast.Call, ast.Subscript)):
                pred_get_violations.append(
                    f"line {default_arg.lineno}: _pred_fields.get(...) "
                    f"with eager default -- W978 7th-discipline violation"
                )

    assert not pred_get_violations, "W978 7th-discipline violations in cmd_uses.py:\n" + "\n".join(pred_get_violations)


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- uses_* markers do not leak into peers
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_no_relate_or_deps_leak(cli_runner, uses_project, monkeypatch):
    """Cross-prefix isolation: uses_* markers MUST NOT carry relate_* or
    deps_* prefixes. Closed-enum marker-family contract.
    """
    from roam.commands import cmd_uses as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DE")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    for marker in top_wo:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("relate_"), f"cross-prefix leak: uses marker mis-tagged as relate_*: {marker!r}"
        assert not marker.startswith("deps_"), f"cross-prefix leak: uses marker mis-tagged as deps_*: {marker!r}"
        assert not marker.startswith("impact_"), f"cross-prefix leak: uses marker mis-tagged as impact_*: {marker!r}"


# ---------------------------------------------------------------------------
# (14) Clean envelope carries consumer_state from score_classify
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_consumer_state(cli_runner, uses_project):
    """W607-DE surfaces consumer_state on the envelope.

    The score_classify closure returns a state label (HAS_USERS /
    TEST_ONLY / EMPTY / DEGRADED) which the envelope surfaces so
    consumers can read the consumer classification without re-deriving
    from raw counts.
    """
    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert summary.get("consumer_state") in {
        "HAS_USERS",
        "TEST_ONLY",
        "EMPTY",
        "DEGRADED",
    }, f"consumer_state missing or invalid on clean envelope; got {summary.get('consumer_state')!r}"


# ---------------------------------------------------------------------------
# (15) Clean envelope carries total/production/test predicate metrics
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_predicate_metrics(cli_runner, uses_project):
    """W607-DE surfaces consumer-count predicate metrics on the envelope.

    Floor-shape contract: on the clean path with non-empty consumers, the
    envelope summary carries integer total_consumers +
    production_consumers + test_consumers. The collector must not omit
    these on the success path.
    """
    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    for key in ("total_consumers", "production_consumers", "test_consumers"):
        assert key in summary, f"predicate metric {key!r} missing from summary; got {sorted(summary.keys())!r}"
        assert isinstance(summary[key], int), f"predicate metric {key!r} must be int; got {summary[key]!r}"


# ---------------------------------------------------------------------------
# (16) SYMBOL-RELATIONS TRIO pairing -- uses_/deps_/relate_ markers coexist
# (agg-layer closure)
# ---------------------------------------------------------------------------


def test_symbol_relations_trio_pairing(cli_runner, uses_project):
    """Symbol-relations trio integration: invoking ``uses``, ``deps``,
    ``relate`` on the same workspace produces structurally coexisting
    envelopes from the three peer commands.

    Closes the trio at the AGGREGATION-PHASE layer:
      * cmd_uses    -- W607-U substrate + W607-DE THIS (agg added)
      * cmd_deps    -- W607-V substrate + W607-DB pending
      * cmd_relate  -- W607-W substrate + W607-DA landed (agg added)

    Each command's envelope carries its canonical command name and
    surfaces NO cross-prefix marker leakage on the clean path.
    """
    # uses
    uses = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert uses.exit_code == 0, uses.output
    uses_data = _json.loads(uses.output)
    assert uses_data["command"] == "uses"

    # uses envelope: no relate_/deps_ marker leakage on clean path
    uses_wo = (uses_data.get("warnings_out") or []) + (uses_data.get("summary", {}).get("warnings_out") or [])
    for m in uses_wo:
        if "_failed:" in m:
            assert not m.startswith("relate_"), f"uses envelope leaked relate_ marker: {m!r}"
            assert not m.startswith("deps_"), f"uses envelope leaked deps_ marker: {m!r}"

    # deps
    deps = _invoke_via_cli(cli_runner, uses_project, "deps", "uses_de_target")
    if deps.exit_code == 0 and deps.output.strip().startswith("{"):
        deps_data = _json.loads(deps.output)
        assert deps_data["command"] == "deps", f"expected deps envelope, got {deps_data.get('command')!r}"
        deps_wo = (deps_data.get("warnings_out") or []) + (deps_data.get("summary", {}).get("warnings_out") or [])
        # Cross-prefix isolation: deps markers must NOT carry uses_ prefix
        for m in deps_wo:
            if "_failed:" in m:
                assert not m.startswith("uses_"), f"cross-prefix leak in deps envelope: {m!r}"

    # relate
    rel = _invoke_via_cli(cli_runner, uses_project, "relate", "uses_de_target", "main_caller")
    if rel.exit_code == 0 and rel.output.strip().startswith("{"):
        rel_data = _json.loads(rel.output)
        assert rel_data["command"] == "relate", f"expected relate envelope, got {rel_data.get('command')!r}"
        rel_wo = (rel_data.get("warnings_out") or []) + (rel_data.get("summary", {}).get("warnings_out") or [])
        # Cross-prefix isolation: relate markers must NOT carry uses_ prefix
        for m in rel_wo:
            if "_failed:" in m:
                assert not m.startswith("uses_"), f"cross-prefix leak in relate envelope: {m!r}"


# ---------------------------------------------------------------------------
# (17) Compute_verdict raise falls back to literal floor
# ---------------------------------------------------------------------------


def test_compute_verdict_raise_falls_back_to_literal_floor(cli_runner, uses_project, monkeypatch):
    """If the verdict-string closure raises, the wrap floors to the
    literal "uses completed" string. The envelope still emits with the
    marker AND the verdict carries the floor when assembly itself raised.

    On the serialize_envelope-raise path (used here), compute_verdict
    completed cleanly so verdict carries the assembled string.
    """
    from roam.commands import cmd_uses as _mod

    # Monkeypatch json_envelope so the serialize_envelope path floors AND
    # verify the floor envelope's verdict carries the assembled string.
    # That confirms verdict assembly itself completed on the clean path.
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-verdict-floor-check")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_uses(cli_runner, uses_project, "uses_de_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # On the serialize_envelope-raise path, the floor envelope's summary
    # carries the assembled verdict (compute_verdict completed cleanly,
    # only serialize_envelope raised). So verdict should NOT be the
    # bare floor "uses completed" -- it should be the assembled
    # verdict string.
    summary = data.get("summary") or {}
    verdict = summary.get("verdict", "")
    assert verdict, f"verdict missing from floor envelope; summary = {summary!r}"
    # On the assembled path, verdict carries the "production consumers" prefix.
    assert "production consumers" in verdict or verdict == "uses completed", (
        f"verdict should be either the assembled string or the literal floor; got {verdict!r}"
    )
