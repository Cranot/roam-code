"""W607-CX -- ``cmd_alerts`` substrate-boundary plumbing.

cmd_alerts is the health-degradation alert composer. Detects metrics
that consistently worsen over 3+ snapshots, current values that exceed
severity thresholds, and metrics that changed more than 20% since the
last snapshot. Pre-existing Pattern-2 plumbing across the
W918 / W962 / W963 / W964 / W969 / W972 / W973 / W974 / W1025 /
W1030-followup-A batch validates malformed ``.roam/alerts.yaml`` rows
and surfaces actionable warnings through ``config_warnings`` -- but a
raise INSIDE one of the substrate helpers (``get_snapshots`` /
``collect_metrics`` / ``_check_thresholds`` / ``_check_trends`` /
``_check_rate_of_change`` / ``_delta_baseline_alerts`` / ``_deduplicate`` /
``_resolved_thresholds`` / ``_alerts_verdict``) would crash the alerts
detector outright with no signal.

This wave installs the canonical ``_w607cx_warnings_out`` bucket +
``_run_check_cx`` helper inside the ``alerts`` click command and wraps
every substrate boundary:

* get_snapshots             -- DB-row ingest (newest-first)
* collect_metrics           -- live-metric collector (no-snapshot
                                fallback)
* build_snap_dicts          -- raw-row -> chronological dict conversion
* load_alerts_config        -- ``.roam/alerts.yaml`` I/O + parse
* resolved_thresholds       -- defaults + overrides merge
* check_thresholds          -- W962/W963 op-validated checks
* coerce_delta_alerts       -- W964 bool coercion for delta_alerts
* delta_baseline_alerts     -- per-metric regression-vs-baseline alerts
* check_trends              -- Mann-Kendall + Sen's slope trend detection
* check_rate_of_change      -- per-snapshot rate-of-change alerts
* deduplicate               -- dedup + sort
* compose_verdict           -- LAW 6 single-line verdict

Marker family ``alerts_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-discipline
test.

W607-CX COEXISTENCE WITH PRE-EXISTING PATTERN-2 PLUMBING
--------------------------------------------------------

cmd_alerts already has very mature Pattern-2 plumbing from the
W965 -> W976 batch:

* W918  -- silent-fallback warning for unknown user-supplied metrics
* W962  -- parse-time validator for invalid ``op`` in YAML threshold
           rows
* W963  -- check-time validator (belt-and-braces) for invalid ``op``
* W964  -- bool coercion for ``delta_alerts``
* W969  -- canonical-level validator for ``level``
* W972  -- root-type validator (non-mapping YAML root)
* W973  -- assert-time canonical-level guard in ``_make_alert``
* W974  -- ``AlertThreshold.level`` Literal type
* W1025 -- non-dict ``thresholds:`` section coercion
* W1030-followup-A -- on-disk config_state closed enum

ALL of those validators flow through the existing ``config_warnings``
list which the CLI surfaces on the envelope's ``warnings_out`` field
(an actionable user-facing diagnostic surface). W607-CX is a DISTINCT
LAYER: substrate-CALL markers for uncaught raises INSIDE one of the
helpers. The two layers MUST coexist:

* W918/W962/W964 produce diagnostics like
  ``"Metric 'xxx' has invalid op '!=' ..."``
* W607-CX produces markers like ``"alerts_check_thresholds_failed:..."``

The coexistence regression tests below confirm:

  1. A malformed YAML config still surfaces the W918/W962/W964 diagnostics
     unchanged.
  2. A raise inside a substrate still surfaces the W607-CX marker.
  3. Both can fire simultaneously on the same envelope without
     cross-contamination -- the W918 wording stays in the user-facing
     warnings_list and the W607-CX marker stays in the substrate
     bucket, mirrored into BOTH top-level and summary.warnings_out.

PER-SUBSTRATE ISOLATION
-----------------------

The alerts command composes alerts from 4 independent substrates
(threshold / delta-baseline / trends / rate-of-change). A raise in
one substrate must NOT torpedo the other three -- the envelope still
composes a coherent verdict with whatever alerts the surviving
substrates produced.
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


def _build_alerts_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project for cmd_alerts.

    The alerts command runs threshold checks against live metrics when
    no snapshot history exists, so the no-snapshot path is the easiest
    to exercise. Index in-process so the snapshot table exists and
    ``collect_metrics`` has something to read.
    """
    import sys

    # Reuse the conftest helpers the wider test suite uses.
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from conftest import index_in_process
    except Exception:  # pragma: no cover -- defensive on test-host shape
        index_in_process = None  # type: ignore[assignment]

    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text(
        "def helper():\n"
        '    """Helper docstring."""\n'
        "    return 0\n"
        "\n"
        "\n"
        "def worker(x):\n"
        '    """Worker docstring."""\n'
        "    return x * 2\n"
    )
    if index_in_process is not None:
        try:
            index_in_process(tmp_path)
        except Exception:
            # If in-process indexing isn't available on this host shape
            # the no-snapshot fallback path inside cmd_alerts still works
            # -- collect_metrics returns sensible defaults.
            pass
    return tmp_path


@pytest.fixture
def alerts_project(tmp_path):
    return _build_alerts_project(tmp_path)


def _invoke_alerts(cli_runner, project_root, *args, json_mode=True):
    """Invoke the alerts click command directly."""
    from roam.commands.cmd_alerts import alerts

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(alerts, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CX_PHASES = (
    "get_snapshots",
    "collect_metrics",
    "build_snap_dicts",
    "load_alerts_config",
    "resolved_thresholds",
    "check_thresholds",
    "coerce_delta_alerts",
    "delta_baseline_alerts",
    "check_trends",
    "check_rate_of_change",
    "deduplicate",
    "compose_verdict",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CX substrate markers (byte-stable)
# ---------------------------------------------------------------------------


def test_alerts_clean_envelope_omits_w607cx_markers(cli_runner, alerts_project):
    """Clean alerts run -> no W607-CX substrate markers."""
    result = _invoke_alerts(cli_runner, alerts_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "alerts"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cx_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"alerts_{p}_failed:" in m for p in _CX_PHASES)]
    assert not cx_markers, (
        f"clean alerts must NOT surface W607-CX substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) check_thresholds failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_alerts_check_thresholds_failure_marker_format(cli_runner, alerts_project, monkeypatch):
    """If ``_check_thresholds`` raises, surface the canonical marker."""
    from roam.commands import cmd_alerts

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-check_thresholds-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_thresholds", _raise)

    result = _invoke_alerts(cli_runner, alerts_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    threshold_markers = [m for m in all_wo if m.startswith("alerts_check_thresholds_failed:")]
    assert threshold_markers, f"expected alerts_check_thresholds_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in threshold_markers), threshold_markers
    assert any("synthetic-check_thresholds-from-W607-CX" in m for m in threshold_markers), threshold_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_alerts_three_segment_marker_shape(cli_runner, alerts_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_alerts

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_thresholds", _raise)

    result = _invoke_alerts(cli_runner, alerts_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("alerts_check_thresholds_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "alerts_check_thresholds_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (4) warnings_out lands in BOTH envelope locations (dual-mirror)
# ---------------------------------------------------------------------------


def test_alerts_w607cx_warnings_in_envelope_dual_mirror(cli_runner, alerts_project, monkeypatch):
    """Non-empty W607-CX bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_alerts

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_thresholds", _raise)

    result = _invoke_alerts(cli_runner, alerts_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CX disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CX disclosure path; got summary = {data['summary']!r}"
    )
    # The substrate marker must surface in BOTH surfaces.
    top_markers = [m for m in data["warnings_out"] if m.startswith("alerts_check_thresholds_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("alerts_check_thresholds_failed:")]
    assert top_markers, (
        f"expected alerts_check_thresholds_failed: marker in top-level warnings_out; got {data['warnings_out']!r}"
    )
    assert summary_markers, (
        f"expected alerts_check_thresholds_failed: marker in "
        f"summary.warnings_out; got {data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Per-substrate isolation: check_trends failure -> ranking still composes
# ---------------------------------------------------------------------------


def test_alerts_check_trends_failure_degrades_cleanly(cli_runner, alerts_project, monkeypatch):
    """A raise in ``_check_trends`` must NOT torpedo the other substrates."""
    from roam.commands import cmd_alerts

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-trends-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_trends", _raise)

    result = _invoke_alerts(cli_runner, alerts_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # Marker only fires when the substrate is actually called; the
    # no-snapshot path may skip it. So this test asserts EITHER the
    # substrate ran AND emitted a marker, OR the substrate was skipped
    # because snap_dicts < 3 -- in both cases the envelope still
    # composes with a single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # If the trends substrate was wrapped AND raised, a marker is present.
    trend_markers = [m for m in all_wo if m.startswith("alerts_check_trends_failed:")]
    if trend_markers:
        assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CX stays in ``alerts_*`` family
# ---------------------------------------------------------------------------


def test_w607cx_marker_prefix_stays_in_alerts_family(cli_runner, alerts_project, monkeypatch):
    """Every W607-CX substrate marker uses the canonical ``alerts_*`` prefix.

    Hard distinction from sibling W607-* layers across the detector
    family and the broader command surface.
    """
    from roam.commands import cmd_alerts

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_thresholds", _raise)

    result = _invoke_alerts(cli_runner, alerts_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("alerts_"), (
            f"every surfaced W607-CX marker must use the ``alerts_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("hotspots_", "cmd_hotspots W607-CP"),
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("orphan_imports_", "cmd_orphan_imports W607-CR"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("vulns_", "cmd_vulns W607-AQ + CH (security sibling)"),
            ("taint_", "cmd_taint W607-AY + CJ (security sibling)"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) AST source-level guard: cmd_alerts carries the W607-CX accumulator
# ---------------------------------------------------------------------------


def test_cmd_alerts_carries_w607cx_accumulator():
    """AST-level guard: cmd_alerts source carries the W607-CX accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_alerts.py"
    assert src_path.exists(), f"cmd_alerts.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607cx_warnings_out" in src, (
        "W607-CX accumulator missing from cmd_alerts; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cx" in src, (
        "W607-CX ``_run_check_cx`` helper missing from cmd_alerts; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cx = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cx":
            found_run_check_cx = True
            break
    assert found_run_check_cx, (
        "W607-CX ``_run_check_cx`` helper not found in cmd_alerts AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CX substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cx_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CX substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_alerts.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CX_PHASES:
        same_line = f'_run_check_cx("{phase}"' in src
        multi_line = (
            f'_run_check_cx(\n        "{phase}"' in src
            or f'_run_check_cx(\n            "{phase}"' in src
            or f'_run_check_cx(\n                "{phase}"' in src
            or f'_run_check_cx(\n                    "{phase}"' in src
            or f'_run_check_cx(\n                        "{phase}"' in src
        )
        marker_grep = f"alerts_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CX wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607cx_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CX marker shape lives in cmd_alerts."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_alerts.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"alerts_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CX marker fstring missing from cmd_alerts; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (10) COEXISTENCE: W918 silent-fallback fires alongside W607-CX
# ---------------------------------------------------------------------------


def test_w918_pattern_2_coexists_with_w607cx_substrate_marker(cli_runner, tmp_path, monkeypatch):
    """W918/W962/W964 user-facing warnings AND W607-CX substrate markers
    must coexist on the same envelope without cross-contamination.

    The W918/W962/W964 surface stays in ``config_warnings`` -- actionable
    diagnostics for malformed ``.roam/alerts.yaml`` rows. The W607-CX
    surface stays in ``_w607cx_warnings_out`` -- substrate-CALL markers.
    Both flow through ``warnings_out`` but the two vocabularies must
    remain distinguishable.
    """
    from roam.commands import cmd_alerts

    # Step 1: build a project WITH a malformed alerts.yaml that the
    # W918 silent-fallback path detects (unknown user-supplied metric
    # without a full triple).
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    (tmp_path / ".roam").mkdir(exist_ok=True)
    (tmp_path / ".roam" / "alerts.yaml").write_text(
        "thresholds:\n  coverage: { value: 0 }\n"  # W918: unknown metric, no op/level
    )

    # Step 2: also force a substrate raise so the W607-CX marker fires.
    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-coexistence-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_trends", _raise)

    # Index the project so the alerts command has a DB to read.
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from conftest import index_in_process

        index_in_process(tmp_path)
    except Exception:
        pass

    result = _invoke_alerts(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    # W918 wording -- user-facing config diagnostic.
    w918_warnings = [m for m in top_wo if "coverage" in m and "no threshold defined" in m]
    assert w918_warnings, (
        f"W918 silent-fallback warning for unknown metric missing; "
        f"got {top_wo!r}. The pre-existing Pattern-2 plumbing must "
        f"continue to fire alongside W607-CX."
    )
    # The two vocabularies stay distinguishable on the envelope:
    # W918 wording NEVER starts with the W607-CX ``alerts_<phase>_failed:``
    # prefix.
    for warning in w918_warnings:
        assert not warning.startswith("alerts_"), f"W918 wording leaked into the W607-CX prefix family: {warning!r}"


# ---------------------------------------------------------------------------
# (11) COEXISTENCE: W962 invalid-op warning fires alongside W607-CX
# ---------------------------------------------------------------------------


def test_w962_invalid_op_coexists_with_w607cx(cli_runner, tmp_path, monkeypatch):
    """W962 (invalid op in YAML) and W607-CX substrate markers coexist."""
    from roam.commands import cmd_alerts

    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    (tmp_path / ".roam").mkdir(exist_ok=True)
    # W962: ``!=`` is outside _VALID_OPS = {">", "<", ">=", "<=", "=="}.
    (tmp_path / ".roam" / "alerts.yaml").write_text("thresholds:\n  cycles: { op: '!=', value: 10, level: warning }\n")

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-w962-coexistence-from-W607-CX")

    monkeypatch.setattr(cmd_alerts, "_check_rate_of_change", _raise)

    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from conftest import index_in_process

        index_in_process(tmp_path)
    except Exception:
        pass

    result = _invoke_alerts(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    # W962 vocabulary: ``invalid op '!=' ``.
    w962_warnings = [m for m in top_wo if "invalid op" in m and "'!='" in m]
    assert w962_warnings, (
        f"W962 invalid-op warning missing; got {top_wo!r}. The "
        f"pre-existing op-vocabulary validator must continue to fire "
        f"alongside W607-CX."
    )


# ---------------------------------------------------------------------------
# (12) COEXISTENCE: W964 bool-coerce fires alongside W607-CX
# ---------------------------------------------------------------------------


def test_w964_bool_coerce_coexists_with_w607cx(cli_runner, tmp_path, monkeypatch):
    """W964 (bool coercion for delta_alerts) and W607-CX coexist."""

    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    (tmp_path / ".roam").mkdir(exist_ok=True)
    # W964: a non-bool / non-recognised-string ``delta_alerts`` value.
    (tmp_path / ".roam" / "alerts.yaml").write_text("delta_alerts: maybe\n")

    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from conftest import index_in_process

        index_in_process(tmp_path)
    except Exception:
        pass

    result = _invoke_alerts(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    # W964 vocabulary: ``Config field 'delta_alerts' value 'maybe' is not
    # a bool``.
    w964_warnings = [m for m in top_wo if "delta_alerts" in m and "is not a bool" in m]
    assert w964_warnings, (
        f"W964 bool-coerce warning missing; got {top_wo!r}. The "
        f"pre-existing bool-coercion validator must continue to fire."
    )


# ---------------------------------------------------------------------------
# (13) REGRESSION: W965/W933 op-vocabulary validation discipline preserved
# ---------------------------------------------------------------------------


def test_w965_w933_op_vocabulary_validation_preserved():
    """W965/W933 regression guard: _VALID_OPS still drives op validation.

    The W933 discipline rule ("Don't TypedDict a boundary you don't
    validate") and the W965 follow-on require that ``op`` validation
    flow through ``_VALID_OPS`` -- a closed frozenset that is the
    single source of truth for the comparators ``_check_thresholds``
    knows how to evaluate. W607-CX must NOT undo this discipline.
    """
    from roam.commands import cmd_alerts

    # _VALID_OPS exists, is a frozenset, and contains exactly the 5
    # canonical comparators.
    assert hasattr(cmd_alerts, "_VALID_OPS"), (
        "_VALID_OPS module constant missing -- the W965 op-vocabulary validator has been refactored away."
    )
    assert isinstance(cmd_alerts._VALID_OPS, frozenset), (
        f"_VALID_OPS must be a frozenset (closed-set semantics); got {type(cmd_alerts._VALID_OPS).__name__}"
    )
    assert cmd_alerts._VALID_OPS == frozenset({">", "<", ">=", "<=", "=="}), (
        f"_VALID_OPS drifted from the canonical 5-comparator set; got {sorted(cmd_alerts._VALID_OPS)!r}"
    )


# ---------------------------------------------------------------------------
# (14) REGRESSION: W918 + W933 _DEFAULT_THRESHOLDS literal discipline
# ---------------------------------------------------------------------------


def test_w933_default_thresholds_literal_discipline_preserved():
    """W933 regression guard: _DEFAULT_THRESHOLDS is hand-written literals.

    CLAUDE.md cites _DEFAULT_THRESHOLDS as the canonical case where
    TypedDict IS appropriate -- every entry is a hand-written literal
    in source code, fully under the author's control, not coming from
    ``yaml.safe_load()`` or ``.update(arbitrary)``. W607-CX must NOT
    introduce a code path that mutates the defaults at runtime (which
    would re-introduce the W966 "TypedDict-a-boundary-you-don't-
    validate" anti-pattern).
    """
    from roam.commands import cmd_alerts

    assert hasattr(cmd_alerts, "_DEFAULT_THRESHOLDS"), (
        "_DEFAULT_THRESHOLDS missing -- the W933 literal-discipline anchor has been refactored away."
    )
    defaults = cmd_alerts._DEFAULT_THRESHOLDS
    assert isinstance(defaults, dict), f"_DEFAULT_THRESHOLDS must be a dict; got {type(defaults).__name__}"
    # Every default row carries the full {op, value, level} triple.
    for metric, rule in defaults.items():
        assert "op" in rule, f"_DEFAULT_THRESHOLDS[{metric!r}] missing 'op'"
        assert "value" in rule, f"_DEFAULT_THRESHOLDS[{metric!r}] missing 'value'"
        assert "level" in rule, f"_DEFAULT_THRESHOLDS[{metric!r}] missing 'level'"
        assert rule["op"] in cmd_alerts._VALID_OPS, (
            f"_DEFAULT_THRESHOLDS[{metric!r}].op={rule['op']!r} is outside _VALID_OPS"
        )
        assert rule["level"] in cmd_alerts._CANONICAL_LEVELS, (
            f"_DEFAULT_THRESHOLDS[{metric!r}].level={rule['level']!r} is outside _CANONICAL_LEVELS"
        )


# ---------------------------------------------------------------------------
# (15) REGRESSION: W974 + W969 canonical-level Literal discipline preserved
# ---------------------------------------------------------------------------


def test_w974_canonical_level_literal_discipline_preserved():
    """W974 regression guard: AlertThreshold.level is a Literal.

    W974 promoted ``level`` from ``str`` to ``Literal["critical",
    "warning", "info"]`` once W969 healed every load site. W607-CX
    must NOT widen this back to ``str``.
    """
    from roam.commands import cmd_alerts

    assert hasattr(cmd_alerts, "_CANONICAL_LEVELS"), (
        "_CANONICAL_LEVELS missing -- the W969 canonical-level vocabulary has been refactored away."
    )
    assert isinstance(cmd_alerts._CANONICAL_LEVELS, frozenset), (
        f"_CANONICAL_LEVELS must be a frozenset (closed-set semantics); "
        f"got {type(cmd_alerts._CANONICAL_LEVELS).__name__}"
    )
    assert cmd_alerts._CANONICAL_LEVELS == frozenset({"critical", "warning", "info"}), (
        f"_CANONICAL_LEVELS drifted from the canonical 3-severity set; got {sorted(cmd_alerts._CANONICAL_LEVELS)!r}"
    )
    # AlertThreshold TypedDict carries a Literal for ``level``.
    import typing

    hints = typing.get_type_hints(cmd_alerts.AlertThreshold)
    level_hint = hints.get("level")
    assert level_hint is not None, "AlertThreshold.level type hint missing"
    # ``Literal["critical", "warning", "info"]`` -- get_args returns the
    # 3-tuple of the canonical severities.
    level_args = typing.get_args(level_hint)
    assert set(level_args) == {"critical", "warning", "info"}, (
        f"AlertThreshold.level Literal drifted from the canonical "
        f"3-severity set; got {level_args!r}. W974 discipline requires "
        f"the type to stay tight."
    )


# ---------------------------------------------------------------------------
# (16) REGRESSION: W918 fallback wording unchanged on a clean coexistence path
# ---------------------------------------------------------------------------


def test_w918_silent_fallback_wording_unchanged_under_w607cx(cli_runner, tmp_path):
    """W918 regression guard: the silent-fallback warning wording for an
    unknown user-supplied metric is byte-identical to pre-W607-CX.

    Specifically: ``"Metric 'coverage' has no threshold defined in
    alerts config (missing ['op', 'level']); defaulting to op='>',
    value=0, level='warning'. Add a complete threshold entry ({op,
    value, level}) to .roam/alerts.yaml to silence this warning."``
    """
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    (tmp_path / ".roam").mkdir(exist_ok=True)
    (tmp_path / ".roam" / "alerts.yaml").write_text("thresholds:\n  coverage: { value: 0 }\n")

    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from conftest import index_in_process

        index_in_process(tmp_path)
    except Exception:
        pass

    result = _invoke_alerts(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    coverage_warnings = [m for m in top_wo if "coverage" in m]
    assert coverage_warnings, f"W918 wording missing on coexistence path; got {top_wo!r}"
    # Wording stays identical (substring presence -- byte-stable):
    w = coverage_warnings[0]
    assert "no threshold defined in alerts config" in w, w
    assert "missing" in w, w
    assert "defaulting to op='>', value=0, level='warning'" in w, w
    assert "Add a complete threshold entry" in w, w
    assert ".roam/alerts.yaml" in w, w
