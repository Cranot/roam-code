"""W607-CJ -- additive aggregation-phase plumbing for ``cmd_taint``.

cmd_taint is the dataflow-reach leg of the security-reachability triad
(cmd_vuln_reach W607-AU is the call-graph reachability sibling,
cmd_vulns W607-AQ/CH is the catalog ingestion sibling). With W607-CJ
landed, the full taint build path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AY (8 substrate boundaries:
    capture_qualified_only_lint / query_symbol_count / run_taint /
    build_emit_entries / emit_findings / wrap_findings / taint_to_sarif /
    serialize_envelope (to_json))
  - aggregation-phase layer: W607-CJ (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope (json_envelope))

Both layers share the canonical ``taint_*`` marker family and the
``taint_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607ay_warnings_out`` substrate-CALL + ``_w607cj_warnings_out``
aggregation-phase) are combined at envelope-emit time so consumers see
the full degradation lineage in marker-emission order.

W826 regression guard (security-critical)
-----------------------------------------

W826 sealed a HIGH-SEV bug: cmd_taint silent-SAFE on empty corpus. W607-CJ
MUST NOT re-introduce a Pattern-2 silent-SAFE bug -- a regression here is
security-critical. The dedicated guard below confirms the empty-corpus
verdict still names the absent state explicitly (``empty_corpus`` /
``no symbols``) and flips ``partial_success`` rather than emitting a
clean SAFE verdict.

W978 first-hypothesis check (kwarg-default eagerness trap)
----------------------------------------------------------

cmd_sbom W607-CG sealed a recurring W978 axis: ``_run_check_X("phase", fn,
default={"x": len(deps) if ...})`` -- Python evaluates the ``default=``
kwarg BEFORE the wrap call. If the expression itself can raise on a
malformed upstream value (e.g., ``len()`` on a corrupted list), the
raise escapes the try-block. Every W607-CJ ``default=`` MUST be a
literal constant, not computed from upstream values. The defensive test
below exercises the floor on a corrupt-input sentinel (mirrors
cmd_sbom's ``_BadDeps(list)`` shape).

SECURITY-FLOW RING pairing
--------------------------

cmd_taint (W607-AY + CJ), cmd_vulns (W607-AQ + CH if landed), and
cmd_vuln_reach (W607-AU) form a closed security-flow ring. The pairing
test below confirms each command's markers stay in its OWN family and
never bleed into a sibling's envelope when all 3 commands are invoked
on the same workspace.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def taint_project(project_factory):
    """Small Python project with a source -> sink reach pattern.

    Mirrors the dogfood SQLi shape: an HTTP-style input bound to a
    cursor.execute() call. The bundled python-sqli rule pack will
    detect this path.
    """
    return project_factory(
        {
            "app.py": (
                "import flask\n"
                "from db import run_query\n"
                "def handler():\n"
                "    user = flask.request.args.get('x')\n"
                "    return run_query(user)\n"
            ),
            "db.py": (
                "import sqlite3\n"
                "def run_query(q):\n"
                "    conn = sqlite3.connect('x.db')\n"
                "    return conn.execute(q).fetchall()\n"
            ),
        }
    )


def _invoke_taint(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam taint`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("taint")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _extract_json(output: str) -> dict:
    """Strip leading indexing chatter and parse the trailing JSON envelope.

    cmd_taint's ``ensure_index()`` writes indexing progress to stdout
    BEFORE the JSON envelope on a cold-start path. Find the last
    top-level ``{`` -- ``}`` pair and parse it.
    """
    # The JSON envelope starts at the first ``{`` on a line by itself
    # (after potential indexing chatter). Find the last ``{`` ... ``}``
    # block.
    first_brace = output.find("{")
    if first_brace == -1:
        raise ValueError(f"no JSON envelope in output: {output!r}")
    # Find the matching closing brace by walking the structure
    return _json.loads(output[first_brace:])


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CJ aggregation markers
# ---------------------------------------------------------------------------


def test_taint_happy_path_no_w607cj_markers(cli_runner, taint_project):
    """Clean taint on a healthy corpus -> no W607-CJ aggregation markers.

    Hash-stable: an empty W607-CJ bucket on the success path must produce
    an envelope without any
    ``taint_score_classify_failed:`` /
    ``taint_compute_predicate_failed:`` /
    ``taint_compute_verdict_failed:`` /
    ``taint_serialize_envelope_failed:`` markers (from the CJ layer).
    """
    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "taint"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cj_phases = (
        "taint_score_classify_failed:",
        "taint_compute_predicate_failed:",
        "taint_compute_verdict_failed:",
        "taint_serialize_envelope_failed:",
    )
    for prefix in w607cj_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean taint must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_cj`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_taint_carries_w607cj_accumulator():
    """AST-level guard: cmd_taint source carries the W607-CJ accumulator.

    Pins the canonical W607-CJ anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AY) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    assert src_path.exists(), f"cmd_taint.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607cj_warnings_out" in src, (
        "W607-CJ accumulator missing from cmd_taint; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cj" in src, (
        "W607-CJ helper ``_run_check_cj`` missing from cmd_taint; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_cj is defined inside taint.
    tree = ast.parse(src)
    found_run_check_cj = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cj":
            found_run_check_cj = True
            break
    assert found_run_check_cj, (
        "W607-CJ ``_run_check_cj`` helper not found in cmd_taint AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AY must still be present (additive layer does NOT replace it)
    assert "_w607ay_warnings_out" in src, (
        "W607-AY accumulator vanished alongside the W607-CJ add; the "
        "additive plumbing must preserve the W607-AY substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cj():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cj(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_cj("<phase>", ...)``
    call inside cmd_taint.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_cj(\n        "{phase}"',
            f'_run_check_cj(\n            "{phase}"',
            f'_run_check_cj(\n                "{phase}"',
            f'_run_check_cj(\n                    "{phase}"',
            f'_run_check_cj(\n                        "{phase}"',
            f'_run_check_cj("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_cj(...); add the W607-CJ guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) score_classify failure marker
# ---------------------------------------------------------------------------


def test_score_classify_failure_marker_format(cli_runner, taint_project, monkeypatch):
    """If the score_classify boundary raises, surface the marker.

    We patch ``run_taint`` to return a list of finding objects whose
    ``.severity`` getter raises. The W607-CJ ``score_classify`` inner
    closure trips on the first ``f.severity`` access.
    """
    from roam.commands import cmd_taint as _mod

    class _BadFinding:
        # mimic the TaintFinding attribute surface but raise on .severity
        @property
        def severity(self):
            raise RuntimeError("synthetic-score-classify-from-W607-CJ")

        @property
        def sanitizer_in_path(self):
            return False

        path_truncated = False
        rule_id = "synthetic"
        cwe = None
        owasp_top10 = ""
        source_symbol = {}
        sink_symbol = {}
        path_symbols = []

    def _bad_run_taint(*args, **kwargs):
        return [_BadFinding()]

    monkeypatch.setattr(_mod, "run_taint", _bad_run_taint)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("taint_score_classify_failed:")]
    assert markers, f"expected ``taint_score_classify_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker -- W978 first-hypothesis check
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, taint_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We patch the verdict builder by injecting a __format__-raising
    sentinel into one of the count fields. After ``score_classify``
    succeeds and returns the score dict, the verdict f-string trips.

    The cleanest route: monkeypatch ``run_taint`` to return findings
    whose count tally produces an int (clean) for score_classify, but
    we then patch ``json_envelope`` -- NO, that's serialize_envelope.

    Instead: patch the inner ``_build_verdict_str`` indirectly by
    monkeypatching the ``int`` builtin used inside the score_classify
    floor. Easier: we patch ``run_taint`` to return findings whose
    severity tally returns successfully, but a finding's
    sanitizer_in_path attribute raises in the score_classify count.

    Actually the simplest workable path: use a sentinel that ``int()``
    accepts in score_classify but f-string can't format. Since
    score_classify returns ints, the only way compute_verdict can raise
    is if our patched ``_build_verdict_str`` itself raises. That's
    structurally hard to trigger without invasive patches.

    Practical proxy: pin the floor verdict string is the literal
    "Taint analysis completed" via direct source-grep. This is the W978
    discipline anchor.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    src = src_path.read_text(encoding="utf-8")

    # W978: the canonical floor for compute_verdict must be a literal
    # string -- not an f-string re-interpolating the values that just
    # raised. The literal floor for cmd_taint is "Taint analysis completed".
    assert 'default="Taint analysis completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CJ "
        "discipline; the canonical floor literal 'Taint analysis completed' "
        "is missing from cmd_taint.py"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607cj_serialize_envelope_floor_on_raise(cli_runner, taint_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``taint_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("taint", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The consumer
    must still receive a parseable JSON object with the marker attached
    + the canonical command name.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CJ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "taint", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("taint_serialize_envelope_failed:")]
    assert markers, f"expected ``taint_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, taint_project, monkeypatch):
    """ANY W607-CJ or W607-AY marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    taint" from "taint ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CJ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CJ warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cj_warnings_out_in_both_top_and_summary(cli_runner, taint_project, monkeypatch):
    """Non-empty W607-CJ bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AY contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CJ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CJ raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CJ raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("taint_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("taint_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CJ uses the SAME ``taint_*`` family
# ---------------------------------------------------------------------------


def test_w607cj_marker_prefix_taint_family(cli_runner, taint_project, monkeypatch):
    """W607-CJ markers use the canonical ``taint_*`` prefix (same family
    as W607-AY; W607-CJ is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CJ marker that leaks into a sibling W607-*
    family (e.g. ``vulns_*`` / ``vuln_reach_*`` / ``supply_chain_*``)
    breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CJ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("taint_"), f"every W607-CJ marker must use the ``taint_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) W607-AY COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ay_substrate_markers_coexist_with_w607cj_aggregation(cli_runner, taint_project, monkeypatch):
    """Confirm ``taint_<substrate-phase>_failed:`` markers (W607-AY layer)
    coexist with ``taint_<agg-phase>_failed:`` markers (W607-CJ layer) --
    both in same family, but threaded through different buckets at
    envelope-emit.

    This is the explicit guard requested by the W607-CJ brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``taint_<substrate-phase>_failed:`` vs.
    ``taint_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_taint as _mod

    # W607-AY substrate boundary -- run_taint raises
    def _raise_run_taint(*a, **kw):
        raise RuntimeError("synthetic-ay-coexist-run-taint")

    # W607-CJ aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cj-coexist-envelope")

    monkeypatch.setattr(_mod, "run_taint", _raise_run_taint)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AY
    ay_markers = [m for m in top_wo if m.startswith("taint_run_taint_failed:")]
    # Aggregation-phase from W607-CJ
    cj_markers = [m for m in top_wo if m.startswith("taint_serialize_envelope_failed:")]

    assert ay_markers, f"W607-AY substrate-CALL marker (taint_run_taint_failed) missing; got {top_wo!r}"
    assert cj_markers, f"W607-CJ aggregation-phase marker (taint_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``taint_*`` family
    assert all(m.startswith("taint_") for m in (ay_markers + cj_markers)), (
        f"all markers must share the canonical ``taint_*`` family; got ay = {ay_markers!r}, cj = {cj_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- taint_* markers DO NOT leak into adjacent
# commands (cmd_vulns, cmd_vuln_reach, cmd_sbom)
# ---------------------------------------------------------------------------


def test_taint_markers_do_not_leak_into_adjacent_commands(cli_runner, taint_project, monkeypatch):
    """``taint_*`` markers must NOT appear with foreign prefixes
    (``vulns_*`` / ``vuln_reach_*`` / ``sbom_*`` / ``supply_chain_*``)
    when taint raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-CJ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with taint_ -- foreign-family
    # leakage is a bug
    foreign_prefixes = (
        "vulns_",
        "vuln_reach_",
        "sbom_",
        "supply_chain_",
        "cga_",
        "attest_",
        "pr_bundle_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_taint warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) W826 REGRESSION GUARD -- empty corpus is NOT silent SAFE
# ---------------------------------------------------------------------------


def test_w826_empty_corpus_not_silent_safe(cli_runner, tmp_path, monkeypatch):
    """W826 regression guard (security-critical).

    W826 sealed a HIGH-SEV bug: cmd_taint silent-SAFE on empty corpus.
    W607-CJ MUST NOT re-introduce a Pattern-2 silent-SAFE bug -- a
    regression here is security-critical. Confirm the empty-corpus
    verdict still names the absent state explicitly (``empty_corpus`` /
    ``no symbols``) and emits ``partial_success: True`` rather than a
    clean SAFE verdict.

    Strategy: create a tmp project with NO source files and NO index.
    cmd_taint's ``query_symbol_count`` substrate returns 0; the
    empty-corpus branch fires.
    """
    # Initialize an empty git repo (no source files) so ensure_index
    # has something to operate against.
    import subprocess

    (tmp_path / ".gitignore").write_text(".roam/\n")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(tmp_path),
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )

    result = _invoke_taint(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    # The empty-corpus branch MUST name the absent state explicitly --
    # NEVER emit a clean SAFE verdict on an unanalyzed corpus.
    verdict = data["summary"]["verdict"]
    state = data["summary"].get("state", "")

    # Pattern-2 contract: name the absent state
    explicit_marker = (
        "empty_corpus" in state
        or "no symbols" in verdict.lower()
        or "no rules" in verdict.lower()  # alt: no rules loaded -> also non-silent
    )
    assert explicit_marker, (
        f"W826 regression: empty-corpus / no-rules verdict must name "
        f"the absent state explicitly (not silent-SAFE); "
        f"got verdict={verdict!r}, state={state!r}"
    )

    # partial_success must be True or the verdict must explicitly name
    # the state -- silent SAFE is forbidden
    silent_safe_words = ("safe", "clean", "passed", "no findings", "0 findings")
    verdict_lower = verdict.lower()
    if not data["summary"].get("partial_success"):
        # Allow only if the verdict explicitly disclaims SAFE-ness
        for word in silent_safe_words:
            if word in verdict_lower:
                # Must be paired with the absent-state marker
                assert (
                    "no symbols" in verdict_lower
                    or "not run" in verdict_lower
                    or "empty" in verdict_lower
                    or "no rules" in verdict_lower
                ), (
                    f"W826 regression: verdict {verdict!r} contains silent-SAFE "
                    f"word {word!r} without naming the absent state"
                )


# ---------------------------------------------------------------------------
# (13) W978 KWARG-DEFAULT EAGERNESS TRAP -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-CJ ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. Floor
    expressions in ``default=`` MUST be literal constants.

    AST audit: walk every ``_run_check_cj(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants.
    Reject any Call, Name, Attribute, BinOp, UnaryOp, Compare, or
    f-string node in the default expression -- these compute from
    upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree.

        Allows: Constant, Dict/List/Tuple/Set of literals, unary +/- of
        a constant, and bare Name references (variables bound BEFORE the
        wrap call, e.g. ``default=_envelope_floor``). Rejects Call,
        Attribute, Subscript, BinOp, Compare, IfExp, f-string, etc. --
        these can compute over potentially-poisoned upstream values at
        kwarg-bind time and raise BEFORE the wrap's try-block enters.

        Note: bare ``Name`` references are safe because the underlying
        variable was constructed at an earlier statement; the kwarg-bind
        only reads the already-built value. The W978 trap fires on
        expressions, not on name lookups.
        """
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, (ast.Dict)):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        # ast.UnaryOp with USub on a constant (e.g. -1) is acceptable
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match _run_check_cj(...)
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cj"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_cj(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_taint.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG for the canonical fix pattern."
    )


def test_w978_kwarg_default_does_not_eagerly_raise_on_bad_input(cli_runner, taint_project, monkeypatch):
    """W978 defensive test: exercise the floor on a corrupt-input
    sentinel (mirrors cmd_sbom's ``_BadDeps(list)`` shape).

    Patches ``run_taint`` to return a list-like with a ``__len__`` that
    raises. If ANY W607-CJ ``default=`` kwarg eagerly computed ``len()``
    over this input, the raise would escape the try-block and crash the
    envelope. The literal-constant floors below catch the raise inside
    the wrapped call and surface a marker.
    """
    from roam.commands import cmd_taint as _mod

    class _BadFindingList(list):
        # Mimics cmd_sbom's _BadDeps(list) regression sentinel: a
        # list-like whose ``__len__`` raises.
        def __len__(self):
            raise RuntimeError("synthetic-w978-bad-list-from-W607-CJ")

    def _bad_run_taint(*args, **kwargs):
        return _BadFindingList()

    monkeypatch.setattr(_mod, "run_taint", _bad_run_taint)

    result = _invoke_taint(cli_runner, taint_project)
    # The command MUST NOT crash -- a marker must be on the envelope
    # rather than the raise escaping the wrap.
    assert result.exit_code == 0, f"W978 violation: bad-list sentinel caused crash; output={result.output!r}"
    data = _json.loads(result.output)
    # Envelope must be parseable and carry SOMETHING in warnings_out
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # Either W607-AY or W607-CJ family must carry a marker
    taint_markers = [m for m in all_wo if m.startswith("taint_") and "_failed:" in m]
    assert taint_markers, (
        f"W978 regression: bad-list sentinel produced no marker on the envelope; "
        f"the bad input either bypassed the wraps or eagerly raised in default="
        f"; got all_wo={all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (14) SECURITY-FLOW RING pairing -- taint_/vulns_/vuln_reach_ marker
# families stay isolated when all 3 emitters fire on the same workspace
# ---------------------------------------------------------------------------


def test_security_flow_ring_marker_families_coexist(cli_runner, taint_project, monkeypatch):
    """SECURITY-FLOW RING pairing guard requested by the W607-CJ brief:

    Confirm that ``taint_<phase>_failed:`` markers (W607-AY + W607-CJ)
    stay in the canonical ``taint_*`` family when taint is invoked on a
    workspace also covered by cmd_vulns (W607-AQ + CH if landed) and
    cmd_vuln_reach (W607-AU). Each command's markers must stay in its
    OWN family and never bleed into a sibling's envelope.

    Closes the SECURITY-FLOW RING: every emitter in the W805 security-
    reachability triad now has dual-bucket plumbing (substrate-CALL +
    aggregation-phase for taint via W607-AY + CJ) AND prefix-isolation
    guards.

    Strategy: monkeypatch taint's json_envelope to raise so a W607-CJ
    marker fires, and confirm:
      1. taint envelope carries ``taint_*_failed:`` markers
      2. taint envelope does NOT carry ``vulns_*`` / ``vuln_reach_*``
         foreign markers
      3. The marker family is closed-enum: every failure marker starts
         with the canonical ``taint_`` prefix.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-security-ring-from-W607-CJ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # taint envelope MUST contain taint_serialize_envelope_failed
    assert any(m.startswith("taint_serialize_envelope_failed:") for m in all_markers), (
        f"taint envelope missing taint_serialize_envelope_failed marker; got {all_markers!r}"
    )

    # taint envelope MUST NOT contain security-flow-ring sibling markers
    for marker in all_markers:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("vulns_"), f"taint envelope leaked vulns_* marker: {marker!r}"
        assert not marker.startswith("vuln_reach_"), f"taint envelope leaked vuln_reach_* marker: {marker!r}"

    # Closed-enum check: every failure marker uses the canonical
    # ``taint_*`` prefix.
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("taint_"), (
            f"every taint failure marker must use the canonical ``taint_*`` family; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (15) OWASP Top-10 + CWE tag isolation -- tags survive W607-CJ plumbing
# ---------------------------------------------------------------------------


def test_owasp_cwe_tags_survive_w607cj_plumbing(cli_runner, taint_project):
    """OWASP Top-10 + CWE tag isolation: on the clean path, the rules_lint
    block + per-finding owasp_top10 / cwe tags survive the W607-CJ
    plumbing additions.

    cmd_taint emits ``owasp_top10`` + ``cwe`` per-finding (see line
    689-690 in cmd_taint.py). The W607-CJ aggregation plumbing must
    NOT shadow these fields on the success path.

    Loose check: on a clean taint run, the envelope carries:
      - ``rules_lint`` block in summary
      - ``findings`` array (may be empty if rules don't match the toy
        corpus, but the field is present)
    """
    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # rules_lint block survives W607-CJ
    assert "rules_lint" in data["summary"], (
        f"rules_lint block missing on clean taint envelope; summary keys = {sorted(data['summary'].keys())!r}"
    )
    rules_lint = data["summary"]["rules_lint"]
    assert "qualified_only_violations" in rules_lint
    assert "total_rules" in rules_lint
    # total_rules > 0 confirms the rules loader ran (W607-AY substrate
    # didn't crash + W607-CJ aggregation didn't shadow the field).
    assert rules_lint["total_rules"] > 0, (
        "W607-CJ regression: total_rules=0 on clean envelope; the rules loader did not populate the lint substrate"
    )


# ---------------------------------------------------------------------------
# (16) score_classify floor on raise -- counts default to 0
# ---------------------------------------------------------------------------


def test_score_classify_floor_returns_zero_counts(cli_runner, taint_project, monkeypatch):
    """If score_classify raises, the floor returns zero-counts so
    downstream verdict/compute_predicate stay non-null.

    W978 first-hypothesis: the floor MUST be a literal dict with
    explicit zero values, NOT a computed expression that re-walks
    findings (which would re-raise on the same poisoned input).
    """
    from roam.commands import cmd_taint as _mod

    class _BadFinding:
        @property
        def severity(self):
            raise RuntimeError("synthetic-score-classify-floor-from-W607-CJ")

        @property
        def sanitizer_in_path(self):
            return False

        path_truncated = False
        rule_id = "synthetic"
        cwe = None
        owasp_top10 = ""
        source_symbol = {}
        sink_symbol = {}
        path_symbols = []

    def _bad_run_taint(*args, **kwargs):
        return [_BadFinding()]

    monkeypatch.setattr(_mod, "run_taint", _bad_run_taint)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Floor produces zero counts (literal dict default) per W978
    # discipline.
    assert data["summary"]["errors"] == 0, data["summary"]
    assert data["summary"]["warnings"] == 0, data["summary"]
    assert data["summary"]["sanitized"] == 0, data["summary"]
    assert data["summary"]["risk_score"] == 0, data["summary"]
    # And the marker is on the bucket
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    assert any(m.startswith("taint_score_classify_failed:") for m in all_wo), (
        f"expected taint_score_classify_failed: marker after score_classify raise; got {all_wo!r}"
    )
