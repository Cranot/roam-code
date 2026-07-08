"""W607-BJ -- ``cmd_complexity`` substrate-boundary plumbing.

Forty-sixth-in-batch W607 consumer-layer arc. FRESH plumbing: cmd_complexity
had no prior W607 instrumentation. This wave installs the canonical
``_w607bj_warnings_out`` bucket + ``_run_check_bj`` helper inside the
``complexity`` click command and wraps the substrate boundaries:

* query_symbol_metrics       -- main symbol_metrics JOIN with WHERE/ORDER/LIMIT
* apply_filters              -- in-Python role-filter chain (tooling,
                                framework, imports)
* compute_distribution_stats -- the full-table cognitive_complexity scan
                                used for avg/p90/critical/high
* classify_severity          -- the wrap_findings + confidence
                                distribution layer
* serialize_to_sarif         -- SARIF projection
* emit_findings              -- W93/W102 findings-registry mirror

cmd_complexity is the third leg of the health/debt/complexity DB-substrate
trio: cmd_health (W607-M + W607-BA, ``health_*``) scores the whole codebase,
cmd_debt (W607-BG, ``debt_*``) ranks files by hotspot-weighted remediation
cost, and cmd_complexity (W607-BJ, ``complexity_*``) ranks individual symbols
by cognitive complexity. All three consume the same DB substrate
(symbol_metrics, symbols, files); each owns a distinct marker prefix family
for observability discipline.

Marker family ``complexity_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-discipline
test.

W978 first-hypothesis check
---------------------------

Each W607-BJ-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Substrates are patched
via ``monkeypatch.setattr`` on module-level helpers / inline lambdas via
the underlying SQL connection mock.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

W806/W810 Pattern-1B regression guard
-------------------------------------

The empty-corpus envelope path (no symbol_metrics rows) is fully covered by
``test_w806_complexity_empty_corpus``. W607-BJ markers must NOT regress
that path: a clean run on an empty corpus must NOT introduce complexity_*
markers (the count-probe short-circuits BEFORE any substrate-CALL boundaries
are crossed, so the W607-BJ bucket stays empty by construction).
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
def complexity_project(project_factory):
    """Small indexed corpus -- enough for the complexity pipeline to
    produce a non-empty envelope (symbol_metrics populated by the
    indexer)."""
    return project_factory(
        {
            "service.py": (
                "def process(items):\n"
                "    total = 0\n"
                "    for x in items:\n"
                "        if x > 0:\n"
                "            for y in range(x):\n"
                "                if y % 2 == 0:\n"
                "                    if y > 5:\n"
                "                        total += y\n"
                "                    else:\n"
                "                        total -= y\n"
                "    return total\n"
                "\n"
                "def helper(items):\n"
                "    return process(items) * 2\n"
            ),
            "api.py": (
                "from service import process\n"
                "def handle(payload):\n"
                "    return process(payload)\n"
                "def route(request):\n"
                "    return handle(request)\n"
            ),
            "lib/util.py": "def util_fn():\n    return 42\n",
        }
    )


def _invoke_complexity(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam complexity`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("complexity")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BJ substrate markers
# ---------------------------------------------------------------------------


def test_complexity_clean_envelope_omits_w607bj_markers(cli_runner, complexity_project):
    """Clean complexity run -> no W607-BJ substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BJ bucket on
    the success path must NOT introduce new ``complexity_<phase>_failed:``
    markers tied to the W607-BJ wrap.
    """
    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "complexity"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bj_phases = (
        "query_symbol_metrics",
        "apply_filters",
        "compute_distribution_stats",
        "classify_severity",
        "serialize_to_sarif",
        "emit_findings",
    )
    bj_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"complexity_{p}_failed:" in m for p in bj_phases)
    ]
    assert not bj_markers, (
        f"clean complexity must NOT surface W607-BJ substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) classify_severity failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_complexity_classify_severity_failure_marker_format(cli_runner, complexity_project, monkeypatch):
    """If ``wrap_findings`` (classify_severity substrate) raises, surface
    the W607-BJ marker with the canonical three-segment shape.
    """
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-classify-from-W607-BJ")

    monkeypatch.setattr(cmd_complexity, "wrap_findings", _raise)

    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    classify_markers = [m for m in all_wo if m.startswith("complexity_classify_severity_failed:")]
    assert classify_markers, f"expected complexity_classify_severity_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in classify_markers), classify_markers
    assert any("synthetic-classify-from-W607-BJ" in m for m in classify_markers), classify_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"classify-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_complexity_w607bj_warnings_in_envelope(cli_runner, complexity_project, monkeypatch):
    """Non-empty W607-BJ bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BJ")

    monkeypatch.setattr(cmd_complexity, "wrap_findings", _raise)

    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BJ disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BJ disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("complexity_classify_severity_failed:")]
    assert markers, f"expected complexity_classify_severity_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_complexity_three_segment_marker_shape(cli_runner, complexity_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    """
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BJ")

    monkeypatch.setattr(cmd_complexity, "wrap_findings", _raise)

    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("complexity_classify_severity_failed:")]
    assert failure_markers, f"expected complexity_classify_severity_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "complexity_classify_severity_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) apply_filters failure -> rankings still emit
# ---------------------------------------------------------------------------


def test_complexity_apply_filters_degradation_rankings_still_emit(cli_runner, complexity_project, monkeypatch):
    """A raise in ``_apply_role_filters`` must NOT crash the complexity
    report wholesale. The unfiltered rankings continue to emit (the
    natural fallback when the role-filter layer collapses).
    """
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-filters-from-W607-BJ")

    monkeypatch.setattr(cmd_complexity, "_apply_role_filters", _raise)

    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    filter_markers = [m for m in all_wo if m.startswith("complexity_apply_filters_failed:")]
    assert filter_markers, f"expected complexity_apply_filters_failed: marker; got {all_wo!r}"

    # The rankings array still appears (per-signal degradation).
    symbols = data.get("symbols")
    assert isinstance(symbols, list), f"symbols must still emit on filter degradation; got data = {data!r}"
    # summary.partial_success flipped.
    assert data["summary"].get("partial_success") is True, (
        f"filter degradation must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) PER-SIGNAL DEGRADATION: one phase failure doesn't sink the envelope
# ---------------------------------------------------------------------------


def test_complexity_per_signal_degradation_other_phases_complete(cli_runner, complexity_project, monkeypatch):
    """A raise in ``wrap_findings`` (classify_severity) must NOT prevent
    the rest of the envelope from being composed (symbol rankings,
    verdict, distribution stats all still emit).
    """
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-per-signal-from-W607-BJ")

    monkeypatch.setattr(cmd_complexity, "wrap_findings", _raise)

    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) classify_severity failure marker present.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    sn_markers = [m for m in all_wo if m.startswith("complexity_classify_severity_failed:")]
    assert sn_markers, f"expected complexity_classify_severity_failed: marker; got {all_wo!r}"

    # 2) The headline symbols array still appears.
    symbols = data.get("symbols")
    assert isinstance(symbols, list), f"symbols missing despite classify degrade; got data = {data!r}"

    # 3) The verdict still appears and is one line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"

    # 4) Distribution stats still populated from compute_distribution_stats.
    assert "total_analyzed" in data["summary"]

    # 5) summary partial_success flipped.
    assert data["summary"].get("partial_success") is True, (
        f"per-signal failure must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-BJ stays in ``complexity_*`` family
# ---------------------------------------------------------------------------


def test_w607bj_marker_prefix_stays_in_complexity_family(cli_runner, complexity_project, monkeypatch):
    """Every W607-BJ substrate marker uses the canonical ``complexity_*``
    prefix.

    Hard distinction from sibling W607-* layers including the paired
    cmd_health (W607-M / W607-BA, ``health_*``) and cmd_debt (W607-BG,
    ``debt_*``) surfaces that share the same DB substrate.
    """
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BJ")

    monkeypatch.setattr(cmd_complexity, "wrap_findings", _raise)

    result = _invoke_complexity(cli_runner, complexity_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("complexity_"), (
            f"every surfaced W607-BJ marker must use the ``complexity_*`` "
            f"prefix family (cmd_complexity scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("health_", "cmd_health W607-M / W607-BA"),
            ("debt_", "cmd_debt W607-BG"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("audit_trail_", "cmd_audit_trail W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
            ("evidence_diff_", "cmd_evidence_diff W607-AX"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Source-level guard: cmd_complexity carries the W607-BJ accumulator
# ---------------------------------------------------------------------------


def test_cmd_complexity_carries_w607bj_accumulator():
    """AST-level guard: cmd_complexity source carries the W607-BJ accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_complexity.py"
    assert src_path.exists(), f"cmd_complexity.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bj_warnings_out" in src, (
        "W607-BJ accumulator missing from cmd_complexity; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bj" in src, (
        "W607-BJ ``_run_check_bj`` helper missing from cmd_complexity; the "
        "per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bj is defined inside cmd_complexity.
    tree = ast.parse(src)
    found_run_check_bj = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bj":
            found_run_check_bj = True
            break
    assert found_run_check_bj, (
        "W607-BJ ``_run_check_bj`` helper not found in cmd_complexity AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-BJ substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bj_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BJ substrate boundary is wrapped.

    W607-BJ substrate inventory (cmd_complexity):

    * query_symbol_metrics       -- main symbol_metrics JOIN query
    * apply_filters              -- in-Python role-filter chain
    * compute_distribution_stats -- full-table cognitive_complexity scan
    * classify_severity          -- wrap_findings + confidence distribution
    * serialize_to_sarif         -- SARIF projection
    * emit_findings              -- W93/W102 findings-registry mirror

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_complexity.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "query_symbol_metrics",
        "apply_filters",
        "compute_distribution_stats",
        "classify_severity",
        "serialize_to_sarif",
        "emit_findings",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bj("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bj(\n        "{phase}"' in src
            or f'_run_check_bj(\n            "{phase}"' in src
            or f'_run_check_bj(\n                "{phase}"' in src
            or f'_run_check_bj(\n                    "{phase}"' in src
            or f'_run_check_bj(\n                        "{phase}"' in src
        )
        # ``emit_findings`` is wrapped via a direct ``try/except`` block
        # (NOT ``_run_check_bj``) because it needs to distinguish
        # ``sqlite3.OperationalError`` (expected pre-W89 path -> W1086
        # ``warnings`` axis) from generic Exception (W607-BJ marker).
        # Source-grep on the marker name in both modes.
        marker_grep = f"complexity_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-BJ wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) HEALTH/DEBT/COMPLEXITY trio coexistence -- markers coexist
# ---------------------------------------------------------------------------


def test_health_debt_complexity_marker_families_coexist_on_same_corpus(cli_runner, complexity_project, monkeypatch):
    """cmd_health (W607-M + W607-BA, ``health_*``), cmd_debt (W607-BG,
    ``debt_*``), and cmd_complexity (W607-BJ, ``complexity_*``) share
    substrate boundaries on the same DB shape (symbol_metrics, symbols,
    files). Verify markers from ALL THREE families surface together when
    run sequentially on the same corpus.

    This is the canonical health/debt/complexity trio coexistence bonus
    -- three consumer surfaces of the same DB substrate layer, distinct
    prefix families, coexist cleanly on the same warnings_out axis.
    """
    from roam.cli import cli as _cli
    from roam.commands import cmd_complexity, cmd_debt, cmd_health

    def _raise_complexity(*args, **kwargs):
        raise RuntimeError("synthetic-complexity-from-trio")

    def _raise_debt(*args, **kwargs):
        raise RuntimeError("synthetic-debt-from-trio")

    def _raise_health(*args, **kwargs):
        raise RuntimeError("synthetic-health-from-trio")

    # Inject a raise into ONE substrate per command.
    monkeypatch.setattr(cmd_complexity, "wrap_findings", _raise_complexity)
    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise_debt)
    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise_health)

    # Run complexity first.
    complexity_result = _invoke_complexity(cli_runner, complexity_project)
    assert complexity_result.exit_code == 0, complexity_result.output
    complexity_data = _json.loads(complexity_result.output)
    complexity_top_wo = complexity_data.get("warnings_out") or []
    complexity_summary_wo = complexity_data["summary"].get("warnings_out") or []
    complexity_all_wo = list(complexity_top_wo) + list(complexity_summary_wo)
    complexity_markers = [m for m in complexity_all_wo if m.startswith("complexity_classify_severity_failed:")]
    assert complexity_markers, (
        f"expected complexity_classify_severity_failed: marker on complexity envelope; got {complexity_all_wo!r}"
    )

    # Run debt on the same corpus.
    old_cwd = os.getcwd()
    try:
        os.chdir(str(complexity_project))
        debt_result = cli_runner.invoke(_cli, ["--json", "debt"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert debt_result.exit_code == 0, debt_result.output
    debt_data = _json.loads(debt_result.output)
    debt_top_wo = debt_data.get("warnings_out") or []
    debt_summary_wo = debt_data["summary"].get("warnings_out") or []
    debt_all_wo = list(debt_top_wo) + list(debt_summary_wo)
    debt_markers = [m for m in debt_all_wo if m.startswith("debt_improvement_suggestions_failed:")]
    assert debt_markers, f"expected debt_improvement_suggestions_failed: marker on debt envelope; got {debt_all_wo!r}"

    # Run health on the same corpus.
    try:
        os.chdir(str(complexity_project))
        health_result = cli_runner.invoke(_cli, ["--json", "health"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert health_result.exit_code == 0, health_result.output
    health_data = _json.loads(health_result.output)
    health_top_wo = health_data.get("warnings_out") or []
    health_summary_wo = health_data["summary"].get("warnings_out") or []
    health_all_wo = list(health_top_wo) + list(health_summary_wo)
    health_markers = [m for m in health_all_wo if m.startswith("health_suggest_next_steps_call_failed:")]
    assert health_markers, (
        f"expected health_suggest_next_steps_call_failed: marker on health envelope; got {health_all_wo!r}"
    )

    # Prefix-family isolation across the trio: each envelope must NOT
    # carry markers from the other two prefix families.
    complexity_leak = [m for m in complexity_all_wo if m.startswith(("health_", "debt_"))]
    assert not complexity_leak, f"complexity envelope must NOT carry health_/debt_ markers; got {complexity_leak!r}"
    debt_leak = [m for m in debt_all_wo if m.startswith(("health_", "complexity_"))]
    assert not debt_leak, f"debt envelope must NOT carry health_/complexity_ markers; got {debt_leak!r}"
    health_leak = [m for m in health_all_wo if m.startswith(("debt_", "complexity_"))]
    assert not health_leak, f"health envelope must NOT carry debt_/complexity_ markers; got {health_leak!r}"

    # All three surfaces flipped partial_success.
    assert complexity_data["summary"].get("partial_success") is True
    assert debt_data["summary"].get("partial_success") is True
    assert health_data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (11) W806/W810 Pattern-1B regression guard -- empty corpus stays clean
# ---------------------------------------------------------------------------


def test_w806_w810_empty_corpus_envelope_stays_clean_under_w607bj(cli_runner, tmp_path):
    """W806/W810 Pattern-1B regression guard: the empty-corpus envelope
    path must not regress under W607-BJ markers.

    The count-probe ``SELECT COUNT(*) FROM symbol_metrics`` short-circuits
    BEFORE any W607-BJ substrate-CALL boundaries are crossed (the empty
    return + state="no_complexity_data" path). The W607-BJ bucket stays
    empty by construction -- the envelope is byte-identical for the
    complexity_* marker axis to pre-W607-BJ.

    Pinned by ``test_w806_complexity_empty_corpus`` (the broader empty-
    corpus envelope contract). This test specifically asserts that the
    new W607-BJ markers do NOT regress that path.
    """
    from roam.cli import cli

    repo = tmp_path / "empty_corpus_w607bj"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    # Empty .py file -- indexable, parseable, but yields zero rows in
    # symbol_metrics.
    (repo / "empty.py").write_text("")

    # Minimal git init so roam can index it.
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        # Run roam init explicitly to ensure index exists.
        init_result = cli_runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_result.exit_code == 0, init_result.output
        result = cli_runner.invoke(cli, ["--json", "complexity"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    # Even though the empty-corpus path may exit with SystemExit -- W806
    # accepts (0, 1) -- the JSON envelope MUST be present in stdout.
    raw = result.output.strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        assert start >= 0 and end > start, f"complexity emitted no JSON envelope on empty corpus:\n{result.output}"
        raw = raw[start : end + 1]
    data = _json.loads(raw)

    # The W607-BJ marker axis MUST be empty on the empty-corpus path.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    bj_markers = [m for m in all_wo if m.startswith("complexity_") and "_failed:" in m]
    assert not bj_markers, (
        f"empty-corpus envelope must NOT surface W607-BJ markers "
        f"(count-probe short-circuits before substrate-CALL boundaries); "
        f"got {bj_markers!r}"
    )

    # AST/state pin: the empty-corpus state marker is intact.
    verdict = data["summary"].get("verdict", "")
    assert any(token in verdict.lower() for token in ("no complexity data", "no data", "no symbols", "no findings")), (
        f"empty-corpus verdict must disclose no-data state (Pattern 2); got {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (12) --by-file path also threads W607-BJ warnings
# ---------------------------------------------------------------------------


def test_complexity_by_file_threads_w607bj_warnings(cli_runner, complexity_project, monkeypatch):
    """The ``--by-file`` (grouped) JSON branch also threads W607-BJ
    markers via the ``warnings`` parameter into ``_by_file_output``.
    """
    from roam.commands import cmd_complexity

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-byfile-from-W607-BJ")

    # apply_filters is the easiest substrate to trigger on the --by-file
    # path since the rankings query feeds straight into _by_file_output.
    monkeypatch.setattr(cmd_complexity, "_apply_role_filters", _raise)

    result = _invoke_complexity(cli_runner, complexity_project, "--by-file")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("complexity_apply_filters_failed:")]
    assert markers, f"expected complexity_apply_filters_failed: marker on --by-file path; got {all_wo!r}"
    # summary.partial_success flipped via the warnings list threaded into
    # _by_file_output.
    assert data["summary"].get("partial_success") is True
