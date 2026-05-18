"""W607-R -- ``cmd_preflight`` threads ``warnings_out`` onto its envelope.

Eighteenth-in-batch W607 consumer-layer arc. Direct continuation after
W607-Q (cmd_pr_risk PR-time risk aggregator) and the W607-K..P aggregator
cohort (describe / minimap / health / doctor / dashboard / audit).
cmd_preflight is the **pre-change safety gate** composing 6 substrate
helpers (``_resolve_targets`` / ``_check_blast_radius`` /
``_check_affected_tests`` / ``_check_complexity`` / ``_check_coupling`` /
``_check_conventions`` / ``_check_fitness``) into a single 5-signal
CRITICAL/HIGH/MEDIUM/LOW envelope.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_preflight's substrate-call sites are direct helper invocations
(``_check_blast_radius(conn, sym_ids, file_paths)`` etc.) -- NOT a
uniform ``_capture`` boundary. Each helper has its own internal
try/except returning a safe floor (``_check_blast_radius`` floors on
``ImportError`` if networkx missing; ``_check_fitness`` floors per-rule
with its own ``try/except``). But a helper itself can still raise
BEFORE reaching that floor (downstream SQL-shape refactor changes the
``symbol_metrics`` join shape, networkx blowing up during
``build_symbol_graph``, YAML loader surfacing an unexpected raise from
``.roam-fitness.yml``, a third-party patch reaching the wrong helper).
The outer call sites in ``preflight()`` previously had no guards, so
the envelope crashed whole. W607-R wraps each substrate boundary with
``_run_check(phase, fn, *args)`` so the raise becomes a
``preflight_<phase>_failed:<exc_class>:<detail>`` marker via
``warnings_out`` and the envelope still emits the remaining sections
cleanly.

Marker family is ``preflight_*`` -- NOT ``pr_risk_*`` (W607-Q), NOT
``audit_*`` (W607-P), NOT ``dashboard_*`` (W607-O), NOT ``doctor_*``
(W607-N), NOT ``health_*`` (W607-M), NOT ``describe_*`` (W607-K), NOT
``minimap_*`` (W607-L). The marker-prefix discipline test pins this
closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``networkx`` and
``build_symbol_graph`` are deferred-imported inline inside
``_check_blast_radius`` (cost-deferred lazy import, NOT a cycle hedge);
no remediation needed.

Pattern 4 cross-check
---------------------

cmd_preflight is one of the 5 ``conventions_helper`` sites (W133 --
describe / understand / minimap / preflight / conventions). The W607-R
wrap of ``_check_conventions`` does NOT change the delegation: the
helper still calls ``compute_conventions(conn, min_majority_pct=...)``
from ``roam.commands.conventions_helper``. The Pattern 4 guard below
asserts the import line + call site survive verbatim.

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
# Helpers -- invoke preflight via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_preflight(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam preflight`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("preflight")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a resolvable symbol
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def preflight_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``unique_target``).

    Two-file fixture so blast-radius / coupling / tests / complexity /
    conventions / fitness all have real inputs to chew on. ``unique_target``
    has at least one caller so blast radius isn't zero, and the fixture
    name is intentionally unique to avoid LIKE-fallback false-positives
    in the resolver.
    """
    proj = tmp_path / "preflight_w607r_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def unique_target():\n    return helper()\n\n"
        "def helper():\n    return inner()\n\n"
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
# (1) Happy path -- clean preflight -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_preflight_empty_corpus_envelope_byte_identical(cli_runner, preflight_project):
    """Clean preflight on a healthy corpus -> no W607-R warnings_out.

    Hash-stable: an empty W607-R bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-Q contract.

    Note: ``partial_success`` MAY be True on the success path if the
    resolver fired a degraded tier (file / fuzzy), but the W607-R axis
    does NOT independently flip it -- the assertion here only pins the
    absence of W607-R markers, not the value of ``partial_success``
    (which is owned by the W1243 resolution-disclosure axis).
    """
    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "preflight"
    # The verdict is a real one-line risk verdict.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-R markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607r_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("preflight_")]
    assert not w607r_markers, (
        f"clean preflight must NOT surface preflight_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_preflight.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_preflight

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_preflight, attr_name, _raise)


def test_preflight_resolve_targets_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_resolve_targets`` raises, surface ``preflight_resolve_targets_failed:``.

    The resolver default floors to ``(set(), set(), [], target, "unresolved")``
    so the empty-sym_ids branch fires and the not-found envelope still
    emits the marker (with partial_success already True from W1243).
    """
    _patch_helper(
        monkeypatch,
        "_resolve_targets",
        RuntimeError("synthetic-resolve-targets-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out") or []
    assert top_wo, f"_resolve_targets RuntimeError must surface warnings_out; got data keys = {sorted(data.keys())!r}"
    markers = [m for m in top_wo if m.startswith("preflight_resolve_targets_failed:")]
    assert markers, f"expected ``preflight_resolve_targets_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-resolve-targets-from-W607-R" in m for m in markers), markers


def test_preflight_blast_radius_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_check_blast_radius`` raises, surface ``preflight_blast_radius_failed:``."""
    _patch_helper(
        monkeypatch,
        "_check_blast_radius",
        RuntimeError("synthetic-blast-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_blast_radius_failed:")]
    assert markers, f"expected ``preflight_blast_radius_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


def test_preflight_affected_tests_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_check_affected_tests`` raises, surface ``preflight_affected_tests_failed:``."""
    _patch_helper(
        monkeypatch,
        "_check_affected_tests",
        PermissionError("synthetic-tests-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_affected_tests_failed:")]
    assert markers, f"expected ``preflight_affected_tests_failed:`` marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in markers), markers


def test_preflight_complexity_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_check_complexity`` raises, surface ``preflight_complexity_failed:``."""
    _patch_helper(
        monkeypatch,
        "_check_complexity",
        RuntimeError("synthetic-complexity-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_complexity_failed:")]
    assert markers, f"expected ``preflight_complexity_failed:`` marker; got {top_wo!r}"


def test_preflight_coupling_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_check_coupling`` raises, surface ``preflight_coupling_failed:``."""
    _patch_helper(
        monkeypatch,
        "_check_coupling",
        RuntimeError("synthetic-coupling-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_coupling_failed:")]
    assert markers, f"expected ``preflight_coupling_failed:`` marker; got {top_wo!r}"


def test_preflight_conventions_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_check_conventions`` raises, surface ``preflight_conventions_failed:``."""
    _patch_helper(
        monkeypatch,
        "_check_conventions",
        RuntimeError("synthetic-conventions-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_conventions_failed:")]
    assert markers, f"expected ``preflight_conventions_failed:`` marker; got {top_wo!r}"


def test_preflight_fitness_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_check_fitness`` raises, surface ``preflight_fitness_failed:``."""
    _patch_helper(
        monkeypatch,
        "_check_fitness",
        RuntimeError("synthetic-fitness-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_fitness_failed:")]
    assert markers, f"expected ``preflight_fitness_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_preflight_warnings_out_in_envelope(cli_runner, preflight_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A..Q consumers.
    """
    _patch_helper(
        monkeypatch,
        "_check_blast_radius",
        RuntimeError("synthetic-mirror-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY preflight helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_preflight_helper_raises(cli_runner, preflight_project, monkeypatch):
    """Any non-empty W607-R bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    preflight" from "preflight ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    cmd_preflight previously only flipped partial_success on the W1243
    resolution-disclosure axis -- the W607-R wave extends the flip to
    ANY substrate-CALL raise on the success path too.
    """
    _patch_helper(
        monkeypatch,
        "_check_fitness",
        RuntimeError("synthetic-partial-success-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, preflight_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..Q contracts.
    """
    _patch_helper(
        monkeypatch,
        "_check_coupling",
        PermissionError("synthetic-shape-detail-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_check_coupling guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("preflight_coupling_failed:")]
    assert failure_markers, f"expected preflight_coupling_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "preflight_coupling_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``preflight_*`` not pr_risk/audit/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_preflight_not_pr_risk_or_audit(cli_runner, preflight_project, monkeypatch):
    """Every surfaced marker uses the canonical ``preflight_*`` prefix.

    cmd_preflight is the PRE-CHANGE-SAFETY-GATE axis -- distinct from:

    * cmd_pr_risk          -> ``pr_risk_*`` (W607-Q PR-time risk aggregator)
    * cmd_audit            -> ``audit_*`` (W607-P one-shot architecture audit)
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
    _patch_helper(
        monkeypatch,
        "_check_complexity",
        PermissionError("synthetic-prefix-discipline-from-W607-R"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("preflight_"), (
            f"every surfaced W607-R marker must use the ``preflight_*`` "
            f"prefix family (cmd_preflight pre-change safety gate scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("pr_risk_", "cmd_pr_risk W607-Q"),
            ("audit_", "cmd_audit W607-P"),
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
# (7) Sibling parity -- W607-Q cmd_pr_risk surface unchanged
# ---------------------------------------------------------------------------


def test_w607_q_cmd_pr_risk_xfails_unaffected():
    """Sibling parity guard: W607-Q cmd_pr_risk source surface unchanged.

    W607-R lands only in cmd_preflight. The W607-Q cmd_pr_risk surface
    (per-helper ``_run_check`` wrapper + ``_w607q_warnings_out``
    accumulator + ``pr_risk_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_pr_risk while editing preflight,
    the canonical anchors below catch the drift before sibling tests
    fail downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_risk.py"
    assert src_path.exists(), f"cmd_pr_risk.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607q_warnings_out" in src, (
        "W607-Q accumulator removed from cmd_pr_risk; W607-R must not regress the sibling instrumentation."
    )
    assert "pr_risk_" in src, (
        "W607-Q marker prefix removed from cmd_pr_risk; W607-R must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Pattern 4 cross-check -- conventions still delegates to canonical helper
# ---------------------------------------------------------------------------


def test_conventions_helper_still_canonical():
    """Pattern 4 guard: ``_check_conventions`` still calls ``compute_conventions``.

    cmd_preflight is one of 5 conventions-helper sites (W133 -- describe /
    understand / minimap / preflight / conventions). The W607-R wrap of
    ``_check_conventions`` must NOT inline a separate conventions
    detector; the helper still delegates to the canonical
    ``roam.commands.conventions_helper.compute_conventions``.

    Any post-W607-R refactor that inlines convention logic (rather than
    calling the helper) re-introduces the W133 sealed Pattern 4 bug
    family (preflight reporting different conventions than describe /
    understand / minimap / conventions on the same codebase).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_preflight.py"
    assert src_path.exists(), f"cmd_preflight.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    # Import-line guard: canonical helper still wired in.
    assert "from roam.commands.conventions_helper import compute_conventions" in src, (
        "cmd_preflight no longer imports compute_conventions from "
        "conventions_helper; W607-R may have inadvertently broken the W133 "
        "Pattern-4 conventions-delegation guarantee."
    )
    # Call-site guard: ``compute_conventions(conn, min_majority_pct=...)``
    # still fires inside _check_conventions.
    assert "compute_conventions(conn, min_majority_pct=" in src, (
        "cmd_preflight no longer calls compute_conventions(conn, "
        "min_majority_pct=...); the canonical conventions detector has "
        "been bypassed by an inline reimplementation (W133 Pattern-4 "
        "regression)."
    )


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously -- all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, preflight_project, monkeypatch):
    """Two simultaneous substrate raises -> two markers, both surfaced.

    Gate scope: the preflight value proposition is composing 6 signals.
    The W607-R guard must NOT short-circuit on the first raise -- each
    subsequent substrate still runs and emits its own marker on failure.
    Consumers see the full degradation lineage.
    """
    from roam.commands import cmd_preflight

    def _raise_blast(*a, **kw):
        raise RuntimeError("synthetic-multi-blast-from-W607-R")

    def _raise_coupling(*a, **kw):
        raise PermissionError("synthetic-multi-coupling-from-W607-R")

    monkeypatch.setattr(cmd_preflight, "_check_blast_radius", _raise_blast)
    monkeypatch.setattr(cmd_preflight, "_check_coupling", _raise_coupling)

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    blast_markers = [m for m in top_wo if m.startswith("preflight_blast_radius_failed:")]
    coup_markers = [m for m in top_wo if m.startswith("preflight_coupling_failed:")]
    assert blast_markers, f"expected preflight_blast_radius_failed: marker; got {top_wo!r}"
    assert coup_markers, f"expected preflight_coupling_failed: marker; got {top_wo!r}"
    # partial_success still flips with multiple markers.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_preflight uses the canonical W607-R accumulator
# ---------------------------------------------------------------------------


def test_cmd_preflight_carries_w607r_accumulator():
    """AST-level guard: cmd_preflight source carries the W607-R accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_preflight.py"
    assert src_path.exists(), f"cmd_preflight.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607r_warnings_out" in src, (
        "W607-R accumulator missing from cmd_preflight; the substrate-CALL marker plumbing has been removed."
    )
    assert "preflight_" in src, (
        "W607-R marker prefix missing from cmd_preflight; check the "
        '`f"preflight_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside preflight().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-R ``_run_check`` helper not found in cmd_preflight AST; the "
        "per-substrate wrapper has been refactored away."
    )
