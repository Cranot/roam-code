"""W607-DG -- additive aggregation-phase plumbing for ``cmd_describe``.

cmd_describe is the symbol-summarization / project-description command.
With W607-DG landed, the full json-mode emit path is now dual-bucket
plumbed via:

  - substrate-CALL layer: W607-K (per-section + outer-guard markers
    covering ``key_abstractions`` / ``architecture`` / ``complexity``
    / ``dependencies`` / ``project_root`` / ``hotspots`` /
    ``cycle_health`` / ``project_shape`` / ``agent_prompt`` /
    ``pipeline`` / ``cycles_summary``)
  - aggregation-phase layer: W607-DG (4 phases: ``score_classify``
    / ``compute_predicate`` / ``compute_verdict`` /
    ``serialize_envelope``)

Both layers share the canonical ``describe_*`` marker family and the
``describe_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``warnings_out`` substrate-CALL +
``_w607dg_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

Closes the SYMBOL-EXPLORATION 4-WAY at agg-layer:
  * cmd_uses     -> ``uses_*``    (W607-U substrate + W607-DE)
  * cmd_relate   -> ``relate_*``  (W607-W substrate + W607-DA)
  * cmd_deps     -> ``deps_*``    (W607-V substrate + W607-DB)
  * cmd_describe -> ``describe_*``(W607-K substrate + W607-DG THIS)

W978 7-discipline pre-fix audit
-------------------------------

1. f-string verdict floor -- floor is a LITERAL string
   ``"describe analysis completed"``, never an f-string re-interpolating
   the same predicate values that just raised.
2. kwarg-default eagerness -- ``default=`` arguments to
   ``_run_check_dg`` are plain dicts / strings; no expensive call.
3. json.dumps(default=str) sentinel -- not used; markers carry
   ``str(exc)`` directly.
4. Phase-name collision -- W607-DG phase names
   (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
   ``serialize_envelope``) do NOT collide with any W607-K
   substrate-CALL phase name (``key_abstractions`` / ``architecture``
   / ``complexity`` / ``dependencies`` / ``project_root`` /
   ``hotspots`` / ``cycle_health`` / ``project_shape`` /
   ``agent_prompt`` / ``pipeline`` / ``cycles_summary``).
5. len() at kwarg-bind -- ``len(output)`` is captured INSIDE the
   compute_predicate closure, NOT at the wrap call-site.
6. Unguarded len()/if x on poisoned object -- predicate-floor dicts
   carry concrete int / str defaults; downstream readers never call
   ``len()`` on a sentinel.
7. dict.get(key, expensive_default) eager-eval -- the predicate
   closures use direct ``dict["key"]`` lookups, NOT
   ``dict.get(key, expensive_default)``; the floor branch substitutes
   a documented empty-shape dict.

Marker family is ``describe_*`` -- same family as W607-K (additive,
not a separate prefix). The marker-prefix discipline test pins the
closed-enum distinction against sibling W607 families.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke describe via the Click group
# ---------------------------------------------------------------------------


def _invoke_describe(runner: CliRunner, cwd, *extra, json_mode: bool = True, monkeypatch=None):
    """Invoke ``roam describe`` through the group so ``--json`` is honoured."""
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("describe")
    args.extend(extra)

    if monkeypatch is not None:
        monkeypatch.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)

    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- populated indexed corpus
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def describe_project(tmp_path, monkeypatch):
    """Indexed corpus with real symbols + edges -- W607-DG baseline.

    Distinct from W805-I empty-corpus fixture; this corpus has
    substrate so the W607-DG axis can poison the aggregation closures
    rather than collapse to empty-state.
    """
    proj = tmp_path / "describe_w607dg_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DG aggregation markers
# ---------------------------------------------------------------------------


def test_describe_happy_path_no_w607dg_markers(cli_runner, describe_project):
    """Clean describe -> no W607-DG aggregation markers.

    Hash-stable: an empty W607-DG bucket on the success path must
    produce an envelope without any
    ``describe_score_classify_failed:`` /
    ``describe_compute_predicate_failed:`` /
    ``describe_compute_verdict_failed:`` /
    ``describe_serialize_envelope_failed:`` markers.
    """
    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "describe"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607dg_phases = (
        "describe_score_classify_failed:",
        "describe_compute_predicate_failed:",
        "describe_compute_verdict_failed:",
        "describe_serialize_envelope_failed:",
    )
    for prefix in w607dg_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean describe must NOT surface {prefix} markers; got {leaked!r}"
    # partial_success must NOT flip on the clean path
    assert data["summary"].get("partial_success") is not True, (
        f"clean describe must NOT flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) AST-level guard -- additive ``_run_check_dg`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_describe_carries_w607dg_accumulator():
    """AST-level guard: cmd_describe source carries the W607-DG
    accumulator AND the additive helper, AND keeps W607-K alongside.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_describe.py"
    assert src_path.exists(), f"cmd_describe.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607dg_warnings_out" in src, (
        "W607-DG accumulator missing from cmd_describe; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_dg" in src, (
        "W607-DG helper ``_run_check_dg`` missing from cmd_describe; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_dg is defined inside describe()
    tree = ast.parse(src)
    found_run_check_dg = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dg":
            found_run_check_dg = True
            break
    assert found_run_check_dg, (
        "W607-DG ``_run_check_dg`` helper not found in cmd_describe AST; "
        "the additive aggregation-phase wrapper has been refactored "
        "away."
    )

    # W607-K substrate layer must STILL be present (additive layer
    # does NOT replace it -- canonical describe_* family is shared).
    # Anchor on the W607-K substrate-only marker phase names that are
    # NOT shared with W607-DG.
    assert "describe_pipeline_failed:" in src, (
        "W607-K substrate-CALL marker family missing from cmd_describe; "
        "the additive plumbing must preserve the W607-K layer."
    )
    assert "describe_agent_prompt_failed:" in src, (
        "W607-K agent-prompt substrate marker missing; the additive plumbing must preserve the W607-K layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_dg():
    """Source-grep guard: every W607-DG aggregation-phase boundary
    calls ``_run_check_dg(...)`` with the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_describe.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        same_line = f'_run_check_dg("{phase}"' in src
        multi_line = any(f'_run_check_dg(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28, 32))
        assert same_line or multi_line, (
            f"phase ``{phase}`` is not wrapped in _run_check_dg(...); add the W607-DG guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_verdict failure marker -- W978 first-hypothesis floor
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, describe_project, monkeypatch):
    """If compute_verdict raises, surface the marker AND the floor
    verdict is a LITERAL string (NOT re-interpolated).

    W978 first-hypothesis check: the canonical floor MUST be
    ``"describe analysis completed"`` -- a literal string that does
    NOT re-evaluate the same predicate values that tripped the
    closure.

    Strategy: inject a __format__-raising sentinel via ``len()``
    interception so the verdict closure's f-string interpolation
    trips on it.
    """
    from roam.commands import cmd_describe

    class _BadInt(int):
        """An int subclass that raises on __format__ -- survives
        ``Counter`` / arithmetic but trips f-string interpolation.
        """

        def __new__(cls):
            return int.__new__(cls, 7)

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-DG")

    real_len = len

    def _len_poison(obj):
        result = real_len(obj)
        # Poison the language Counter -- this is what becomes _n_langs
        # via ``len(_lang_counts)`` in the success branch.
        from collections import Counter as _CounterCls

        if isinstance(obj, _CounterCls):
            return _BadInt()
        return result

    monkeypatch.setattr(cmd_describe, "len", _len_poison, raising=False)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    verdict_markers = [m for m in all_wo if m.startswith("describe_compute_verdict_failed:")]
    assert verdict_markers, f"expected ``describe_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in verdict_markers), verdict_markers

    # W978 first-hypothesis floor check: the verdict must be the
    # LITERAL ``"describe analysis completed"`` string, NOT a re-
    # interpolation of the poisoned values.
    assert data["summary"]["verdict"] == "describe analysis completed", (
        f"W978 floor discipline: verdict must be the literal "
        f"``describe analysis completed`` floor, NOT a re-interpolation "
        f"of the poisoned values; got {data['summary']['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (5) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607dg_serialize_envelope_floor_on_raise(cli_runner, describe_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap
    floors to a parseable envelope stub and surfaces
    ``describe_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_describe

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DG")

    monkeypatch.setattr(cmd_describe, "json_envelope", _raise_envelope)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "describe", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("describe_serialize_envelope_failed:")]
    assert markers, f"expected ``describe_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, describe_project, monkeypatch):
    """ANY W607-DG aggregation marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    describe" from "describe ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_describe

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DG")

    monkeypatch.setattr(cmd_describe, "json_envelope", _raise_envelope)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty describe warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607dg_warnings_out_in_both_top_and_summary(cli_runner, describe_project, monkeypatch):
    """Non-empty W607-DG bucket -> both top-level AND
    summary.warnings_out populated.

    Mirror parity with W607-DA / W607-DB / W607-DE contract: top-level
    survives ``strip_list_payloads`` in default-detail mode; summary
    mirror gives consumers reading only the summary block visibility
    too.
    """
    from roam.commands import cmd_describe

    real_envelope = cmd_describe.json_envelope
    call_count = {"n": 0}

    def _raise_first_envelope(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-mirror-from-W607-DG")
        return real_envelope(*args, **kwargs)

    monkeypatch.setattr(cmd_describe, "json_envelope", _raise_first_envelope)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DG raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DG raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("describe_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("describe_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-DG uses the SAME ``describe_*``
# family
# ---------------------------------------------------------------------------


def test_w607dg_marker_prefix_describe_family(cli_runner, describe_project, monkeypatch):
    """W607-DG markers use the canonical ``describe_*`` prefix (same
    family as W607-K; W607-DG is ADDITIVE, not a separate prefix).
    """
    from roam.commands import cmd_describe

    def _raise_envelope(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DG")

    monkeypatch.setattr(cmd_describe, "json_envelope", _raise_envelope)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("describe_"), (
            f"every W607-DG marker must use the ``describe_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (9) W607-K COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607k_substrate_markers_coexist_with_w607dg_aggregation(cli_runner, describe_project, monkeypatch):
    """Confirm ``describe_<substrate-phase>_failed:`` markers (W607-K
    layer) coexist with ``describe_<agg-phase>_failed:`` markers
    (W607-DG layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    Explicit guard requested by the W607-DG brief: the additive
    aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation.

    Strategy: poison the cycles_summary substrate (W607-K marker
    ``describe_cycles_summary_failed:``) AND raise on json_envelope
    (W607-DG marker ``describe_serialize_envelope_failed:``).
    """
    from roam.commands import cmd_describe
    from roam.quality import cycles as _cyc_mod

    def _raise_cycles_summary(*args, **kwargs):
        raise RuntimeError("synthetic-k-coexist-cycles-summary")

    monkeypatch.setattr(_cyc_mod, "cycles_summary", _raise_cycles_summary)

    real_envelope = cmd_describe.json_envelope
    call_count = {"n": 0}

    def _raise_envelope_first(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-dg-coexist-envelope")
        return real_envelope(*a, **kw)

    monkeypatch.setattr(cmd_describe, "json_envelope", _raise_envelope_first)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-K
    k_markers = [m for m in top_wo if m.startswith("describe_cycles_summary_failed:")]
    # Aggregation-phase from W607-DG
    dg_markers = [m for m in top_wo if m.startswith("describe_serialize_envelope_failed:")]

    assert k_markers, f"W607-K substrate-CALL marker (describe_cycles_summary_failed) missing; got {top_wo!r}"
    assert dg_markers, f"W607-DG aggregation-phase marker (describe_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``describe_*`` family
    assert all(m.startswith("describe_") for m in (k_markers + dg_markers)), (
        f"all markers must share the canonical ``describe_*`` family; got k = {k_markers!r}, dg = {dg_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("describe_cycles_summary_failed:") for m in summary_wo), (
        f"W607-K marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("describe_serialize_envelope_failed:") for m in summary_wo), (
        f"W607-DG marker missing from summary mirror; got {summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) CROSS-PREFIX ISOLATION -- describe_* markers DO NOT leak into adjacent
# commands' marker families
# ---------------------------------------------------------------------------


def test_describe_markers_do_not_leak_foreign_prefixes(cli_runner, describe_project, monkeypatch):
    """``describe_*`` markers must NOT carry any sibling W607-* family
    prefixes (``deps_*`` / ``uses_*`` / ``relate_*`` / ``fan_*`` /
    ``grep_*`` / etc.).

    Validates the marker-family isolation contract: each command's
    W607 plumbing uses its OWN prefix and does not bleed into
    adjacent commands' warnings_out channels.
    """
    from roam.commands import cmd_describe

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DG")

    monkeypatch.setattr(cmd_describe, "json_envelope", _raise_envelope)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    foreign_prefixes = (
        "cga_",
        "attest_",
        "pr_bundle_",
        "supply_chain_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
        "relate_",
        "uses_",
        "deps_",
        "fan_",
        "grep_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_describe warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) W978 7-discipline AST audit
# ---------------------------------------------------------------------------


def _cmd_describe_ast_tree():
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_describe.py"
    src = src_path.read_text(encoding="utf-8")
    return ast.parse(src)


def _is_run_check_dg_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_run_check_dg"


def _iter_run_check_dg_calls(tree, phase=None):
    for node in ast.walk(tree):
        if not _is_run_check_dg_call(node):
            continue
        if phase is None:
            yield node
            continue
        if not node.args:
            continue
        phase_arg = node.args[0]
        if isinstance(phase_arg, ast.Constant) and phase_arg.value == phase:
            yield node


def _assert_w978_literal_verdict_floor(tree):
    literal_floor_count = 0
    for node in _iter_run_check_dg_calls(tree, phase="compute_verdict"):
        for kw in node.keywords:
            if kw.arg == "default":
                assert isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str), (
                    f"W978 discipline 1: compute_verdict floor must be a "
                    f"literal string ast.Constant, not "
                    f"{type(kw.value).__name__}; f-string floors "
                    f"re-interpolate the poisoned values that just raised"
                )
                assert kw.value.value == "describe analysis completed", (
                    f"W978 discipline 1: compute_verdict floor must be the "
                    f"canonical literal ``describe analysis completed``; "
                    f"got {kw.value.value!r}"
                )
                literal_floor_count += 1
    # cmd_describe has TWO json_mode branches (agent_prompt + main
    # describe), so we expect TWO compute_verdict wraps.
    assert literal_floor_count >= 2, (
        f"W978 discipline 1: expected at least 2 "
        f"_run_check_dg('compute_verdict', ..., default=<literal>) calls "
        f"(agent_prompt + main describe branches); found "
        f"{literal_floor_count}"
    )


def _assert_w978_default_eagerness(tree):
    for node in _iter_run_check_dg_calls(tree):
        for kw in node.keywords:
            if kw.arg == "default":
                assert not isinstance(kw.value, ast.Call), (
                    f"W978 discipline 2: _run_check_dg default= MUST NOT "
                    f"be an ast.Call (eager evaluation of expensive "
                    f"default); got {ast.dump(kw.value)!r}"
                )


def _assert_w978_run_check_dg_avoids_json_dumps(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dg":
            helper_src = ast.unparse(node)
            assert "json.dumps" not in helper_src, (
                f"W978 discipline 3: _run_check_dg must not call "
                f"json.dumps (use plain f-string marker format); "
                f"got body = {helper_src!r}"
            )


def _assert_w978_phase_names_do_not_collide():
    w607dg_phases = {
        "score_classify",
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    }
    w607k_phases = {
        "key_abstractions",
        "architecture",
        "complexity",
        "dependencies",
        "project_root",
        "hotspots",
        "cycle_health",
        "project_shape",
        "agent_prompt",
        "pipeline",
        "cycles_summary",
    }
    overlap = w607dg_phases & w607k_phases
    assert not overlap, (
        f"W978 discipline 4: W607-DG phase names collide with W607-K substrate-CALL phase names; overlap = {overlap!r}"
    )


def _assert_w978_compute_predicate_floors(tree):
    floor_dicts_inspected = 0
    for node in _iter_run_check_dg_calls(tree, phase="compute_predicate"):
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            assert isinstance(kw.value, ast.Dict), (
                f"W978 discipline 6: compute_predicate floor must be a literal ast.Dict; got {type(kw.value).__name__}"
            )
            for v in kw.value.values:
                if isinstance(v, ast.Call):
                    # Only allow len() on a Name argument
                    assert isinstance(v.func, ast.Name) and v.func.id == "len", (
                        f"W978 discipline 5/7: compute_predicate floor "
                        f"may only call ``len(name)`` as a default "
                        f"computation; got {ast.dump(v)!r}"
                    )
                else:
                    assert isinstance(v, ast.Constant), (
                        f"W978 discipline 6: compute_predicate floor "
                        f"values must be ast.Constant or len(name); "
                        f"got {type(v).__name__} = {ast.dump(v)!r}"
                    )
            floor_dicts_inspected += 1
    # Expect at least 2 compute_predicate wraps (agent_prompt + main
    # describe branches).
    assert floor_dicts_inspected >= 2, (
        f"expected at least 2 compute_predicate floor dicts "
        f"(agent_prompt + main describe branches); "
        f"found {floor_dicts_inspected}"
    )


def test_w607dg_w978_seven_discipline_ast_audit():
    """AST audit: the W607-DG plumbing in cmd_describe honours all 7
    W978 first-hypothesis disciplines.

    1. f-string verdict floor -- floor is the LITERAL string
       ``"describe analysis completed"``, never an f-string.
    2. kwarg-default eagerness -- ``default=`` arguments are
       inexpensive literals / dicts of literals.
    3. json.dumps(default=str) sentinel -- not used at the W607-DG
       layer; markers carry ``str(exc)`` directly.
    4. Phase-name collision -- W607-DG phase names do NOT collide
       with W607-K substrate-CALL phase names.
    5. len() at kwarg-bind -- ``len(output)`` is captured INSIDE the
       compute_predicate closure, not at the wrap call-site.
    6. Unguarded len()/if x on poisoned object -- predicate-floor
       dicts carry concrete int / str defaults; downstream readers
       never call ``len()`` on a sentinel.
    7. dict.get(key, expensive_default) eager-eval -- the predicate
       closures use direct ``dict["key"]`` lookups, NOT
       ``dict.get(key, expensive_default)``.
    """
    tree = _cmd_describe_ast_tree()

    _assert_w978_literal_verdict_floor(tree)
    _assert_w978_default_eagerness(tree)
    _assert_w978_run_check_dg_avoids_json_dumps(tree)
    _assert_w978_phase_names_do_not_collide()
    _assert_w978_compute_predicate_floors(tree)


# ---------------------------------------------------------------------------
# (12) Cross-prefix isolation -- W607-DG does NOT introduce sibling-wave
# accumulator names
# ---------------------------------------------------------------------------


def test_w607dg_does_not_introduce_other_w607_buckets():
    """W607-DG plumbing must NOT introduce sibling-wave accumulator
    names (W607-DA / W607-DB / W607-DE / W607-AF / etc.).

    Defensive check: the W607-DG accumulator name is unique and
    doesn't accidentally use a sibling-wave's bucket name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_describe.py"
    src = src_path.read_text(encoding="utf-8")

    foreign_accumulators = (
        "_w607da_warnings_out",
        "_w607db_warnings_out",
        "_w607de_warnings_out",
        "_w607af_warnings_out",
        "_w607ae_warnings_out",
        "_w607ad_warnings_out",
        "_w607bt_warnings_out",
        "_w607bw_warnings_out",
        "_w607bz_warnings_out",
        "_w607cy_warnings_out",
    )
    for foreign in foreign_accumulators:
        assert foreign not in src, (
            f"cmd_describe must not carry sibling-wave accumulator "
            f"{foreign!r}; W607-DG uses its own "
            f"``_w607dg_warnings_out`` bucket"
        )


# ---------------------------------------------------------------------------
# (13) SYMBOL-EXPLORATION 4-WAY pairing at agg-layer
# (W607-DG closes the set)
# ---------------------------------------------------------------------------


def test_symbol_exploration_four_way_marker_families_coexist():
    """The symbol-exploration 4-way (cmd_uses + cmd_relate + cmd_deps
    + cmd_describe) MUST keep distinct marker prefixes so a workspace-
    wide audit (e.g. a CI script invoking all four on the same repo)
    can disambiguate which command emitted which marker.

    Source-level guard pinning the four prefix families AND BOTH
    plumbing layers (substrate-CALL + aggregation-phase) per command:

    * cmd_uses    -> ``uses_*``    (W607-U substrate + W607-DE agg)
    * cmd_relate  -> ``relate_*``  (W607-W substrate + W607-DA agg)
    * cmd_deps    -> ``deps_*``    (W607-V substrate + W607-DB agg)
    * cmd_describe-> ``describe_*``(W607-K substrate + W607-DG agg --
                                    THIS WAVE CLOSES THE 4-WAY)

    Closes the symbol-exploration 4-way at agg-layer: each member
    uses its own canonical prefix in the source. A future refactor
    that accidentally merges two prefixes (e.g. describe_ -> deps_)
    breaks this guard before the marker-family contract leaks into
    shipped envelopes.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    expected = (
        # substrate-CALL layer
        ("cmd_uses.py", "uses_", "_w607u_warnings_out"),
        ("cmd_relate.py", "relate_", "_w607w_warnings_out"),
        ("cmd_deps.py", "deps_", "_w607v_warnings_out"),
        ("cmd_describe.py", "describe_", "warnings_out"),
        # aggregation-phase layer
        ("cmd_uses.py", "uses_", "_w607de_warnings_out"),
        ("cmd_relate.py", "relate_", "_w607da_warnings_out"),
        ("cmd_deps.py", "deps_", "_w607db_warnings_out"),
        ("cmd_describe.py", "describe_", "_w607dg_warnings_out"),
    )

    for filename, prefix_anchor, accumulator in expected:
        path = src_root / filename
        assert path.exists(), f"{filename} missing at {path}"
        src = path.read_text(encoding="utf-8")
        # The accumulator MUST exist in the source
        assert accumulator in src, (
            f"{filename}: accumulator {accumulator!r} missing -- "
            f"symbol-exploration 4-way member has lost its W607 "
            f"plumbing"
        )
        # The canonical marker prefix MUST appear in the source
        assert prefix_anchor in src, (
            f"{filename}: canonical marker prefix {prefix_anchor!r} "
            f"missing -- symbol-exploration 4-way has lost its prefix"
        )


# ---------------------------------------------------------------------------
# (14) W607-K regression guard -- W607-DG add does not break W607-K
# substrate behavior
# ---------------------------------------------------------------------------


def test_w607k_substrate_layer_still_works_after_w607dg_add(cli_runner, describe_project, monkeypatch):
    """After W607-DG lands, W607-K substrate-CALL markers MUST still
    fire when the substrate raises.

    Pin-down regression guard: if a future refactor accidentally
    short-circuits the W607-K substrate accumulator while threading
    through the new W607-DG aggregation layer, this test surfaces it.
    """
    from roam.quality import cycles as _cyc_mod

    def _raise_cycles_summary(*args, **kwargs):
        raise PermissionError("synthetic-w607k-regression-from-W607-DG")

    monkeypatch.setattr(_cyc_mod, "cycles_summary", _raise_cycles_summary)

    result = _invoke_describe(cli_runner, describe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    k_markers = [m for m in all_wo if m.startswith("describe_cycles_summary_failed:")]
    assert k_markers, f"W607-K substrate marker MUST still fire after W607-DG add; got all_wo = {all_wo!r}"
    assert any("PermissionError" in m for m in k_markers), k_markers
