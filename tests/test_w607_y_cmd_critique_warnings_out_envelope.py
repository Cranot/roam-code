"""W607-Y -- ``cmd_critique`` threads ``warnings_out`` onto its envelope.

Twenty-fifth-in-batch W607 consumer-layer arc. Direct sibling of W607-W
(cmd_relate multi-target axis). cmd_critique is the **patch-verifier /
diff-text-substrate axis** variant -- consumes a unified diff via stdin or
``--input``, parses it into changed regions, resolves changed symbols
against the graph, fans out to three checks (clones-not-edited / impact /
intent), and aggregates the per-region findings into one ranked report.

Substrate boundaries wrapped by W607-Y
--------------------------------------

Eight substrate-call sites in ``critique()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``parse_diff``               -- parse_diff(diff_text)
* ``find_changed_symbols``     -- find_changed_symbols(conn, regions)
* ``run_checks``               -- _run_checks_with_status(...)
* ``aggregate``                -- aggregate(findings, check_status=...)
* ``emit_findings``            -- _emit_critique_findings(...) on --persist
* ``load_overrides``           -- _load_critique_overrides()
* ``bench_relevance_hint``     -- _bench_relevance_hint(regions, overrides=...)
* ``compute_risk_level``       -- _critique_risk_level(findings, warnings_out=...)

Each raise becomes a ``critique_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607y_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_critique's substrate-call sites are direct function invocations on the
module-level helpers (``parse_diff``, ``find_changed_symbols``, ``aggregate``,
the local ``_run_checks_with_status`` / ``_load_critique_overrides`` /
``_bench_relevance_hint`` / ``_critique_risk_level`` / ``_emit_critique_findings``).
The dominant raise axis is the helper-CALL boundary -- consistent with
W607-N..W. Each helper can raise on a diff-parse error, a SQL-shape
refactor, a transient OperationalError, or an aggregator schema drift.
The outer call sites in ``critique()`` previously had no top-level
guards (the inner ``_run_checks_with_status`` swallows per-check raises
into a status string, but ``parse_diff`` / ``find_changed_symbols`` /
``aggregate`` were unguarded), so the envelope crashed whole. W607-Y
wraps each substrate boundary so the raise becomes a structured marker.

Marker family is ``critique_*`` -- NOT ``relate_*`` (W607-W), NOT ``deps_*``
(W607-V), etc. The marker-prefix discipline test pins this closed-enum
distinction.

W805-HHHH axis disconfirmation (pre-existing record)
----------------------------------------------------

A prior W805 wave (W805-HHHH) probed cmd_critique on the SHARED-HELPER
axis (the ``get_changed_files`` substrate used by cmd_diff / cmd_pr_risk)
and DISCONFIRMED that hypothesis: cmd_critique consumes diff TEXT via
stdin, not git refs through ``get_changed_files``. W607-Y is the
substrate-CALL axis (NOT the shared-helper axis) -- cmd_critique still
has its own substrate boundaries (parse_diff, find_changed_symbols,
aggregate, ...) that need W607 plumbing regardless of the W805 outcome.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_critique has lazy
``from roam.output.errors import ...`` inside the click command for the
empty-input / invalid-diff branches -- these are genuine deferred-load
imports (the errors module pulls structured-usage helpers only needed
on the bad-input path), NOT cargo-cult cycle hedges. Left untouched per
W907.

Two warnings_out buckets, one channel
-------------------------------------

cmd_critique already carries a ``_critique_warnings_out`` bucket
(W641-followup-B unknown-severity tracking — flips ``partial_success``
when a finding's severity label is unrecognised). W607-Y adds a
DISTINCT ``_w607y_warnings_out`` bucket (substrate-CALL markers) so the
two axes (unknown-severity data shape vs. helper-raised substrate
boundary) don't conflate at the call site. They MERGE into a single
``warnings_out`` list on envelope emission; the marker PREFIX
disambiguates them downstream (``critique_unknown_severity:*`` vs.
``critique_<phase>_failed:*``). ``partial_success`` flips when EITHER
bucket is non-empty -- consumers reading ``partial_success`` alone need
not distinguish the two flavours.

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
# Helpers -- invoke critique via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


_DIFF_TEXT = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -1,5 +1,6 @@\n"
    " from src.models import User\n"
    " \n"
    " def verify_token(t):\n"
    "+    # tweak\n"
    "     return User('test')\n"
    " \n"
)


def _invoke_critique(runner: CliRunner, cwd, *extra, json_mode: bool = True, stdin: str | None = None):
    """Invoke ``roam critique`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("critique")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, input=stdin, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with diff target file
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def critique_project(tmp_path, monkeypatch):
    """Indexed corpus with a symbol the diff modifies."""
    proj = tmp_path / "critique_w607y_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    return User('test')\n\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean critique -> envelope omits W607-Y warnings_out
# ---------------------------------------------------------------------------


def test_critique_clean_envelope_omits_w607y_markers(cli_runner, critique_project):
    """Clean critique on a healthy diff -> no W607-Y substrate markers.

    Hash-stable: an empty W607-Y bucket on the success path must produce
    an envelope without W607-Y substrate markers. The pre-existing
    ``_critique_warnings_out`` (unknown-severity) bucket may or may not
    emit independently; this test asserts that no marker carries the
    ``critique_<phase>_failed:`` substrate prefix on the clean path.
    """
    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "critique"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-Y substrate markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m
        for m in (list(top_wo) + list(summary_wo))
        if m.startswith("critique_") and m.endswith("_failed:") is False and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean critique must NOT surface critique_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) parse_diff failure -> critique_parse_diff_failed marker
# ---------------------------------------------------------------------------


def test_critique_parse_diff_failure_marker_format(cli_runner, critique_project, monkeypatch):
    """If parse_diff raises, surface ``critique_parse_diff_failed:``."""
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-parse-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "parse_diff", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    parse_markers = [m for m in top_wo if m.startswith("critique_parse_diff_failed:")]
    assert parse_markers, f"expected critique_parse_diff_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in parse_markers), parse_markers
    assert any("synthetic-parse-from-W607-Y" in m for m in parse_markers), parse_markers


# ---------------------------------------------------------------------------
# (3) find_changed_symbols failure -> critique_find_changed_symbols_failed
# ---------------------------------------------------------------------------


def test_critique_find_changed_symbols_failure_marker_format(cli_runner, critique_project, monkeypatch):
    """If find_changed_symbols raises, surface ``critique_find_changed_symbols_failed:``."""
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-changed-symbols-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "find_changed_symbols", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    cs_markers = [m for m in top_wo if m.startswith("critique_find_changed_symbols_failed:")]
    assert cs_markers, f"expected critique_find_changed_symbols_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) aggregate failure -> critique_aggregate_failed marker
# ---------------------------------------------------------------------------


def test_critique_aggregate_failure_marker_format(cli_runner, critique_project, monkeypatch):
    """If aggregate raises, surface ``critique_aggregate_failed:``."""
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-aggregate-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "aggregate", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    agg_markers = [m for m in top_wo if m.startswith("critique_aggregate_failed:")]
    assert agg_markers, f"expected critique_aggregate_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) run_checks failure -> critique_run_checks_failed marker
# ---------------------------------------------------------------------------


def test_critique_run_checks_failure_marker_format(cli_runner, critique_project, monkeypatch):
    """If _run_checks_with_status raises, surface ``critique_run_checks_failed:``."""
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-run-checks-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "_run_checks_with_status", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    rc_markers = [m for m in top_wo if m.startswith("critique_run_checks_failed:")]
    assert rc_markers, f"expected critique_run_checks_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_critique_warnings_out_in_envelope(cli_runner, critique_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "aggregate", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("critique_aggregate_failed:")]
    assert markers, f"expected critique_aggregate_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-Y" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY critique helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_critique_helper_raises(cli_runner, critique_project, monkeypatch):
    """Any non-empty W607-Y bucket -> summary.partial_success = True."""
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "find_changed_symbols", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, critique_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..W contracts.
    """
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "aggregate", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "aggregate guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("critique_aggregate_failed:")]
    assert failure_markers, f"expected critique_aggregate_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "critique_aggregate_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``critique_*`` not relate/deps/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_critique_not_relate_or_other(cli_runner, critique_project, monkeypatch):
    """Every surfaced W607-Y marker uses the canonical ``critique_*`` prefix.

    cmd_critique is the patch-verifier / diff-text-substrate variant --
    distinct from sibling W607-* layers (relate / deps / uses / impact /
    diagnose / preflight / pr_risk / audit / dashboard / doctor / health /
    describe / minimap / grep / history / refs_text / delete_check / search /
    complete / semantic / findings_query / dogfood / retrieve). Hard guard
    against accidental marker-prefix drift.
    """
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-Y")

    monkeypatch.setattr(cmd_critique, "aggregate", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (skip the W641-followup-B
    # unknown-severity markers, which are a distinct axis on the same
    # warnings_out channel).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("critique_"), (
            f"every surfaced W607-Y marker must use the ``critique_*`` prefix "
            f"family (cmd_critique scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
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
# (10) Sibling parity -- W607-W cmd_relate surface unchanged
# ---------------------------------------------------------------------------


def test_w607_w_cmd_relate_unaffected():
    """Sibling parity guard: W607-W cmd_relate source surface unchanged.

    W607-Y lands only in cmd_critique. The W607-W cmd_relate surface
    (per-helper ``_run_check`` wrapper + ``_w607w_warnings_out``
    accumulator + ``relate_*`` marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_relate.py"
    assert src_path.exists(), f"cmd_relate.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607w_warnings_out" in src, (
        "W607-W accumulator removed from cmd_relate; W607-Y must not regress the sibling instrumentation."
    )
    assert "relate_{phase}_failed" in src, (
        "W607-W marker prefix removed from cmd_relate; W607-Y must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (11) Source-level guard: cmd_critique carries the canonical W607-Y accumulator
# ---------------------------------------------------------------------------


def test_cmd_critique_carries_w607y_accumulator():
    """AST-level guard: cmd_critique source carries the W607-Y accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_critique.py"
    assert src_path.exists(), f"cmd_critique.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607y_warnings_out" in src, (
        "W607-Y accumulator missing from cmd_critique; the substrate-CALL marker plumbing has been removed."
    )
    assert "critique_{phase}_failed" in src, (
        "W607-Y marker prefix template missing from cmd_critique; check the "
        '`f"critique_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside critique().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-Y ``_run_check`` helper not found in cmd_critique AST; the "
        "per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_critique substrate boundary is wrapped.

    W607-Y substrate inventory (top boundaries):

    * parse_diff               -- parse_diff(diff_text)
    * find_changed_symbols     -- find_changed_symbols(conn, regions)
    * run_checks               -- _run_checks_with_status(...)
    * aggregate                -- aggregate(findings, ...)
    * load_overrides           -- _load_critique_overrides()
    * bench_relevance_hint     -- _bench_relevance_hint(regions, ...)
    * compute_risk_level       -- _critique_risk_level(...)
    * emit_findings            -- _emit_critique_findings(...) on --persist

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_critique.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "parse_diff",
        "find_changed_symbols",
        "run_checks",
        "aggregate",
        "load_overrides",
        "bench_relevance_hint",
        "compute_risk_level",
        "emit_findings",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (12/16/20/24 spaces) depending on the
        # site's nesting; accept any of the canonical depths.
        same_line = f'_run_check("{phase}"' in src
        multi_line = (
            f'_run_check(\n        "{phase}"' in src
            or f'_run_check(\n            "{phase}"' in src
            or f'_run_check(\n                "{phase}"' in src
            or f'_run_check(\n                    "{phase}"' in src
            or f'_run_check(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-Y _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
