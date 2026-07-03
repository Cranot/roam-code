"""W607-CY -- additive aggregation-phase plumbing for ``cmd_fan``.

cmd_fan is the dual-mode fan-out/fan-in detector. With W607-CY landed,
the full json-mode emit path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-X (6 phases: fetch_symbol_rows /
    filter_tooling / file_scope_metrics / emit_findings_symbol /
    fetch_file_rows / emit_findings_file)
  - aggregation-phase layer: W607-CY (3 phases: compute_predicate /
    compute_verdict / serialize_envelope)

Both layers share the canonical ``fan_*`` marker family and the
``fan_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607x_warnings_out`` substrate-CALL +
``_w607cy_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

Relation to W607-X
------------------

cmd_fan already carries W607-X substrate-CALL plumbing covering 6
substrate-helper boundaries on the json-mode path. W607-CY is ADDITIVE
on top of W607-X, extending marker coverage to the AGGREGATION-PHASE
boundaries that W607-X left unguarded:

  - ``compute_predicate``   -- per-mode extraction of fan-count
                                predicate fields (top_fan_in /
                                top_fan_out / counts) used to compose
                                the verdict string.
  - ``compute_verdict``     -- verdict string assembly (LAW 6
                                standalone-parse).
  - ``serialize_envelope``  -- ``json_envelope("fan", ...)`` projection.

cmd_fan is NOT a risk scorer (unlike cmd_attest / cmd_pr_bundle); it
is a fan-out / fan-in detector with no auto_log call. So the W607-CY
phase set drops ``score_classify`` / ``severity_normalize`` /
``auto_log`` and keeps the 3 phases above. Mirror of cmd_cga's
W607-BZ phase set adapted for the dual-mode aggregator.

W978 7-discipline pre-fix audit
-------------------------------

1. f-string verdict floor -- floor is a LITERAL string
   ``"fan analysis completed"``, never an f-string re-interpolating
   the same predicate values that just raised.
2. kwarg-default eagerness -- ``default=`` arguments to
   ``_run_check_cy`` are plain dicts / strings; no expensive call.
3. json.dumps(default=str) sentinel -- not used; markers carry
   ``str(exc)`` directly.
4. Phase-name collision -- W607-CY phase names
   (``compute_predicate`` / ``compute_verdict`` / ``serialize_envelope``)
   do NOT collide with any W607-X substrate-CALL phase name
   (``fetch_symbol_rows`` / ``filter_tooling`` / ``file_scope_metrics``
   / ``emit_findings_symbol`` / ``fetch_file_rows`` /
   ``emit_findings_file``).
5. len() at kwarg-bind -- ``len(rows)`` is captured into an int
   BEFORE being passed through ``_run_check_cy``; no poisoned-object
   ``len()`` at the wrap call-site.
6. Unguarded len()/if x on poisoned object -- predicate-floor dicts
   carry concrete int / str defaults; downstream readers never call
   ``len()`` on a sentinel.
7. dict.get(key, expensive_default) eager-eval -- predicate-extraction
   uses direct ``dict["key"]`` lookups inside the wrap, NOT
   ``dict.get(key, expensive_default)``; the floor branch substitutes
   a documented empty-shape dict.

Marker family is ``fan_*`` -- same family as W607-X (additive, not a
separate prefix). The marker-prefix discipline test pins the
closed-enum distinction against sibling W607 families.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
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
# Helpers -- invoke fan via the Click group
# ---------------------------------------------------------------------------


def _invoke_fan(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam fan`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("fan")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with cross-file edges for fan analysis
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def fan_project(tmp_path, monkeypatch):
    """Indexed corpus with cross-file edges for fan analysis.

    Mirror of the W607-X fixture: three-file fixture so cmd_fan
    substrates have signal (graph_metrics populates with non-zero
    degrees, file_edges has rows, _file_scope_metrics has cross-file
    targets).
    """
    proj = tmp_path / "fan_w607cy_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "def shared_helper():\n    return 1\n\ndef secondary_helper():\n    return shared_helper()\n",
        encoding="utf-8",
    )
    (src / "consumer_a.py").write_text(
        "from src.core import shared_helper, secondary_helper\n\n"
        "def use_a():\n"
        "    shared_helper()\n"
        "    return secondary_helper()\n",
        encoding="utf-8",
    )
    (src / "consumer_b.py").write_text(
        "from src.core import shared_helper\n\ndef use_b():\n    return shared_helper()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CY aggregation markers
# ---------------------------------------------------------------------------


def test_fan_symbol_happy_path_no_w607cy_markers(cli_runner, fan_project):
    """Clean fan symbol-mode -> no W607-CY aggregation markers.

    Hash-stable: an empty W607-CY bucket on the success path must
    produce an envelope without any
    ``fan_compute_predicate_failed:`` /
    ``fan_compute_verdict_failed:`` /
    ``fan_serialize_envelope_failed:`` markers.
    """
    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "fan"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cy_phases = (
        "fan_compute_predicate_failed:",
        "fan_compute_verdict_failed:",
        "fan_serialize_envelope_failed:",
    )
    for prefix in w607cy_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean fan symbol-mode must NOT surface {prefix} markers; got {leaked!r}"
    # partial_success must NOT flip on the clean path
    assert data["summary"].get("partial_success") is not True, (
        f"clean fan must NOT flip partial_success; got summary = {data['summary']!r}"
    )


def test_fan_file_happy_path_no_w607cy_markers(cli_runner, fan_project):
    """Clean fan file-mode -> no W607-CY aggregation markers (file-mode parity)."""
    result = _invoke_fan(cli_runner, fan_project, "file")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "fan"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cy_phases = (
        "fan_compute_predicate_failed:",
        "fan_compute_verdict_failed:",
        "fan_serialize_envelope_failed:",
    )
    for prefix in w607cy_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean fan file-mode must NOT surface {prefix} markers; got {leaked!r}"
    assert data["summary"].get("partial_success") is not True, data["summary"]


# ---------------------------------------------------------------------------
# (2) AST-level guard -- additive ``_run_check_cy`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_fan_carries_w607cy_accumulator():
    """AST-level guard: cmd_fan source carries the W607-CY accumulator.

    Pins the canonical W607-CY anchors so a future refactor that
    removes the additive instrumentation (or merges it back into
    W607-X) fails this guard rather than silently regressing the
    aggregation-phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"
    assert src_path.exists(), f"cmd_fan.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607cy_warnings_out" in src, (
        "W607-CY accumulator missing from cmd_fan; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cy" in src, (
        "W607-CY helper ``_run_check_cy`` missing from cmd_fan; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_cy is defined inside fan()
    tree = ast.parse(src)
    found_run_check_cy = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cy":
            found_run_check_cy = True
            break
    assert found_run_check_cy, (
        "W607-CY ``_run_check_cy`` helper not found in cmd_fan AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-X must still be present (additive layer does NOT replace it)
    assert "_w607x_warnings_out" in src, (
        "W607-X accumulator vanished alongside the W607-CY add; the "
        "additive plumbing must preserve the W607-X substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cy():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cy(...)`` with the canonical phase name.

    The three phases must appear inside a ``_run_check_cy("<phase>", ...)``
    call inside cmd_fan. Multi-indent variants are all valid wrap
    call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        same_line = f'_run_check_cy("{phase}"' in src
        multi_line = any(f'_run_check_cy(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28, 32))
        assert same_line or multi_line, (
            f"phase ``{phase}`` is not wrapped in _run_check_cy(...); add the W607-CY guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker -- symbol mode
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_format_symbol(cli_runner, fan_project, monkeypatch):
    """If compute_predicate raises (symbol mode), surface the marker.

    Drive the failure by patching ``max`` inside cmd_fan to raise so
    the inner closure trips. The W607-CY wrap surfaces a structured
    marker rather than crashing the envelope.
    """
    from roam.commands import cmd_fan

    real_max = max

    def _raising_max(*args, **kwargs):
        # Tripwire: raise specifically inside the compute_predicate
        # closure but let other max() calls (in symbol_items building,
        # etc.) succeed. The compute_predicate closure passes a key=
        # callable so we recognize it.
        if "key" in kwargs:
            raise RuntimeError("synthetic-compute-predicate-symbol-from-W607-CY")
        return real_max(*args, **kwargs)

    monkeypatch.setattr(cmd_fan, "max", _raising_max, raising=False)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("fan_compute_predicate_failed:")]
    assert markers, f"expected ``fan_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker -- W978 first-hypothesis floor check
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format_symbol(cli_runner, fan_project, monkeypatch):
    """If compute_verdict raises (symbol mode), surface the marker
    AND the floor verdict is a LITERAL string (NOT re-interpolated).

    W978 first-hypothesis check: the canonical floor MUST be
    ``"fan analysis completed"`` -- a literal string that does NOT
    re-evaluate the same predicate values that tripped the closure.
    """
    from roam.commands import cmd_fan

    # Patch json_envelope FIRST so we can capture the args it gets
    # (we need to inspect summary.verdict after compute_verdict floors).
    captured_calls: list = []
    real_envelope = cmd_fan.json_envelope

    def _capturing_envelope(name, **kwargs):
        captured_calls.append({"name": name, "kwargs": kwargs})
        return real_envelope(name, **kwargs)

    monkeypatch.setattr(cmd_fan, "json_envelope", _capturing_envelope)

    # Patch the predicate's max so the predicate floors AND the
    # verdict closure trips on the empty-shape floor (top_in_degree=0,
    # top_in_name=""). We need the verdict to ALSO raise, so patch
    # the predicate to return a poisoned object.
    class _BadStr:
        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-symbol-from-W607-CY")

    # Replace max inside cmd_fan so compute_predicate returns a dict
    # whose values contain a __format__-raising sentinel
    real_max = max

    def _max_with_bad(*args, **kwargs):
        result = real_max(*args, **kwargs)

        # Wrap the row to inject a poisoned value
        class _PoisonRow:
            def __getitem__(self, key):
                if key == "name":
                    return _BadStr()
                if key == "in_degree":
                    return 1
                if key == "out_degree":
                    return 1
                if key == "fan_in":
                    return _BadStr()
                if key == "fan_out":
                    return 1
                if key == "path":
                    return _BadStr()
                return result[key]

        return _PoisonRow()

    monkeypatch.setattr(cmd_fan, "max", _max_with_bad, raising=False)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("fan_compute_verdict_failed:")]
    assert markers, f"expected ``fan_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers

    # W978 first-hypothesis floor check: the verdict must be the
    # LITERAL ``"fan analysis completed"`` string, NOT a re-interpolation
    # of the poisoned values.
    assert data["summary"]["verdict"] == "fan analysis completed", (
        f"W978 floor discipline: verdict must be the literal "
        f"``fan analysis completed`` floor, NOT a re-interpolation "
        f"of the poisoned values; got {data['summary']['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607cy_serialize_envelope_floor_on_raise_symbol(cli_runner, fan_project, monkeypatch):
    """If ``json_envelope`` raises on the symbol-mode success path,
    the wrap floors to a parseable envelope stub and surfaces
    ``fan_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_fan

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-symbol-from-W607-CY")

    monkeypatch.setattr(cmd_fan, "json_envelope", _raise_envelope)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "fan", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("fan_serialize_envelope_failed:")]
    assert markers, f"expected ``fan_serialize_envelope_failed:`` marker; got {top_wo!r}"


def test_w607cy_serialize_envelope_floor_on_raise_file(cli_runner, fan_project, monkeypatch):
    """File-mode parity: if ``json_envelope`` raises, floor + marker."""
    from roam.commands import cmd_fan

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-file-from-W607-CY")

    monkeypatch.setattr(cmd_fan, "json_envelope", _raise_envelope)

    result = _invoke_fan(cli_runner, fan_project, "file")
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "fan", data
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("fan_serialize_envelope_failed:")]
    assert markers, f"expected ``fan_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, fan_project, monkeypatch):
    """ANY W607-CY or W607-X marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    fan" from "fan ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CY")

    # Trip a W607-X substrate boundary to surface a fan_* marker
    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty fan warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cy_warnings_out_in_both_top_and_summary(cli_runner, fan_project, monkeypatch):
    """Non-empty W607-CY bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BZ / W607-BT contract: top-level survives
    ``strip_list_payloads`` in default-detail mode; summary mirror gives
    consumers reading only the summary block visibility too.
    """
    from roam.commands import cmd_fan

    real_envelope = cmd_fan.json_envelope
    call_count = {"n": 0}

    def _raise_first_envelope(*args, **kwargs):
        # Raise on the first call (the success path); subsequent calls
        # (if any) succeed via the floor path's rebuild.
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-mirror-from-W607-CY")
        return real_envelope(*args, **kwargs)

    monkeypatch.setattr(cmd_fan, "json_envelope", _raise_first_envelope)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CY raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CY raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("fan_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("fan_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CY uses the SAME ``fan_*`` family
# ---------------------------------------------------------------------------


def test_w607cy_marker_prefix_fan_family(cli_runner, fan_project, monkeypatch):
    """W607-CY markers use the canonical ``fan_*`` prefix (same family
    as W607-X; W607-CY is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CY marker that leaks into a sibling W607-*
    family (e.g. ``cga_*`` / ``attest_*`` / ``preflight_*`` /
    ``relate_*`` / ``deps_*``) breaks the closed-enum marker-family
    contract.
    """
    from roam.commands import cmd_fan

    def _raise_envelope(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CY")

    monkeypatch.setattr(cmd_fan, "json_envelope", _raise_envelope)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("fan_"), f"every W607-CY marker must use the ``fan_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) W607-X COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607x_substrate_markers_coexist_with_w607cy_aggregation(cli_runner, fan_project, monkeypatch):
    """Confirm ``fan_<substrate-phase>_failed:`` markers (W607-X layer)
    coexist with ``fan_<agg-phase>_failed:`` markers (W607-CY layer) --
    both in same family, but threaded through different buckets at
    envelope-emit.

    This is the explicit guard requested by the W607-CY brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``fan_<substrate-phase>_failed:`` vs.
    ``fan_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_fan

    # W607-X substrate boundary -- _file_scope_metrics
    def _raise_scope(*a, **kw):
        raise RuntimeError("synthetic-x-coexist-scope")

    # W607-CY aggregation boundary -- json_envelope
    real_envelope = cmd_fan.json_envelope
    call_count = {"n": 0}

    def _raise_envelope_first(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-cy-coexist-envelope")
        return real_envelope(*a, **kw)

    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise_scope)
    monkeypatch.setattr(cmd_fan, "json_envelope", _raise_envelope_first)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-X
    x_markers = [m for m in top_wo if m.startswith("fan_file_scope_metrics_failed:")]
    # Aggregation-phase from W607-CY
    cy_markers = [m for m in top_wo if m.startswith("fan_serialize_envelope_failed:")]

    assert x_markers, f"W607-X substrate-CALL marker (fan_file_scope_metrics_failed) missing; got {top_wo!r}"
    assert cy_markers, f"W607-CY aggregation-phase marker (fan_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``fan_*`` family
    assert all(m.startswith("fan_") for m in (x_markers + cy_markers)), (
        f"all markers must share the canonical ``fan_*`` family; got x = {x_markers!r}, cy = {cy_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("fan_file_scope_metrics_failed:") for m in summary_wo), (
        f"W607-X marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("fan_serialize_envelope_failed:") for m in summary_wo), (
        f"W607-CY marker missing from summary mirror; got {summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- fan_* markers DO NOT leak into adjacent
# commands' marker families
# ---------------------------------------------------------------------------


def test_fan_markers_do_not_leak_foreign_prefixes(cli_runner, fan_project, monkeypatch):
    """``fan_*`` markers must NOT carry any sibling W607-* family
    prefixes (``cga_*`` / ``attest_*`` / ``pr_bundle_*`` /
    ``preflight_*`` / ``relate_*`` / etc.).

    Validates the marker-family isolation contract: each command's
    W607 plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_fan

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-CY")

    monkeypatch.setattr(cmd_fan, "json_envelope", _raise_envelope)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
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
        "deps_",
        "grep_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_fan warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) W978 7-discipline AST audit -- the W607-CY plumbing follows all
# 7 disciplines
# ---------------------------------------------------------------------------


def _cmd_fan_ast() -> ast.Module:
    """Parse ``cmd_fan.py`` source into an AST for the W978 audit helpers."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"
    return ast.parse(src_path.read_text(encoding="utf-8"))


def _run_check_cy_calls(tree: ast.AST):
    """Yield every ``_run_check_cy(...)`` Call node in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_run_check_cy":
            yield node


def _calls_for_phase(tree: ast.AST, phase: str):
    """Yield ``_run_check_cy`` calls whose first positional arg is the
    string constant ``phase``."""
    for call in _run_check_cy_calls(tree):
        if not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and first.value == phase:
            yield call


def _default_kwarg(call: ast.Call):
    """Return the ``default=`` kwarg value of ``call`` (or ``None``).

    A Python call cannot bind the same keyword twice, so the first match
    is the only one.
    """
    for kw in call.keywords:
        if kw.arg == "default":
            return kw.value
    return None


def _audit_d1_verdict_floor_is_literal(tree: ast.AST) -> None:
    """Discipline 1: the ``compute_verdict`` floor is the literal string
    ``"fan analysis completed"``, never an f-string re-interpolating the
    predicate values that just raised."""
    found = False
    for call in _calls_for_phase(tree, "compute_verdict"):
        default = _default_kwarg(call)
        if default is None:
            continue
        assert isinstance(default, ast.Constant) and isinstance(default.value, str), (
            f"W978 discipline 1: compute_verdict floor must be a "
            f"literal string ast.Constant, not {type(default).__name__}; "
            f"f-string floors re-interpolate the poisoned values "
            f"that just raised"
        )
        assert default.value == "fan analysis completed", (
            f"W978 discipline 1: compute_verdict floor must be the "
            f"canonical literal ``fan analysis completed``; "
            f"got {default.value!r}"
        )
        found = True
    assert found, (
        "W978 discipline 1: no _run_check_cy('compute_verdict', ..., "
        "default=<literal>) call found; the verdict floor must be a "
        "literal string"
    )


def _audit_d2_kwarg_defaults_not_calls(tree: ast.AST) -> None:
    """Discipline 2: no ``default=`` argument is an ``ast.Call`` (which
    would eagerly evaluate an expensive default at call time)."""
    for call in _run_check_cy_calls(tree):
        default = _default_kwarg(call)
        if default is None:
            continue
        assert not isinstance(default, ast.Call), (
            f"W978 discipline 2: _run_check_cy default= MUST NOT be "
            f"an ast.Call (eager evaluation of expensive default); "
            f"got {ast.dump(default)!r}"
        )


def _audit_d3_no_json_dumps_in_helper(tree: ast.AST) -> None:
    """Discipline 3: the ``_run_check_cy`` helper body must not call
    ``json.dumps`` -- markers carry ``str(exc)`` directly."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cy":
            helper_src = ast.unparse(node)
            assert "json.dumps" not in helper_src, (
                f"W978 discipline 3: _run_check_cy must not call json.dumps "
                f"(use plain f-string marker format); got body = {helper_src!r}"
            )


def _audit_d4_no_phase_name_collision() -> None:
    """Discipline 4: W607-CY phase names must not collide with the
    W607-X substrate-CALL phase names."""
    w607cy_phases = {"compute_predicate", "compute_verdict", "serialize_envelope"}
    w607x_phases = {
        "fetch_symbol_rows",
        "filter_tooling",
        "file_scope_metrics",
        "emit_findings_symbol",
        "fetch_file_rows",
        "emit_findings_file",
    }
    overlap = w607cy_phases & w607x_phases
    assert not overlap, (
        f"W978 discipline 4: W607-CY phase names collide with W607-X substrate-CALL phase names; overlap = {overlap!r}"
    )


def _audit_d567_predicate_floor_constants(tree: ast.AST) -> None:
    """Disciplines 5/6/7: each ``compute_predicate`` floor is a literal
    ``ast.Dict`` of ``ast.Constant`` values (or a single ``len(name)``);
    no expensive-call defaults, no poisoned-object ``len()``, no
    ``dict.get(key, expensive_default)``."""
    inspected = 0
    for call in _calls_for_phase(tree, "compute_predicate"):
        default = _default_kwarg(call)
        if default is None:
            continue
        # The floor must be an ast.Dict whose values are ast.Constant
        # (concrete int / str), NOT ast.Call to an expensive helper and
        # NOT ast.Attribute references that could be poisoned.
        assert isinstance(default, ast.Dict), (
            f"W978 discipline 6: compute_predicate floor must be a literal ast.Dict; got {type(default).__name__}"
        )
        for value in default.values:
            # Allow ast.Constant + ast.Call(len, ...) -- the len(rows)
            # call IS evaluated at floor-construction time but rows is the
            # live list, not a poisoned object. Block everything else.
            if isinstance(value, ast.Call):
                assert isinstance(value.func, ast.Name) and value.func.id == "len", (
                    f"W978 discipline 5/7: compute_predicate floor "
                    f"may only call ``len(name)`` as a default "
                    f"computation; got {ast.dump(value)!r}"
                )
            else:
                assert isinstance(value, ast.Constant), (
                    f"W978 discipline 6: compute_predicate floor "
                    f"values must be ast.Constant or len(name); "
                    f"got {type(value).__name__} = {ast.dump(value)!r}"
                )
        inspected += 1
    assert inspected >= 2, f"expected at least 2 compute_predicate floor dicts (symbol + file mode); found {inspected}"


def test_w607cy_w978_seven_discipline_ast_audit():
    """AST audit: the W607-CY plumbing in cmd_fan honours all 7 W978
    first-hypothesis disciplines.

    1. f-string verdict floor -- floor is the LITERAL string
       ``"fan analysis completed"``, never an f-string.
    2. kwarg-default eagerness -- ``default=`` arguments are
       inexpensive literals / dicts of literals.
    3. json.dumps(default=str) sentinel -- not used at the W607-CY
       layer; markers carry ``str(exc)`` directly.
    4. Phase-name collision -- W607-CY phase names do NOT collide
       with W607-X substrate-CALL phase names.
    5. len() at kwarg-bind -- ``len(rows)`` is captured into an int
       BEFORE the wrap call-site, not inside the kwarg-bind of
       ``_run_check_cy(...)``.
    6. Unguarded len()/if x on poisoned object -- predicate-floor dicts
       carry concrete int / str defaults; downstream readers never
       call ``len()`` on a sentinel.
    7. dict.get(key, expensive_default) eager-eval -- the predicate
       closures use direct ``dict["key"]`` lookups, NOT
       ``dict.get(key, expensive_default)``.
    """
    tree = _cmd_fan_ast()

    _audit_d1_verdict_floor_is_literal(tree)
    _audit_d2_kwarg_defaults_not_calls(tree)
    _audit_d3_no_json_dumps_in_helper(tree)
    _audit_d4_no_phase_name_collision()
    _audit_d567_predicate_floor_constants(tree)


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- W607-CY does NOT introduce W607-AF /
# W607-BZ / W607-BT / W607-BW prefix markers
# ---------------------------------------------------------------------------


def test_w607cy_does_not_introduce_other_w607_buckets(cli_runner, fan_project):
    """W607-CY plumbing must NOT introduce W607-AF / W607-BZ / W607-BT /
    W607-BW / W607-AD / W607-AE prefix markers.

    Defensive check: the W607-CY accumulator name is unique and
    doesn't accidentally use a sibling-wave's bucket name. Source-grep
    on cmd_fan.py.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"
    src = src_path.read_text(encoding="utf-8")

    foreign_accumulators = (
        "_w607af_warnings_out",
        "_w607ae_warnings_out",
        "_w607ad_warnings_out",
        "_w607bt_warnings_out",
        "_w607bw_warnings_out",
        "_w607bz_warnings_out",
    )
    for foreign in foreign_accumulators:
        assert foreign not in src, (
            f"cmd_fan must not carry sibling-wave accumulator "
            f"{foreign!r}; W607-CY uses its own ``_w607cy_warnings_out`` "
            f"bucket"
        )
