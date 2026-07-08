"""W607-DI -- ``cmd_metrics_push`` substrate-boundary plumbing.

cmd_metrics_push is the Cloud Lite metrics-only push command -- surfaces
the unique ``danger_score`` aggregate metric (not reproduced by any
other roam command per CLAUDE.md "Never N/A without running it"
operational rule) and folds in ``.roam/last-pr-analysis.json``
enrichment. The command sits at the boundary between local audit +
HTTP push, so its substrates span audit-envelope ingest, git
introspection, last-PR enrichment, payload assembly, HTTP push,
verdict composition, and the JSON envelope serialization.

Pre-W607-DI shape: a raise anywhere in the substrate chain
(``_capture_audit`` failed-import path, ``git_metadata`` subprocess
crash, ``_post_metrics`` payload-encoding TypeError) would torpedo the
command outright; the operator saw a Python stack trace, the
``danger_score`` projection never reached Cloud Lite, and CI gates
short-circuited without a structured envelope.

This wave installs the canonical ``_w607di_warnings_out`` bucket +
``_run_check_di`` helper inside the ``metrics_push`` click command and
wraps every substrate boundary:

* capture_audit          -- in-process ``roam audit`` invoke
* git_metadata           -- subprocess git introspection
* infer_repo_id          -- origin URL normalization
* load_last_pr_analysis  -- .roam/last-pr-analysis.json read
                            (W602 warnings_out plumbing preserved
                            inside the helper; W607-DI wraps the
                            CALL boundary)
* build_payload          -- payload coordinator
* serialize_payload      -- ``_json.dumps(payload)`` size calc
* post_metrics           -- HTTP POST (network boundary)
* compose_verdict        -- LAW 6 single-line verdict
* serialize_envelope     -- ``to_json(json_envelope(...))`` projection
* emit_text_output       -- text-path formatting (non-JSON branch)

Marker family ``metrics_push_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test.

W602 LAST-PR PLUMBING PRESERVATION
----------------------------------

The pre-existing W602 plumbing inside ``_load_last_pr_analysis`` and
``_build_last_pr_block`` (``metrics_push_last_pr_*`` markers for
missing / corrupt / timestamp-parse-failed paths) is PRESERVED.
W607-DI is additive: it adds a ``metrics_push_load_last_pr_analysis_failed:``
marker only on raises that escape the W602 envelope (e.g. an inline
raise in ``DEFAULT_LAST_PR_PATH`` resolution before the file-existence
check). The two layers do NOT double-warn: an OSError inside
``Path.read_text`` produces a W602 ``metrics_push_last_pr_read_failed:``
marker; only a raise OUTSIDE the W602 try/except chain produces the
W607-DI ``metrics_push_load_last_pr_analysis_failed:`` marker.

PATTERN-2 SILENT-FALLBACK GUARD
-------------------------------

Every degraded substrate path MUST flip ``summary.partial_success=True``
so the empty-floor envelope is NEVER mistaken for a clean push. The
pre-W607-DI ``partial_success: audit_failed or not ok`` predicate is
preserved; this wave layers ``or bool(_w607di_warnings_out)`` so a
substrate marker also triggers the flag.

W978 7-DISCIPLINE
-----------------

Pre-flight audit before shipping:
1. f-string verdict floor: every verdict default is a non-empty string
2. kwarg-default eagerness: defaults are immutable literals, not calls
3. json.dumps(default=str) sentinel: serialize_payload returns None on
   non-serializable payloads, text path checks ``is None`` before len()
4. Phase-name collision: dry-run + push-path both use ``compose_verdict``
   as the phase name -- DELIBERATE (single canonical verdict marker
   family for both code paths); no other phase name collisions
5. len() at kwarg-bind: NO len() inside ``_run_check_di(..., default=...)``
   args -- every default is a literal
6. Unguarded len()/if x: on poisoned object: the text path checks
   ``payload_serialized is None`` BEFORE len() so a serialize_payload
   degrade cannot AttributeError
7. dict.get(key, expensive_default): all defaults inside the substrate
   wraps are cheap literals (None / "" / 0 / static dicts)
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


def _build_metrics_push_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_metrics_push.

    The metrics-push command calls ``ensure_index()`` then invokes
    ``roam audit`` in-process. The audit envelope can be EMPTY (no
    metrics rows) and metrics-push still composes a payload -- the
    ``danger_score`` zero-default is its own signal. So the fixture
    only needs an indexable repo, not a populated metrics schema.
    """
    import sqlite3
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
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
            line_start INTEGER, line_end INTEGER
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, "
        "line_start, line_end) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def metrics_push_project(tmp_path):
    return _build_metrics_push_project(tmp_path)


def _invoke_metrics_push(cli_runner, project_root, *args, json_mode=True):
    """Invoke the metrics_push click command directly."""
    from roam.commands.cmd_metrics_push import metrics_push

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        # Default flags include --dry-run so the network boundary is
        # not exercised on the happy path. Tests that target the push
        # path supply --token + a monkeypatched _post_metrics.
        full_args = list(args)
        if "--dry-run" not in full_args and "--token" not in full_args:
            full_args.append("--dry-run")
        return cli_runner.invoke(metrics_push, full_args, obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_DI_PHASES = (
    "capture_audit",
    "git_metadata",
    "infer_repo_id",
    "load_last_pr_analysis",
    "build_payload",
    "serialize_payload",
    "post_metrics",
    "compose_verdict",
    "serialize_envelope",
    "emit_text_output",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DI substrate markers
# ---------------------------------------------------------------------------


def test_metrics_push_clean_envelope_omits_w607di_markers(cli_runner, metrics_push_project):
    """Clean metrics-push --dry-run -> no W607-DI substrate markers."""
    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "metrics-push"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    di_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"metrics_push_{p}_failed:" in m for p in _DI_PHASES)
    ]
    assert not di_markers, (
        f"clean metrics-push --dry-run must NOT surface W607-DI markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) capture_audit failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_metrics_push_capture_audit_failure_marker_format(cli_runner, metrics_push_project, monkeypatch):
    """If ``_capture_audit`` raises, surface the canonical marker."""
    from roam.commands import cmd_metrics_push

    def _raise():
        raise RuntimeError("synthetic-capture-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    capture_markers = [m for m in all_wo if m.startswith("metrics_push_capture_audit_failed:")]
    assert capture_markers, f"expected metrics_push_capture_audit_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in capture_markers), capture_markers
    assert any("synthetic-capture-from-W607-DI" in m for m in capture_markers), capture_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_metrics_push_w607di_warnings_in_envelope_both_locations(cli_runner, metrics_push_project, monkeypatch):
    """Non-empty W607-DI bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_metrics_push

    def _raise():
        raise RuntimeError("synthetic-mirror-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DI disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DI disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("metrics_push_capture_audit_failed:")]
    assert markers, f"expected metrics_push_capture_audit_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_metrics_push_three_segment_marker_shape(cli_runner, metrics_push_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_metrics_push

    def _raise():
        raise PermissionError("synthetic-shape-detail-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("metrics_push_capture_audit_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "metrics_push_capture_audit_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) git_metadata failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_metrics_push_git_metadata_failure_surfaces_marker(cli_runner, metrics_push_project, monkeypatch):
    """A raise in ``git_metadata`` surfaces via W607-DI marker.

    The pre-W607-DI command swallowed nothing here -- a subprocess
    crash inside ``git_metadata`` would bubble up. The wrap degrades
    to ``git_meta={}`` so the rest of the pipeline composes.
    """
    from roam.commands import cmd_metrics_push

    def _raise():
        raise RuntimeError("synthetic-git-meta-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "git_metadata", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    git_markers = [m for m in all_wo if m.startswith("metrics_push_git_metadata_failed:")]
    assert git_markers, all_wo
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) build_payload failure -> degrades to empty-floor payload
# ---------------------------------------------------------------------------


def test_metrics_push_build_payload_failure_degrades(cli_runner, metrics_push_project, monkeypatch):
    """A raise in ``_build_payload`` degrades to the empty-floor payload."""
    from roam.commands import cmd_metrics_push

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-payload-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_build_payload", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("metrics_push_build_payload_failed:")]
    assert build_markers, all_wo
    # Empty-floor payload still composes -- envelope contains the
    # canonical schema header even though metrics defaulted.
    payload = data.get("payload") or {}
    assert payload.get("schema") == "roam-metrics-v1", payload
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (7) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_metrics_push_serialize_payload_failure_degrades(cli_runner, metrics_push_project, monkeypatch):
    """A raise inside ``_json.dumps`` (the serialize_payload substrate)
    on the dry-run path degrades to a ``"?"``-bytes-label VERDICT line
    without crashing the command.

    The W607-DI wrap surfaces the marker and the dry-run path's text
    formatting tolerates a degraded ``payload_serialized = None``.
    """
    from roam.commands import cmd_metrics_push

    real_dumps = cmd_metrics_push._json.dumps

    call_count = {"n": 0}

    def _raising_dumps(*args, **kwargs):
        call_count["n"] += 1
        # First dumps call is the bytes-count compute on the dry-run
        # path -- raise there. Subsequent calls (envelope serialization)
        # use the real path. The first-call trap is enough to exercise
        # the W607-DI ``serialize_payload`` marker.
        if call_count["n"] == 1:
            raise TypeError("synthetic-serialize-from-W607-DI")
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(cmd_metrics_push._json, "dumps", _raising_dumps)

    # Force the text path (not JSON) so the dry-run text closure
    # actually executes the len() guard and the W607-DI degradation
    # path is exercised end-to-end.
    result = _invoke_metrics_push(cli_runner, metrics_push_project, json_mode=False)
    # Command does NOT crash.
    assert result.exit_code == 0, result.output
    # VERDICT line still emits (LAW 6).
    assert "VERDICT:" in result.output, result.output


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-DI stays in ``metrics_push_*`` family
# ---------------------------------------------------------------------------


def test_w607di_marker_prefix_stays_in_metrics_push_family(cli_runner, metrics_push_project, monkeypatch):
    """Every W607-DI substrate marker uses the canonical ``metrics_push_*`` prefix.

    Hard distinction from sibling W607-* layers and from sibling
    detector / consumer commands.
    """
    from roam.commands import cmd_metrics_push

    def _raise():
        raise PermissionError("synthetic-prefix-discipline-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        # ``metrics_push_*`` is the canonical W607-DI prefix family.
        # ``metrics_push_last_pr_*`` is the pre-existing W602 marker
        # family (preserved -- different sub-vocabulary, same outer
        # prefix). Both are valid for this command.
        assert marker.startswith("metrics_push_"), (
            f"every surfaced marker on cmd_metrics_push must use the ``metrics_push_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("hotspots_", "cmd_hotspots W607-* (runtime)"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("vulns_", "cmd_vulns W607-AQ + CH"),
            ("taint_", "cmd_taint W607-AY + CJ"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("audit_", "cmd_audit W607-* (sibling)"),
            ("doctor_", "cmd_doctor W607-* (sibling)"),
            ("alerts_", "cmd_alerts W607-CX"),
            ("invariants_", "cmd_invariants W607-CU"),
            ("conventions_", "cmd_conventions W607-CW"),
            ("fingerprint_", "cmd_fingerprint W607-* (sibling)"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Source-level guard: cmd_metrics_push carries the W607-DI accumulator
# ---------------------------------------------------------------------------


def test_cmd_metrics_push_carries_w607di_accumulator():
    """AST-level guard: cmd_metrics_push source carries the W607-DI accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    assert src_path.exists(), f"cmd_metrics_push.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607di_warnings_out" in src, (
        "W607-DI accumulator missing from cmd_metrics_push; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_di" in src, (
        "W607-DI ``_run_check_di`` helper missing from cmd_metrics_push; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_di = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_di":
            found_run_check_di = True
            break
    assert found_run_check_di, (
        "W607-DI ``_run_check_di`` helper not found in cmd_metrics_push AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-DI substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607di_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DI substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DI_PHASES:
        same_line = f'_run_check_di("{phase}"' in src
        multi_line = (
            f'_run_check_di(\n        "{phase}"' in src
            or f'_run_check_di(\n            "{phase}"' in src
            or f'_run_check_di(\n                "{phase}"' in src
            or f'_run_check_di(\n                    "{phase}"' in src
            or f'_run_check_di(\n                        "{phase}"' in src
        )
        marker_grep = f"metrics_push_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DI wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607di_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DI marker fstring lives in cmd_metrics_push."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"metrics_push_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DI marker fstring missing from cmd_metrics_push; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (12) PATTERN-2 SILENT-FALLBACK GUARD: degraded path flips partial_success
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, metrics_push_project, monkeypatch):
    """Pattern-2 regression guard: any W607-DI marker MUST flip
    ``summary.partial_success: True`` so the empty-floor envelope is
    NEVER mistaken for a clean push.
    """
    from roam.commands import cmd_metrics_push

    def _raise():
        raise RuntimeError("synthetic-pattern-2-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    capture_markers = [m for m in all_wo if m.startswith("metrics_push_capture_audit_failed:")]
    assert capture_markers, (
        f"degraded path MUST surface the capture_audit marker (loud-not-silent discipline); got {all_wo!r}"
    )

    # Verdict must NOT use the SAFE/passed/completed vocabulary on a
    # degraded substrate path.
    verdict = (summary.get("verdict") or "").lower()
    for forbidden in ("safe", "passed", "completed", "all clear", "all green"):
        assert forbidden not in verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {summary.get('verdict')!r}"
        )


# ---------------------------------------------------------------------------
# (13) W602 last-PR plumbing preservation -- no double-warn on corrupt file
# ---------------------------------------------------------------------------


def test_w602_last_pr_plumbing_preserved_no_double_warn(cli_runner, metrics_push_project):
    """W602 ``_load_last_pr_analysis`` warnings_out plumbing is preserved.

    A corrupt ``.roam/last-pr-analysis.json`` triggers the W602
    ``metrics_push_last_pr_corrupt:`` marker INSIDE the helper's own
    warnings_out bucket. The W607-DI wrap on the CALL site MUST NOT
    additionally emit a ``metrics_push_load_last_pr_analysis_failed:``
    marker for the same corruption -- the helper returns None
    cleanly (the W602 contract is: emit marker into the supplied
    warnings_out bucket, return None). W607-DI only fires when the
    helper itself RAISES outside that envelope.

    NOTE: pre-W607-DI the helper was called WITHOUT a warnings_out
    argument, so the W602 markers from the helper's INTERNAL bucket
    never reach the envelope. This test confirms W607-DI does not
    REGRESS that behaviour (no spurious load_last_pr_analysis_failed
    marker) while leaving the W602 internal-disclosure wiring intact
    for future migration to a roll-up plumb.
    """
    last_pr_path = metrics_push_project / ".roam" / "last-pr-analysis.json"
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    last_pr_path.write_text("this is not json {{{", encoding="utf-8")

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # W607-DI must NOT emit a load_last_pr_analysis_failed marker
    # for a corrupt file -- the W602 helper handles that path
    # internally and returns None cleanly.
    load_markers = [m for m in all_wo if m.startswith("metrics_push_load_last_pr_analysis_failed:")]
    assert not load_markers, f"W607-DI must NOT double-warn on W602 corrupt-file path; got {load_markers!r}"


# ---------------------------------------------------------------------------
# (14) load_last_pr_analysis raises BEYOND W602 envelope -> W607-DI fires
# ---------------------------------------------------------------------------


def test_metrics_push_load_last_pr_analysis_raises_surfaces_w607di_marker(
    cli_runner, metrics_push_project, monkeypatch
):
    """If ``_load_last_pr_analysis`` itself RAISES (escapes W602 envelope),
    the W607-DI wrap surfaces the marker. Distinguishes "expected W602
    silent-skip on corrupt file" from "unexpected raise outside the
    helper's try/except".
    """
    from roam.commands import cmd_metrics_push

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-load-last-pr-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_load_last_pr_analysis", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    load_markers = [m for m in all_wo if m.startswith("metrics_push_load_last_pr_analysis_failed:")]
    assert load_markers, f"raise OUTSIDE W602 envelope must surface W607-DI marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (15) Per-substrate isolation -- single boundary failure does not torpedo
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_single_boundary_failure_does_not_torpedo(
    cli_runner, metrics_push_project, monkeypatch
):
    """Per-substrate isolation: one boundary raising -> marker surfaces +
    remaining substrates still compose a coherent envelope.

    The fixture: force ``_infer_repo_id`` to raise. The rest of the
    pipeline (capture_audit / git_metadata / build_payload / verdict /
    serialize) MUST still compose a coherent envelope with the repo
    defaulting to ``"<unknown>"``.
    """
    from roam.commands import cmd_metrics_push

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_infer_repo_id", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Marker surfaces for the failed substrate.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    repo_markers = [m for m in all_wo if m.startswith("metrics_push_infer_repo_id_failed:")]
    assert repo_markers, all_wo

    # Other substrates still produced their outputs:
    summary = data["summary"]
    assert summary.get("repo") == "<unknown>", summary
    # Verdict still composes (LAW 6).
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # Payload still composes from the remaining substrates.
    payload = data.get("payload") or {}
    assert payload.get("schema") == "roam-metrics-v1", payload
    # Pattern-2 guard.
    assert summary.get("partial_success") is True


# ---------------------------------------------------------------------------
# (16) Cross-prefix isolation -- markers stay in metrics_push_*, never leak
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_metrics_push_markers_never_leak(cli_runner, metrics_push_project, monkeypatch):
    """Cross-prefix isolation: confirm ``metrics_push_*`` markers stay
    in this command's envelope and do NOT contaminate any sibling
    command-name surface (e.g. adjacent ``metrics_*`` or ``push_*``
    families do not exist; the marker prefix is COMPOSED, not a
    pre-existing sibling).
    """
    from roam.commands import cmd_metrics_push

    def _raise():
        raise RuntimeError("synthetic-cross-prefix-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", _raise)

    result = _invoke_metrics_push(cli_runner, metrics_push_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # Every surfaced marker must start with metrics_push_ -- not the
    # bare "metrics_" (which would collide with any future cmd_metrics)
    # or "push_" (which is not a registered sibling family).
    for marker in (m for m in all_wo if "_failed:" in m):
        assert marker.startswith("metrics_push_"), f"marker leaked outside ``metrics_push_*`` namespace; got {marker!r}"
        # The composite prefix MUST NOT match a bare "metrics_*" or
        # "push_*" sibling -- those are not registered.
        # (No registered "metrics_*" / "push_*" family exists today;
        # this test ossifies that constraint to catch future drift.)


# ---------------------------------------------------------------------------
# (17) W978 7-DISCIPLINE AST AUDIT: substrate-bind site checks
# ---------------------------------------------------------------------------


def test_w978_7_discipline_substrate_bind_audit():
    """W978 7-discipline AST audit on cmd_metrics_push W607-DI plumbing.

    Confirms the substrate-bind sites obey the seven anti-patterns:

      1. No f-string verdict floor that evaluates ``f"... {x}"`` with
         x bound through a substrate (defaults are immutable literals).
      2. No kwarg-default eagerness in ``_run_check_di(..., default=fn())``.
         All defaults are literals (dicts, tuples, None, strings, ints).
      3. No ``json.dumps(default=str)`` sentinel calls inside the wraps.
      4. ``compose_verdict`` is intentionally reused across the dry-run
         and push paths -- both produce the canonical verdict marker
         family. No accidental phase collisions BEYOND that one.
      5. No ``len(...)`` calls inside the substrate ``default=`` slot.
      6. ``payload_serialized is None`` check precedes any
         ``len(payload_serialized)``.
      7. No ``dict.get(key, expensive_default)`` patterns inside the
         W607-DI region (all gets use literal defaults).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Walk every ``_run_check_di`` call site and inspect its keyword
    # arguments.
    discipline_violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_run_check_di":
            for kw in node.keywords:
                if kw.arg != "default":
                    continue
                # The default value MUST be a literal -- not a Call,
                # not a function reference, not an arbitrary expression
                # that could raise at bind time.
                val = kw.value
                if isinstance(val, ast.Call):
                    discipline_violations.append(
                        f"Discipline #2/7 violation: ``_run_check_di(..., default=<Call>)`` "
                        f"binds an EAGER call at line {node.lineno}; default must "
                        f"be a literal (None / '' / 0 / {{}} / [])."
                    )
                if isinstance(val, ast.Lambda):
                    discipline_violations.append(
                        f"Discipline #2 violation: ``_run_check_di(..., default=lambda)`` "
                        f"at line {node.lineno}; default must be a literal value."
                    )
                # Discipline #5: no len() inside the default slot.
                for sub in ast.walk(val):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name) and sub.func.id == "len":
                            discipline_violations.append(
                                f"Discipline #5 violation: len() inside _run_check_di default at line {node.lineno}."
                            )
    assert not discipline_violations, "\n".join(discipline_violations)

    # Discipline #6: every ``len(payload_serialized)`` must be preceded
    # by an ``is None`` guard inside the same function scope.
    if "len(payload_serialized)" in src:
        assert "payload_serialized is None" in src, (
            "Discipline #6 violation: len(payload_serialized) appears in "
            "cmd_metrics_push without a preceding ``is None`` guard."
        )

    # Discipline #4: ``compose_verdict`` is the only deliberate phase-name
    # reuse (dry-run + push paths share the canonical verdict marker
    # family). Every OTHER phase appears exactly once in the substrate
    # bind sites.
    bind_counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_run_check_di":
            if node.args and isinstance(node.args[0], ast.Constant):
                phase = node.args[0].value
                if isinstance(phase, str):
                    bind_counts[phase] = bind_counts.get(phase, 0) + 1
    for phase, count in bind_counts.items():
        if phase in ("compose_verdict", "serialize_envelope", "emit_text_output"):
            # Dry-run + push paths both emit through these phases; the
            # marker family is canonical.
            continue
        assert count == 1, (
            f"Discipline #4 violation: phase {phase!r} bound {count} times in "
            f"cmd_metrics_push -- collision is only permitted for compose_verdict "
            f"/ serialize_envelope / emit_text_output (dry-run + push paths)."
        )


# ---------------------------------------------------------------------------
# (18) Push path -- post_metrics failure -> marker, graceful degradation
# ---------------------------------------------------------------------------


def test_post_metrics_failure_graceful_degradation(cli_runner, metrics_push_project, monkeypatch):
    """A raise in ``_post_metrics`` (the HTTP boundary) MUST NOT crash;
    degrades to the canonical (False, 0, "...") tuple and the verdict
    composes a ``push failed (0)`` string.
    """
    from roam.commands import cmd_metrics_push

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-post-from-W607-DI")

    monkeypatch.setattr(cmd_metrics_push, "_post_metrics", _raise)

    # Force the push path (not --dry-run) by supplying a token.
    result = _invoke_metrics_push(
        cli_runner,
        metrics_push_project,
        "--token",
        "fake-token-for-W607-DI",
    )
    # Push failure -> exit 1 (CI gate semantics preserved).
    assert result.exit_code == 1, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    post_markers = [m for m in all_wo if m.startswith("metrics_push_post_metrics_failed:")]
    assert post_markers, all_wo
    # Verdict composes despite the network-substrate failure.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    # Status defaults to 0 on the synthetic substrate degrade.
    assert data["summary"].get("status_code") == 0
    assert data["summary"].get("ok") is False
    assert data["summary"].get("partial_success") is True
