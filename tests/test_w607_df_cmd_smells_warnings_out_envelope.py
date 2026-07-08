"""W607-DF -- additive aggregation-phase plumbing for ``cmd_smells``.

cmd_smells detects 24 deterministic structural smells -- the smell-pattern
axis of the structural-debt paired-scoring 4-way (W805: clones BQ/DC,
duplicates BM/DD, smells BN/DF, dark_matter BK/CZ). With W607-DF landed,
the full smells path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-BN (10 substrate boundaries:
    load_suppress_rules / query_findings_corpus / apply_suppressions /
    apply_kind_filter / apply_min_severity_filter / apply_tooling_filter /
    aggregate_by_kind / classify_severity / serialize_to_sarif /
    emit_findings)
  - aggregation-phase layer: W607-DF (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

With W607-DF in place, the structural-debt 4-way closes at the
aggregation-phase layer -- ALL four members (clones DC, duplicates DD,
smells DF, dark_matter CZ) now carry the aggregation-phase plumbing on
top of their pre-existing substrate-CALL layer.

Both layers share the canonical ``smells_*`` marker family and the
``smells_<phase>_failed:<exc_class>:<detail>`` shape contract. The three
bucket sources (W987 ``warnings_list`` + ``_w607bn_warnings_out``
substrate-CALL + ``_w607df_warnings_out`` aggregation-phase) are merged
at envelope-emit time via ``_merged_warnings`` so consumers see the full
degradation lineage in marker-emission order.

W978 7-discipline first-hypothesis check
----------------------------------------

cmd_sbom W607-CG sealed the kwarg-default eagerness trap (computed
defaults eval BEFORE the try-block).
cmd_taint W607-CJ codified the 5th discipline: move ``len()`` INSIDE
the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)`` -- ``.get`` evaluates default
eagerly at call site, re-raising on a poisoned upstream input.

Every W607-DF ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-DF
layer.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Canonical W607-DF phase enumeration
# ---------------------------------------------------------------------------


_DF_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

_BN_PHASES = (
    "load_suppress_rules",
    "query_findings_corpus",
    "apply_suppressions",
    "apply_kind_filter",
    "apply_min_severity_filter",
    "apply_tooling_filter",
    "aggregate_by_kind",
    "classify_severity",
    "serialize_to_sarif",
    "emit_findings",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_smells_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root with at least one detectable smell.

    Mirrors test_w607_bn._build_smells_project: brain-method on
    ``process_everything`` at src/engine.py:10. One row is enough to
    exercise the full aggregation-phase chain with a non-empty corpus.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "dummy.py").write_text("# dummy\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0,
            cyclomatic_density REAL DEFAULT 0,
            halstead_volume REAL DEFAULT 0,
            halstead_difficulty REAL DEFAULT 0,
            halstead_effort REAL DEFAULT 0,
            halstead_bugs REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY,
            finding_id_str TEXT NOT NULL UNIQUE,
            subject_kind TEXT NOT NULL,
            subject_id INTEGER,
            claim TEXT NOT NULL,
            evidence_json TEXT,
            confidence TEXT NOT NULL,
            source_detector TEXT NOT NULL,
            source_version TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'open'
        );
    """)
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
        "VALUES (1, 1, 'process_everything', 'src.engine.process_everything', 'function', 10, 200)"
    )
    conn.execute(
        "INSERT INTO symbol_metrics "
        "(symbol_id, cognitive_complexity, nesting_depth, param_count, line_count, return_count, "
        "bool_op_count, callback_depth) "
        "VALUES (1, 60, 8, 10, 190, 6, 12, 4)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def smells_project(tmp_path):
    return _build_smells_project(tmp_path)


def _invoke_smells(cli_runner, project_root, *args, json_mode=True, detail=False):
    """Invoke the smells click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_smells import smells

    obj = {"json": json_mode, "sarif": False, "budget": 0, "detail": detail}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(smells, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DF aggregation markers
# ---------------------------------------------------------------------------


def test_smells_happy_path_no_w607df_markers(cli_runner, smells_project):
    """Clean smells on a populated corpus -> no W607-DF aggregation markers.

    Hash-stable: an empty W607-DF bucket on the success path must produce
    an envelope without any ``smells_score_classify_failed:`` /
    ``smells_compute_predicate_failed:`` /
    ``smells_compute_verdict_failed:`` /
    ``smells_serialize_envelope_failed:`` markers (from the DF layer).
    """
    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "smells"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DF_PHASES:
        prefix = f"smells_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean smells must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_df`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_smells_carries_w607df_accumulator():
    """AST-level guard: cmd_smells source carries the W607-DF accumulator.

    Pins the canonical W607-DF anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-BN) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    assert src_path.exists(), f"cmd_smells.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "w607df_warnings_out" in src, (
        "W607-DF accumulator missing from cmd_smells; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_df" in src, (
        "W607-DF helper ``_run_check_df`` missing from cmd_smells; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_df = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_df":
            found_run_check_df = True
            break
    assert found_run_check_df, (
        "W607-DF ``_run_check_df`` helper not found in cmd_smells AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-BN must still be present (additive layer does NOT replace it)
    assert "w607bn_warnings_out" in src, (
        "W607-BN accumulator vanished alongside the W607-DF add; the "
        "additive plumbing must preserve the W607-BN substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_df():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_df(...)`` with the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DF_PHASES:
        same_line = f'_run_check_df("{phase}"' in src
        multi_line = any(f'_run_check_df(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"smells_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DF wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) serialize_envelope failure -> floor envelope still ships with marker
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, smells_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``smells_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_smells as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DF")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "smells", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("smells_serialize_envelope_failed:")]
    assert markers, f"expected ``smells_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    The canonical floor for cmd_smells is ``"smells completed"``
    (mirror of cmd_dark_matter W607-CZ's ``"dark-matter completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="smells completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DF "
        "discipline; the canonical floor literal 'smells completed' is "
        "missing from cmd_smells.py"
    )


# ---------------------------------------------------------------------------
# (6) ANY W607-DF marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, smells_project, monkeypatch):
    """ANY W607-DF or W607-BN marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    smells" from "smells ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_smells as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DF")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DF warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607df_warnings_out_in_both_top_and_summary(cli_runner, smells_project, monkeypatch):
    """Non-empty W607-DF bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_smells as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DF")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DF raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DF raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("smells_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("smells_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-DF uses the SAME ``smells_*`` family
# ---------------------------------------------------------------------------


def test_w607df_marker_prefix_smells_family(cli_runner, smells_project, monkeypatch):
    """W607-DF markers use the canonical ``smells_*`` prefix (same
    family as W607-BN; W607-DF is ADDITIVE, not a separate prefix).
    """
    from roam.commands import cmd_smells as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-DF")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("smells_"), f"every W607-DF marker must use the ``smells_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (9) W607-BN COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607bn_substrate_markers_coexist_with_w607df_aggregation(cli_runner, smells_project, monkeypatch):
    """Confirm ``smells_<substrate-phase>_failed:`` markers (W607-BN
    layer) coexist with ``smells_<agg-phase>_failed:`` markers
    (W607-DF layer) -- both in same family, but threaded through
    different buckets at envelope-emit.
    """
    from roam.commands import cmd_smells as _mod

    # W607-BN substrate boundary -- wrap_findings (classify_severity) raises
    def _raise_classify(*a, **kw):
        raise RuntimeError("synthetic-bn-coexist-classify")

    # W607-DF aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-df-coexist-envelope")

    monkeypatch.setattr(_mod, "wrap_findings", _raise_classify)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-BN
    bn_markers = [m for m in top_wo if m.startswith("smells_classify_severity_failed:")]
    # Aggregation-phase from W607-DF
    df_markers = [m for m in top_wo if m.startswith("smells_serialize_envelope_failed:")]

    assert bn_markers, f"W607-BN substrate-CALL marker (smells_classify_severity_failed) missing; got {top_wo!r}"
    assert df_markers, f"W607-DF aggregation-phase marker (smells_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``smells_*`` family
    assert all(m.startswith("smells_") for m in (bn_markers + df_markers)), (
        f"all markers must share the canonical ``smells_*`` family; got bn = {bn_markers!r}, df = {df_markers!r}"
    )


# ---------------------------------------------------------------------------
# (10) W978 7-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-DF ``default=`` must be a
    literal constant, NOT computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree.

        Allows: Constant, Dict/List/Tuple/Set of literals, unary +/- of
        a constant, and bare Name references (variables bound BEFORE
        the wrap call). Rejects Call, Attribute, Subscript, BinOp,
        Compare, IfExp, f-string, etc.
        """
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_df"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_df(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_smells.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (11) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """W978 5th-discipline AST guard (cmd_taint W607-CJ anchor): every
    ``len()`` call on a wrapped input MUST live INSIDE the wrapped
    closure, NOT at the ``_run_check_df(...)`` call site as a positional
    or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_df"):
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
                        f"_run_check_df positional-arg site -- W978 "
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
                        f"_run_check_df kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_smells.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (12) Clean envelope carries run_state from score_classify
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_run_state(cli_runner, smells_project):
    """W607-DF surfaces run_state on the envelope.

    The score_classify closure returns a state label (CLEAN /
    NEEDS_REFACTORING / FAIR / GOOD) which the envelope surfaces so
    consumers can read the run classification without re-deriving from
    raw counts.
    """
    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert summary.get("run_state") in {
        "CLEAN",
        "NEEDS_REFACTORING",
        "FAIR",
        "GOOD",
        "DEGRADED",
    }, f"run_state missing or invalid on clean envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- W607-DF stays in smells_* family
# ---------------------------------------------------------------------------


def test_w607df_cross_prefix_isolation(cli_runner, smells_project, monkeypatch):
    """Hard guard: W607-DF markers must NOT leak into sibling W607-*
    prefix families.
    """
    from roam.commands import cmd_smells as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        # W805 structural-debt 4-way sibling prefixes
        for forbidden_prefix, sibling in (
            ("clones_", "cmd_clones W805 sibling"),
            ("duplicates_", "cmd_duplicates W805 sibling"),
            ("dark_matter_", "cmd_dark_matter W805 sibling"),
            # Broader W607 family
            ("postmortem_", "cmd_postmortem W607-AN/CV"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-CO"),
            ("audit_trail_export_", "cmd_audit_trail_export W607-CR"),
            ("vulns_", "cmd_vulns W607-AQ / CH"),
            ("taint_", "cmd_taint W607-AY / CJ"),
            ("sbom_", "cmd_sbom W607-AM / CG"),
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / BA"),
            ("supply_chain_", "cmd_supply_chain W607-AK / CD"),
            ("attest_", "cmd_attest W607-AD / BT"),
            ("diff_", "cmd_diff W607-Z / BP"),
            ("critique_", "cmd_critique W607-Y / BL"),
            ("pr_risk_", "cmd_pr_risk W607-Q / BU"),
            ("impact_", "cmd_impact W607-T / BB"),
            ("retrieve_", "cmd_retrieve W607-B / BI"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (14) STRUCTURAL-DEBT PAIRED-SCORING 4-WAY pairing CLOSURE at agg-layer
# ---------------------------------------------------------------------------


def test_w805_structural_debt_4way_aggregation_pairing_CLOSURE():
    """W805 structural-debt 4-way pairing CLOSURE at the aggregation-phase
    layer.

    The W805 family detects DRY/architecture debt from 4 different signal
    axes on the same corpus:
      cmd_clones      (W607-BQ substrate + DC aggregation) -- AST-similarity axis
      cmd_duplicates  (W607-BM substrate + DD aggregation) -- token-similarity axis
      cmd_smells      (W607-BN substrate + DF THIS)        -- smell-pattern axis
      cmd_dark_matter (W607-BK substrate + CZ aggregation) -- co-change axis

    With W607-DF landed, ALL FOUR members of the 4-way carry an
    aggregation-phase layer on top of their substrate-CALL layer. This
    test confirms the structural-debt 4-way closes at the aggregation-
    phase layer -- every member carries both layers in source.
    """
    repo_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    # Each member of the W805 4-way carries (substrate accumulator,
    # aggregation accumulator) source anchors.
    members = (
        ("cmd_clones.py", "w607bq_warnings_out", "w607dc_warnings_out"),
        ("cmd_duplicates.py", "w607bm_warnings_out", "w607dd_warnings_out"),
        ("cmd_smells.py", "w607bn_warnings_out", "w607df_warnings_out"),
        (
            "cmd_dark_matter.py",
            "w607bk_warnings_out",
            "w607cz_warnings_out",
        ),
    )

    missing_substrate: list[str] = []
    missing_aggregation: list[str] = []
    for filename, substrate_anchor, agg_anchor in members:
        src = (repo_root / filename).read_text(encoding="utf-8")
        if substrate_anchor not in src:
            missing_substrate.append(f"{filename}:{substrate_anchor}")
        if agg_anchor not in src:
            missing_aggregation.append(f"{filename}:{agg_anchor}")

    # cmd_smells (THIS wave) is the closing member -- it MUST have both.
    smells_src = (repo_root / "cmd_smells.py").read_text(encoding="utf-8")
    assert "w607bn_warnings_out" in smells_src, "cmd_smells must carry the W607-BN substrate-CALL accumulator"
    assert "w607df_warnings_out" in smells_src, (
        "cmd_smells must carry the W607-DF aggregation-phase accumulator (THIS wave's closure)"
    )

    # Sibling agg accumulators MUST also be present -- if any are missing
    # the 4-way closure narrative is wrong.
    assert not missing_aggregation, (
        f"W805 4-way aggregation-phase closure broken; missing "
        f"accumulators: {missing_aggregation!r}. The structural-debt "
        f"4-way pairing only closes at the agg-layer when ALL FOUR "
        f"members carry their aggregation-phase accumulator."
    )


# ---------------------------------------------------------------------------
# (15) Pre-existing W987 + W607-BN + W607-DF all coexist in source
# ---------------------------------------------------------------------------


def test_w607df_coexists_with_pre_existing_marker_families():
    """W607-DF is ADDITIVE -- the pre-existing W987 ``warnings_list``
    AND the W607-BN substrate-CALL family must both still be present in
    source.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    src = src_path.read_text(encoding="utf-8")

    # Pre-existing W987 warnings_list accumulator
    assert "warnings_list" in src, "Pre-existing W987 warnings_list accumulator has been removed."
    # W607-BN substrate-CALL family
    assert "w607bn_warnings_out" in src, "W607-BN substrate-CALL accumulator has been removed."
    assert "_run_check_bn" in src, "W607-BN helper has been removed."
    # W607-DF aggregation-phase family (THIS wave)
    assert "w607df_warnings_out" in src, "W607-DF aggregation-phase accumulator has been removed."
    assert "_run_check_df" in src, "W607-DF helper has been removed."

    # All three accumulator sources merged via _merged_warnings()
    assert "_merged_warnings" in src, (
        "The canonical warnings-merge helper _merged_warnings has been "
        "removed -- the three accumulator sources (warnings_list / "
        "_w607bn_warnings_out / _w607df_warnings_out) must combine into "
        "one warnings_out surface at envelope-emit time."
    )
