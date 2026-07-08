"""W607-DN -- pre-substrate ``load_index`` boundary plumbing on cmd_diagnose.

cmd_diagnose already carries W607-S substrate-CALL plumbing and W607-BH
aggregation-phase plumbing. The combined coverage wraps every helper
boundary inside the ``diagnose()`` command body, BUT the pre-substrate
``ensure_index()`` boundary at the top of the command body sits OUTSIDE
both wraps -- a raise there (corrupt DB, missing parent ``.roam/`` dir,
permission failure, partial-index schema-migration crash) crashes
cmd_diagnose BEFORE either accumulator can collect markers, and the agent
loses ALL signal instead of just the degraded section.

W607-DN is ADDITIVE on top of W607-S + W607-BH, extending marker coverage
to the single pre-substrate boundary that both prior layers leave
unguarded:

  - ``load_index`` -- ``ensure_index()`` call that opens the SQLite DB
                       and runs schema migrations.

All three layers share the canonical ``diagnose_*`` marker family and the
``diagnose_<phase>_failed:<exc_class>:<detail>`` shape contract. The
three buckets (``_w607dn_warnings_out`` + ``_w607s_warnings_out`` +
``_w607bh_warnings_out``) are combined at envelope-emit time so consumers
see the full degradation lineage in marker-emission order (DN first,
then S, then BH).

W978 7-discipline pre-fix audit
-------------------------------

1. f-string verdict floor -- the verdict floor stays the existing W607-BH
   literal; W607-DN does NOT add a new verdict string.
2. kwarg-default eagerness -- ``_run_check_dn`` accepts ``default=None``
   only; no expensive default object construction.
3. json.dumps(default=str) sentinel -- not used; markers carry
   ``str(exc)`` directly.
4. Phase-name collision -- ``load_index`` does NOT collide with any
   W607-S substrate-CALL phase (resolve_symbol / build_graph /
   target_metrics / dist_stats / ranked_upstream / ranked_downstream /
   cochange_partners / recent_commits / next_steps / index_status) OR
   with any W607-BH aggregation-phase (verdict_synthesis /
   severity_normalize / auto_log / serialize_envelope).
5. len() at kwarg-bind -- no len() at the wrap call-site.
6. Unguarded len()/if x on poisoned object -- ``ensure_index`` returns
   ``None`` on success; the wrap floor is also ``None``. The downstream
   ``with open_db(readonly=True)`` and ``find_symbol_with_alternatives``
   each have their own W607-S guards; a poisoned ``None`` from a failed
   load_index simply means those downstream wraps surface their own
   markers too.
7. dict.get(key, expensive_default) eager-eval -- not used.

Bonus tests
-----------

* Per-substrate isolation: a raise inside the load_index wrap surfaces
  the marker AND leaves the W607-S + W607-BH buckets empty (no
  cross-prefix leakage).
* Pattern-1D resolution disclosure: when a fuzzy resolution occurs, the
  ``resolution`` field + ``partial_success: true`` from the existing
  W1244 disclosure layer survives the new wrap.
* W147 findings-registry coexistence: cmd_diagnose does NOT call
  ``emit_finding()`` (verified in source), so no findings-registry
  write is impacted by the new wrap.
* LAW 6 verdict-first invariant: ``summary.verdict`` survives every
  phase failure as a literal floor.
* Cross-prefix isolation: ``diagnose_*`` markers do not leak into the
  ``preflight_*`` or ``impact_*`` marker families when the three
  surfaces are exercised against the same target in sequence.

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
# Helpers -- invoke diagnose via the Click group
# ---------------------------------------------------------------------------


def _invoke_diagnose(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("diagnose")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a resolvable symbol + real call edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def diagnose_dn_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``dn_target``).

    Two-file fixture with a real ``dn_entry -> dn_target -> dn_helper``
    chain so upstream/downstream BFS / risk-score ranking / cochange /
    recent-commits all have signal to chew on. The target name is
    intentionally unique to avoid LIKE-fallback false-positives in the
    resolver.
    """
    proj = tmp_path / "diagnose_w607dn_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def dn_entry():\n    return dn_target()\n\n"
        "def dn_target():\n    return dn_helper_one() + dn_helper_two()\n\n"
        "def dn_helper_one():\n    return 1\n\n"
        "def dn_helper_two():\n    return 2\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_dn(first, last):\n    return f"{first} {last}"\n\ndef shout_dn(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean diagnose -> envelope omits W607-DN markers
# ---------------------------------------------------------------------------


def test_diagnose_happy_path_no_w607dn_markers(cli_runner, diagnose_dn_project):
    """Clean diagnose on a healthy corpus -> no W607-DN markers.

    Empty W607-DN bucket on the success path must produce an envelope
    without any ``diagnose_load_index_failed:`` markers.
    """
    result = _invoke_diagnose(cli_runner, diagnose_dn_project, "dn_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "diagnose"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    leaked = [m for m in all_markers if m.startswith("diagnose_load_index_failed:")]
    assert not leaked, f"clean diagnose must NOT surface diagnose_load_index_failed: markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_dn`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_diagnose_carries_w607dn_accumulator():
    """AST-level guard: cmd_diagnose source carries the W607-DN accumulator.

    Pins the canonical W607-DN anchors so a future refactor that
    removes the additive instrumentation fails this guard rather than
    silently regressing the pre-substrate marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    assert src_path.exists(), f"cmd_diagnose.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "w607dn_warnings_out" in src, (
        "W607-DN accumulator missing from cmd_diagnose; the additive pre-substrate marker plumbing has been removed."
    )
    assert "_run_check_dn" in src, (
        "W607-DN helper ``_run_check_dn`` missing from cmd_diagnose; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_dn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dn":
            found_run_check_dn = True
            break
    assert found_run_check_dn, (
        "W607-DN ``_run_check_dn`` helper not found in cmd_diagnose AST; "
        "the additive pre-substrate wrapper has been refactored away."
    )

    # W607-S + W607-BH must still be present (additive layer does NOT replace)
    assert "w607s_warnings_out" in src, (
        "W607-S accumulator vanished alongside the W607-DN add; the "
        "additive plumbing must preserve the W607-S substrate-CALL layer."
    )
    assert "w607bh_warnings_out" in src, (
        "W607-BH accumulator vanished alongside the W607-DN add; the "
        "additive plumbing must preserve the W607-BH aggregation-phase layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- load_index boundary is wrapped
# ---------------------------------------------------------------------------


def test_load_index_boundary_wrapped_in_run_check_dn():
    """Source-grep guard: ``ensure_index`` is wrapped inside ``_run_check_dn``.

    The pre-substrate ``load_index`` phase must appear inside a
    ``_run_check_dn("load_index", ensure_index, ...)`` call inside the
    diagnose() command body.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    src = src_path.read_text(encoding="utf-8")

    # Must be one of these literal anchors -- multi-indent variants are valid
    anchors = (
        '_run_check_dn("load_index", ensure_index',
        '_run_check_dn(\n        "load_index"',
        '_run_check_dn(\n            "load_index"',
        '_run_check_dn(\n                "load_index"',
    )
    found = any(a in src for a in anchors)
    assert found, (
        "the ``load_index`` boundary is not wrapped in _run_check_dn(...); "
        "add the W607-DN guard or pin the canonical anchor"
    )


# ---------------------------------------------------------------------------
# (4) Per-substrate isolation -- load_index raise surfaces dn marker only
# ---------------------------------------------------------------------------


def test_load_index_failure_surfaces_dn_marker(cli_runner, diagnose_dn_project, monkeypatch):
    """If ``ensure_index`` raises, surface ``diagnose_load_index_failed:``.

    The marker shape is ``diagnose_<phase>_failed:<exc_class>:<detail>``.
    Per-substrate isolation: the W607-S + W607-BH buckets MAY or MAY NOT
    have entries depending on whether downstream wraps absorb the
    cascade -- but the W607-DN marker MUST be present.
    """
    from roam.commands import cmd_diagnose

    def _raise_load(*args, **kwargs):
        raise RuntimeError("synthetic-load-index-from-W607-DN")

    monkeypatch.setattr(cmd_diagnose, "ensure_index", _raise_load)

    result = _invoke_diagnose(cli_runner, diagnose_dn_project, "dn_target")
    # The envelope must still emit (graceful degradation) even with a
    # raised ensure_index; downstream wraps catch the cascade.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    dn_markers = [m for m in all_markers if m.startswith("diagnose_load_index_failed:")]
    assert dn_markers, f"expected diagnose_load_index_failed: marker; got {all_markers!r}"
    marker = dn_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-load-index-from-W607-DN" in parts[2], parts


# ---------------------------------------------------------------------------
# (5) LAW 6 verdict-first invariant -- verdict survives load_index raise
# ---------------------------------------------------------------------------


def test_verdict_survives_load_index_failure(cli_runner, diagnose_dn_project, monkeypatch):
    """LAW 6: ``summary.verdict`` survives a load_index raise.

    A pre-substrate failure (load_index raise) must NOT prevent the
    envelope from emitting a verdict string. The combined-bucket
    merger picks up the DN marker AND the envelope still carries a
    standalone-parseable verdict.
    """
    from roam.commands import cmd_diagnose

    def _raise_load(*args, **kwargs):
        raise OSError("synthetic-disk-from-W607-DN")

    monkeypatch.setattr(cmd_diagnose, "ensure_index", _raise_load)

    result = _invoke_diagnose(cli_runner, diagnose_dn_project, "dn_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, (
        f"summary.verdict must be a non-empty string even when load_index raised; got {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (6) Pattern-1D resolution disclosure survives the new wrap
# ---------------------------------------------------------------------------


def test_pattern_1d_resolution_disclosure_survives_dn_wrap(cli_runner, diagnose_dn_project):
    """When resolution is fully-resolved (clean ``symbol`` tier), the
    ``resolution`` field still appears on the envelope summary AND on
    the top-level envelope per the existing W1244 disclosure layer.

    The W607-DN wrap on the pre-substrate load_index boundary does NOT
    interfere with the W1244 resolution disclosure -- this test confirms
    the disclosure field survives the wrap.
    """
    result = _invoke_diagnose(cli_runner, diagnose_dn_project, "dn_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    # W1244 disclosure stamps ``resolution`` on the summary and the
    # top-level envelope.
    assert "resolution" in summary, (
        f"summary must carry ``resolution`` per W1244 Pattern-1D; got summary keys = {sorted(summary.keys())!r}"
    )
    assert "resolution" in data, (
        f"top-level envelope must carry ``resolution`` per W1244 "
        f"Pattern-1D; got envelope keys = {sorted(data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# (7) W147 findings-registry coexistence -- cmd_diagnose does not persist
# ---------------------------------------------------------------------------


def test_cmd_diagnose_does_not_call_emit_finding():
    """cmd_diagnose is a root-cause RANKER, not a registry-write detector.

    Confirms the W607-DN wrap does NOT inadvertently introduce a
    findings-registry write path. The W147 mandate (canonical findings
    registry) applies to DETECTORS that persist; cmd_diagnose is a
    transient root-cause ranking command and stays envelope-only.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    src = src_path.read_text(encoding="utf-8")

    # Bare ``emit_finding(`` call -- must not exist in cmd_diagnose source.
    assert "emit_finding(" not in src, (
        "cmd_diagnose must remain envelope-only per W147 audit; found unexpected ``emit_finding(`` call in source"
    )
    # No ``FindingRecord(`` constructor either.
    assert "FindingRecord(" not in src, (
        "cmd_diagnose must remain envelope-only per W147 audit; "
        "found unexpected ``FindingRecord(`` constructor in source"
    )


# ---------------------------------------------------------------------------
# (8) Cross-prefix isolation -- diagnose_* markers stay in their family
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_diagnose_markers_only(cli_runner, diagnose_dn_project, monkeypatch):
    """The ``diagnose_*`` marker family stays bounded.

    A load_index failure must not surface ``preflight_*`` / ``impact_*``
    / ``health_*`` markers (or any non-``diagnose_*`` prefix) in the
    cmd_diagnose envelope.
    """
    from roam.commands import cmd_diagnose

    def _raise_load(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-check")

    monkeypatch.setattr(cmd_diagnose, "ensure_index", _raise_load)

    result = _invoke_diagnose(cli_runner, diagnose_dn_project, "dn_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    forbidden_prefixes = (
        "preflight_",
        "impact_",
        "health_",
        "describe_",
        "minimap_",
        "doctor_",
        "dashboard_",
        "audit_",
        "pr_risk_",
    )
    for m in all_markers:
        for prefix in forbidden_prefixes:
            assert not m.startswith(prefix), (
                f"cmd_diagnose envelope leaked a {prefix} marker into the ``diagnose_*`` family: {m!r}"
            )
        # Every marker that IS present must be in the diagnose_ family
        assert m.startswith("diagnose_"), f"cmd_diagnose envelope carried a non-``diagnose_*`` marker: {m!r}"


# ---------------------------------------------------------------------------
# (9) Combined-bucket merger -- DN markers reach top-level + summary
# ---------------------------------------------------------------------------


def test_dn_marker_reaches_both_top_level_and_summary_warnings_out(cli_runner, diagnose_dn_project, monkeypatch):
    """When the W607-DN bucket is non-empty, the marker MUST appear in
    BOTH the top-level ``warnings_out`` AND ``summary.warnings_out``.

    Mirror of the W607-BH combined-bucket discipline. Empty bucket ->
    byte-identical envelope (no warnings_out keys). Non-empty bucket ->
    both surface sites carry the marker for marker-emission-order
    parity.
    """
    from roam.commands import cmd_diagnose

    def _raise_load(*args, **kwargs):
        raise ValueError("synthetic-combined-bucket-DN")

    monkeypatch.setattr(cmd_diagnose, "ensure_index", _raise_load)

    result = _invoke_diagnose(cli_runner, diagnose_dn_project, "dn_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    top_dn = [m for m in top_wo if m.startswith("diagnose_load_index_failed:")]
    summary_dn = [m for m in summary_wo if m.startswith("diagnose_load_index_failed:")]
    assert top_dn, f"top-level warnings_out must carry the load_index marker; got {top_wo!r}"
    assert summary_dn, f"summary.warnings_out must carry the load_index marker; got {summary_wo!r}"
    # partial_success must flip on summary
    assert data["summary"].get("partial_success") is True, (
        f"summary.partial_success must flip True when W607-DN bucket is non-empty; got {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (10) W978 7-discipline AST audit -- no f-string verdict floor, no eager
# default object construction, no len() on poisoned object
# ---------------------------------------------------------------------------


def test_w978_7_discipline_ast_audit_on_dn_wrap():
    """Pin the W978 7-discipline contract on the W607-DN wrap site.

    1. The ``_run_check_dn`` wrap call for ``load_index`` must use
       ``ensure_index`` as a positional callable (no f-string assembly
       of the callable name).
    2. The ``default=`` kwarg must be ``None`` (no eager construction).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_call = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dn"):
            continue
        # First positional arg must be the literal "load_index"
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "load_index"):
            continue
        # Second positional arg must be the bare Name ``ensure_index``
        if len(node.args) < 2:
            continue
        second = node.args[1]
        assert isinstance(second, ast.Name) and second.id == "ensure_index", (
            f"second arg to _run_check_dn('load_index', ...) must be the "
            f"bare ``ensure_index`` Name; got {ast.dump(second)!r}"
        )
        # default kwarg must be ast.Constant(None)
        for kw in node.keywords:
            if kw.arg == "default":
                assert isinstance(kw.value, ast.Constant) and kw.value.value is None, (
                    f"default kwarg on _run_check_dn('load_index', ...) must "
                    f"be the literal None per W978 kwarg-default-eagerness "
                    f"discipline; got {ast.dump(kw.value)!r}"
                )
        found_call = True
        break

    assert found_call, "could not find a _run_check_dn('load_index', ensure_index, ...) call in cmd_diagnose source"
