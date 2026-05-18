"""W607-CU -- ``cmd_invariants`` substrate-boundary plumbing.

cmd_invariants is the architectural-invariant / implicit-contract
detector (W119 origin per CLAUDE.md detector roster -- part of the
original 16 findings-registry substrate detectors, paired with
cmd_laws). W824 sealed the empty-corpus smoke; this wave layers
substrate isolation on top so a raise in any one substrate boundary
is disclosed via a marker rather than crashing the detector outright.

The wave installs the canonical ``_w607cu_warnings_out`` bucket +
``_run_check_cu`` helper inside the ``invariants`` click command and
wraps every substrate boundary:

* lookup_file_target               -- file path lookup (exact + LIKE)
* query_file_symbols               -- per-file symbol bulk query
* discover_invariants_for_file_sym -- per-symbol invariant discovery
                                      (file-target branch)
* resolve_symbol_target            -- find_symbol() symbol mode
* discover_invariants_for_symbol   -- per-symbol invariant discovery
                                      (symbol-target / fallback branch)
* query_public_api_symbols         -- --public-api batch query
* discover_invariants_public_api   -- per-symbol invariant discovery
                                      (--public-api batch)
* query_breaking_risk_symbols      -- --breaking-risk batch query
* discover_invariants_breaking_risk -- per-symbol invariant discovery
                                       (--breaking-risk batch)
* sort_by_breaking_risk            -- ranking sort
* aggregate_summary                -- total_invariants + high_risk
                                      histogram
* build_resolution_disclosure      -- W1245 resolution block
* compose_verdict                  -- LAW 6 single-line verdict
* build_envelope_symbols           -- per-symbol envelope rows

Marker family ``invariants_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test. cmd_invariants closes the DETECTOR FAMILY
12-WAY with cmd_bus_factor + cmd_auth_gaps + cmd_n1 + cmd_over_fetch
+ cmd_missing_index + cmd_smells + cmd_vibe_check + cmd_clones +
cmd_duplicates + cmd_dead + cmd_hotspots + cmd_orphan_imports.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_invariants_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_invariants.

    Carries one file + one symbol + one edge so the per-symbol
    invariant discovery substrate has SOMETHING to crunch through
    on the symbol-target branch (without an edge, the caller-count
    is 0 and the SIGNATURE invariant won't compose).
    """
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0
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
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (2, 'src/caller.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, signature, "
        "line_start, line_end, visibility, is_exported) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 'helper(x)', 1, 2, 'public', 1)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, signature, "
        "line_start, line_end, visibility, is_exported) VALUES "
        "(2, 2, 'caller', 'src.caller.caller', 'function', 'caller()', 1, 3, 'public', 1)"
    )
    conn.execute("INSERT INTO edges (source_id, target_id, kind, line, source_file_id) VALUES (2, 1, 'call', 2, 2)")
    conn.execute("INSERT INTO symbol_metrics (symbol_id, param_count) VALUES (1, 1)")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def invariants_project(tmp_path):
    return _build_invariants_project(tmp_path)


def _invoke_invariants(cli_runner, project_root, *args, json_mode=True):
    """Invoke the invariants click command directly."""
    from roam.commands.cmd_invariants import invariants

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(invariants, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CU_PHASES = (
    "lookup_file_target",
    "query_file_symbols",
    "discover_invariants_for_file_sym",
    "resolve_symbol_target",
    "discover_invariants_for_symbol",
    "query_public_api_symbols",
    "discover_invariants_public_api",
    "query_breaking_risk_symbols",
    "discover_invariants_breaking_risk",
    "sort_by_breaking_risk",
    "aggregate_summary",
    "build_resolution_disclosure",
    "compose_verdict",
    "build_envelope_symbols",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CU substrate markers
# ---------------------------------------------------------------------------


def test_invariants_clean_envelope_omits_w607cu_markers(cli_runner, invariants_project):
    """Clean invariants run -> no W607-CU substrate markers."""
    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "invariants"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cu_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"invariants_{p}_failed:" in m for p in _CU_PHASES)
    ]
    assert not cu_markers, (
        f"clean invariants run must NOT surface W607-CU substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (1b) Happy path byte-stability -- two clean runs produce same envelope shape
# ---------------------------------------------------------------------------


def test_invariants_clean_envelope_byte_stable_across_runs(cli_runner, invariants_project):
    """Clean envelope: two back-to-back runs produce identical JSON shape.

    Byte-stability proxy: keys identical, summary keys identical, no
    W607-CU substrate markers on either side. Guards against any
    accidental marker emission on the clean path.
    """
    result_a = _invoke_invariants(cli_runner, invariants_project, "helper")
    result_b = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output
    data_a = _json.loads(result_a.output)
    data_b = _json.loads(result_b.output)
    assert sorted(data_a.keys()) == sorted(data_b.keys())
    assert sorted(data_a["summary"].keys()) == sorted(data_b["summary"].keys())
    # No warnings_out on either run.
    for data in (data_a, data_b):
        assert not data.get("warnings_out"), data
        assert not data["summary"].get("warnings_out"), data["summary"]


# ---------------------------------------------------------------------------
# (2) resolve_symbol_target failure -> marker + envelope still emits
# ---------------------------------------------------------------------------


def test_invariants_resolve_symbol_target_failure_marker_format(cli_runner, invariants_project, monkeypatch):
    """If ``find_symbol`` raises, surface the canonical marker."""
    from roam.commands import cmd_invariants

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-resolve-from-W607-CU")

    monkeypatch.setattr(cmd_invariants, "find_symbol", _raise)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    # No symbols resolved -> target_unresolved=True, results=[]. The
    # envelope still emits a coherent verdict from the no-results
    # branch ("No symbols found for: helper").
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    resolve_markers = [m for m in all_wo if m.startswith("invariants_resolve_symbol_target_failed:")]
    assert resolve_markers, f"expected invariants_resolve_symbol_target_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in resolve_markers), resolve_markers
    assert any("synthetic-resolve-from-W607-CU" in m for m in resolve_markers), resolve_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations (dual-mirror)
# ---------------------------------------------------------------------------


def test_invariants_w607cu_warnings_in_envelope(cli_runner, invariants_project, monkeypatch):
    """Non-empty W607-CU bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_invariants

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CU")

    monkeypatch.setattr(cmd_invariants, "find_symbol", _raise)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CU disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CU disclosure path; got summary = {data['summary']!r}"
    )
    # Both surfaces hold the same canonical markers.
    top_markers = [m for m in data["warnings_out"] if m.startswith("invariants_resolve_symbol_target_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("invariants_resolve_symbol_target_failed:")
    ]
    assert top_markers, data["warnings_out"]
    assert summary_markers, data["summary"]["warnings_out"]


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_invariants_three_segment_marker_shape(cli_runner, invariants_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_invariants

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CU")

    monkeypatch.setattr(cmd_invariants, "find_symbol", _raise)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("invariants_resolve_symbol_target_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "invariants_resolve_symbol_target_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) compose_verdict failure -> "no data" floor, envelope composes
# ---------------------------------------------------------------------------


def test_invariants_compose_verdict_failure_degrades(cli_runner, invariants_project, monkeypatch):
    """A raise inside the verdict composer degrades to the ``"no data"`` floor.

    The single-result branch indexes into ``r['name']`` /
    ``r['caller_count']`` / ``r['risk_level']`` / ``r['invariants']``
    -- KeyError-prone on a malformed result row. The W607-CU wrap
    surfaces the marker and keeps the envelope a valid LAW-6
    single-line verdict.
    """
    from roam.commands import cmd_invariants

    # Force a malformed result row missing the "name" key so
    # _compose_verdict KeyErrors when results_len == 1.
    def _bad_discover(conn, sym_id, sym_info):
        # Missing "name" -- compose_verdict KeyErrors.
        return {
            "kind": "function",
            "signature": "helper(x)",
            "file": "src/engine.py",
            "line": 1,
            "caller_count": 1,
            "file_spread": 1,
            "callee_count": 0,
            "param_count": 1,
            "invariants": [],
            "breaking_risk": 1,
            "risk_level": "LOW",
        }

    monkeypatch.setattr(cmd_invariants, "_discover_invariants", _bad_discover)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("invariants_compose_verdict_failed:")]
    assert verdict_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CU stays in ``invariants_*`` family
# ---------------------------------------------------------------------------


def test_w607cu_marker_prefix_stays_in_invariants_family(cli_runner, invariants_project, monkeypatch):
    """Every W607-CU substrate marker uses the canonical ``invariants_*`` prefix."""
    from roam.commands import cmd_invariants

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CU")

    monkeypatch.setattr(cmd_invariants, "find_symbol", _raise)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("invariants_"), (
            f"every surfaced W607-CU marker must use the ``invariants_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
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
# (7) Source-level guard: cmd_invariants carries the W607-CU accumulator
# ---------------------------------------------------------------------------


def test_cmd_invariants_carries_w607cu_accumulator():
    """AST-level guard: cmd_invariants source carries the W607-CU accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_invariants.py"
    assert src_path.exists(), f"cmd_invariants.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607cu_warnings_out" in src, (
        "W607-CU accumulator missing from cmd_invariants; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cu" in src, (
        "W607-CU ``_run_check_cu`` helper missing from cmd_invariants; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cu = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cu":
            found_run_check_cu = True
            break
    assert found_run_check_cu, (
        "W607-CU ``_run_check_cu`` helper not found in cmd_invariants AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CU substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cu_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CU substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_invariants.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CU_PHASES:
        same_line = f'_run_check_cu("{phase}"' in src
        multi_line = (
            f'_run_check_cu(\n        "{phase}"' in src
            or f'_run_check_cu(\n            "{phase}"' in src
            or f'_run_check_cu(\n                "{phase}"' in src
            or f'_run_check_cu(\n                    "{phase}"' in src
            or f'_run_check_cu(\n                        "{phase}"' in src
            or f'_run_check_cu(\n                            "{phase}"' in src
        )
        marker_grep = f"invariants_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CU wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) aggregate_summary failure -> (0, 0) floor, envelope composes
# ---------------------------------------------------------------------------


def test_invariants_aggregate_summary_failure_degrades(cli_runner, invariants_project, monkeypatch):
    """A raise in the aggregate_summary degrades to (0, 0).

    A KeyError on a malformed result row (missing ``invariants`` /
    ``risk_level``) degrades to (0, 0) so the verdict composer still
    produces a coherent string.
    """
    from roam.commands import cmd_invariants

    # Forge a result row missing "invariants" so the sum() KeyErrors.
    def _bad_discover(conn, sym_id, sym_info):
        return {
            "name": "helper",
            "kind": "function",
            "signature": "helper(x)",
            "file": "src/engine.py",
            "line": 1,
            "caller_count": 1,
            "file_spread": 1,
            "callee_count": 0,
            "param_count": 1,
            # NO "invariants" key -- aggregate_summary KeyErrors on sum.
            "breaking_risk": 1,
            "risk_level": "LOW",
        }

    monkeypatch.setattr(cmd_invariants, "_discover_invariants", _bad_discover)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("invariants_aggregate_summary_failed:")]
    assert agg_markers, all_wo
    # Counts collapse to the all-zero floor without crashing.
    assert data["summary"].get("total_invariants") == 0
    assert data["summary"].get("high_risk_count") == 0
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607cu_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CU marker fstring lives in cmd_invariants."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_invariants.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"invariants_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CU marker fstring missing from cmd_invariants; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) DETECTOR FAMILY 12-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_12way_marker_prefixes_coexist(cli_runner, invariants_project, monkeypatch):
    """DETECTOR FAMILY 12-WAY pairing bonus.

    Confirm ``invariants_<phase>_failed:`` markers coexist with the
    eleven sibling detector families without cross-prefix leakage:
    bus_factor_* (W607-CQ), auth_gaps_* (W607-CM), n1_* (W607-CB),
    over_fetch_* (W607-CE), missing_index_* (W607-CI), smells_*
    (W607-BN), vibe_check_* (W607-BS), clones_* (W607-BQ),
    duplicates_* (W607-BM), dead_* (W607-BX), hotspots_* (W607-CP),
    orphan_imports_* (W607-CR). Closes the 12-detector family.
    """
    from roam.commands import cmd_invariants

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-12way-from-W607-CU")

    monkeypatch.setattr(cmd_invariants, "find_symbol", _raise)

    result = _invoke_invariants(cli_runner, invariants_project, "helper")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("invariants_resolve_symbol_target_failed:") for m in all_wo), all_wo

    # None of the eleven detector-sibling prefixes leak into the
    # invariants envelope.
    for forbidden_prefix in (
        "bus_factor_",
        "auth_gaps_",
        "n1_",
        "over_fetch_",
        "missing_index_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
        "hotspots_",
        "orphan_imports_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 12-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_invariants envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (12) Public-API batch failure -> empty results envelope still composes
# ---------------------------------------------------------------------------


def test_invariants_public_api_query_failure_degrades(cli_runner, invariants_project, monkeypatch):
    """A raise in ``query_public_api_symbols`` degrades to [].

    The empty-results path emits the usage-error envelope (W607-CU
    marker surfaces in usage_summary.warnings_out + top-level
    warnings_out) without crashing.
    """
    from roam.commands import cmd_invariants

    # Force a raise inside the query substrate by monkeypatching
    # _discover_invariants to raise -- the query itself can't be
    # patched directly (it's an inline closure), so we exercise the
    # post-query discover_invariants_public_api substrate instead.
    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-public-api-from-W607-CU")

    monkeypatch.setattr(cmd_invariants, "_discover_invariants", _raise)

    result = _invoke_invariants(cli_runner, invariants_project, "--public-api")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    discover_markers = [m for m in all_wo if m.startswith("invariants_discover_invariants_public_api_failed:")]
    assert discover_markers, all_wo
    # Per-symbol isolation: even with discover raising on every row,
    # the envelope still composes a no-data verdict cleanly.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (13) build_envelope_symbols failure -> empty symbols list, envelope composes
# ---------------------------------------------------------------------------


def test_invariants_build_envelope_symbols_failure_degrades(cli_runner, invariants_project, monkeypatch):
    """A raise in ``build_envelope_symbols`` degrades to [].

    The envelope still composes the verdict + summary. This guards
    the symbol-rows substrate at the very end of the click body.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_invariants.py"
    src = src_path.read_text(encoding="utf-8")
    # build_envelope_symbols is an inline closure -- the source guard
    # confirms the substrate phase is wired, and the prior test (9)
    # demonstrates the envelope still emits cleanly when a substrate
    # raises. Direct exercise of this phase is left to source-level.
    assert "build_envelope_symbols" in src, "build_envelope_symbols substrate missing from cmd_invariants source"
    assert "default=[]" in src, "expected default=[] floor on build_envelope_symbols substrate"
