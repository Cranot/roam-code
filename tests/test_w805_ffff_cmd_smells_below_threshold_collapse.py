"""W805-FFFF — cmd_smells AST-similarity below-threshold collapse probe.

Eighty-fourth-in-batch W805 sweep. **FOURTH in the paired-scoring detector
family** (cmd_dark_matter cochange -> cmd_duplicates pair_similarity ->
cmd_clones AST Jaccard -> cmd_smells AST-similarity smell detectors).

Mirror of W805-WWW (cmd_dark_matter), W805-ZZZ (cmd_duplicates), and
W805-CCCC (cmd_clones). The cmd_smells dispatcher hosts TWO registered
AST-similarity detectors that share the paired-scoring pre-filter +
threshold architecture: ``cross-layer-clone`` (Jaccard 0.70) and
``parallel-hierarchy`` (Jaccard 0.70 on subclass marker token sets).

A THIRD detector exists in the codebase
(``catalog/clones_rename_invariant.py::detect_rename_invariant_clones``,
cosine 0.95 with bucket pre-filter) but it is NOT wired into
``catalog/smells.py`` via ``@detector(...)`` — the valid ``--only``
set excludes ``rename-invariant-clone``. Probe surfaces an orphan
detector (W978 first-hypothesis finding): the rename-invariant code
fits the FAMILY shape but cannot be reached through the cmd_smells
dispatch surface. Out of W805-FFFF pinning scope until the wiring
lands; flagged separately as a W805-GGGG candidate.

W978 first-hypothesis re-run BEFORE writing any pin
====================================================

Hypothesis: ``cmd_smells --only <ast-sim-detector>`` collapses three
distinguishable input shapes to a SINGLE silent SAFE verdict
("Clean: no code smells detected"):

    (I) Empty corpus -> no functions / no classes / no callers
        verdict = "Clean: no code smells detected"

    (II) Populated corpus, pre-filter EXCLUDES every pair (e.g.
         min_shared_callees < 3 for cross-layer-clone, _MIN_AST_NODES
         < 8 for rename-invariant-clone, min_subclasses < 2 for
         parallel-hierarchy) -> 0 candidates pass shape gate
         verdict = "Clean: no code smells detected"

    (III) Above pre-filter but below similarity threshold (jaccard <
          0.70 or cosine < 0.95) -> 0 pairs survive scoring
          verdict = "Clean: no code smells detected"

All three converge to the same envelope: ``smells=[]``, ``total_smells=0``,
``summary.verdict = "Clean: no code smells detected"``, NO ``state``
field, NO ``partial_success`` field naming the gate that excluded
every candidate, NO ``candidate_count`` field.

Architecture re-verification (W978 + W907 re-read of cmd_smells + helpers)
==========================================================================

cmd_smells.py:685-686         verdict construction -- single "Clean: no
                              code smells detected" string for
                              total_smells == 0; collapses all gate-
                              collapse shapes to ONE verdict
cmd_smells.py:601             findings = run_all_detectors(conn, only=only_dispatch)
cmd_smells.py:778-786         JSON summary -- no state / partial_success
                              / candidate_count fields on the empty path

catalog/clones_cross_layer.py:322-411
                              detect_cross_layer_clones(conn, jaccard_threshold=0.7,
                              min_shared_callees=3)
                              GATE A: min_shared_callees=3 (line 415)
                              GATE B: jaccard_threshold=0.7 (line 421)

catalog/clones_rename_invariant.py:283-372
                              detect_rename_invariant_clones(conn,
                              similarity_threshold=0.95, min_lines=5)
                              GATE A: _MIN_AST_NODES=8 (line 79) at extraction
                              GATE B: bucket pre-filter (line 240)
                              GATE C: similarity_threshold=0.95 (line 351)

catalog/parallel_hierarchy.py:212-355
                              detect_parallel_hierarchy(conn,
                              jaccard_threshold=0.7, min_subclasses=2)
                              GATE A: min_subclasses=2 (line 273, 285, 291)
                              GATE B: jaccard_threshold=0.7 (line 295)
                              GATE C: min_subclasses on matched_pairs (line 310)

W978 hypothesis CONFIRMED: cmd_smells inherits the paired-scoring family
bug at the dispatch layer. Every gate-collapse path returns an empty
finding list, and cmd_smells emits "Clean: no code smells detected"
indistinguishable from a truly clean corpus.

PAIRED-SCORING DETECTOR FAMILY 4-STRONG CONFIRMED:

    cmd_dark_matter.py:389-393      (W805-WWW)  cochange_count < min_cochanges
    cmd_duplicates.py:734/782/909-910 (W805-ZZZ) bucket pre-filter +
                                                 similarity threshold
    cmd_clones.py:374-383 +
    clone_detect.py:527,530         (W805-CCCC) AST-Jaccard pre-filter +
                                                threshold
    cmd_smells.py:685-686 +
    {cross_layer_clones, rename_invariant_clones, parallel_hierarchy}
                                    (W805-FFFF) AST-similarity pre-filter
                                                + threshold (THIS PIN)

W978 per-detector fit table
============================

In-scope (FAMILY MATCH — paired-scoring pre-filter + similarity threshold,
AND registered in cmd_smells dispatch via @detector):
    - cross-layer-clone (catalog/clones_cross_layer.py + smells.py:3011)
        Pre-filter: min_shared_callees=3
        Threshold: jaccard >= 0.7
        FITS + REACHABLE via --only.
    - parallel-hierarchy (catalog/parallel_hierarchy.py + smells.py:3010)
        Pre-filter: min_subclasses=2 (twice — on superclass + matched pairs)
        Threshold: jaccard >= 0.7 on subclass marker tokens
        FITS + REACHABLE via --only.

Orphan FAMILY-shape detector (W978 first-hypothesis paid off):
    - rename-invariant-clone (catalog/clones_rename_invariant.py)
        Pre-filter: _MIN_AST_NODES=8 + bucket pre-filter
        Threshold: cosine >= 0.95
        FITS the FAMILY shape, but the @detector decorator wiring is
        absent in catalog/smells.py — ``--only rename-invariant-clone``
        produces a usage error ("unknown smell id"). The detector
        function exists and is publicly importable, but not reachable
        through the cmd_smells dispatch surface today. Out of W805-FFFF
        pinning scope; flagged as a W805-GGGG candidate axis.

Out-of-scope (W978 first-hypothesis: same intuition, different shape):
    - type-switch (catalog/type_switch.py)
        Single threshold: min_class_arms >= 3. NOT a paired scoring
        loop — it's a single-symbol threshold. DOES NOT FIT the
        paired-scoring family (still subject to Pattern-2 silent-SAFE
        on empty corpus, but the bug mechanism is single-shape, not
        triple-collapse). Out of W805-FFFF scope.
    - duplicate-conditionals (smells.py:1747)
        Counts predicate frequency >= threshold over a SINGLE AST
        traversal. NOT paired scoring. DOES NOT FIT.
    - switch-statement (smells.py:2272)
        AST-walk threshold on case count. NOT paired scoring.
        DOES NOT FIT.
    - temporal-coupling (smells.py:2447)
        Pair-scoring on cochange_count via SQL JOIN — does fit the
        FAMILY shape on the "below-threshold" axis (cochange_count <
        _TEMPORAL_COUPLING_COCHANGE_THRESHOLD = 10). But it's already
        covered by the W805-WWW cmd_dark_matter pin since both share
        the same underlying ``git_cochange`` table. Folded into the
        WWW finding rather than re-pinned here. Out of W805-FFFF scope.

W907 verify-cycle check
========================

cmd_smells.py has no defensive "would create cycle" docstrings. Lazy
imports at line 525 (smells_suppress), line 529 (find_project_root),
line 636 (file_role_hints), line 716 (sarif) are documented heavy-
dependency lazies, not false hedges. PASS.

Bug class: Pattern-1-V-D silent success on degraded resolution +
Pattern-2 silent fallback (CLAUDE.md "Six systemic anti-patterns" #1.D
+ #2). Each AST-similarity detector applies one or more gates that can
silently collapse every candidate. cmd_smells emits the empty verdict
without naming the gate.

Fix template (NOT applied — pinned via xfail only)
===================================================

Per-detector candidate-count threading in ``run_all_detectors`` so the
cmd_smells dispatcher can disclose the empty cause:

    detector_stats = {
        "cross-layer-clone": {
            "candidates_pre_filter": <int>,    # post-min_shared_callees
            "pairs_above_threshold": <int>,    # post-jaccard
            "threshold": 0.7,
        },
        ...
    }

    if total_smells == 0:
        empty_states = []
        for did, stats in detector_stats.items():
            if stats["candidates_pre_filter"] == 0:
                empty_states.append((did, "empty_corpus"))
            elif stats["pairs_above_threshold"] == 0:
                empty_states.append((did, "no_pairs_above_threshold"))
        if empty_states:
            state = empty_states[0][1]   # OR aggregate
            verdict = (
                f"{empty_states[0][0]}: all candidates excluded by "
                f"{empty_states[0][1]} gate"
            )
            partial_success = True

Pinned via ``xfail(strict=True)`` so a future fix flips xpass + fails
the gate loudly. Positive companion tests assert wrapper crash-free +
existing W808/W813 invariants stay landed.

Run isolation:
    python -m pytest tests/test_w805_ffff_cmd_smells_below_threshold_collapse.py -x -n 0

W805 sweep update: 84th-in-batch.
W805-GGGG candidate: cmd_taint cross-language tainted-path scoring
(or pivot to fresh axis: cmd_invariants paired-rule conjunction
threshold).
"""

from __future__ import annotations

import importlib.util
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_SMELLS_SPEC = importlib.util.find_spec("roam.commands.cmd_smells")
_CATALOG_SMELLS_SPEC = importlib.util.find_spec("roam.catalog.smells")
_CROSS_LAYER_SPEC = importlib.util.find_spec("roam.catalog.clones_cross_layer")
_PARALLEL_HIERARCHY_SPEC = importlib.util.find_spec("roam.catalog.parallel_hierarchy")
_RENAME_INVARIANT_SPEC = importlib.util.find_spec("roam.catalog.clones_rename_invariant")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_smells and the in-scope AST-sim detectors must be importable."""
    if _CMD_SMELLS_SPEC is None:
        pytest.skip("roam.commands.cmd_smells not installed in this environment")
    if _CATALOG_SMELLS_SPEC is None:
        pytest.skip("roam.catalog.smells not installed in this environment")
    if _CROSS_LAYER_SPEC is None:
        pytest.skip("cross_layer_clones detector not installed")
    if _PARALLEL_HIERARCHY_SPEC is None:
        pytest.skip("parallel_hierarchy detector not installed")
    assert _CMD_SMELLS_SPEC is not None


def test_rename_invariant_orphan_detector_documented():
    """W978 finding: rename_invariant_clones detector exists but is NOT wired.

    The detector function ``detect_rename_invariant_clones`` is publicly
    importable from ``roam.catalog.clones_rename_invariant``, but
    ``catalog/smells.py`` does NOT register it via ``@detector(...)``.
    The valid smell-id set returned by ``ALL_DETECTORS`` excludes
    ``rename-invariant-clone``. Document this as a W805-GGGG candidate
    axis rather than rolling it into the FFFF pin.
    """
    if _RENAME_INVARIANT_SPEC is None:
        pytest.skip("rename_invariant_clones module not installed")
    from roam.catalog.clones_rename_invariant import detect_rename_invariant_clones  # noqa: F401
    from roam.catalog.smells import ALL_DETECTORS

    registered_ids = {smell_id for smell_id, _fn in ALL_DETECTORS}
    assert "rename-invariant-clone" not in registered_ids, (
        "rename-invariant-clone now appears in ALL_DETECTORS — the "
        "orphan-detector finding has been resolved. Update the "
        "W805-FFFF docstring and either expand the FFFF scope to "
        "include rename-invariant-clone OR open a fresh W805-GGGG pin."
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file (zero functions, zero classes).

    Input shape (I) of the three-shape family: empty input. Every
    AST-similarity detector has zero candidates to score.
    """
    proj = tmp_path / "empty_corpus_ffff"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on empty-corpus fixture: {out}"
    return proj


@pytest.fixture
def cross_layer_below_threshold_project(tmp_path, monkeypatch):
    """Two callers in different layers but their callee sets share < 3 elements.

    Triggers cross-layer-clone GATE A (min_shared_callees=3): both
    callers have callees but the intersection is below the pre-filter.
    Input shape (II/III merged): pre-filter excludes every pair.
    """
    proj = tmp_path / "cross_layer_below_ffff"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # Two callers across "controllers" + "services" layers with mostly
    # disjoint callees — intersection too small to pass min_shared_callees.
    (proj / "controllers").mkdir()
    (proj / "controllers" / "auth_ctrl.py").write_text(
        "def auth_handler():\n    a()\n    b()\n    c()\n\ndef a():\n    pass\ndef b():\n    pass\ndef c():\n    pass\n"
    )
    (proj / "services").mkdir()
    (proj / "services" / "auth_svc.py").write_text(
        "def auth_service():\n    x()\n    y()\n    z()\n\ndef x():\n    pass\ndef y():\n    pass\ndef z():\n    pass\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on cross-layer fixture: {out}"
    return proj


@pytest.fixture
def parallel_hierarchy_below_threshold_project(tmp_path, monkeypatch):
    """Two inheritance trees but their subclass-marker token sets have low Jaccard.

    Triggers parallel-hierarchy GATE B (jaccard < 0.7): both superclasses
    have >= min_subclasses=2 subclasses (so GATE A passes), but the
    subclass token sets are disjoint enough that Jaccard < 0.7.
    """
    proj = tmp_path / "parallel_hierarchy_below_ffff"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # Hierarchy A: EmployeeUS, EmployeeUK (tokens: us, uk)
    # Hierarchy B: VendorCN, VendorJP (tokens: cn, jp)
    # Token intersection empty => Jaccard 0.
    (proj / "models.py").write_text(
        "class Employee:\n    pass\n"
        "class EmployeeUS(Employee):\n    pass\n"
        "class EmployeeUK(Employee):\n    pass\n"
        "\n"
        "class Vendor:\n    pass\n"
        "class VendorCN(Vendor):\n    pass\n"
        "class VendorJP(Vendor):\n    pass\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on parallel-hierarchy fixture: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke(runner, project_path, json_mode=False, extra_args=()):
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("smells")
    args.extend(extra_args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"smells exit={result.exit_code}:\n{result.output}"
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# Positive shape tests — envelope well-formed (Pattern-1-V-C invariant)
# ---------------------------------------------------------------------------


class TestEnvelopeShapePreserved:
    """Each gate-collapse path must emit a parseable JSON envelope (no crash)."""

    def test_empty_corpus_emits_envelope(self, empty_corpus_project, cli_runner):
        result = _invoke(
            cli_runner,
            empty_corpus_project,
            json_mode=True,
            extra_args=("--only", "cross-layer-clone"),
        )
        data = _parse_json(result)
        assert data.get("command") == "smells"
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"]["verdict"]
        assert data["summary"].get("total_smells") == 0

    def test_cross_layer_below_threshold_emits_envelope(self, cross_layer_below_threshold_project, cli_runner):
        result = _invoke(
            cli_runner,
            cross_layer_below_threshold_project,
            json_mode=True,
            extra_args=("--only", "cross-layer-clone"),
        )
        data = _parse_json(result)
        assert data.get("command") == "smells"
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"].get("total_smells") == 0

    def test_parallel_hierarchy_below_threshold_emits_envelope(
        self, parallel_hierarchy_below_threshold_project, cli_runner
    ):
        result = _invoke(
            cli_runner,
            parallel_hierarchy_below_threshold_project,
            json_mode=True,
            extra_args=("--only", "parallel-hierarchy"),
        )
        data = _parse_json(result)
        assert data.get("command") == "smells"
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"].get("total_smells") == 0


# ---------------------------------------------------------------------------
# Per-detector empty-corpus disclosure — each AST-similarity detector
# MUST distinguish "no candidates" from "candidates excluded by gate"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFFF Pattern-1-V-D + Pattern-2: cmd_smells --only "
        "cross-layer-clone collapses (empty corpus / below pre-filter / "
        "below jaccard threshold) to a single silent 'Clean: no code "
        "smells detected' verdict. Fix: stamp summary.state in "
        "{'empty_corpus','no_pairs_above_threshold'} + "
        "partial_success=True when --only restricts to a single "
        "AST-similarity detector that produced zero findings. Mirror of "
        "W805-WWW + W805-ZZZ + W805-CCCC. FOURTH detector in the "
        "paired-scoring-detector below-threshold collapse family."
    ),
)
class TestCrossLayerClonePattern1VD:
    def test_partial_success_when_pre_filter_excludes_all(self, cross_layer_below_threshold_project, cli_runner):
        result = _invoke(
            cli_runner,
            cross_layer_below_threshold_project,
            json_mode=True,
            extra_args=("--only", "cross-layer-clone"),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-1-V-D: cross-layer-clone pre-filter "
            f"(min_shared_callees=3) excluded every pair; expected "
            f"partial_success=True, got {summary.get('partial_success')!r}. "
            f"summary={summary!r}"
        )

    def test_state_named_for_gate_collapse(self, cross_layer_below_threshold_project, cli_runner):
        result = _invoke(
            cli_runner,
            cross_layer_below_threshold_project,
            json_mode=True,
            extra_args=("--only", "cross-layer-clone"),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state")
        assert state in (
            "no_pairs_after_prefilter",
            "no_pairs_above_threshold",
            "no_candidates",
            "empty_corpus",
        ), (
            f"Pattern-1-V-D: expected state naming the gate that "
            f"excluded every cross-layer-clone candidate, got "
            f"state={state!r}. summary={summary!r}"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFFF: cmd_smells --only parallel-hierarchy emits silent "
        "SAFE when subclass-marker Jaccard falls below 0.7 across all "
        "hierarchy pairs. Mirror of cross-layer-clone variant; fix MUST "
        "stamp state + partial_success."
    ),
)
class TestParallelHierarchyPattern1VD:
    def test_partial_success_when_below_jaccard_threshold(self, parallel_hierarchy_below_threshold_project, cli_runner):
        result = _invoke(
            cli_runner,
            parallel_hierarchy_below_threshold_project,
            json_mode=True,
            extra_args=("--only", "parallel-hierarchy"),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-1-V-D: parallel-hierarchy detector saw >=2 "
            f"hierarchies but Jaccard fell below 0.7 for every pair; "
            f"expected partial_success=True, got "
            f"{summary.get('partial_success')!r}. summary={summary!r}"
        )

    def test_state_named_for_jaccard_collapse(self, parallel_hierarchy_below_threshold_project, cli_runner):
        result = _invoke(
            cli_runner,
            parallel_hierarchy_below_threshold_project,
            json_mode=True,
            extra_args=("--only", "parallel-hierarchy"),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state")
        assert state in (
            "no_pairs_above_threshold",
            "no_pairs_after_prefilter",
            "no_candidates",
            "empty_corpus",
        ), (
            f"Pattern-1-V-D: expected state naming the Jaccard threshold "
            f"collapse, got state={state!r}. summary={summary!r}"
        )


# ---------------------------------------------------------------------------
# FAMILY-confirms invariant — empty / pre-filter / below-threshold MUST
# emit distinct disclosure for the same --only detector.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFFF FAMILY invariant: cmd_smells --only cross-layer-clone "
        "collapses empty-corpus AND below-pre-filter input shapes to a "
        "byte-identical 'Clean: no code smells detected' verdict. Mirror "
        "of W805-WWW/ZZZ/CCCC: distinct gate-collapse states MUST emit "
        "distinct verdicts after the fix. Pinned xfail-strict so the "
        "fix flips xpass -> test failure."
    ),
)
def test_family_shape_match_w805_www_zzz_cccc(
    empty_corpus_project,
    cross_layer_below_threshold_project,
    cli_runner,
):
    """Empty corpus and pre-filter-collapse MUST emit distinct verdicts.

    Mirror of the FAMILY-confirms test in W805-CCCC. Validates the
    four-strong paired-scoring detector family hypothesis.
    """
    v_empty = (
        _parse_json(
            _invoke(
                cli_runner,
                empty_corpus_project,
                json_mode=True,
                extra_args=("--only", "cross-layer-clone"),
            )
        )
        .get("summary", {})
        .get("verdict", "")
        .lower()
    )
    v_prefilter = (
        _parse_json(
            _invoke(
                cli_runner,
                cross_layer_below_threshold_project,
                json_mode=True,
                extra_args=("--only", "cross-layer-clone"),
            )
        )
        .get("summary", {})
        .get("verdict", "")
        .lower()
    )
    assert v_empty != v_prefilter, (
        f"empty_corpus and cross-layer-clone pre-filter-collapse emit "
        f"byte-identical verdict: {v_empty!r}. FAMILY invariant requires "
        f"distinct disclosure for each gate that collapses every "
        f"candidate."
    )


# ---------------------------------------------------------------------------
# Pair-scoring smells collapse — aggregated invariant across all three
# AST-similarity detectors. After the fix, at least one of the three
# detectors must surface non-empty disclosure on its gate-collapse fixture.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFFF aggregate invariant: at least one of the two "
        "registered AST-similarity smell detectors (cross-layer-clone, "
        "parallel-hierarchy) MUST surface partial_success=True on its "
        "gate-collapse fixture after the fix. Today neither of them "
        "does — both collapse to the shared cmd_smells empty verdict. "
        "Pinned xfail-strict; flips xpass when EITHER detector lands "
        "the disclosure path."
    ),
)
def test_pair_scoring_smells_collapse_disclosed(
    cross_layer_below_threshold_project,
    parallel_hierarchy_below_threshold_project,
    cli_runner,
):
    """At least one AST-similarity detector must disclose its gate-collapse path."""
    fixtures = [
        (cross_layer_below_threshold_project, "cross-layer-clone"),
        (parallel_hierarchy_below_threshold_project, "parallel-hierarchy"),
    ]
    any_partial = False
    for project, detector in fixtures:
        result = _invoke(
            cli_runner,
            project,
            json_mode=True,
            extra_args=("--only", detector),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        if summary.get("partial_success") is True:
            any_partial = True
            break
    assert any_partial, (
        "Aggregate FAMILY invariant: neither of the two registered "
        "AST-similarity detectors (cross-layer-clone, "
        "parallel-hierarchy) surfaced partial_success=True on its "
        "gate-collapse fixture. Expected at least one to disclose."
    )


# ---------------------------------------------------------------------------
# Sister-suite parity smoke check — fixture parity with W805-CCCC
# (the W805-CCCC test fixture for empty_corpus uses index_in_process +
# git_init; this suite reuses the same helpers).
# ---------------------------------------------------------------------------


def test_sister_xfails_unaffected_smoke(empty_corpus_project, cli_runner):
    """Sister W805-{WWW,ZZZ,CCCC} suites continue to work on their own
    corpora — this is a no-op smoke check that the W805-FFFF fixtures
    do not somehow collide with the sister suites' shared fixtures.

    Running cmd_smells over the empty-corpus fixture must produce a
    parseable envelope (no crash); the existing sister xfails remain
    untouched on their respective fixtures.
    """
    result = _invoke(cli_runner, empty_corpus_project, json_mode=True, extra_args=("--only", "parallel-hierarchy"))
    data = _parse_json(result)
    assert data.get("command") == "smells"
    assert data["summary"].get("total_smells") == 0
