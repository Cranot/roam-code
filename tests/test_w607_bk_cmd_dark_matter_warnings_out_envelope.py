"""W607-BK -- ``cmd_dark_matter`` substrate-boundary plumbing.

Forty-sixth-in-batch W607 consumer-layer arc. ADDITIVE plumbing: cmd_dark_matter
already carried a ``dark_matter_unknown_severity:<value>`` marker family
(W641-followup-G, emitted by ``_dark_matter_risk_level`` via the
``warnings_out`` parameter). W607-BK extends the same prefix family with
substrate-call markers:

* compute_cochange_pairs    -- the core ``dark_matter_edges`` call
                               (NPMI + cochange-count filter).
* hypothesize_pairs         -- ``HypothesisEngine.classify_all``
                               (typed SHARED_DB / EVENT_BUS / SHARED_CONFIG
                               / SHARED_API / TEXT_SIMILARITY classifier).
* emit_findings             -- registry mirror under ``--persist``
                               (W154 file_pair subject_kind).
* query_cochange_count      -- empty-floor disclosure probe
                               (replaces silent
                               ``except sqlite3.OperationalError: pass``).
* serialize_to_sarif        -- SARIF projection for CI gates.

cmd_dark_matter is one of the W805 paired-scoring detector family
(dark_matter + duplicates + clones + smells) -- each detects DRY/architecture
debt from a different signal axis (co-change vs AST-similarity vs
token-similarity vs smell-patterns). The marker-prefix discipline test
locks in the ``dark_matter_*`` family so a future drift into ``clones_*``
or ``duplicates_*`` is caught.

Marker family ``dark_matter_<phase>_failed:<exc_class>:<detail>``
(underscore form -- matches the pre-existing
``dark_matter_unknown_severity:<value>`` marker).

W978 first-hypothesis check
---------------------------

Each W607-BK-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Substrates are patched
via ``monkeypatch.setattr`` on module-level helpers.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

PATTERN-2 ELIMINATIONS
----------------------

Two Pattern-2 silent fallbacks were removed in-place by W607-BK:

1. The ``except sqlite3.OperationalError: pass`` inside the
   ``--persist`` path silently no-op'd whenever ANY OperationalError
   surfaced (locked DB, full disk, etc.) -- not just a missing findings
   table. Replaced with ``_run_check_bk("emit_findings", ...)``.
2. The ``except sqlite3.OperationalError: pass`` inside the
   ``cochange_count`` probe silently degraded to the "no cochange
   history" verdict whenever the SELECT raised. Replaced with
   ``_run_check_bk("query_cochange_count", ..., default=0)`` on BOTH
   the JSON and text branches.
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
def dark_matter_project(project_factory):
    """Small indexed corpus -- enough for cmd_dark_matter to emit a
    non-empty envelope. Pair counts may be 0 on this small corpus
    (cmd_dark_matter requires git_cochange history to produce non-empty
    pairs) but the envelope is fully formed either way."""
    return project_factory(
        {
            "service.py": ("def process():\n    return 42\n\ndef helper():\n    return process()\n"),
            "api.py": ("from service import process\ndef handle():\n    return process()\n"),
            "lib/util.py": "def util_fn():\n    return 42\n",
        }
    )


def _invoke_dark_matter(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam dark-matter`` against a project root via top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("dark-matter")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BK substrate markers
# ---------------------------------------------------------------------------


def test_dark_matter_clean_envelope_omits_w607bk_markers(cli_runner, dark_matter_project):
    """Clean dark-matter run -> no W607-BK substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BK bucket on
    the success path must NOT introduce new ``dark_matter_<phase>_failed:``
    markers tied to the W607-BK wrap.
    """
    result = _invoke_dark_matter(cli_runner, dark_matter_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "dark-matter"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bk_phases = (
        "compute_cochange_pairs",
        "hypothesize_pairs",
        "emit_findings",
        "query_cochange_count",
        "serialize_to_sarif",
    )
    bk_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"dark_matter_{p}_failed:" in m for p in bk_phases)
    ]
    assert not bk_markers, (
        f"clean dark-matter must NOT surface W607-BK substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) compute_cochange_pairs failure -> structured marker + partial_success
# ---------------------------------------------------------------------------


def test_dark_matter_compute_pairs_failure_marker_format(cli_runner, dark_matter_project, monkeypatch):
    """If ``dark_matter_edges`` raises, surface the W607-BK marker with
    the canonical three-segment shape.
    """
    from roam.graph import dark_matter as _dm_graph

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pairs-from-W607-BK")

    monkeypatch.setattr(_dm_graph, "dark_matter_edges", _raise)

    result = _invoke_dark_matter(cli_runner, dark_matter_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("dark_matter_compute_cochange_pairs_failed:")]
    assert markers, f"expected dark_matter_compute_cochange_pairs_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-pairs-from-W607-BK" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"compute-failure degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_dark_matter_w607bk_warnings_in_envelope(cli_runner, dark_matter_project, monkeypatch):
    """Non-empty W607-BK bucket -> both top-level AND summary.warnings_out."""
    from roam.graph import dark_matter as _dm_graph

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BK")

    monkeypatch.setattr(_dm_graph, "dark_matter_edges", _raise)

    result = _invoke_dark_matter(cli_runner, dark_matter_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BK disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BK disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("dark_matter_compute_cochange_pairs_failed:")]
    assert markers, f"expected dark_matter_compute_cochange_pairs_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_dark_matter_three_segment_marker_shape(cli_runner, dark_matter_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    """
    from roam.graph import dark_matter as _dm_graph

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BK")

    monkeypatch.setattr(_dm_graph, "dark_matter_edges", _raise)

    result = _invoke_dark_matter(cli_runner, dark_matter_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("dark_matter_compute_cochange_pairs_failed:")]
    assert failure_markers, f"expected dark_matter_compute_cochange_pairs_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "dark_matter_compute_cochange_pairs_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) hypothesize_pairs failure -> envelope still emits with pairs
# ---------------------------------------------------------------------------


def test_dark_matter_hypothesize_failure_envelope_still_emits(cli_runner, dark_matter_project, monkeypatch):
    """A raise inside ``HypothesisEngine.classify_all`` must NOT crash
    the dark-matter command. The pairs still emit; the hypothesis fields
    fall back to whatever the engine populated before the crash (or
    ``UNKNOWN`` defaults).

    NOTE: on a small corpus without git history the pairs list is
    usually empty so the classify_all call is short-circuited; this
    test focuses on the source-level guarantee that the wrap exists
    and degrades cleanly when triggered.
    """
    from roam.graph import dark_matter as _dm_graph

    def _raise(self, pairs):
        raise RuntimeError("synthetic-hypothesize-from-W607-BK")

    # Patch the bound method on the class so any HypothesisEngine
    # instance routes through the raise.
    monkeypatch.setattr(_dm_graph.HypothesisEngine, "classify_all", _raise)

    # Force a non-empty pairs list so the hypothesis call path runs.
    fake_pair = [
        {
            "path_a": "service.py",
            "path_b": "api.py",
            "npmi": 0.5,
            "lift": 2.0,
            "strength": 0.3,
            "cochange_count": 5,
        }
    ]
    monkeypatch.setattr(
        _dm_graph,
        "dark_matter_edges",
        lambda conn, **kw: fake_pair,
    )

    result = _invoke_dark_matter(cli_runner, dark_matter_project, "--explain")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # The pairs still emit (degraded hypothesis layer).
    pairs = data.get("dark_matter_pairs", [])
    assert isinstance(pairs, list), f"dark_matter_pairs must still emit on hypothesize degrade; got {data!r}"
    # Surface marker present.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("dark_matter_hypothesize_pairs_failed:")]
    assert markers, f"expected dark_matter_hypothesize_pairs_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-BK stays in ``dark_matter_*`` family
# ---------------------------------------------------------------------------


def test_w607bk_marker_prefix_stays_in_dark_matter_family(cli_runner, dark_matter_project, monkeypatch):
    """Every W607-BK substrate marker uses the canonical ``dark_matter_*``
    prefix.

    Hard distinction from sibling W607-* layers including the paired
    W805 detectors (clones / duplicates / smells) that share the same
    DRY/architecture-debt axis.

    Locks in the W979 vocabulary regression guard: the marker prefix
    uses the underscore form ``dark_matter_*`` (matches the pre-existing
    ``dark_matter_unknown_severity:`` family from W641-followup-G), NOT
    the hyphenated ``dark-matter_*`` -- per W982 the detector-key
    canonical form is whatever the adjacent commands use, and the
    existing marker family is the source of truth here.
    """
    from roam.graph import dark_matter as _dm_graph

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BK")

    monkeypatch.setattr(_dm_graph, "dark_matter_edges", _raise)

    result = _invoke_dark_matter(cli_runner, dark_matter_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("dark_matter_"), (
            f"every surfaced W607-BK marker must use the ``dark_matter_*`` "
            f"prefix family (cmd_dark_matter scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers (paired W805
        # detectors + the broader W607 family).
        for forbidden_prefix, sibling in (
            # W805 paired-scoring detector family
            ("clones_", "cmd_clones W805 sibling"),
            ("duplicates_", "cmd_duplicates W805 sibling"),
            ("smells_", "cmd_smells W805 sibling"),
            # Broader W607 family
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("taint_", "cmd_taint W607-AY"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("impact_", "cmd_impact W607-T"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )

    # W979 vocabulary regression guard: marker prefix uses underscore
    # form (matches pre-existing dark_matter_unknown_severity family).
    for marker in substrate_markers:
        assert not marker.startswith("dark-matter_"), (
            f"marker uses hyphenated form -- inconsistent with the "
            f"pre-existing dark_matter_unknown_severity family from "
            f"W641-followup-G; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_dark_matter carries the W607-BK accumulator
# ---------------------------------------------------------------------------


def test_cmd_dark_matter_carries_w607bk_accumulator():
    """AST-level guard: cmd_dark_matter source carries the W607-BK accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dark_matter.py"
    assert src_path.exists(), f"cmd_dark_matter.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bk_warnings_out" in src, (
        "W607-BK accumulator missing from cmd_dark_matter; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bk" in src, (
        "W607-BK ``_run_check_bk`` helper missing from cmd_dark_matter; "
        "the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bk is defined inside cmd_dark_matter.
    tree = ast.parse(src)
    found_run_check_bk = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bk":
            found_run_check_bk = True
            break
    assert found_run_check_bk, (
        "W607-BK ``_run_check_bk`` helper not found in cmd_dark_matter AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-BK substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bk_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BK substrate boundary is wrapped.

    W607-BK substrate inventory (cmd_dark_matter):

    * compute_cochange_pairs    -- ``dark_matter_edges``
    * hypothesize_pairs         -- ``HypothesisEngine.classify_all``
    * emit_findings             -- ``_emit_dark_matter_findings`` + commit
    * query_cochange_count      -- ``SELECT COUNT(*) FROM git_cochange``
    * serialize_to_sarif        -- ``dark_matter_to_sarif``

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dark_matter.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "compute_cochange_pairs",
        "hypothesize_pairs",
        "emit_findings",
        "query_cochange_count",
        "serialize_to_sarif",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bk("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bk(\n        "{phase}"' in src
            or f'_run_check_bk(\n            "{phase}"' in src
            or f'_run_check_bk(\n                "{phase}"' in src
            or f'_run_check_bk(\n                    "{phase}"' in src
            or f'_run_check_bk(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BK _run_check_bk wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) Pre-existing dark_matter_unknown_severity marker still emits (ADDITIVE)
# ---------------------------------------------------------------------------


def test_w607bk_coexists_with_unknown_severity_family():
    """W607-BK is ADDITIVE to the pre-existing W641-followup-G
    ``dark_matter_unknown_severity:<value>`` marker family.

    Source-level guard: BOTH marker prefix families are present in the
    cmd_dark_matter source. A future refactor that removes the
    unknown_severity emission must not silently drop the W641-followup-G
    contract; conversely, the W607-BK substrate-call markers must
    coexist with it on the same accumulator.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dark_matter.py"
    src = src_path.read_text(encoding="utf-8")

    # Pre-existing W641-followup-G family (Pattern-2 silent-fallback fix
    # for unknown/negative risk-projection inputs).
    assert "dark_matter_unknown_severity:" in src, (
        "Pre-existing W641-followup-G dark_matter_unknown_severity: "
        "marker family has been removed from cmd_dark_matter."
    )
    # New W607-BK family (substrate-CALL markers).
    assert "w607bk_warnings_out" in src, "W607-BK substrate-CALL accumulator has been removed."
    # Both families share the dark_matter_* prefix discipline -- the
    # marker-prefix test above pins the runtime invariant.


# ---------------------------------------------------------------------------
# (10) PAIRED-SCORING coexistence: dark_matter + clones markers can coexist
# ---------------------------------------------------------------------------


def test_w805_paired_scoring_markers_coexist_on_same_corpus(cli_runner, dark_matter_project, monkeypatch):
    """W805 paired-scoring detector family: dark_matter + clones +
    duplicates + smells all detect DRY/architecture debt from different
    signal axes on the same corpus. They can produce non-empty
    warnings_out buckets simultaneously when each substrate raises on
    its own axis.

    cmd_clones is NOT yet W607-plumbed (per the wave plan it's an
    upcoming consumer), so this test verifies the SYMMETRIC half: the
    dark_matter envelope correctly emits its W607-BK markers without
    leaking into the clones_* / duplicates_* / smells_* prefix families
    that the upcoming paired waves will own.

    When clones / duplicates / smells get their W607 plumbing, the
    paired-coexistence test for those waves should run BOTH commands
    on this corpus and verify each envelope carries its own family
    cleanly.
    """
    from roam.graph import dark_matter as _dm_graph

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-paired-from-W607-BK")

    monkeypatch.setattr(_dm_graph, "dark_matter_edges", _raise)

    result = _invoke_dark_matter(cli_runner, dark_matter_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # dark_matter_* markers present.
    dm_markers = [m for m in all_wo if m.startswith("dark_matter_")]
    assert dm_markers, f"expected dark_matter_* markers on dark-matter envelope; got {all_wo!r}"

    # No paired-W805 sibling prefix leakage.
    for sibling_prefix in ("clones_", "duplicates_", "smells_"):
        sibling_leak = [m for m in all_wo if m.startswith(sibling_prefix)]
        assert not sibling_leak, (
            f"dark-matter envelope leaked into {sibling_prefix}* family "
            f"(W805 paired-scoring sibling scope); got {sibling_leak!r}"
        )


# ---------------------------------------------------------------------------
# (11) --persist path: emit_findings degradation surfaces a marker
# ---------------------------------------------------------------------------


def test_dark_matter_persist_emit_findings_degradation(cli_runner, dark_matter_project, monkeypatch):
    """Pattern-2 silent-fallback elimination: the pre-W607-BK
    ``except sqlite3.OperationalError: pass`` inside the ``--persist``
    branch silently no-op'd whenever the findings table was missing
    OR any other OperationalError surfaced. FIXED IN PLACE: the
    exception still degrades to no-write, but now surfaces a
    ``dark_matter_emit_findings_failed:<exc>:<detail>`` marker.

    NOTE: this test forces a non-empty pairs list via monkeypatch so
    the ``--persist`` write path actually runs (small corpora without
    git history yield empty pairs, which short-circuits the emit).
    """
    from roam.commands import cmd_dark_matter
    from roam.graph import dark_matter as _dm_graph

    fake_pair = [
        {
            "path_a": "service.py",
            "path_b": "api.py",
            "npmi": 0.5,
            "lift": 2.0,
            "strength": 0.3,
            "cochange_count": 5,
            "hypothesis": {"category": "UNKNOWN", "detail": ""},
        }
    ]
    monkeypatch.setattr(
        _dm_graph,
        "dark_matter_edges",
        lambda conn, **kw: list(fake_pair),
    )

    # Patch the emit helper at the module level so the --persist path
    # routes through the raise.
    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-BK")

    monkeypatch.setattr(cmd_dark_matter, "_emit_dark_matter_findings", _raise)

    result = _invoke_dark_matter(cli_runner, dark_matter_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    emit_markers = [m for m in all_wo if m.startswith("dark_matter_emit_findings_failed:")]
    assert emit_markers, f"expected dark_matter_emit_findings_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in emit_markers), emit_markers
    # The envelope still emits cleanly.
    assert "dark_matter_pairs" in data
    assert data["summary"].get("partial_success") is True
