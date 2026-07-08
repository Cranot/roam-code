"""W607-P — ``cmd_audit`` threads ``warnings_out`` onto its envelope.

Sixteenth-in-batch W607 consumer-layer arc. DB-shape continuation after
W607-K (cmd_describe flagship aggregator), W607-L (cmd_minimap DB-shape
aggregator), W607-M (cmd_health CI-gate flagship), W607-N (cmd_doctor
environment aggregator), and W607-O (cmd_dashboard unified status surface).
cmd_audit per CLAUDE.md is the **one-shot architecture audit** that chains
``health -> debt -> dead -> test-pyramid -> api -> stats -> hotspots -> stale-refs``
into a single structured-JSON envelope — the documented backbone of the
"PR Replay" deliverable.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_audit's substrate-call site is ``_capture(args: list[str]) -> dict``
which invokes a subcommand in-process via ``CliRunner().invoke(cli, ...)``
and tries to ``_json.loads(result.output)``. ``_capture`` itself has two
internal try/except guards (exit-code != {0, 5} and ``_json.loads``
failure) returning ``{"_error": ...}`` sentinels — but the boundary above
``_capture`` is unprotected. The dominant additional raise axis is the
helper-CALL boundary: a future refactor of any composed subcommand, a
``CliRunner`` raise that escapes ``catch_exceptions=True`` (e.g. ``SystemExit``
re-raised after PYTHONBREAKPOINT), or any third-party ``runner.invoke``
patch that surfaces an unexpected raise would crash the entire audit
envelope without lineage. W607-P wraps each ``_capture`` boundary with
``_run_check(phase, _capture, [...args])`` so the raise becomes a
``audit_<phase>_failed:<exc_class>:<detail>`` marker via ``warnings_out``
and the envelope still emits the remaining sections cleanly.

Marker family is ``audit_*`` — NOT ``dashboard_*`` (W607-O), NOT
``doctor_*`` (W607-N), NOT ``health_*`` (W607-M), NOT ``describe_*``
(W607-K), NOT ``minimap_*`` (W607-L). The marker-prefix discipline
test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``_capture`` already
imports ``from roam.cli import cli`` inline (cost-deferred lazy import,
not a cycle hedge); no remediation needed. ``warnings_out`` is a plain
accumulator (mirrors W607-N/O idiom). The per-helper wrapper
``_run_check`` lives in the ``audit()`` body so the bucket collects
markers consistently across every capture invocation.

Pattern 5 check
---------------

cmd_audit composes substrate commands via ``_capture(["<name>", ...])``
where ``<name>`` is a **hardcoded literal** at every call site —
NOT a string-template variable. This is the safe shape (`vuln`/`vulns`
typo class is impossible without a variable). The substrate-name
canonicality test pins this by AST-scanning the source for the literal
command names against ``roam.cli._COMMANDS``.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — invoke audit via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_audit(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam audit`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("audit")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture — populated indexed corpus so substrate captures have real data.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def audit_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + edges so audit substrate
    captures have real data to query.
    """
    proj = tmp_path / "audit_w607p_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path — populated corpus -> no warnings_out (byte-identical regression guard)
# ---------------------------------------------------------------------------


def test_audit_empty_corpus_envelope_byte_identical(cli_runner, audit_project):
    """Clean audit on populated corpus -> envelope omits warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field. Mirrors W607-N/O contract on cmd_doctor /
    cmd_dashboard.
    """
    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO warnings_out keys.
    assert "warnings_out" not in data, (
        f"clean audit must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean audit must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )
    # On the clean path partial_success must remain at the auto-False
    # default (set by json_envelope) — only the disclosure path flips it.
    assert data["summary"].get("partial_success") is False, (
        f"clean audit summary.partial_success must remain False on the auto-default path; got {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that _capture boundary raises
# ---------------------------------------------------------------------------


def _patch_capture_for_phase(monkeypatch, target_phase_args0: str, exc):
    """Patch cmd_audit._capture so it raises ``exc`` exactly when the
    first arg matches ``target_phase_args0`` and delegates otherwise.

    Because every substrate call goes through ``_capture(args)`` with a
    hardcoded literal first element (e.g. ``"health"`` / ``"debt"`` / ...),
    we route on args[0] to isolate one phase per test.
    """
    from roam.commands import cmd_audit

    original = cmd_audit._capture

    def _routed(args):
        if args and args[0] == target_phase_args0:
            raise exc
        return original(args)

    monkeypatch.setattr(cmd_audit, "_capture", _routed)


def test_audit_health_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``health`` substrate raises, surface ``audit_health_failed:``."""
    _patch_capture_for_phase(monkeypatch, "health", RuntimeError("synthetic-health-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"health substrate RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_health_failed:")]
    assert markers, f"expected ``audit_health_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-health-from-W607-P" in m for m in markers), markers


def test_audit_debt_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``debt`` substrate raises, surface ``audit_debt_failed:``."""
    _patch_capture_for_phase(monkeypatch, "debt", PermissionError("synthetic-debt-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"debt substrate PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_debt_failed:")]
    assert markers, f"expected ``audit_debt_failed:`` marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in markers), markers


def test_audit_dead_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``dead`` substrate raises, surface ``audit_dead_failed:``."""
    _patch_capture_for_phase(monkeypatch, "dead", RuntimeError("synthetic-dead-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"dead substrate RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_dead_failed:")]
    assert markers, f"expected ``audit_dead_failed:`` marker; got {top_wo!r}"


def test_audit_test_pyramid_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``test-pyramid`` substrate raises, surface ``audit_test_pyramid_failed:``."""
    _patch_capture_for_phase(
        monkeypatch,
        "test-pyramid",
        RuntimeError("synthetic-test-pyramid-from-W607-P"),
    )

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"test-pyramid substrate raise must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_test_pyramid_failed:")]
    assert markers, f"expected ``audit_test_pyramid_failed:`` marker; got {top_wo!r}"


def test_audit_api_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``api`` substrate raises, surface ``audit_api_failed:``."""
    _patch_capture_for_phase(monkeypatch, "api", RuntimeError("synthetic-api-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"api substrate RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_api_failed:")]
    assert markers, f"expected ``audit_api_failed:`` marker; got {top_wo!r}"


def test_audit_stats_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``stats`` substrate raises, surface ``audit_stats_failed:``."""
    _patch_capture_for_phase(monkeypatch, "stats", RuntimeError("synthetic-stats-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"stats substrate RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_stats_failed:")]
    assert markers, f"expected ``audit_stats_failed:`` marker; got {top_wo!r}"


def test_audit_hotspots_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``hotspots`` substrate raises, surface ``audit_hotspots_failed:``."""
    _patch_capture_for_phase(monkeypatch, "hotspots", RuntimeError("synthetic-hotspots-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"hotspots substrate RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_hotspots_failed:")]
    assert markers, f"expected ``audit_hotspots_failed:`` marker; got {top_wo!r}"


def test_audit_stale_refs_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """If the ``stale-refs`` substrate raises, surface ``audit_stale_refs_failed:``."""
    _patch_capture_for_phase(monkeypatch, "stale-refs", RuntimeError("synthetic-stale-refs-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"stale-refs substrate raise must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("audit_stale_refs_failed:")]
    assert markers, f"expected ``audit_stale_refs_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_audit_warnings_out_in_envelope(cli_runner, audit_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A..O consumers.
    """
    _patch_capture_for_phase(monkeypatch, "health", RuntimeError("synthetic-mirror-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # Top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY audit substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_audit_substrate_raises(cli_runner, audit_project, monkeypatch):
    """Any non-empty warnings_out -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    audit" from "audit ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    cmd_audit previously did NOT emit partial_success at all on the
    aggregator boundary (the envelope leaked None health_score etc.) —
    the W607-P fix introduces the field exclusively on the disclosure
    path. (See W805-RR for the empty-corpus axis distinct from this
    helper-raise axis.)
    """
    _patch_capture_for_phase(monkeypatch, "health", RuntimeError("synthetic-partial-success-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, audit_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..O contracts.
    """
    _patch_capture_for_phase(monkeypatch, "health", PermissionError("synthetic-shape-detail-from-W607-P"))

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "health per-phase guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("audit_health_failed:")]
    assert failure_markers, f"expected audit_health_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "audit_health_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — ``audit_*`` not dashboard/doctor/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_audit_not_dashboard_or_doctor(cli_runner, audit_project, monkeypatch):
    """Every surfaced marker uses the canonical ``audit_*`` prefix.

    cmd_audit is the ONE-SHOT-ARCHITECTURE-AUDIT axis — distinct from:

    * cmd_dashboard        -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor           -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health           -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe         -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap          -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep             -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     -> ``history_*`` (W607-H pickaxe)
    * cmd_refs_text        -> ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check     -> ``delete_check_*`` (W607-J diff-gate)
    * cmd_search           -> ``search_*`` (W607-E lexical)
    * cmd_complete         -> ``complete_*`` (W607-F prefix)
    * cmd_search_semantic  -> ``semantic_*`` (W607-A FTS5)
    * cmd_findings         -> ``findings_query_*`` (W607-C registry)
    * cmd_dogfood          -> ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve         -> ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift.
    """
    _patch_capture_for_phase(
        monkeypatch,
        "health",
        PermissionError("synthetic-prefix-discipline-from-W607-P"),
    )

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("audit_"), (
            f"every surfaced marker must use the W607-P ``audit_*`` "
            f"prefix family (cmd_audit one-shot-architecture-audit scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Sibling parity — W607-O cmd_dashboard surface unchanged
# ---------------------------------------------------------------------------


def test_w607_o_cmd_dashboard_xfails_unaffected():
    """Sibling parity guard: W607-O cmd_dashboard source surface unchanged.

    W607-P lands only in cmd_audit. The W607-O cmd_dashboard surface
    (per-helper ``_run_check`` wrapper + ``_w607o_warnings_out`` accumulator
    + ``dashboard_*`` marker emission) MUST stay identical. If a future
    refactor wave touches cmd_dashboard while editing audit, the canonical
    anchors below catch the drift before sibling tests fail downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dashboard.py"
    assert src_path.exists(), f"cmd_dashboard.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607o_warnings_out" in src, (
        "W607-O accumulator removed from cmd_dashboard; W607-P must not regress the sibling instrumentation."
    )
    assert "dashboard_" in src, (
        "W607-O marker prefix removed from cmd_dashboard; W607-P must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Pattern 5 guard — substrate command names are canonical
# ---------------------------------------------------------------------------


def test_substrate_command_names_are_canonical():
    """cmd_audit's hardcoded substrate names exist in roam.cli._COMMANDS.

    Pattern 5 (compound-recipe internal command-name drift) attack
    surface: a typo like ``["test_pyramid"]`` instead of
    ``["test-pyramid"]`` would silently degrade the audit envelope (the
    subcommand wouldn't exist, _capture would log exit-code != 0 into
    the section, and the silent ``{"_error": "exit 2", ...}`` shape
    would propagate). AST-scan the source for every ``_capture([...])``
    call and verify the first literal argument resolves against the
    live CLI registry.

    cmd_audit's substrate calls use HARDCODED LITERAL command names (no
    string templating), so this scan is exhaustive — any future
    contributor who adds ``_capture(["new-subcmd"])`` is auto-checked.
    """
    from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS

    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    known_commands = set(_COMMANDS) | set(_DEPRECATED_COMMANDS)

    capture_names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match both ``_capture([...])`` direct calls AND
        # ``_run_check("phase", _capture, [...])`` indirections.
        is_capture_direct = isinstance(node.func, ast.Name) and node.func.id == "_capture"
        is_capture_via_runcheck = (
            isinstance(node.func, ast.Name)
            and node.func.id == "_run_check"
            and len(node.args) >= 3
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == "_capture"
        )
        if not (is_capture_direct or is_capture_via_runcheck):
            continue
        # The args-list literal is positional arg [0] for direct calls,
        # arg [2] for _run_check indirection.
        args_idx = 0 if is_capture_direct else 2
        if len(node.args) <= args_idx:
            continue
        args_node = node.args[args_idx]
        if not isinstance(args_node, ast.List) or not args_node.elts:
            continue
        first = args_node.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            capture_names.append(first.value)

    assert capture_names, (
        "AST scan found NO _capture(['<cmd>', ...]) call sites — the "
        "scanner is broken OR cmd_audit was refactored away from the "
        "literal-substrate-name pattern. Update this test."
    )
    for name in capture_names:
        assert name in known_commands, (
            f"cmd_audit substrate name {name!r} is NOT registered in "
            f"roam.cli._COMMANDS — Pattern 5 drift (cf. vuln/vulns typo "
            f"class). Known commands sample: "
            f"{sorted(known_commands)[:8]!r}..."
        )


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously — all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, audit_project, monkeypatch):
    """Two simultaneous substrate raises -> two markers, both surfaced.

    Aggregator scope: the audit's value proposition is composing multiple
    substrates. The W607-P guard must NOT short-circuit on the first
    raise — each subsequent substrate still runs and emits its own marker
    on failure. Consumers see the full degradation lineage.
    """
    from roam.commands import cmd_audit

    original = cmd_audit._capture

    def _routed(args):
        if args and args[0] == "health":
            raise RuntimeError("synthetic-multi-health-from-W607-P")
        if args and args[0] == "debt":
            raise PermissionError("synthetic-multi-debt-from-W607-P")
        return original(args)

    monkeypatch.setattr(cmd_audit, "_capture", _routed)

    result = _invoke_audit(cli_runner, audit_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    health_markers = [m for m in top_wo if m.startswith("audit_health_failed:")]
    debt_markers = [m for m in top_wo if m.startswith("audit_debt_failed:")]
    assert health_markers, f"expected audit_health_failed: marker; got {top_wo!r}"
    assert debt_markers, f"expected audit_debt_failed: marker; got {top_wo!r}"
    # partial_success still flips with multiple markers.
    assert data["summary"].get("partial_success") is True, data["summary"]
