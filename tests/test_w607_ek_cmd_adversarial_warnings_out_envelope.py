"""W607-EK -- ``cmd_adversarial`` substrate-boundary plumbing.

cmd_adversarial is a multi-substrate aggregator that composes cycles +
clusters + layers + catalog + dead + complexity on changed files
(W148-doc characterization + W150 detector-candidacy audit). Until this
wave the command had no substrate-boundary marker plumbing -- a raise
inside any constituent helper (_check_new_cycles,
_check_layer_violations, _check_anti_patterns, _check_cross_cluster,
_check_orphaned_symbols, _check_high_fan_out), the changed-file
resolver, the changed-symbol batched lookup, the severity classifier,
the verdict composer, or the envelope serializer would crash the
adversarial command outright.

This wave installs the canonical ``_w607ek_warnings_out`` bucket +
``_run_check_ek`` helper inside the ``adversarial`` click command and
wraps every substrate boundary:

* resolve_changed_files     -- get_changed_files + resolve_changed_to_db
* lookup_changed_symbols    -- batched_in changed-symbol-id lookup
* compose_cycles_check      -- _check_new_cycles (cycles substrate)
* compose_layers_check      -- _check_layer_violations (layers substrate)
* compose_catalog_check     -- _check_anti_patterns (algo catalog
                               substrate)
* compose_clusters_check    -- _check_cross_cluster (clusters substrate)
* compose_dead_check        -- _check_orphaned_symbols (dead substrate)
* compose_complexity_check  -- _check_high_fan_out (complexity substrate)
* score_classify            -- severity filter + sort + counters
* compose_verdict           -- LAW 6 single-line verdict floor
* serialize_envelope        -- JSON envelope emission

Marker family ``adversarial_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

W148-DOC PRESERVATION
---------------------

The W148-doc adversarial mischaracterization fix is preserved: the
command continues to compose cycles + clusters + layers + catalog +
dead + complexity substrates on changed files. The W607-EK plumbing
adds substrate-boundary disclosure WITHOUT collapsing the multi-leg
composition. The 6-way constituent invocation pin (AST-scan)
confirms all six legs remain present after the W607-EK refactor.

W150 DETECTOR-CANDIDACY PRESERVATION
------------------------------------

The W150 detector-candidacy audit decision (adversarial is an
invocation-scoped aggregator, NOT a findings-registry detector) is
preserved -- the W607-EK marker plumbing surfaces substrate-call
failures via warnings_out, NOT via emit_finding(). The envelope
remains invocation-scoped per the W150 decision; no per-location
findings rows are emitted from the W607-EK plumbing.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string;
the verdict is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``adversarial_*`` markers do NOT leak into the constituent substrate
prefixes (``cycles_*``, ``clusters_*``, ``layers_*``, ``dead_*``,
``complexity_*``) or into any sibling W607-* command family. The
prefix-discipline test confirms hard distinction.
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


def _build_adv_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_adversarial.

    The W607-EK substrate boundary tests monkeypatch the interior
    helpers (get_changed_files, resolve_changed_to_db, the six
    _check_* functions, batched_in) so the actual graph contents
    matter less than DB-and-index presence. We just need
    ensure_index() to find a .roam DB rooted at tmp_path.
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
        CREATE TABLE IF NOT EXISTS symbol_references (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER,
            kind TEXT,
            line INTEGER
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/a.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (2, 'src/b.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'foo', 'src.a.foo', 'function', 1, 2, 'public', 1)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(2, 2, 'bar', 'src.b.bar', 'function', 1, 2, 'public', 1)"
    )
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (2, 1, 'calls')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def adv_project(tmp_path):
    return _build_adv_project(tmp_path)


def _invoke_adversarial(cli_runner, project_root, *args, json_mode=True):
    """Invoke the adversarial click command directly."""
    from roam.commands.cmd_adversarial import adversarial

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(adversarial, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_EK_PHASES = (
    "resolve_changed_files",
    "lookup_changed_symbols",
    "compose_cycles_check",
    "compose_layers_check",
    "compose_catalog_check",
    "compose_clusters_check",
    "compose_dead_check",
    "compose_complexity_check",
    "score_classify",
    "compose_verdict",
    "serialize_envelope",
)


def _stub_changed_files(monkeypatch):
    """Default monkeypatch: pretend ``src/a.py`` changed."""
    import roam.commands.cmd_adversarial as _adv

    monkeypatch.setattr(
        _adv,
        "get_changed_files",
        lambda root, staged=False, commit_range=None: ["src/a.py"],
    )
    monkeypatch.setattr(
        _adv,
        "resolve_changed_to_db",
        lambda conn, paths: {"src/a.py": 1},
    )


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EK substrate markers
# ---------------------------------------------------------------------------


def test_adversarial_clean_envelope_omits_w607ek_markers(cli_runner, adv_project, monkeypatch):
    """Clean adversarial run -> no W607-EK substrate markers."""
    _stub_changed_files(monkeypatch)
    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "adversarial"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ek_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"adversarial_{p}_failed:" in m for p in _EK_PHASES)
    ]
    assert not ek_markers, (
        f"clean adversarial must NOT surface W607-EK substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) resolve_changed_files failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_adversarial_resolve_changed_files_failure_marker_format(cli_runner, adv_project, monkeypatch):
    """If ``get_changed_files`` raises, surface the canonical marker."""
    import roam.commands.cmd_adversarial as _adv

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-resolve-from-W607-EK")

    monkeypatch.setattr(_adv, "get_changed_files", _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    resolve_markers = [m for m in all_wo if m.startswith("adversarial_resolve_changed_files_failed:")]
    assert resolve_markers, f"expected adversarial_resolve_changed_files_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in resolve_markers), resolve_markers
    assert any("synthetic-resolve-from-W607-EK" in m for m in resolve_markers), resolve_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_adversarial_w607ek_warnings_in_envelope(cli_runner, adv_project, monkeypatch):
    """Non-empty W607-EK bucket -> both top-level AND summary.warnings_out."""
    import roam.commands.cmd_adversarial as _adv

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EK")

    monkeypatch.setattr(_adv, "get_changed_files", _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EK disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EK disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_adversarial_three_segment_marker_shape(cli_runner, adv_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.commands.cmd_adversarial as _adv

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-EK")

    monkeypatch.setattr(_adv, "get_changed_files", _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("adversarial_resolve_changed_files_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "adversarial_resolve_changed_files_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) Per-substrate isolation -- each constituent leg raises independently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("helper_name", "phase"),
    [
        ("_check_new_cycles", "compose_cycles_check"),
        ("_check_layer_violations", "compose_layers_check"),
        ("_check_anti_patterns", "compose_catalog_check"),
        ("_check_cross_cluster", "compose_clusters_check"),
        ("_check_orphaned_symbols", "compose_dead_check"),
        ("_check_high_fan_out", "compose_complexity_check"),
    ],
)
def test_per_substrate_isolation_each_constituent_check(cli_runner, adv_project, monkeypatch, helper_name, phase):
    """Each constituent check raising surfaces only its own marker."""
    import roam.commands.cmd_adversarial as _adv

    _stub_changed_files(monkeypatch)

    def _raise(*args, **kwargs):
        raise RuntimeError(f"synthetic-{phase}-from-W607-EK")

    monkeypatch.setattr(_adv, helper_name, _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    expected_markers = [m for m in all_wo if m.startswith(f"adversarial_{phase}_failed:")]
    assert expected_markers, f"expected marker for phase {phase!r} when {helper_name} raises; got {all_wo!r}"
    # Envelope still composes a coherent verdict on the degraded path.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-EK stays in ``adversarial_*`` family
# ---------------------------------------------------------------------------


def test_w607ek_marker_prefix_stays_in_adversarial_family(cli_runner, adv_project, monkeypatch):
    """Every W607-EK substrate marker uses the canonical ``adversarial_*`` prefix.

    Hard distinction from constituent substrate prefixes (cycles,
    layers, clusters, dead, complexity) AND from sibling W607-* layers
    across the broader command surface. Confirms cross-prefix isolation
    per the wave contract.
    """
    import roam.commands.cmd_adversarial as _adv

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EK")

    monkeypatch.setattr(_adv, "get_changed_files", _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("adversarial_"), (
            f"every surfaced W607-EK marker must use the ``adversarial_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from constituent substrate prefixes (the
        # six legs adversarial composes) and from broader sibling
        # W607-* command families. Adversarial markers must NOT leak
        # into prefixes that would conflict with a substrate-level
        # wave.
        for forbidden_prefix, sibling in (
            ("cycles_", "cycles substrate (constituent leg)"),
            ("clusters_", "clusters substrate (constituent leg)"),
            ("layers_", "layers substrate (constituent leg)"),
            ("catalog_", "catalog substrate (constituent leg)"),
            ("dead_", "dead substrate (constituent leg)"),
            ("complexity_", "complexity substrate (constituent leg)"),
            ("simulate_", "cmd_simulate W607-EF"),
            ("critique_", "cmd_critique W607-EJ"),
            ("orchestrate_", "cmd_orchestrate W607-DS"),
            ("partition_", "cmd_partition W607-DU"),
            ("agent_plan_", "cmd_agent_plan W607-DY"),
            ("fleet_", "cmd_fleet W607-EB"),
            ("mutate_", "cmd_mutate W607-EG"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("vulns_", "cmd_vulns W607-AQ + CH (security sibling)"),
            ("taint_", "cmd_taint W607-AY + CJ (security sibling)"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_adversarial carries the W607-EK accumulator
# ---------------------------------------------------------------------------


def test_cmd_adversarial_carries_w607ek_accumulator():
    """AST-level guard: cmd_adversarial source carries the W607-EK accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    assert src_path.exists(), f"cmd_adversarial.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ek_warnings_out" in src, (
        "W607-EK accumulator missing from cmd_adversarial; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ek" in src, (
        "W607-EK ``_run_check_ek`` helper missing from cmd_adversarial; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ek = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ek":
            found_run_check_ek = True
            break
    assert found_run_check_ek, (
        "W607-EK ``_run_check_ek`` helper not found in cmd_adversarial AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-EK substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ek_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EK substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EK_PHASES:
        same_line = f'_run_check_ek("{phase}"' in src
        multi_line = (
            f'_run_check_ek(\n        "{phase}"' in src
            or f'_run_check_ek(\n            "{phase}"' in src
            or f'_run_check_ek(\n                "{phase}"' in src
            or f'_run_check_ek(\n                    "{phase}"' in src
            or f'_run_check_ek(\n                        "{phase}"' in src
        )
        marker_grep = f"adversarial_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EK wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607ek_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EK marker fstring lives in cmd_adversarial."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"adversarial_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-EK marker fstring missing from cmd_adversarial; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (10) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, adv_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.

    Exercise: raise inside ``get_changed_files`` so the downstream
    substrates short-circuit early via the empty-floor; the verdict
    still emits as the LAW-6 zero-count floor string.
    """
    import roam.commands.cmd_adversarial as _adv

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-EK")

    monkeypatch.setattr(_adv, "get_changed_files", _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 invariant violated: verdict missing/empty on degraded path; got summary={summary!r}"
    )
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # The floor names the zero-count state, NOT a SAFE/passed vocabulary.
    forbidden_vocab = ("safe", "passed", "all clear")
    for forbidden in forbidden_vocab:
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (11) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, adv_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``get_changed_files`` raises, the empty-floor default kicks in
    and the envelope is emitted. The W607-EK wrap MUST flip
    ``partial_success: True`` on that branch so the empty-state
    envelope is NOT mistaken for a clean adversarial verdict.
    """
    import roam.commands.cmd_adversarial as _adv

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-EK")

    monkeypatch.setattr(_adv, "get_changed_files", _raise)

    result = _invoke_adversarial(cli_runner, adv_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    resolve_markers = [m for m in all_wo if m.startswith("adversarial_resolve_changed_files_failed:")]
    assert resolve_markers, (
        f"degraded path MUST surface the resolve_changed_files marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (12) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_ek_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_ek helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local). A raise
    inside the wrapped fn falls through to ``return default`` so the
    caller's empty-floor default actually propagates.

    AST-level guard: locate the ``_run_check_ek`` FunctionDef and walk
    its body to confirm the last statement of the ``except`` handler
    is ``Return(value=Name(id='default'))``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_ek"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                # Last statement in the except body must be ``return default``.
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_ek except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_ek must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_ek must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_ek FunctionDef / except handler not found in "
        "cmd_adversarial AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) W148-DOC PRESERVATION + 6-WAY CONSTITUENT INVOCATION PIN
# ---------------------------------------------------------------------------


def test_w148_doc_six_way_constituent_invocation_pin():
    """AST-scan: cmd_adversarial still invokes all six constituent
    substrate checks after the W607-EK refactor.

    W148-doc characterizes adversarial as a multi-substrate aggregator
    composing cycles + clusters + layers + catalog + dead + complexity
    on changed files. The W607-EK plumbing must NOT collapse the
    composition. The 6-way pin walks the source AST and confirms each
    of the six ``_check_*`` helpers is called inside the adversarial
    click-command body.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    expected_helpers = {
        "_check_new_cycles",
        "_check_layer_violations",
        "_check_anti_patterns",
        "_check_cross_cluster",
        "_check_orphaned_symbols",
        "_check_high_fan_out",
    }
    seen_helpers: set[str] = set()

    # Find the adversarial click command function and walk its body.
    # The W607-EK refactor passes each constituent helper as a Name
    # argument to ``_run_check_ek("<phase>", <helper>, ...)`` rather
    # than calling it directly. Walk Name references inside Call args
    # (NOT the Call.func slot) so we count both the legacy
    # ``helper(...)`` call shape AND the new ``_run_check_ek("...",
    # helper, ...)`` argument-pass shape.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "adversarial"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                # Direct call: helper(args...)
                func = sub.func
                if isinstance(func, ast.Name) and func.id in expected_helpers:
                    seen_helpers.add(func.id)
                # Argument-pass: _run_check_ek("phase", helper, ...)
                for arg in sub.args:
                    if isinstance(arg, ast.Name) and arg.id in expected_helpers:
                        seen_helpers.add(arg.id)

    missing = expected_helpers - seen_helpers
    assert not missing, (
        f"W148-doc 6-way constituent invocation pin failed -- the "
        f"adversarial command must call all six constituent substrate "
        f"helpers; missing = {sorted(missing)!r}"
    )


# ---------------------------------------------------------------------------
# (14) W150 DETECTOR-CANDIDACY PRESERVATION
# ---------------------------------------------------------------------------


def test_w150_no_findings_registry_writes_from_adversarial():
    """W150 audit preservation guard.

    The W150 detector-candidacy audit decision: adversarial is an
    invocation-scoped aggregator that emits architectural challenges
    via the JSON envelope, NOT a findings-registry detector that
    persists per-location findings rows. The W607-EK marker plumbing
    surfaces substrate-call failures via warnings_out, NOT via
    emit_finding(). Confirm the W607-EK refactor did NOT introduce a
    findings-registry write.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    assert "emit_finding" not in src, (
        "W150 detector-candidacy decision violated: adversarial is an "
        "invocation-scoped aggregator, not a findings-registry detector. "
        "The W607-EK refactor must NOT introduce ``emit_finding()`` calls."
    )
    assert "from roam.db.findings import" not in src, (
        "W150 detector-candidacy decision violated: adversarial must not import the findings-registry helpers."
    )


# ---------------------------------------------------------------------------
# (15) Existing W607-* coexistence -- adversarial is uniquely ``ek``
# ---------------------------------------------------------------------------


def test_w607ek_unique_prefix_in_cmd_adversarial_source():
    """Source-level guard: cmd_adversarial carries ONLY the W607-EK
    plumbing (no other W607-* prefix appears as an accumulator name).

    Confirms the wave naming is unique within the source file; the
    earlier waves' accumulator names (_w607ef_*, _w607ej_*, etc.) do
    NOT leak in. Prevents accidental copy-paste from sibling waves.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    # The EK accumulator is the only W607 accumulator allowed in this file.
    assert "_w607ek_warnings_out" in src
    # No sibling-wave accumulator should appear (we sample a representative set
    # of recently shipped waves so a copy-paste regression fires loudly).
    forbidden_accumulators = (
        "_w607ef_warnings_out",  # cmd_simulate
        "_w607eg_warnings_out",  # cmd_mutate
        "_w607ej_warnings_out",  # cmd_critique
        "_w607ds_warnings_out",  # cmd_orchestrate
        "_w607du_warnings_out",  # cmd_partition
        "_w607dy_warnings_out",  # cmd_agent_plan
        "_w607eb_warnings_out",  # cmd_fleet
        "_w607ec_warnings_out",  # cmd_preflight
        "_w607ed_warnings_out",  # cmd_auth_gaps
    )
    for forbidden in forbidden_accumulators:
        assert forbidden not in src, (
            f"sibling-wave accumulator ``{forbidden}`` leaked into cmd_adversarial -- copy-paste regression."
        )


# ---------------------------------------------------------------------------
# (16) Compound 6-way constituent invocation: each leg wrapped in EK
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase",
    [
        "compose_cycles_check",
        "compose_layers_check",
        "compose_catalog_check",
        "compose_clusters_check",
        "compose_dead_check",
        "compose_complexity_check",
    ],
)
def test_each_constituent_leg_is_run_check_wrapped(phase):
    """Source-level guard: each of the six constituent substrate
    invocations is wrapped by ``_run_check_ek("<phase>", ...)``.

    This is the multi-substrate dual-pin -- it complements the W148-doc
    6-way invocation pin by confirming each leg's call site goes
    through the W607-EK helper, not a bare ``challenges.extend(...)``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py"
    src = src_path.read_text(encoding="utf-8")
    same_line = f'_run_check_ek(\n            "{phase}"' in src
    next_line = f'_run_check_ek(\n            "{phase}"' in src
    deeper = f'_run_check_ek("{phase}"' in src
    multi = f'_run_check_ek(\n        "{phase}"' in src or f'_run_check_ek(\n                "{phase}"' in src
    assert same_line or next_line or deeper or multi, (
        f"constituent leg ``{phase}`` is NOT wrapped by _run_check_ek; the multi-substrate dual-pin failed."
    )
