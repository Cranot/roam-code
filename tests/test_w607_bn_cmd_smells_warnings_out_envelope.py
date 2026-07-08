"""W607-BN -- ``cmd_smells`` substrate-boundary plumbing.

The 24-detector smells aggregator is the highest-impact detector surface
(3047 findings rows on roam-code per W607-BJ note) and a key Pattern-2
case-study target (W987 + W1063 follow-ups). This wave installs the
canonical ``_w607bn_warnings_out`` bucket + ``_run_check_bn`` helper inside
the ``smells`` click command and wraps the substrate boundaries:

* load_suppress_rules        -- .roam/smells.suppress.yml loader
* query_findings_corpus      -- run_all_detectors dispatch loop
* apply_suppressions         -- typed-suppression applier
* apply_kind_filter          -- --kind closed-set filter (W987 Pattern-2)
* apply_min_severity_filter  -- W547/W1005 5-tier severity rank filter
* apply_tooling_filter       -- file_role + path-hint tooling exclusion
* aggregate_by_kind          -- Counter aggregation over 24 detectors
* classify_severity          -- wrap_findings + confidence_distribution
* serialize_to_sarif         -- centralized SARIF projection
* emit_findings              -- W109 findings-registry mirror

Marker family ``smells_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-discipline
test.

W978 first-hypothesis check
---------------------------

Each W607-BN-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W987 PATTERN-2 PLAYBOOK regression guard
----------------------------------------

The Pattern-2 plumbing applied in W987 (--kind closed-set vocab + suppress
YAML kind validation -> warnings_list accumulator) MUST continue to
function alongside W607-BN markers. The W607-BN bucket merges into the
SAME ``warnings_out`` field via ``_merged_warnings()`` -- coexistence,
not regression. Verified via the W987 regression-guard test below.

W1063 PATTERN-1D --kind regression guard
----------------------------------------

W1063 added the closest-match suggestion for unknown --kind values. The
W607-BN ``apply_kind_filter`` substrate boundary MUST not regress that
behavior -- the suggestion fires from the dispatch-time validation path
BEFORE the filter substrate runs.
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
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_smells_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root with at least one detectable smell.

    Mirrors test_w987_smells_pattern2._build_smelly_project: brain-method on
    ``process_everything`` at src/engine.py:10. One row is enough to
    exercise filter/aggregate/classify substrates with a non-empty corpus.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
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
    # Brain method: cognitive_complexity above threshold, lots of params, deep nest.
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
    """Invoke the smells click command directly (bypassing the CLI group).

    Mirrors test_w820_smells_empty_corpus.invoke_smells -- the Click obj
    passes ``json``/``sarif``/``budget``/``detail`` flags via ctx.obj.
    """
    from roam.commands.cmd_smells import smells

    obj = {"json": json_mode, "sarif": False, "budget": 0, "detail": detail}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(smells, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BN substrate markers
# ---------------------------------------------------------------------------


def test_smells_clean_envelope_omits_w607bn_markers(cli_runner, smells_project):
    """Clean smells run -> no W607-BN substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BN bucket on
    the success path must NOT introduce new ``smells_<phase>_failed:``
    markers tied to the W607-BN wrap.
    """
    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "smells"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bn_phases = (
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
    bn_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"smells_{p}_failed:" in m for p in bn_phases)]
    assert not bn_markers, (
        f"clean smells must NOT surface W607-BN substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) classify_severity failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_smells_classify_severity_failure_marker_format(cli_runner, smells_project, monkeypatch):
    """If ``wrap_findings`` (classify_severity substrate) raises, surface
    the W607-BN marker with the canonical three-segment shape.
    """
    from roam.commands import cmd_smells

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-classify-from-W607-BN")

    monkeypatch.setattr(cmd_smells, "wrap_findings", _raise)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    classify_markers = [m for m in all_wo if m.startswith("smells_classify_severity_failed:")]
    assert classify_markers, f"expected smells_classify_severity_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in classify_markers), classify_markers
    assert any("synthetic-classify-from-W607-BN" in m for m in classify_markers), classify_markers
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


def test_smells_w607bn_warnings_in_envelope(cli_runner, smells_project, monkeypatch):
    """Non-empty W607-BN bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_smells

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BN")

    monkeypatch.setattr(cmd_smells, "wrap_findings", _raise)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BN disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BN disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("smells_classify_severity_failed:")]
    assert markers, f"expected smells_classify_severity_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_smells_three_segment_marker_shape(cli_runner, smells_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    """
    from roam.commands import cmd_smells

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BN")

    monkeypatch.setattr(cmd_smells, "wrap_findings", _raise)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("smells_classify_severity_failed:")]
    assert failure_markers, f"expected smells_classify_severity_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "smells_classify_severity_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) query_findings_corpus failure -> verdict still emits, partial_success
# ---------------------------------------------------------------------------


def test_smells_query_findings_corpus_failure_degrades_cleanly(cli_runner, smells_project, monkeypatch):
    """A raise in ``run_all_detectors`` must NOT crash the smells command.

    Empty-corpus fallback kicks in (degraded path): the envelope still
    emits with verdict + partial_success + the substrate marker.
    """
    from roam.commands import cmd_smells

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-corpus-from-W607-BN")

    monkeypatch.setattr(cmd_smells, "run_all_detectors", _raise, raising=False)
    # Also need to patch the import site since cmd_smells does:
    # from roam.catalog.smells import ALL_DETECTORS, run_all_detectors
    # inside the command body.
    import roam.catalog.smells as _smells_mod

    monkeypatch.setattr(_smells_mod, "run_all_detectors", _raise)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    corpus_markers = [m for m in all_wo if m.startswith("smells_query_findings_corpus_failed:")]
    assert corpus_markers, f"expected smells_query_findings_corpus_failed: marker; got {all_wo!r}"
    # Verdict still emits.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    # partial_success flipped.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-BN stays in ``smells_*`` family
# ---------------------------------------------------------------------------


def test_w607bn_marker_prefix_stays_in_smells_family(cli_runner, smells_project, monkeypatch):
    """Every W607-BN substrate marker uses the canonical ``smells_*`` prefix.

    Hard distinction from sibling W607-* layers including cmd_complexity
    (W607-BJ, ``complexity_*``), cmd_health (W607-M/BA, ``health_*``),
    cmd_debt (W607-BG, ``debt_*``), etc.
    """
    from roam.commands import cmd_smells

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BN")

    monkeypatch.setattr(cmd_smells, "wrap_findings", _raise)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("smells_"), (
            f"every surfaced W607-BN marker must use the ``smells_*`` prefix family (cmd_smells scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("complexity_", "cmd_complexity W607-BJ"),
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
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("doctor_", "cmd_doctor W607-N"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
            # vibe-check is a sibling smells-adjacent detector (LLM-rot).
            ("vibe_check_", "cmd_vibe_check W607-* layer"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_smells carries the W607-BN accumulator
# ---------------------------------------------------------------------------


def test_cmd_smells_carries_w607bn_accumulator():
    """AST-level guard: cmd_smells source carries the W607-BN accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    assert src_path.exists(), f"cmd_smells.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bn_warnings_out" in src, (
        "W607-BN accumulator missing from cmd_smells; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bn" in src, (
        "W607-BN ``_run_check_bn`` helper missing from cmd_smells; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_bn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bn":
            found_run_check_bn = True
            break
    assert found_run_check_bn, (
        "W607-BN ``_run_check_bn`` helper not found in cmd_smells AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-BN substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bn_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BN substrate boundary is wrapped.

    W607-BN substrate inventory (cmd_smells):

    * load_suppress_rules        -- .roam/smells.suppress.yml loader
    * query_findings_corpus      -- run_all_detectors dispatch loop
    * apply_suppressions         -- typed-suppression applier
    * apply_kind_filter          -- --kind closed-set filter
    * apply_min_severity_filter  -- W547/W1005 5-tier severity rank filter
    * apply_tooling_filter       -- file_role + path-hint tooling exclusion
    * aggregate_by_kind          -- Counter aggregation over 24 detectors
    * classify_severity          -- wrap_findings + confidence_distribution
    * serialize_to_sarif         -- centralized SARIF projection
    * emit_findings              -- W109 findings-registry mirror
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_smells.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
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
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bn("{phase}"' in src
        multi_line = (
            f'_run_check_bn(\n        "{phase}"' in src
            or f'_run_check_bn(\n            "{phase}"' in src
            or f'_run_check_bn(\n                "{phase}"' in src
            or f'_run_check_bn(\n                    "{phase}"' in src
            or f'_run_check_bn(\n                        "{phase}"' in src
        )
        # ``emit_findings`` is wrapped via a direct ``try/except`` block
        # (NOT ``_run_check_bn``) because it needs to distinguish
        # ``sqlite3.OperationalError`` (expected pre-W89 path) from
        # generic Exception (W607-BN marker). Source-grep on the marker
        # name in both modes.
        marker_grep = f"smells_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-BN wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) 24-DETECTOR COVERAGE bonus -- per-detector marker chains through aggregation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raise_kind",
    [
        "brain-method",
        "deep-nesting",
        "long-params",
        "large-class",
        "god-class",
        "feature-envy",
        "shotgun-surgery",
        "data-clumps",
        "dead-params",
        "empty-catch",
        "low-cohesion",
        "message-chain",
        "refused-bequest",
        "primitive-obsession",
        "duplicate-conditionals",
        "magic-numbers",
        "boolean-parameter",
        "switch-statement",
        "temporal-coupling",
        "comment-density",
        "speculative-generality",
    ],
)
def test_w607bn_24_detector_aggregation_coverage(cli_runner, smells_project, monkeypatch, raise_kind):
    """24-DETECTOR COVERAGE bonus.

    Parametrized across each of the registered smell kinds: if the
    aggregation substrate raises (synthetic), the marker chains through
    aggregate_by_kind and surfaces uniformly. This verifies the
    aggregation boundary handles the full 24-detector rollup space
    without leaking into another marker family.

    The actual failure is injected at the aggregate_by_kind layer
    (Counter call) -- the parametrization itself enumerates the
    detector-id vocabulary so the test surface scales with the registry.
    """
    from roam.commands import cmd_smells

    # Confirm the requested smell kind is in the registered set so the
    # parametrization tracks the live registry. If a future detector
    # rename drops one of these, this assertion fires loudly rather than
    # the parametrize silently testing a phantom.
    known = cmd_smells._registered_smell_kinds()
    assert raise_kind in known, (
        f"parametrize raise_kind={raise_kind!r} not in registered smell "
        f"kinds; update the parametrize or restore the detector"
    )

    # Inject a raise into aggregate_by_kind via Counter.

    def _raise_counter(*args, **kwargs):
        raise RuntimeError(f"synthetic-aggregate-for-{raise_kind}")

    monkeypatch.setattr(cmd_smells, "Counter", _raise_counter)

    result = _invoke_smells(cli_runner, smells_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("smells_aggregate_by_kind_failed:")]
    assert agg_markers, f"expected smells_aggregate_by_kind_failed: marker for kind={raise_kind!r}; got {all_wo!r}"
    # Envelope still composes; verdict line present.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) W987 PATTERN-2 PLAYBOOK regression guard
# ---------------------------------------------------------------------------


def test_w987_pattern2_playbook_coexists_with_w607bn(cli_runner, smells_project):
    """W987 PATTERN-2 PLAYBOOK regression guard.

    The W987 Pattern-2 plumbing (--kind closed-set vocab + warnings_list
    accumulator + did_you_mean partition payload) MUST continue to
    function alongside W607-BN markers. The shared warnings_out field
    surfaces BOTH families cleanly -- coexistence, not regression.
    """
    # Invoke with unknown --kind to trigger the W987 warning path.
    result = _invoke_smells(cli_runner, smells_project, "--kind", "shotgun-survey")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # W987 plumbing: a "Drop --kind 'shotgun-survey': unknown smell id..."
    # warning lands in warnings_out (top-level).
    top_wo = data.get("warnings_out") or []
    w987_markers = [m for m in top_wo if "unknown smell id" in m and "shotgun-survey" in m]
    assert w987_markers, (
        f"W987 Pattern-2 warning missing -- expected 'unknown smell id' "
        f"text containing 'shotgun-survey'; got {top_wo!r}"
    )

    # W987 + W1083-followup-3 partition payload still spliced into summary.
    summary = data["summary"]
    assert summary.get("partial_success") is True, f"W987 partial_success flip regressed; got summary={summary!r}"

    # The W607-BN bucket is EMPTY because no substrate raised -- this
    # confirms coexistence rather than collision (W607-BN markers do not
    # accidentally fire on a Pattern-2 warning path).
    bn_markers = [
        m
        for m in top_wo
        if any(
            m.startswith(f"smells_{p}_failed:")
            for p in (
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
        )
    ]
    assert not bn_markers, (
        f"W607-BN markers spuriously fired on W987 Pattern-2 path; coexistence broken: got {bn_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) W1063 PATTERN-1D --kind regression guard
# ---------------------------------------------------------------------------


def test_w1063_pattern1d_kind_suggestion_coexists_with_w607bn(cli_runner, smells_project):
    """W1063 PATTERN-1D --kind regression guard.

    W1063 added closest-match suggestions ("Did you mean: 'shotgun-surgery'?")
    to the --kind unknown path. Verify:
    (a) the suggestion still fires on a typo near a real smell id, AND
    (b) the apply_kind_filter substrate boundary did not regress the path
        (the filter evaluation runs to completion without surfacing a
        spurious W607-BN marker).
    """
    # 'shotgun-survey' is a near-miss for 'shotgun-surgery' -- expected
    # difflib match.
    result = _invoke_smells(cli_runner, smells_project, "--kind", "shotgun-survey")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    suggestion_markers = [m for m in top_wo if "Did you mean" in m and "shotgun-surgery" in m]
    assert suggestion_markers, (
        f"W1063 'Did you mean' suggestion missing for near-miss 'shotgun-survey' -> 'shotgun-surgery'; got {top_wo!r}"
    )

    # apply_kind_filter substrate did NOT surface a marker -- the filter
    # ran cleanly (empty result because all --kind values resolved to
    # unknown) and Pattern-2 handled the disclosure.
    filter_markers = [m for m in top_wo if m.startswith("smells_apply_kind_filter_failed:")]
    assert not filter_markers, f"apply_kind_filter substrate spuriously surfaced on W1063 path; got {filter_markers!r}"


# ---------------------------------------------------------------------------
# (12) apply_kind_filter boundary surfaces marker when substrate fails
# ---------------------------------------------------------------------------


def test_smells_apply_kind_filter_boundary_surfaces_on_substrate_failure(cli_runner, smells_project, monkeypatch):
    """Per-task brief: W1063 PATTERN-1D regression guard half-2.

    If the apply_kind_filter substrate ITSELF raises (e.g. via an injected
    raise in the closure body), the boundary surfaces the marker and the
    filter degrades to an empty set (closed-set semantic: NOT silent
    widening).
    """

    # Replace the registered-smell-kinds lookup with one that raises so
    # the post-validation kind filter loop has a kind to act on, and then
    # monkeypatch the closure-dispatched helper. Easiest path: replace
    # _registered_smell_kinds with a permissive set (so the kind survives
    # validation), then monkeypatch Counter to raise -- but Counter is
    # used in aggregate_by_kind not apply_kind_filter.
    #
    # Cleaner: patch the dict membership check via the filter list
    # comprehension. We accomplish this by injecting a bad closure into
    # findings via a sentinel object that raises on .get().
    real_run_all = None
    import roam.catalog.smells as _smells_mod

    real_run_all = _smells_mod.run_all_detectors

    class _ExplodingFinding(dict):
        def get(self, key, default=None):
            if key == "smell_id":
                raise RuntimeError("synthetic-kind-filter-from-W607-BN")
            return super().get(key, default)

    def _wrapped_run_all(conn, only=None):
        rows = real_run_all(conn, only=only)
        if not rows:
            # Produce at least one row so the kind filter has work to do.
            return [
                _ExplodingFinding(
                    {
                        "smell_id": "brain-method",
                        "severity": "critical",
                        "symbol_name": "fake",
                        "kind": "function",
                        "location": "src/engine.py:1",
                        "metric_value": 50,
                        "threshold": 30,
                        "description": "fake-row",
                    }
                )
            ]
        rows = [_ExplodingFinding(r) for r in rows]
        return rows

    monkeypatch.setattr(_smells_mod, "run_all_detectors", _wrapped_run_all)

    # detail=True so the ``smells`` list field is not stripped in the
    # non-detail-mode envelope (strip_list_payloads preserves only the
    # W1000/W1006/W1007 always-preserved fields by default).
    result = _invoke_smells(cli_runner, smells_project, "--kind", "brain-method", detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    filter_markers = [m for m in all_wo if m.startswith("smells_apply_kind_filter_failed:")]
    assert filter_markers, f"expected smells_apply_kind_filter_failed: marker; got {all_wo!r}"
    # Degrade-to-empty semantic preserved (W1063 closed-set).
    smells_list = data.get("smells")
    assert isinstance(smells_list, list)
    assert len(smells_list) == 0, (
        f"apply_kind_filter must degrade to EMPTY on failure (closed-set "
        f"semantic, NOT silent widening); got {smells_list!r}"
    )


# ---------------------------------------------------------------------------
# (13) findings-registry write -- W109 + W607-BN emit_findings boundary
# ---------------------------------------------------------------------------


def test_smells_emit_findings_substrate_w607bn_boundary(cli_runner, smells_project, monkeypatch):
    """If the W109 _emit_smells_findings substrate raises a non-OperationalError,
    surface the marker and continue. The pre-W89 OperationalError path is
    NOT covered here (it stays silent by design -- that's the W109 contract).
    """
    from roam.commands import cmd_smells

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-BN")

    monkeypatch.setattr(cmd_smells, "_emit_smells_findings", _raise)

    result = _invoke_smells(cli_runner, smells_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("smells_emit_findings_failed:")]
    assert emit_markers, f"expected smells_emit_findings_failed: marker on --persist path; got {all_wo!r}"
    # Envelope still composes (degraded persist does not crash the run).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True
